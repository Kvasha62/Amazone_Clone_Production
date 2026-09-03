# ────────────────────────────────────────────────────────────────────────
# apps/orders/services/order_service.py — бизнес-логика заказов.
#
# АРХИТЕКТУРНЫЙ ПРИНЦИП «Service Layer» (сервисный слой):
#   View → сериализатор (валидация) → сервис (бизнес-логика) → ORM (SQL)
#
#   View НЕ знает про:
#     • transaction.atomic (транзакции)
#     • select_for_update (пессимистичные блокировки)
#     • проверку стока, лимитов, статусов
#     • генерацию номера заказа
#     • расчёт суммы
#   Всё инкапсулировано в сервисе.
#
# БЕЗОПАСНОСТЬ КОНКУРЕНТНОГО ДОСТУПА:
#   Все mutating-методы используют:
#     1. @transaction.atomic — атомарные транзакции
#     2. select_for_update() — пессимистичная блокировка строк
#     3. UniqueConstraint — защита от дублей на уровне БД
#
# ОПЕРАЦИИ:
#   create_from_cart()   — создать заказ из корзины
#   confirm()            — подтвердить (оплачено)
#   cancel()             — отменить
#   transition_status()  — общий метод перехода статуса
#   apply_coupon()       — применить купон
#   remove_coupon()      — снять купон
#
# 📖 Про Service Layer: https://martinfowler.com/eaaCatalog/serviceLayer.html
# 📖 Про select_for_update: https://docs.djangoproject.com/en/stable/ref/models/querysets/#select-for-update
# 📖 Про transaction.atomic: https://docs.djangoproject.com/en/stable/topics/db/transactions/#django.db.transaction.atomic
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from rest_framework.exceptions import NotFound, ValidationError

from apps.cart.models import Cart, CartItem
from apps.orders.constants import (
    CANCELLATION_REASONS,
    MAX_ORDER_ITEMS,
    MIN_ORDER_TOTAL,
)
from apps.orders.models import Order, OrderItem
from apps.orders.models.order import (
    ORDER_STATUS_TRANSITIONS,
    OrderStatus,
)

logger = logging.getLogger(__name__)


class OrderService:
    """Business logic for orders and order-owned state mutations."""

    @staticmethod
    @transaction.atomic
    def create_from_cart(
        user,
        cart: Cart,
        *,
        notes: str = '',
    ) -> Order:
        """Create an order from an active user's cart.

        F-08 / PROD-006: доставка НЕ является входным параметром.
        ``Order.delivery_cost`` вычисляется на сервере через
        ``ShippingService.calculate_order_delivery_cost()`` из суммы
        заказа, адреса доставки и весов вариантов — поэтому ни один
        вызывающий код (включая checkout API) не может подставить
        денежную сумму доставки из запроса клиента.
        """
        from decimal import Decimal

        if cart.user_id != user.pk:
            raise NotFound('Корзина не найдена.')
        if not cart.is_active:
            raise NotFound('Корзина не найдена.')

        cart = (
            Cart.objects
            .select_for_update()
            .prefetch_related(
                'items',
                'items__variant',
                'items__variant__product',
                'items__variant__price',
            )
            .get(pk=cart.pk)
        )

        items = list(cart.items.all())
        if not items:
            raise ValidationError({
                'detail': 'Невозможно оформить заказ из пустой корзины.',
            })

        from apps.catalog.constants import ProductStatus

        valid_items: list[CartItem] = []
        for item in items:
            variant = item.variant
            if not variant or not variant.is_active:
                logger.debug(
                    'order_skip_variant_inactive',
                    extra={'variant_id': item.variant_id},
                )
                continue
            if variant.product.status != ProductStatus.ACTIVE:
                logger.debug(
                    'order_skip_product_unavailable',
                    extra={'product_id': variant.product_id},
                )
                continue
            price_obj = getattr(variant, 'price', None)
            if price_obj is None:
                logger.debug(
                    'order_skip_no_price',
                    extra={'variant_id': item.variant_id},
                )
                continue
            valid_items.append(item)

        if not valid_items:
            raise ValidationError({
                'detail': 'Нет доступных для заказа товаров в корзине.',
            })
        if len(valid_items) > MAX_ORDER_ITEMS:
            raise ValidationError({
                'detail': (
                    f'Максимум позиций в заказе — {MAX_ORDER_ITEMS}. '
                    f'У вас {len(valid_items)}.'
                ),
            })

        address = user.addresses.filter(is_default=True).first()
        if address is None:
            address = user.addresses.first()
        if address is None:
            raise ValidationError({
                'detail': 'Добавьте адрес доставки перед оформлением заказа.',
            })

        # F-08: вес заказа — серверные данные каталога
        # (ProductVariant.weight), а не запрос клиента. Варианты без веса
        # просто не участвуют в весовой части тарифа.
        total_weight = Decimal('0.00')
        for cart_item in valid_items:
            variant_weight = cart_item.variant.weight
            if variant_weight is None:
                continue
            total_weight += variant_weight * cart_item.quantity
        weight_kg = total_weight if total_weight > 0 else None

        # F-13 / PROD-010: номер заказа выдаёт Order.save() через
        # PostgreSQL SEQUENCE (nextval) — атомарно на стороне СУБД, поэтому
        # здесь нет ни чтения MAX(), ни retry на IntegrityError: параллельные
        # оформления не могут получить один order_number.
        order = Order(
            user=user,
            cart=cart,
            status=OrderStatus.PENDING,
            recipient_name=address.recipient_name,
            country=address.country,
            region=address.region,
            city=address.city,
            street=address.street,
            postal_code=address.postal_code,
            subtotal=Decimal('0.00'),
            # F-08: доставка проставляется ниже серверным расчётом.
            delivery_cost=Decimal('0.00'),
            discount=Decimal('0.00'),
            total=Decimal('0.00'),
            notes=notes,
        )
        order.save()

        order_items_bulk = []
        for cart_item in valid_items:
            variant = cart_item.variant
            price_obj = variant.price
            order_items_bulk.append(OrderItem(
                order=order,
                variant=variant,
                product_name=variant.product.name,
                sku=variant.sku,
                unit_price=price_obj.effective_price,
                quantity=cart_item.quantity,
            ))
        OrderItem.objects.bulk_create(order_items_bulk)

        # Сначала subtotal — он нужен как база для порога бесплатной доставки.
        order.recalculate_total()

        # F-08 / PROD-006: единственная авторитетная цена доставки.
        # Считается на сервере из subtotal, адреса доставки и весов
        # вариантов; денежная сумма из запроса клиента не используется.
        from apps.shipping.services.shipping_service import ShippingService

        order.delivery_cost = ShippingService.calculate_order_delivery_cost(
            order_total=order.subtotal,
            region=order.region,
            city=order.city,
            weight_kg=weight_kg,
        )
        # total = subtotal + delivery_cost - discount (формула модели).
        order.recalculate_total()
        order.save(update_fields=[
            'subtotal',
            'delivery_cost',
            'total',
            'updated_at',
        ])

        if order.total < MIN_ORDER_TOTAL:
            raise ValidationError({
                'detail': (
                    f'Минимальная сумма заказа — {MIN_ORDER_TOTAL}₽. '
                    f'Текущая сумма: {order.total}₽.'
                ),
            })

        cart.is_active = False
        cart.save(update_fields=['is_active', 'updated_at'])

        # PROD-025 / F-18: бизнес-событие «заказ создан» подключено к
        # уведомлениям. Планируется после COMMIT, поэтому откат этой
        # транзакции не оставляет уведомления о несуществующем заказе.
        from apps.notifications.services.notification_events import (
            NotificationEvents,
        )

        NotificationEvents.order_created(order)

        logger.info(
            'order_created',
            extra={
                'order_id': order.pk,
                'order_number': order.order_number,
                'user_id': user.pk,
                'total': str(order.total),
                'delivery_cost': str(order.delivery_cost),
                'items_count': len(order_items_bulk),
            },
        )
        return order

    @staticmethod
    @transaction.atomic
    def transition_status(
        order: Order,
        new_status: str,
        *,
        user=None,
    ) -> Order:
        """Transition an order through its finite state machine.

        ``CANCELLED`` is not accepted here. Cancellation is a dedicated
        domain operation owned by ``cancel()`` (coupon release, inventory,
        payment refund). Callers that need to cancel must use
        ``OrderService.cancel()`` so coupon coordination cannot be bypassed.
        """
        # EDU-002 / ARCH-001 stage 3: single cancellation entrypoint.
        # Reject before locking so accidental callers fail fast and cannot
        # leave CouponUsage / times_used inconsistent with Order status.
        if new_status == OrderStatus.CANCELLED:
            raise ValidationError({
                'detail': (
                    'Отмена заказа выполняется только через '
                    'OrderService.cancel(). transition_status() не принимает '
                    'статус CANCELLED.'
                ),
            })

        order = Order.objects.select_for_update().get(pk=order.pk)
        current_status = order.status

        if order.is_terminal:
            raise ValidationError({
                'detail': (
                    f'Заказ {order.order_number} в терминальном статусе '
                    f'«{order.get_status_display()}». Дальнейшие переходы невозможны.'
                ),
            })

        allowed = ORDER_STATUS_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            raise ValidationError({
                'detail': (
                    f'Переход «{current_status} → {new_status}» недопустим. '
                    f'Допустимые: {[s for s in allowed if s != OrderStatus.CANCELLED]}'
                ),
            })

        order.status = new_status
        now = timezone.now()
        if new_status == OrderStatus.CONFIRMED:
            order.confirmed_at = now
        elif new_status == OrderStatus.DELIVERED:
            order.delivered_at = now

        order.save(update_fields=[
            'status',
            'confirmed_at',
            'delivered_at',
            'cancelled_at',
            'updated_at',
        ])
        OrderService._handle_inventory_transition(order, new_status)

        # PROD-025 / F-18: бизнес-событие «статус заказа изменён» подключено
        # к уведомлениям (in-app + существующая email-задача) и планируется
        # после COMMIT: откат (в т.ч. из-за сбоя резервирования стока) не
        # оставляет уведомления о несостоявшемся переходе. Отмена заказа
        # уведомляется из cancel() — единственного пути перехода в CANCELLED.
        from apps.notifications.services.notification_events import (
            NotificationEvents,
        )

        NotificationEvents.order_status_changed(order, new_status)

        logger.info(
            'order_status_changed',
            extra={
                'order_id': order.pk,
                'order_number': order.order_number,
                'old_status': current_status,
                'new_status': new_status,
                'changed_by': getattr(user, 'pk', None),
            },
        )
        return order

    @staticmethod
    def _handle_inventory_transition(order: Order, new_status: str) -> None:
        """Coordinate order status changes with inventory.

        PROD-003 contract (fail-safe, no silent failures):

        - Failures PROPAGATE to the caller. A failed reservation aborts
          the CONFIRM transition — the order stays PENDING and the calling
          payment confirmation rolls back, so money-state can never advance
          without reserved stock. A failed release/commit aborts the
          CANCELLED/DELIVERED transition; the caller (API view, admin,
          shipment sync, Celery task) observes the error and may retry.
        - All three inventory operations are IDEMPOTENT per order
          (RESERVE-movement pairing, order-level lock), so retrying a
          failed transition is always safe: the completed part is not
          applied twice and the missing part is applied once.
        """
        from apps.inventory.services.inventory_service import InventoryService

        if new_status == OrderStatus.CONFIRMED:
            movements = InventoryService.reserve_stock(order)
            if movements:
                logger.info(
                    'inventory_reserved_on_confirm',
                    extra={'order_id': order.pk, 'movements_count': len(movements)},
                )
        elif new_status == OrderStatus.CANCELLED:
            movements = InventoryService.release_stock(order)
            if movements:
                logger.info(
                    'inventory_released_on_cancel',
                    extra={'order_id': order.pk, 'movements_count': len(movements)},
                )
        elif new_status == OrderStatus.DELIVERED:
            movements = InventoryService.commit_stock(order)
            if movements:
                logger.info(
                    'inventory_committed_on_deliver',
                    extra={'order_id': order.pk, 'movements_count': len(movements)},
                )

    @staticmethod
    def confirm(order: Order, *, user=None) -> Order:
        """Move a PENDING order to CONFIRMED."""
        return OrderService.transition_status(
            order, OrderStatus.CONFIRMED, user=user,
        )

    @staticmethod
    @transaction.atomic
    def apply_coupon(
        order: Order,
        code: str,
        *,
        user=None,
    ) -> Order:
        """Apply a coupon while owning the Order-side transaction and mutation.

        Lock order first, then Coupon. Every coupon application therefore uses
        the global lock order Order → Coupon → CouponUsage.
        """
        from apps.discounts.models import Coupon
        from apps.discounts.services.discount_service import DiscountService

        order = Order.objects.select_for_update().get(pk=order.pk)

        if user is not None and order.user_id != user.pk:
            raise NotFound('Заказ не найден.')
        if order.status != OrderStatus.PENDING:
            raise ValidationError({
                'code': 'Скидку можно применить только к заказу в статусе PENDING.',
            })
        if order.discount > 0:
            raise ValidationError({'code': 'На заказе уже применён купон.'})

        apply_user = user or order.user
        coupon = DiscountService.resolve_coupon(code)
        coupon = Coupon.objects.select_for_update().get(pk=coupon.pk)

        DiscountService.validate_coupon_object(
            coupon,
            user=apply_user,
            order=order,
        )

        if coupon.is_exhausted:
            raise ValidationError({'code': 'Лимит использований купона исчерпан.'})

        user_uses = DiscountService.count_user_uses(coupon, apply_user)
        if coupon.max_uses_per_user and user_uses >= coupon.max_uses_per_user:
            raise ValidationError({
                'code': 'Лимит использований купона для пользователя исчерпан.',
            })

        discount_amount = DiscountService.calculate_discount(
            coupon,
            order.subtotal,
        )
        DiscountService.register_usage(
            coupon,
            user=apply_user,
            order=order,
        )

        order.discount = discount_amount
        order.total = order.subtotal + order.delivery_cost - order.discount
        if order.total < 0:
            order.total = 0
        order.save(update_fields=['discount', 'total', 'updated_at'])

        logger.info(
            'coupon_applied',
            extra={
                'order_id': order.pk,
                'coupon_code': coupon.code,
                'discount': str(discount_amount),
                'new_total': str(order.total),
            },
        )
        return order

    @staticmethod
    @transaction.atomic
    def remove_coupon(order: Order, *, user=None) -> Order:
        """Remove the active coupon from a PENDING order and release its slot."""
        from apps.discounts.models import CouponUsage
        from apps.discounts.services.discount_service import DiscountService

        order = Order.objects.select_for_update().get(pk=order.pk)

        if user is not None and order.user_id != user.pk:
            raise NotFound('Заказ не найден.')
        if order.status != OrderStatus.PENDING:
            raise ValidationError({
                'detail': 'Скидку можно снять только с заказа в статусе PENDING.',
            })
        if order.discount <= 0:
            raise ValidationError({'detail': 'На заказе нет скидки.'})

        usage = (
            CouponUsage.objects
            .filter(order_id=order.pk)
            .first()
        )

        if usage is None:
            logger.warning(
                'coupon_usage_missing_on_remove',
                extra={'order_id': order.pk},
            )
            order.discount = 0
            order.total = order.subtotal + order.delivery_cost
            order.save(update_fields=['discount', 'total', 'updated_at'])
            return order

        coupon = usage.coupon
        coupon = coupon.__class__.objects.select_for_update().get(pk=coupon.pk)
        usage = CouponUsage.objects.select_for_update().get(pk=usage.pk)

        DiscountService.release_usage(usage)

        old_discount = order.discount
        order.discount = 0
        order.total = order.subtotal + order.delivery_cost
        order.save(update_fields=['discount', 'total', 'updated_at'])

        logger.info(
            'coupon_removed',
            extra={
                'order_id': order.pk,
                'coupon_id': coupon.pk,
                'removed_discount': str(old_discount),
                'new_total': str(order.total),
            },
        )
        return order

    @staticmethod
    @transaction.atomic
    def cancel(
        order: Order,
        *,
        reason: str = '',
        user=None,
    ) -> Order:
        """Cancel an order and release any active coupon usage atomically."""
        from apps.discounts.models import CouponUsage
        from apps.discounts.services.discount_service import DiscountService

        valid_reasons = [r[0] for r in CANCELLATION_REASONS]
        if reason and reason not in valid_reasons:
            raise ValidationError({
                'reason': f'Недопустимая причина отмены: {reason}',
            })

        order = Order.objects.select_for_update().get(pk=order.pk)

        # ARCH-002: статус фиксируется сразу после захвата lock'а Order и
        # валидируется ДО любых мутаций — release купонного слота возможен
        # ТОЛЬКО при переходе PENDING → CANCELLED.
        current_status = order.status
        if order.is_terminal:
            raise ValidationError({
                'detail': (
                    f'Заказ {order.order_number} в терминальном статусе '
                    f'«{order.get_status_display()}». Дальнейшие переходы невозможны.'
                ),
            })
        allowed = ORDER_STATUS_TRANSITIONS.get(current_status, [])
        if OrderStatus.CANCELLED not in allowed:
            raise ValidationError({
                'detail': (
                    f'Переход «{current_status} → {OrderStatus.CANCELLED}» недопустим.'
                ),
            })

        # ARCH-002: слот купона освобождается только при отмене ещё не
        # подтверждённого заказа. При CONFIRMED/PROCESSING/SHIPPED → CANCELLED
        # использование остаётся потреблённым: times_used не уменьшается,
        # Order.discount/total не пересчитываются.
        # Порядок блокировок прежний: Order → Coupon → CouponUsage.
        usage = None
        if current_status == OrderStatus.PENDING and order.discount > 0:
            usage = (
                CouponUsage.objects
                .filter(order_id=order.pk)
                .first()
            )
            if usage is not None:
                coupon = usage.coupon.__class__.objects.select_for_update().get(
                    pk=usage.coupon_id,
                )
                usage = CouponUsage.objects.select_for_update().get(pk=usage.pk)
                DiscountService.release_usage(usage)
                order.discount = 0
                order.total = order.subtotal + order.delivery_cost

        order.status = OrderStatus.CANCELLED
        order.cancelled_at = timezone.now()
        order.cancellation_reason = reason
        update_fields = [
            'status',
            'cancelled_at',
            'cancellation_reason',
            'updated_at',
        ]
        if usage is not None:
            update_fields.extend(['discount', 'total'])
        order.save(update_fields=update_fields)

        OrderService._handle_inventory_transition(order, OrderStatus.CANCELLED)

        from apps.payments.models import Payment
        from apps.payments.constants import PAYMENT_STATUS_SUCCEEDED

        succeeded_payments = Payment.objects.filter(
            order=order,
            status=PAYMENT_STATUS_SUCCEEDED,
        )
        if succeeded_payments.exists():
            from apps.payments.services.payment_service import PaymentService
            for payment in succeeded_payments:
                try:
                    # Возвращаемый экземпляр — актуальное состояние
                    # платежа (исходный объект из queryset устаревший).
                    refunded = PaymentService.refund_payment(
                        payment,
                        reason=f'Отмена заказа {order.order_number}: {reason}',
                        user=user,
                    )
                    if refunded.refund_pending_amount > 0:
                        # Провайдер не исполнил возврат: обязательство уже
                        # зафиксировано в refund_required_amount + событие
                        # refund_failed; его подхватит retry_pending_refunds.
                        logger.warning(
                            'order_cancel_refund_pending',
                            extra={
                                'order_id': order.pk,
                                'payment_id': refunded.pk,
                                'refund_required_amount': str(
                                    refunded.refund_required_amount,
                                ),
                                'refund_pending_amount': str(
                                    refunded.refund_pending_amount,
                                ),
                            },
                        )
                    else:
                        logger.info(
                            'order_cancel_refund_initiated',
                            extra={
                                'order_id': order.pk,
                                'payment_id': refunded.pk,
                                'refund_amount': str(refunded.amount),
                            },
                        )
                except Exception as exc:
                    # PROD-003: провал возврата НИКОГДА не отбрасывается
                    # молча. refund_payment фиксирует обязательство сам,
                    # когда причина — отказ провайдера; здесь ловим
                    # остальные ошибки (в т.ч. аборт транзакции) и пишем
                    # durable-обязательство через выделенное соединение,
                    # которое переживает откат этой транзакции.
                    logger.error(
                        'order_cancel_refund_failed',
                        extra={
                            'order_id': order.pk,
                            'payment_id': payment.pk,
                            'error': str(exc),
                        },
                    )
                    try:
                        recorded = PaymentService.record_refund_failure_durable(
                            payment.pk,
                            reason=(
                                f'Отмена заказа {order.order_number}: {reason}'
                            ),
                            error=str(exc),
                            user_id=getattr(user, 'pk', None),
                        )
                        if not recorded:
                            logger.error(
                                'order_cancel_refund_failure_not_recorded',
                                extra={
                                    'order_id': order.pk,
                                    'payment_id': payment.pk,
                                },
                            )
                    except Exception as record_exc:
                        # Критический след: обязательство не зафиксировано —
                        # только критический лог + ручная разборка.
                        logger.critical(
                            'order_cancel_refund_record_failed',
                            extra={
                                'order_id': order.pk,
                                'payment_id': payment.pk,
                                'error': str(record_exc),
                            },
                        )

        # PROD-025 / F-18: отмена заказа — отдельный авторитетный путь
        # (transition_status() не принимает CANCELLED), поэтому уведомление
        # о cancelled подключается здесь, после всех побочных эффектов
        # отмены (купон, склад, возвраты).
        from apps.notifications.services.notification_events import (
            NotificationEvents,
        )

        NotificationEvents.order_status_changed(order, OrderStatus.CANCELLED)

        logger.info(
            'order_cancelled',
            extra={
                'order_id': order.pk,
                'order_number': order.order_number,
                'reason': reason,
                'cancelled_by': getattr(user, 'pk', None),
            },
        )
        return order

    @staticmethod
    def get_user_order_summary(user) -> dict:
        """Return a summary of the user's orders."""
        from django.db.models import Count, Q, Sum

        qs = Order.objects.for_user(user)
        stats = qs.aggregate(
            total_orders=Count('id'),
            active_orders=Count(
                'id',
                filter=~Q(
                    status__in=[OrderStatus.DELIVERED, OrderStatus.CANCELLED],
                ),
            ),
            total_spent=Sum(
                'total',
                filter=Q(status=OrderStatus.DELIVERED),
            ),
        )
        return {
            'total_orders': stats['total_orders'] or 0,
            'active_orders': stats['active_orders'] or 0,
            'total_spent': stats['total_spent'] or '0.00',
        }
