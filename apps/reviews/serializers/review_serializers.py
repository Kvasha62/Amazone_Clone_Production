# ────────────────────────────────────────────────────────────────────────
# apps/reviews/serializers/review_serializers.py
#
# СЕРИАЛИЗАТОРЫ ОТЗЫВОВ:
#   CreateReviewInputSerializer — POST (создание)
#   UpdateReviewInputSerializer — PATCH (обновление)
#   HelpfulVoteSerializer       — POST /{id}/helpful/ (голос)
#   ReviewListSerializer        — краткий отзыв (список)
#   ReviewSerializer            — полный отзыв (детали)
# ────────────────────────────────────────────────────────────────────────

from rest_framework import serializers

from apps.reviews.constants import (
    MAX_RATING,
    MIN_RATING,
    MAX_REVIEW_TEXT_LENGTH,
    MIN_REVIEW_TEXT_LENGTH,
)
from apps.reviews.models import Review


# ==============================================================
# INPUT
# ==============================================================

class CreateReviewInputSerializer(serializers.Serializer):
    """POST /api/v1/reviews/

    ИДЕНТИФИКАТОР ТОВАРА (F-8, issue #73):
      ``product_id`` — ЕДИНСТВЕННЫЙ способ сослаться на товар; его тип —
      UUID (ровно то значение, которое каталог отдаёт как ``id``).

      Параллельного поля ``product_uuid`` НЕТ и быть не должно: два ключа
      для одной ссылки — это два пространства идентификаторов на одном
      ресурсе, ради устранения которых и заведён frozen contract.

      Целочисленный PK товара публичным идентификатором никогда не
      являлся (каталог его не отдаёт), поэтому int-значение здесь не
      «устаревший вариант», а ошибка — 400 с явным пояснением.
    """

    product_id = serializers.UUIDField(
        help_text=(
            'UUID товара — канонический публичный идентификатор '
            '(значение поля `id` из каталога).'
        ),
        required=True,
    )
    rating = serializers.IntegerField(
        min_value=MIN_RATING,
        max_value=MAX_RATING,
        help_text=f'Рейтинг от {MIN_RATING} до {MAX_RATING}.',
    )
    title = serializers.CharField(
        max_length=200,
        required=False,
        default='',
        allow_blank=True,
    )
    text = serializers.CharField(
        max_length=MAX_REVIEW_TEXT_LENGTH,
        min_length=MIN_REVIEW_TEXT_LENGTH,
        help_text=f'От {MIN_REVIEW_TEXT_LENGTH} до {MAX_REVIEW_TEXT_LENGTH} символов.',
    )

    def to_internal_value(self, data):
        """Отклоняет целочисленный product_id ДО разбора UUIDField.

        ЗАЧЕМ: DRF UUIDField принимает int и молча трактует его как
        ``UUID(int=value)``. То есть product_id=1 превратился бы в
        ``00000000-...-0001`` — синтаксически корректный, но никому не
        принадлежащий UUID, и клиент получил бы 404 «товар не найден»
        вместо внятного «product_id должен быть UUID».

        Целочисленный PK товара наружу никогда не отдавался, поэтому это
        именно ошибка формата → 400.
        """
        product_id = data.get('product_id') if hasattr(data, 'get') else None
        if isinstance(product_id, bool) or isinstance(product_id, int) or (
            isinstance(product_id, str) and product_id.isascii()
            and product_id.isdigit()
        ):
            raise serializers.ValidationError({
                'product_id': (
                    'product_id должен быть UUID товара, а не числовым PK.'
                ),
            })
        return super().to_internal_value(data)


class UpdateReviewInputSerializer(serializers.Serializer):
    """PATCH /api/v1/reviews/{id}/"""

    rating = serializers.IntegerField(
        min_value=MIN_RATING,
        max_value=MAX_RATING,
        required=False,
    )
    title = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
    )
    text = serializers.CharField(
        max_length=MAX_REVIEW_TEXT_LENGTH,
        min_length=MIN_REVIEW_TEXT_LENGTH,
        required=False,
    )


class HelpfulVoteSerializer(serializers.Serializer):
    """POST /api/v1/reviews/{id}/helpful/"""

    vote = serializers.ChoiceField(
        choices=['yes', 'no'],
        help_text='"yes" = полезно, "no" = неполезно.',
    )


# ==============================================================
# OUTPUT
# ==============================================================

class ReviewListSerializer(serializers.ModelSerializer):
    """Краткий отзыв для списка (пагинированный)."""

    user_email = serializers.CharField(
        source='user.email', read_only=True,
    )
    # F-8 (#73): ссылка на товар — ровно одно поле product_id, тип UUID.
    # Явное объявление обязательно: ModelSerializer иначе подставил бы
    # целочисленный FK-атрибут product_id и вернул внутренний PK.
    product_id = serializers.UUIDField(
        source='product.uuid', read_only=True,
    )
    helpful_score = serializers.IntegerField(read_only=True)
    # my_vote заполняется в view ('yes'/'no'/None)
    my_vote = serializers.CharField(
        read_only=True,
        default=None,
        allow_null=True,
    )

    class Meta:
        model = Review
        fields = (
            'id',
            'user_id',
            'user_email',
            'product_id',
            'rating',
            'title',
            'verified_purchase',
            'helpful_yes',
            'helpful_no',
            'helpful_score',
            'my_vote',
            'created_at',
        )
        read_only_fields = fields


class ReviewSerializer(serializers.ModelSerializer):
    """Полный отзыв с текстом."""

    user_email = serializers.CharField(
        source='user.email', read_only=True,
    )
    # F-8 (#73): ссылка на товар — ровно одно поле product_id, тип UUID
    # (см. пояснение в ReviewListSerializer).
    product_id = serializers.UUIDField(
        source='product.uuid', read_only=True,
    )
    helpful_score = serializers.IntegerField(read_only=True)
    # my_vote заполняется в view ('yes'/'no'/None)
    my_vote = serializers.CharField(
        read_only=True,
        default=None,
        allow_null=True,
    )

    class Meta:
        model = Review
        fields = (
            'id',
            'user_id',
            'user_email',
            'product_id',
            'rating',
            'title',
            'text',
            'verified_purchase',
            'is_approved',
            'helpful_yes',
            'helpful_no',
            'helpful_score',
            'my_vote',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields
