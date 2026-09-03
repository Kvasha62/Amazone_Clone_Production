"""
F-17 regression tests for cleanup_stale_payments command.

Expected per-payment domain/not-found failures are reported while the rest
of the batch is processed; unexpected DB/programming errors must propagate
instead of being masked as a successful cleanup.
"""

import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.orders.tests.factories import create_test_order, create_test_user
from apps.payments.constants import (
    PAYMENT_STATUS_CANCELLED,
    PAYMENT_STATUS_PENDING,
)
from apps.payments.models import Payment
from apps.payments.tests.factories import create_test_payment


class CleanupStalePaymentsExceptionBoundaryTests(TestCase):
    """Behavior-level coverage for the changed exception boundary."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order,
            self.user,
            status=PAYMENT_STATUS_PENDING,
        )
        Payment.objects.filter(pk=self.payment.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=2),
        )

    def _run(self):
        return call_command(
            'cleanup_stale_payments',
            hours=1,
            stdout=StringIO(),
            stderr=StringIO(),
        )

    def test_successful_cancel(self):
        self._run()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_CANCELLED)

    def test_domain_validation_error_continues(self):
        with mock.patch(
            'apps.payments.services.payment_service.PaymentService.cancel_payment',
            side_effect=ValidationError({'detail': 'cannot cancel'}),
        ):
            self._run()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PAYMENT_STATUS_PENDING)

    def test_unexpected_error_propagates(self):
        with mock.patch(
            'apps.payments.services.payment_service.PaymentService.cancel_payment',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                self._run()
