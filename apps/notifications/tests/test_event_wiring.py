# ────────────────────────────────────────────────────────────────────────
# apps/notifications/tests/test_event_wiring.py
#
# PROD-025 / F-18 — регрессионные тесты подключения бизнес-событий
# к bounded context уведомлений.
#
# ПРОВЕРЯЕТ:
#   • создание заказа → уведомление `order_created` (AC-2);
#   • переходы статуса заказа → `order_confirmed` / `order_shipped` /
#     `order_delivered`, отмена → `order_cancelled`, статус без
#     уведомительного контракта (`processing`) → без уведомления (AC-3);
#   • подтверждение оплаты → `payment_success`, повторная доставка
#     вебхука не создаёт дубль (AC-4, AC-7);
#   • email-задачи отправляются из авторитетных событий (AC-5);
#   • откат транзакции не оставляет уведомления (AC-6);
#   • сами Celery-задачи создают email-уведомление и переводят его
#     в статус `sent`.
#
# ПОЧЕМУ captureOnCommitCallbacks: уведомления планируются через
# transaction.on_commit(), поэтому внутри транзакции TestCase они без
# этого контекста не выполняются.
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal
from unittest import mock

from django.db import transaction
from django.test import TestCase

from rest_framework.exceptions import ValidationError

from apps.cart.models import Cart, CartItem
from apps.catalog.constants import ProductStatus
from apps.catalog.models import Brand, Category, Product, ProductVariant
from apps.inventory.models import Stock
from apps.notifications import tasks as notification_tasks
from apps.notifications.models import Notification
from apps.orders.models import Order, OrderItem
from apps.orders.models.order import OrderStatus
from apps.orders.services.order_service import OrderService
from apps.orders.tests.factories import (
    create_test_address,
    create_test_order,
    create_test_user,
)
from apps.payments.services.payment_service import PaymentService
from apps.payments.tests.factories import create_test_payment
from apps.pricing.models import Price


class OrderCreationNotificationTests(TestCase):
    """OrderService.create_from_cart() → уведомление `order_created`."""

    def setUp(self):
        self.user = create_test_user()
        create_test_address(self.user, city='Москва')

        brand = Brand.objects.create(name='NotifBrand')
        category = Category.add_root(name='NotifCat')
        product = Product.objects.create(
            name='Notif Product',
            brand=brand,
            primary_category=category,
            status=ProductStatus.ACTIVE,
        )
        self.variant = ProductVariant.objects.create(
            product=product,
            sku='NOTIF-SKU-A',
        )
        Price.objects.create(variant=self.variant, price=Decimal('1000.00'))

    def _make_cart(self) -> Cart:
        cart = Cart.objects.create(user=self.user, is_active=True)
        CartItem.objects.create(cart=cart, variant=self.variant, quantity=2)
        return cart

    def test_order_created_emits_in_app_notification(self):
        """AC-2: успешное создание заказа → ровно одно уведомление."""
        with self.captureOnCommitCallbacks(execute=True):
            order = OrderService.create_from_cart(self.user, self._make_cart())

        notifications = Notification.objects.filter(user=self.user)
        self.assertEqual(notifications.count(), 1)

        notif = notifications.get()
        self.assertEqual(notif.notification_type, 'order_created')
        self.assertEqual(notif.channel, 'in_app')
        self.assertEqual(notif.related_object_type, 'order')
        self.assertEqual(notif.related_object_id, order.pk)
        self.assertEqual(notif.status, 'sent')
        self.assertIn(order.order_number, notif.title)

    def test_rolled_back_order_creation_emits_nothing(self):
        """AC-6: откат транзакции не оставляет уведомления."""
        with self.assertRaises(RuntimeError):
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    OrderService.create_from_cart(self.user, self._make_cart())
                    raise RuntimeError('order creation must be rolled back')

        self.assertFalse(Notification.objects.filter(user=self.user).exists())
        self.assertFalse(Order.objects.filter(user=self.user).exists())


class OrderStatusNotificationTests(TestCase):
    """OrderService.transition_status() / cancel() → уведомления статуса."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user, status=OrderStatus.PENDING)

        # Постановка email-задач перехватывается: брокера в тестах нет.
        confirm_patcher = mock.patch.object(
            notification_tasks.send_order_confirmation, 'delay',
        )
        shipped_patcher = mock.patch.object(
            notification_tasks.send_order_shipped, 'delay',
        )
        self.confirm_delay = confirm_patcher.start()
        self.shipped_delay = shipped_patcher.start()
        self.addCleanup(confirm_patcher.stop)
        self.addCleanup(shipped_patcher.stop)

    def _run(self, func, *args, **kwargs):
        """Выполняет операцию сервиса, выполняя on_commit-колбэки."""
        with self.captureOnCommitCallbacks(execute=True):
            return func(*args, **kwargs)

    def _notifications(self, notification_type):
        return Notification.objects.filter(
            user=self.user,
            notification_type=notification_type,
        )

    def test_confirmed_emits_in_app_notification_and_email_task(self):
        """AC-3, AC-5: CONFIRMED → `order_confirmed` + email-задача."""
        order = self._run(
            OrderService.transition_status,
            self.order,
            OrderStatus.CONFIRMED,
        )

        notif = self._notifications('order_confirmed').get()
        self.assertEqual(notif.channel, 'in_app')
        self.assertEqual(notif.related_object_id, order.pk)
        self.confirm_delay.assert_called_once_with(order.pk)
        self.shipped_delay.assert_not_called()

    def test_shipped_emits_in_app_notification_and_email_task(self):
        """AC-3, AC-5: SHIPPED → `order_shipped` + email-задача."""
        for status in (OrderStatus.CONFIRMED, OrderStatus.PROCESSING):
            self._run(OrderService.transition_status, self.order, status)

        self.confirm_delay.reset_mock()
        order = self._run(
            OrderService.transition_status,
            self.order,
            OrderStatus.SHIPPED,
        )

        self.assertTrue(self._notifications('order_shipped').exists())
        self.shipped_delay.assert_called_once_with(order.pk)
        # Повторной задачи подтверждения при переходе SHIPPED нет.
        self.confirm_delay.assert_not_called()

    def test_delivered_emits_in_app_notification_without_email_task(self):
        """AC-3: DELIVERED → `order_delivered`, email-задачи для него нет."""
        for status in (
            OrderStatus.CONFIRMED,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
        ):
            self._run(OrderService.transition_status, self.order, status)

        self.confirm_delay.reset_mock()
        self.shipped_delay.reset_mock()
        self._run(
            OrderService.transition_status,
            self.order,
            OrderStatus.DELIVERED,
        )

        self.assertTrue(self._notifications('order_delivered').exists())
        self.confirm_delay.assert_not_called()
        self.shipped_delay.assert_not_called()

    def test_processing_status_emits_no_notification(self):
        """Статус без уведомительного контракта не создаёт уведомлений."""
        self._run(
            OrderService.transition_status,
            self.order,
            OrderStatus.CONFIRMED,
        )
        self.confirm_delay.reset_mock()

        self._run(
            OrderService.transition_status,
            self.order,
            OrderStatus.PROCESSING,
        )

        self.assertEqual(
            Notification.objects.filter(user=self.user).count(), 1,
            'У «processing» нет типа уведомления — уведомление не создаётся.',
        )
        self.confirm_delay.assert_not_called()
        self.shipped_delay.assert_not_called()

    def test_cancelled_emits_notification(self):
        """AC-3: отмена (cancel — единственный путь) → `order_cancelled`."""
        self._run(OrderService.cancel, self.order, reason='')

        self.assertTrue(self._notifications('order_cancelled').exists())
        self.confirm_delay.assert_not_called()
        self.shipped_delay.assert_not_called()


class RolledBackStatusTransitionTests(TestCase):
    """AC-6: откат авторитетного перехода не оставляет уведомления."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user, status=OrderStatus.PENDING)

        brand = Brand.objects.create(name='RollbackBrand')
        category = Category.add_root(name='RollbackCat')
        product = Product.objects.create(
            name='Rollback Product',
            brand=brand,
            primary_category=category,
            status=ProductStatus.ACTIVE,
        )
        variant = ProductVariant.objects.create(
            product=product,
            sku='ROLLBACK-SKU-A',
        )
        Stock.objects.create(variant=variant, quantity=1)
        OrderItem.objects.create(
            order=self.order,
            variant=variant,
            product_name=product.name,
            sku=variant.sku,
            unit_price=Decimal('100.00'),
            quantity=5,
        )

    def test_failed_reservation_leaves_no_notification(self):
        """Провал резервирования откатывает CONFIRMED — уведомления нет."""
        with self.assertRaises(ValidationError):
            with self.captureOnCommitCallbacks(execute=True):
                OrderService.transition_status(
                    self.order, OrderStatus.CONFIRMED,
                )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)
        self.assertFalse(Notification.objects.filter(user=self.user).exists())


class ConfirmationEmailTaskIntegrationTests(TestCase):
    """Сквозной путь события подтверждения заказа до email-уведомления.

    Постановка задач НЕ подменяется: в тестах Celery работает в eager-режиме
    (см. config/test_runner.py), поэтому задача, запланированная после
    COMMIT, выполняется сразу — проверяется вся цепочка
    «событие → задача → email-уведомление → отправка».
    """

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user, status=OrderStatus.PENDING)

    def test_confirmation_email_task_runs_for_business_event(self):
        """CONFIRMED → send_order_confirmation → email-уведомление sent."""
        with self.captureOnCommitCallbacks(execute=True):
            OrderService.transition_status(self.order, OrderStatus.CONFIRMED)

        email_notif = Notification.objects.filter(
            user=self.user,
            notification_type='order_confirmed',
            channel='email',
        ).get()
        self.assertEqual(email_notif.related_object_id, self.order.pk)
        self.assertEqual(email_notif.status, 'sent')
        self.assertIsNotNone(email_notif.sent_at)


class PaymentNotificationTests(TestCase):
    """PaymentService.confirm_payment() → уведомление `payment_success`."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(
            self.user,
            status=OrderStatus.PENDING,
            subtotal=Decimal('1000.00'),
            delivery_cost=Decimal('0.00'),
            total=Decimal('1000.00'),
        )
        self.payment = create_test_payment(self.order, self.user)

    def _confirm(self):
        with mock.patch.object(
            notification_tasks.send_order_confirmation, 'delay',
        ):
            with self.captureOnCommitCallbacks(execute=True):
                return PaymentService.confirm_payment(self.payment)

    def test_payment_success_emits_notification(self):
        """AC-4: подтверждение оплаты → уведомление `payment_success`."""
        payment = self._confirm()

        self.assertEqual(payment.status, 'succeeded')
        notif = Notification.objects.filter(
            user=self.user,
            notification_type='payment_success',
        ).get()
        self.assertEqual(notif.channel, 'in_app')
        self.assertEqual(notif.related_object_id, self.order.pk)
        # Подтверждение оплаты подтверждает и заказ — уведомление заказа
        # создаётся авторитетным путём OrderService.confirm().
        self.assertTrue(
            Notification.objects.filter(
                user=self.user,
                notification_type='order_confirmed',
            ).exists(),
        )

    def test_duplicate_webhook_does_not_duplicate_notification(self):
        """AC-7: повторная доставка вебхука не плодит уведомления."""
        self._confirm()
        first_count = Notification.objects.filter(
            user=self.user,
            notification_type='payment_success',
        ).count()
        self.assertEqual(first_count, 1)

        with self.captureOnCommitCallbacks(execute=True):
            PaymentService.confirm_payment(
                self.payment, external_id=self.payment.external_id,
            )

        self.assertEqual(
            Notification.objects.filter(
                user=self.user,
                notification_type='payment_success',
            ).count(),
            1,
        )


class NotificationEmailTaskTests(TestCase):
    """Контракт существующих Celery-задач уведомлений."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)

    def test_send_order_confirmation_creates_email_notification(self):
        """Задача подтверждения создаёт уведомление канала email."""
        with mock.patch.object(
            notification_tasks.send_email_notification, 'delay',
        ) as email_delay:
            notification_tasks.send_order_confirmation(self.order.pk)

        notif = Notification.objects.filter(
            user=self.user,
            notification_type='order_confirmed',
            channel='email',
        ).get()
        self.assertEqual(notif.related_object_id, self.order.pk)
        self.assertEqual(notif.status, 'pending')
        email_delay.assert_called_once_with(notif.pk)

    def test_send_order_shipped_creates_email_notification(self):
        """Задача об отправке создаёт уведомление канала email."""
        with mock.patch.object(
            notification_tasks.send_email_notification, 'delay',
        ) as email_delay:
            notification_tasks.send_order_shipped(self.order.pk)

        notif = Notification.objects.filter(
            user=self.user,
            notification_type='order_shipped',
            channel='email',
        ).get()
        self.assertEqual(notif.related_object_id, self.order.pk)
        email_delay.assert_called_once_with(notif.pk)

    def test_send_email_notification_marks_notification_sent(self):
        """Задача отправки переводит уведомление в статус sent."""
        notif = Notification.objects.create(
            user=self.user,
            notification_type='order_confirmed',
            channel='email',
            title='Тест',
            body='Тело',
            status='pending',
            related_object_type='order',
            related_object_id=self.order.pk,
        )

        notification_tasks.send_email_notification(notif.pk)

        notif.refresh_from_db()
        self.assertEqual(notif.status, 'sent')
        self.assertIsNotNone(notif.sent_at)
