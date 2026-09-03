"""
F-17 regression tests for cleanup_stale_orders command.

The command accepts expected per-order domain/not-found failures and keeps
processing the remaining orders, but unexpected DB/programming errors must
not be swallowed and turned into a "successful" summary.
"""

import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.orders.models import Order
from apps.orders.models.order import OrderStatus
from apps.orders.tests.factories import create_test_order, create_test_user


class CleanupStaleOrdersExceptionBoundaryTests(TestCase):
    """Behavior-level coverage for the changed exception boundary."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        Order.objects.filter(pk=self.order.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=2),
        )

    def _run(self):
        return call_command(
            'cleanup_stale_orders',
            hours=1,
            stdout=StringIO(),
            stderr=StringIO(),
        )

    def test_successful_cancel(self):
        self._run()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CANCELLED)

    def test_domain_validation_error_continues(self):
        with mock.patch(
            'apps.orders.services.order_service.OrderService.cancel',
            side_effect=ValidationError({'detail': 'cannot cancel'}),
        ):
            self._run()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)

    def test_unexpected_error_propagates(self):
        with mock.patch(
            'apps.orders.services.order_service.OrderService.cancel',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                self._run()
