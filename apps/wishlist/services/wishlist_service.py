# ────────────────────────────────────────────────────────────────────────
# apps/wishlist/services/wishlist_service.py — бизнес-логика избранного.
#
# Service Layer: View → Serializer → Service → ORM
#
# ОПЕРАЦИИ:
#   get_or_create()  — получить/создать список желаний
#   add_item()       — добавить товар
#   remove_item()    — удалить товар
#   move_to_cart()   — перенести товар(ы) в корзину
#   clear()          — очистить список
#   list_items()     — получить все товары списка
#
# 📖 https://martinfowler.com/eaaCatalog/serviceLayer.html
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging

from django.db import models, transaction
from django.db.models import F
from django.db.models.functions import Greatest

from rest_framework.exceptions import NotFound, ValidationError

from apps.wishlist.constants import MAX_WISHLIST_ITEMS
from apps.wishlist.models import Wishlist, WishlistItem

logger = logging.getLogger(__name__)


class WishlistService:
    """Бизнес-логика списка желаний."""

    # ==============================================================
    # Получение / создание списка
    # ==============================================================

    @staticmethod
    def get_or_create(user) -> Wishlist:
        """
        Возвращает список желаний пользователя.
        Создаёт при первом вызове.

        ARGS:
            user: экземпляр User

        RETURNS:
            Wishlist
        """
        wishlist, created = Wishlist.objects.get_or_create(
            user=user,
        )
        if created:
            logger.info(
                'wishlist_created',
                extra={'user_id': user.pk},
            )
        return wishlist

    # ==============================================================
    # Добавление товара
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def add_item(
        user,
        variant,
        *,
        note: str = '',
    ) -> WishlistItem:
        """
        Добавляет вариант товара в список желаний.

        ARGS:
            user: пользователь
            variant: ProductVariant
            note: заметка (опционально)

        RETURNS:
            WishlistItem

        RAISES:
            ValidationError: если лимит превышен или товар уже в списке
        """
        wishlist = WishlistService.get_or_create(user)

        # Проверка лимита
        if wishlist.items_count >= MAX_WISHLIST_ITEMS:
            raise ValidationError({
                'detail': (
                    f'Максимальное количество товаров в избранном — '
                    f'{MAX_WISHLIST_ITEMS}.'
                ),
            })

        # Проверка дубля
        if WishlistItem.objects.filter(
            wishlist=wishlist,
            variant=variant,
        ).exists():
            raise ValidationError({
                'detail': 'Товар уже в списке желаний.',
            })

        item = WishlistItem.objects.create(
            wishlist=wishlist,
            variant=variant,
            note=note,
        )

        # Обновляем items_count
        Wishlist.objects.filter(pk=wishlist.pk).update(
            items_count=F('items_count') + 1,
        )

        logger.info(
            'wishlist_item_added',
            extra={
                'user_id': user.pk,
                'variant_id': variant.pk,
                'wishlist_item_id': item.pk,
            },
        )

        return item

    # ==============================================================
    # Удаление товара
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def remove_item(user, item_id: int) -> None:
        """
        Удаляет товар из списка желаний.

        RAISES:
            NotFound: если позиция не найдена
        """
        wishlist = WishlistService.get_or_create(user)

        try:
            item = WishlistItem.objects.get(
                pk=item_id,
                wishlist=wishlist,
            )
        except WishlistItem.DoesNotExist:
            raise NotFound('Позиция не найдена в списке желаний.')

        item.delete()

        # Обновляем items_count (не ниже 0)
        Wishlist.objects.filter(pk=wishlist.pk).update(
            items_count=Greatest(F('items_count') - 1, 0),
        )

        logger.info(
            'wishlist_item_removed',
            extra={
                'user_id': user.pk,
                'item_id': item_id,
            },
        )

    # ==============================================================
    # Перенос в корзину
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def move_to_cart(
        user,
        *,
        item_ids: list[int] | None = None,
        variant_id: int | None = None,
        quantity: int = 1,
    ) -> int:
        """
        Переносит товар(ы) из избранного в корзину.

        ARGS:
            user: пользователь
            item_ids: список ID позиций (для массового переноса)
            variant_id: ID одного варианта (для единичного)
            quantity: количество для корзины

        RETURNS:
            Количество перенесённых товаров
        """
        from apps.cart.models import Cart
        from apps.cart.services.cart_service import CartService

        wishlist = WishlistService.get_or_create(user)

        if variant_id:
            # Единичный перенос по variant_id
            items_qs = WishlistItem.objects.filter(
                wishlist=wishlist,
                variant_id=variant_id,
            )
        elif item_ids:
            # Массовый перенос по item_ids
            items_qs = WishlistItem.objects.filter(
                pk__in=item_ids,
                wishlist=wishlist,
            )
        else:
            raise ValidationError({
                'detail': 'Укажите variant_id или item_ids.',
            })

        items = list(items_qs)
        if not items:
            raise NotFound('Товары не найдены в списке желаний.')

        # Получаем или создаём активную корзину
        cart, _ = Cart.objects.get_or_create(
            user=user,
            is_active=True,
        )

        moved = 0
        for item in items:
            try:
                CartService.add_item(
                    cart=cart,
                    variant_id=item.variant_id,
                    quantity=quantity,
                )
                item.delete()
                moved += 1
            except (NotFound, ValidationError) as exc:
                # Ожидаемые доменные причины пропуска одной позиции
                # (нет варианта, неактивен, лимит корзины и т.п.) не
                # останавливают перенос остальных. Неожиданные ошибки
                # (БД, программные) пробрасываются и откатывают операцию.
                logger.warning(
                    'wishlist_move_to_cart_skip',
                    extra={
                        'item_id': item.pk,
                        'variant_id': item.variant_id,
                        'error': str(exc),
                    },
                )

        # Обновляем items_count (не ниже 0)
        Wishlist.objects.filter(pk=wishlist.pk).update(
            items_count=Greatest(F('items_count') - moved, 0),
        )

        logger.info(
            'wishlist_moved_to_cart',
            extra={
                'user_id': user.pk,
                'moved_count': moved,
            },
        )

        return moved

    # ==============================================================
    # Очистка списка
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def clear(user) -> int:
        """
        Удаляет все товары из списка желаний.

        RETURNS:
            Количество удалённых товаров
        """
        wishlist = WishlistService.get_or_create(user)

        count, _ = WishlistItem.objects.filter(
            wishlist=wishlist,
        ).delete()

        Wishlist.objects.filter(pk=wishlist.pk).update(
            items_count=0,
        )

        logger.info(
            'wishlist_cleared',
            extra={
                'user_id': user.pk,
                'removed_count': count,
            },
        )

        return count

    # ==============================================================
    # Получение списка товаров
    # ==============================================================

    @staticmethod
    def list_items(user):
        """
        Возвращает QuerySet позиций списка желаний.

        Prefetch: variant, variant.product, variant.price.
        """
        wishlist = WishlistService.get_or_create(user)

        return (
            WishlistItem.objects
            .filter(wishlist=wishlist)
            .select_related(
                'variant',
                'variant__product',
                'variant__price',
            )
            .order_by('sort_order', '-created_at')
        )
