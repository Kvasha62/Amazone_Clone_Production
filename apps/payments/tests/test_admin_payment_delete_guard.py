# ────────────────────────────────────────────────────────────────────────
# PROD-031 / F-24 — Admin не должен позволять удалять Payment,
# так как PaymentEvent.payment использует on_delete=CASCADE и удаление
# платежа уничтожило бы append-only аудит-историю.
#
# ПРОВЕРЯЕТ:
#   • has_delete_permission == False (объектный и списочный уровень)
#   • delete_model / delete_queryset поднимают PermissionDenied
#   • Payment и его PaymentEvent остаются в БД после попытки удаления
#   • В админке нет bulk-action 'delete_selected'
#   • Admin-URL удаления недоступен (403)
# ────────────────────────────────────────────────────────────────────────

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.orders.tests.factories import create_test_order, create_test_user
from apps.payments.admin.payment_admin import PaymentAdmin
from apps.payments.models import Payment, PaymentEvent
from apps.payments.tests.factories import (
    create_test_payment,
    create_test_payment_event,
)

User = get_user_model()


class PaymentAdminDeleteGuardTests(TestCase):
    """Административное удаление Payment запрещено (PROD-031 / F-24)."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(self.order, self.user)
        self.event = create_test_payment_event(self.payment)

        self.staff = User.objects.create_user(
            username='paymentadmin',
            email='paymentadmin@test.com',
            password='admin123!',
            is_staff=True,
            is_superuser=True,
        )

        self.site = AdminSite()
        self.admin = PaymentAdmin(Payment, self.site)
        self.factory = RequestFactory()
        self.request = self.factory.get('/admin/')
        self.request.user = self.staff

    # ── AC-1: одиночное удаление запрещено ──

    def test_has_delete_permission_is_false(self):
        self.assertFalse(self.admin.has_delete_permission(self.request))
        self.assertFalse(
            self.admin.has_delete_permission(self.request, obj=self.payment),
        )

    def test_delete_model_raises_and_keeps_payment_and_events(self):
        with self.assertRaises(PermissionDenied):
            self.admin.delete_model(self.request, self.payment)

        self.assertTrue(Payment.objects.filter(pk=self.payment.pk).exists())
        # AC-3: аудит-история не тронута.
        self.assertTrue(PaymentEvent.objects.filter(pk=self.event.pk).exists())

    # ── AC-2: массовое удаление запрещено ──

    def test_delete_queryset_raises_and_keeps_payment_and_events(self):
        qs = Payment.objects.filter(pk=self.payment.pk)
        with self.assertRaises(PermissionDenied):
            self.admin.delete_queryset(self.request, qs)

        self.assertTrue(Payment.objects.filter(pk=self.payment.pk).exists())
        self.assertTrue(PaymentEvent.objects.filter(pk=self.event.pk).exists())

    def test_delete_selected_action_is_unavailable(self):
        actions = self.admin.get_actions(self.request)
        self.assertNotIn('delete_selected', actions)

    # ── AC-1/AC-2/AC-3 через HTTP-путь админки ──

    def test_admin_delete_view_is_forbidden_and_audit_preserved(self):
        self.client.force_login(self.staff)
        url = reverse('admin:payments_payment_delete', args=[self.payment.pk])

        response = self.client.post(url, {'post': 'yes'})

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Payment.objects.filter(pk=self.payment.pk).exists())
        self.assertTrue(PaymentEvent.objects.filter(pk=self.event.pk).exists())
