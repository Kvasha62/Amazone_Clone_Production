# ────────────────────────────────────────────────────────────────────────
# apps/reviews/api_views/review_views.py
#
# ЭНДПОИНТЫ:
#   GET/POST  /api/v1/reviews/               — список / создание
#   GET/PATCH /api/v1/reviews/{id}/          — детали / обновление
#   DELETE    /api/v1/reviews/{id}/          — удаление
#   POST      /api/v1/reviews/{id}/helpful/  — голос «полезно/неполезно»
#
# СОРТИРОВКА (GET):
#   ?ordering=rating       — по рейтингу ↑
#   ?ordering=-rating      — по рейтингу ↓
#   ?ordering=created_at   — по дате ↑
#   ?ordering=-created_at  — по дате ↓ (default)
#   ?ordering=helpful      — по полезности ↓
#
# ФИЛЬТРАЦИЯ (GET):
#   ?rating=5              — только 5-звёздочные
#   ?rating_gte=4          — рейтинг ≥ 4
#   ?rating_lte=2          — рейтинг ≤ 2
#   ?verified=true         — только с подтверждённой покупкой
#
# ПАГИНАЦИЯ:
#   ?page=1&page_size=20
# ────────────────────────────────────────────────────────────────────────

import logging

from django.db.models import F
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.identifiers import parse_uuid
from apps.core.pagination import (
    build_paginated_response_data,
    ensure_deterministic_ordering,
    paginate_queryset,
    pagination_parameters,
)
from apps.core.serializers import PaginationResponseSerializer

from apps.catalog.models import Product
from apps.reviews.models import Review
from apps.reviews.serializers import (
    CreateReviewInputSerializer,
    HelpfulVoteSerializer,
    ReviewListSerializer,
    ReviewSerializer,
    UpdateReviewInputSerializer,
)
from apps.reviews.services.review_service import ReviewService

try:
    from drf_spectacular.utils import extend_schema, extend_schema_view
except ImportError:
    def extend_schema(**kwargs):
        def decorator(func):
            return func
        return decorator

    def extend_schema_view(**kwargs):
        def decorator(cls):
            return cls
        return decorator

logger = logging.getLogger(__name__)

# ── Допустимые поля сортировки ──
VALID_ORDERINGS = {
    'rating', '-rating',
    'created_at', '-created_at',
    'helpful',
}


@extend_schema_view(
    get=extend_schema(
        summary='Список отзывов',
        description=(
            'Возвращает отзывы. Публичный эндпоинт — авторизация не нужна.\n'
            'Фильтрация: ?product_id=<uuid>&user_id=&rating=&rating_gte=&rating_lte=&verified=\n'
            'Сортировка: ?ordering=-rating|rating|-created_at|created_at|helpful\n'
            'Пагинация: ?page=1&page_size=20'
        ),
        parameters=pagination_parameters(),
        responses={200: PaginationResponseSerializer},
    ),
    post=extend_schema(
        summary='Создать отзыв',
        request=CreateReviewInputSerializer,
        responses={201: ReviewSerializer},
    ),
)
class ReviewListView(APIView):
    """
    GET/POST /api/v1/reviews/

    GET — публичный (AllowAny): можно просматривать отзывы без авторизации.
    POST — требует авторизации (IsAuthenticated).
    """

    def get_permissions(self):
        """Разные права для GET и POST."""
        if self.request.method == 'POST':
            return [IsAuthenticated()]
        return [AllowAny()]

    def get(self, request):
        """
        GET /api/v1/reviews/

        Query params:
          ?product_id=<uuid>   — отзывы на товар (канонический идентификатор)
          ?user_id=2           — отзывы пользователя
          ?rating=5            — только 5-звёздочные
          ?rating_gte=4        — рейтинг ≥ 4
          ?rating_lte=2        — рейтинг ≤ 2
          ?verified=true       — только подтверждённые покупки
          ?ordering=-rating    — сортировка
          ?page=1              — страница
          ?page_size=20        — размер страницы
        """
        qs = Review.objects.approved().with_user().with_product()

        # ── Фильтр по товару (F-8, #73) ──
        # Ровно один ключ — product_id, и его тип UUID. Второго
        # (product_uuid) не существует: параллельные пространства
        # идентификаторов на одном ресурсе запрещены frozen contract.
        # Некорректный UUID → 400, а не тихий пустой список.
        product_id = request.query_params.get('product_id')

        if product_id:
            parsed_uuid = parse_uuid(product_id)
            if parsed_uuid is None:
                raise ValidationError({
                    'product_id': (
                        'Некорректный product_id: ожидается UUID товара.'
                    ),
                })
            product = Product.objects.filter(uuid=parsed_uuid).first()
            if product is None:
                # Валидный, но неизвестный UUID — пустая выдача, а не 404:
                # список отзывов существует, просто он пуст.
                qs = qs.none()
            else:
                qs = qs.for_product_id(product.pk)

        # ── Фильтр по пользователю ──
        user_id = request.query_params.get('user_id')
        if user_id:
            try:
                uid = int(user_id)
            except (ValueError, TypeError):
                raise ValidationError({'user_id': 'Некорректный user_id.'})
            # Non-staff callers may filter only by their own user id. Return
            # the same canonical 404 used for ownership hiding instead of
            # silently changing the requested filter.
            if request.user.is_authenticated:
                if not request.user.is_staff and uid != request.user.pk:
                    raise NotFound('Ресурс не найден.')
            else:
                # Аноним не может запрашивать чужие отзывы
                _, meta = paginate_queryset(Review.objects.none(), request)
                return Response(
                    build_paginated_response_data(request, [], meta),
                )
            qs = qs.for_user_id(uid)
        elif not product_id:
            # Нет фильтра по товару — если авторизован, показываем свои отзывы;
            # если нет — пустой список (нужен product_id или user_id)
            if request.user.is_authenticated:
                qs = qs.for_user(request.user)
            else:
                _, meta = paginate_queryset(Review.objects.none(), request)
                return Response(
                    build_paginated_response_data(request, [], meta),
                )

        # ── Фильтр по рейтингу (точное совпадение) ──
        rating_exact = request.query_params.get('rating')
        if rating_exact:
            try:
                qs = qs.with_rating(int(rating_exact))
            except (ValueError, TypeError):
                raise ValidationError({'rating': 'Рейтинг должен быть числом 1-5.'})

        # ── Фильтр по рейтингу (≥) ──
        rating_gte = request.query_params.get('rating_gte')
        if rating_gte:
            try:
                qs = qs.filter(rating__gte=int(rating_gte))
            except (ValueError, TypeError):
                raise ValidationError({'rating_gte': 'Должно быть числом.'})

        # ── Фильтр по рейтингу (≤) ──
        rating_lte = request.query_params.get('rating_lte')
        if rating_lte:
            try:
                qs = qs.filter(rating__lte=int(rating_lte))
            except (ValueError, TypeError):
                raise ValidationError({'rating_lte': 'Должно быть числом.'})

        # ── Фильтр: подтверждённая покупка ──
        verified = request.query_params.get('verified')
        if verified and verified.lower() in ('true', '1', 'yes'):
            qs = qs.verified()

        # ── Сортировка ──
        ordering = request.query_params.get('ordering', '-created_at')
        if ordering not in VALID_ORDERINGS:
            ordering = '-created_at'

        if ordering == 'helpful':
            # Apply the existing “most helpful” ordering in the database and
            # keep deterministic ties for stable pages. The alias intentionally
            # differs from the ``helpful_score`` property on ``Review``; Django
            # would try to set that read-only property on each instance and
            # raise ``AttributeError``.
            qs = ensure_deterministic_ordering(
                qs.annotate(
                    helpful_order_score=F('helpful_yes') - F('helpful_no'),
                ),
                ['-helpful_order_score', '-created_at'],
            )
        else:
            qs = ensure_deterministic_ordering(qs, [ordering])

        # ── Пагинация (canonical API-05 envelope) ──
        page_items, meta = paginate_queryset(qs, request)

        serializer = ReviewListSerializer(page_items, many=True)
        data = serializer.data

        # ── Аннотируем my_vote для авторизованных пользователей ──
        if request.user.is_authenticated:
            from apps.reviews.models import ReviewHelpfulVote
            review_ids = [r.id for r in page_items]
            user_votes = dict(
                ReviewHelpfulVote.objects.filter(
                    user=request.user,
                    review_id__in=review_ids,
                ).values_list('review_id', 'vote')
            )
            for item in data:
                item['my_vote'] = user_votes.get(item['id'])

        return Response(
            build_paginated_response_data(request, data, meta),
        )

    def post(self, request):
        """POST /api/v1/reviews/ — создать отзыв (требует авторизацию)."""
        input_ser = CreateReviewInputSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)
        data = input_ser.validated_data

        # F-8 (#73): единственная ссылка на товар — product_id типа UUID.
        # Формат уже провалидирован CreateReviewInputSerializer (UUIDField),
        # поэтому здесь остаётся только резолв существования.
        try:
            product = Product.objects.get(uuid=data['product_id'])
        except Product.DoesNotExist:
            raise NotFound('Товар не найден.')

        review = ReviewService.create_review(
            user=request.user,
            product=product,
            rating=data['rating'],
            text=data['text'],
            title=data.get('title', ''),
        )

        return Response(
            ReviewSerializer(review).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(
        summary='Детали отзыва',
        responses={200: ReviewSerializer},
    ),
    patch=extend_schema(
        summary='Обновить отзыв',
        request=UpdateReviewInputSerializer,
        responses={200: ReviewSerializer},
    ),
    delete=extend_schema(
        summary='Удалить отзыв',
        responses={204: None},
    ),
)
class ReviewDetailView(APIView):
    """GET/PATCH/DELETE /api/v1/reviews/{id}/"""

    def get_permissions(self):
        """GET — публичный, PATCH/DELETE — требуют авторизацию."""
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def _get_review(self, request, review_id: int) -> Review:
        try:
            review = Review.objects.select_related('user', 'product').get(pk=review_id)
        except Review.DoesNotExist:
            raise NotFound('Отзыв не найден.')

        # Не-staff видит только свои или одобренные
        if request.user.is_authenticated:
            if not request.user.is_staff:
                if review.user_id != request.user.pk and not review.is_approved:
                    raise NotFound('Отзыв не найден.')
        else:
            # Аноним видит только одобренные
            if not review.is_approved:
                raise NotFound('Отзыв не найден.')

        return review

    def get(self, request, review_id: int):
        """GET — публичный (AllowAny)."""
        review = self._get_review(request, review_id)
        return Response(ReviewSerializer(review).data)

    def patch(self, request, review_id: int):
        """PATCH — только автор."""
        review = self._get_review(request, review_id)

        input_ser = UpdateReviewInputSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)

        review = ReviewService.update_review(
            review,
            user=request.user,
            **input_ser.validated_data,
        )
        return Response(ReviewSerializer(review).data)

    def delete(self, request, review_id: int):
        """DELETE — автор или staff."""
        review = self._get_review(request, review_id)
        ReviewService.delete_review(review, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    summary='Голос за полезность отзыва',
    description=(
        'Пользователь голосует: полезно или нет.\n'
        'Toggle-логика: повторный тот же голос = отмена; '
        'другой голос = переключение.'
    ),
    request=HelpfulVoteSerializer,
    responses={200: ReviewSerializer},
)
class ReviewHelpfulView(APIView):
    """
    POST /api/v1/reviews/{id}/helpful/

    Body: {"vote": "yes"} или {"vote": "no"}

    TOGGLE-ЛОГИКА (как Reddit):
      • Первый голос → добавляет
      • Повторный тот же → отмена (toggle off)
      • Другой голос → переключение (yes→no или no→yes)

    Автор отзыва не может голосовать за свой же отзыв.
    """

    permission_classes = (IsAuthenticated,)

    def post(self, request, review_id: int):
        try:
            review = Review.objects.select_related('user', 'product').get(pk=review_id)
        except Review.DoesNotExist:
            raise NotFound('Отзыв не найден.')

        input_ser = HelpfulVoteSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)

        vote = input_ser.validated_data['vote']
        review = ReviewService.vote_helpful(review, user=request.user, vote=vote)

        # Возвращаем отзыв + текущий голос пользователя
        data = ReviewSerializer(review).data
        data['my_vote'] = ReviewService.get_user_vote(review, request.user)
        return Response(data)
