from django.test import TestCase
from rest_framework.exceptions import NotFound
from apps.orders.tests.factories import create_test_order, create_test_user
from apps.notifications.models import Notification
from apps.notifications.services.notification_service import NotificationService
from apps.notifications.tests.factories import create_test_notification


class CreateNotificationTests(TestCase):

    def setUp(self):
        self.user = create_test_user()

    def test_create(self):
        n = NotificationService.create(
            self.user,
            notification_type='system',
            title='Hello',
        )
        self.assertIsNotNone(n.pk)
        self.assertEqual(n.title, 'Hello')
        self.assertEqual(n.status, 'sent')  # send_immediately=True

    def test_create_no_send(self):
        n = NotificationService.create(
            self.user,
            notification_type='system',
            title='Deferred',
            send_immediately=False,
        )
        self.assertEqual(n.status, 'pending')

    def test_create_with_related_object(self):
        n = NotificationService.create(
            self.user,
            notification_type='order_created',
            title='Order!',
            related_object_type='order',
            related_object_id=42,
        )
        self.assertEqual(n.related_object_type, 'order')
        self.assertEqual(n.related_object_id, 42)


class MarkReadTests(TestCase):

    def setUp(self):
        self.user = create_test_user()
        self.n = create_test_notification(self.user)

    def test_mark_read(self):
        n = NotificationService.mark_read(self.n.pk, self.user)
        self.assertIsNotNone(n.read_at)
        self.assertEqual(n.status, 'read')

    def test_mark_read_already_read(self):
        NotificationService.mark_read(self.n.pk, self.user)
        n = NotificationService.mark_read(self.n.pk, self.user)
        self.assertIsNotNone(n.read_at)

    def test_mark_read_not_found(self):
        with self.assertRaises(NotFound):
            NotificationService.mark_read(99999, self.user)

    def test_mark_read_other_user(self):
        other = create_test_user()
        with self.assertRaises(NotFound):
            NotificationService.mark_read(self.n.pk, other)


class MarkAllReadTests(TestCase):

    def setUp(self):
        self.user = create_test_user()
        create_test_notification(self.user, title='A')
        create_test_notification(self.user, title='B')

    def test_mark_all_read(self):
        count = NotificationService.mark_all_read(self.user)
        self.assertEqual(count, 2)

    def test_mark_all_read_empty(self):
        other = create_test_user()
        count = NotificationService.mark_all_read(other)
        self.assertEqual(count, 0)


class GetUnreadTests(TestCase):

    def setUp(self):
        self.user = create_test_user()
        create_test_notification(self.user)
        create_test_notification(self.user)

    def test_get_unread(self):
        qs = NotificationService.get_unread(self.user)
        self.assertEqual(qs.count(), 2)

    def test_get_unread_count(self):
        count = NotificationService.get_unread_count(self.user)
        self.assertEqual(count, 2)

    def test_get_all(self):
        qs = NotificationService.get_all(self.user)
        self.assertEqual(len(qs), 2)


class ConvenienceMethodsTests(TestCase):

    def test_notify_order_created(self):
        user = create_test_user()
        order = create_test_order(user)
        n = NotificationService.notify_order_created(order)
        self.assertEqual(n.notification_type, 'order_created')
        self.assertIn(order.order_number, n.title)

    def test_notify_order_status_changed(self):
        user = create_test_user()
        order = create_test_order(user)
        n = NotificationService.notify_order_status_changed(order, 'confirmed')
        self.assertEqual(n.notification_type, 'order_confirmed')

    def test_notify_payment_success(self):
        user = create_test_user()
        order = create_test_order(user)
        n = NotificationService.notify_payment_success(order, None)
        self.assertEqual(n.notification_type, 'payment_success')

    def test_notify_order_status_changed_without_notification_type(self):
        """Статус заказа без уведомительного контракта → None, без строки БД.

        PROD-025 / F-18: «processing» не имеет типа уведомления, поэтому
        переход в него не создаёт уведомление (и не создаёт строку
        с типом вне choices модели).
        """
        user = create_test_user()
        order = create_test_order(user)
        self.assertIsNone(
            NotificationService.notify_order_status_changed(order, 'processing'),
        )
        self.assertFalse(Notification.objects.filter(user=user).exists())
