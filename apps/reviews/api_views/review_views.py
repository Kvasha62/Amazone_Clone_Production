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

from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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

# ── Кол-во отзывов на страницу по умолчанию ──
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

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
            'Фильтрация: ?product_id=&product_uuid=&user_id=&rating=&rating_gte=&rating_lte=&verified=\n'
            'Сортировка: ?ordering=-rating|rating|-created_at|created_at|helpful\n'
            'Пагинация: ?page=1&page_size=20'
        ),
        responses={200: ReviewListSerializer(many=True)},
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
          ?product_id=1        — отзывы на товар (числовой PK)
          ?product_uuid=abc    — отзывы на товар (UUID)
          ?user_id=2           — отзывы пользователя
          ?rating=5            — только 5-звёздочные
          ?rating_gte=4        — рейтинг ≥ 4
          ?rating_lte=2        — рейтинг ≤ 2
          ?verified=true       — только подтверждённые покупки
          ?ordering=-rating    — сортировка
          ?page=1              — страница
          ?page_size=20        — размер страницы
        """
        qs = Review.objects.approved().with_user()

        # ── Фильтр по товару (PK) ──
        product_id = request.query_params.get('product_id')
        if product_id:
            try:
                qs = qs.for_product_id(int(product_id))
            except (ValueError, TypeError):
                raise ValidationError({'product_id': 'Некорректный product_id.'})

        # ── Фильтр по товару (UUID) — для React-фронтенда ──
        product_uuid = request.query_params.get('product_uuid')
        if product_uuid and not product_id:
            try:
                import uuid as _uuid
                _uuid.UUID(str(product_uuid))  # валидация формата
                product = Product.objects.filter(uuid=product_uuid).first()
                if product:
                    qs = qs.for_product_id(product.pk)
                else:
                    qs = qs.none()
            except (ValueError, AttributeError):
                qs = qs.none()

        # ── Фильтр по пользователю ──
        user_id = request.query_params.get('user_id')
        if user_id:
            try:
                uid = int(user_id)
            except (ValueError, TypeError):
                raise ValidationError({'user_id': 'Некорректный user_id.'})
            # Не-staff видит только свои отзывы (если авторизован)
            if request.user.is_authenticated:
                if not request.user.is_staff and uid != request.user.pk:
                    uid = request.user.pk
            else:
                # Аноним не может запрашивать чужие отзывы
                return Response([])
            qs = qs.for_user_id(uid)
        elif not product_id and not product_uuid:
            # Нет фильтра по товару — если авторизован, показываем свои отзывы;
            # если нет — пустой список (нужен product_id/product_uuid)
            if request.user.is_authenticated:
                qs = qs.for_user(request.user)
            else:
                return Response([])

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
            # helpful_score = helpful_yes - helpful_no (вычисляемое, сортируем в Python)
            # Для больших объёмов лучше сделать annotate, но для MVP — sort
            reviews_list = list(qs)
            reviews_list.sort(
                key=lambda r: (r.helpful_yes - r.helpful_no),
                reverse=True,
            )
            # Пагинация для отсортированного списка
            page_num = int(request.query_params.get('page', 1))
            page_size = min(
                int(request.query_params.get('page_size', DEFAULT_PAGE_SIZE)),
                MAX_PAGE_SIZE,
            )
            paginator = Paginator(reviews_list, page_size)
            try:
                page = paginator.page(page_num)
            except PageNotAnInteger:
                page = paginator.page(1)
            except EmptyPage:
                page = paginator.page(paginator.num_pages)
            serializer = ReviewListSerializer(page.object_list, many=True)
            data = serializer.data

            # ── Аннотируем my_vote для авторизованных ──
            if request.user.is_authenticated:
                from apps.reviews.models import ReviewHelpfulVote
                review_ids = [r.id for r in page.object_list]
                user_votes = dict(
                    ReviewHelpfulVote.objects.filter(
                        user=request.user,
                        review_id__in=review_ids,
                    ).values_list('review_id', 'vote')
                )
                for item in data:
                    item['my_vote'] = user_votes.get(item['id'])

            return Response({
                'count': paginator.count,
                'page': page.number,
                'page_size': page_size,
                'results': data,
            })

        # Обычная DB-сортировка
        if ordering in ('-created_at',):
            qs = qs.order_by('-created_at')
        elif ordering == 'created_at':
            qs = qs.order_by('created_at')
        elif ordering == 'rating':
            qs = qs.order_by('rating')
        elif ordering == '-rating':
            qs = qs.order_by('-rating')

        # ── Пагинация ──
        page_num = int(request.query_params.get('page', 1))
        page_size = min(
            int(request.query_params.get('page_size', DEFAULT_PAGE_SIZE)),
            MAX_PAGE_SIZE,
        )
        paginator = Paginator(qs, page_size)
        try:
            page = paginator.page(page_num)
        except PageNotAnInteger:
            page = paginator.page(1)
        except EmptyPage:
            page = paginator.page(paginator.num_pages)

        serializer = ReviewListSerializer(page.object_list, many=True)
        data = serializer.data

        # ── Аннотируем my_vote для авторизованных пользователей ──
        if request.user.is_authenticated:
            from apps.reviews.models import ReviewHelpfulVote
            review_ids = [r.id for r in page.object_list]
            user_votes = dict(
                ReviewHelpfulVote.objects.filter(
                    user=request.user,
                    review_id__in=review_ids,
                ).values_list('review_id', 'vote')
            )
            for item in data:
                item['my_vote'] = user_votes.get(item['id'])

        return Response({
            'count': paginator.count,
            'page': page.number,
            'page_size': page_size,
            'total_pages': paginator.num_pages,
            'results': data,
        })

    def post(self, request):
        """POST /api/v1/reviews/ — создать отзыв (требует авторизацию)."""
        input_ser = CreateReviewInputSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)
        data = input_ser.validated_data

        # 🔴 Поддержка product_uuid — для React-фронтенда
        product_uuid = data.get('product_uuid')
        product_id = data.get('product_id')

        if product_uuid:
            try:
                product = Product.objects.get(uuid=product_uuid)
            except Product.DoesNotExist:
                raise NotFound('Товар не найден.')
        elif product_id:
            try:
                product = Product.objects.get(pk=product_id)
            except Product.DoesNotExist:
                raise NotFound('Товар не найден.')
        else:
            raise ValidationError('Укажите product_id или product_uuid.')

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
