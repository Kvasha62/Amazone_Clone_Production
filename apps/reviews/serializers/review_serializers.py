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
      product_uuid — канонический публичный идентификатор товара
      (то же значение, что каталог отдаёт как ``id``);
      product_id — устаревший целочисленный PK, принимается на окно
      совместимости. Оба сразу → 400.
    """

    product_id = serializers.IntegerField(
        help_text='DEPRECATED: целочисленный PK товара. Используйте product_uuid.',
        required=False,
    )
    product_uuid = serializers.UUIDField(
        help_text='UUID товара — канонический публичный идентификатор.',
        required=False,
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

    def validate(self, data):
        """Ровно один идентификатор товара (F-8, #73)."""
        product_id = data.get('product_id')
        product_uuid = data.get('product_uuid')

        if product_id and product_uuid:
            raise serializers.ValidationError(
                'Укажите либо product_uuid, либо product_id (устар.), но не оба.',
            )
        if not product_id and not product_uuid:
            raise serializers.ValidationError(
                {'product_uuid': 'Обязательное поле.'},
            )
        return data


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
    # F-8 (#73): публичный идентификатор товара. 'product_id' остаётся в
    # payload на окно совместимости и помечен как deprecated.
    product_uuid = serializers.UUIDField(
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
            'product_uuid',
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
    # F-8 (#73): публичный идентификатор товара; 'product_id' — deprecated.
    product_uuid = serializers.UUIDField(
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
            'product_uuid',
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
