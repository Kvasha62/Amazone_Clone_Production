"""
F-17 regression tests for cleanup_stale_shipments command.

The stale-shipment save operation has no expected domain exceptions, so any
error (DB/programming) must propagate instead of being reported as "success".
"""

import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.orders.tests.factories import create_test_order, create_test_user
from apps.shipping.constants import SHIPMENT_PREPARING, SHIPMENT_RETURNED
from apps.shipping.models import Shipment
from apps.shipping.tests.factories import create_test_shipment


class CleanupStaleShipmentsExceptionBoundaryTests(TestCase):
    """Behavior-level coverage for the changed exception boundary."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.shipment = create_test_shipment(
            self.order,
            status=SHIPMENT_PREPARING,
        )
        Shipment.objects.filter(pk=self.shipment.pk).update(
            updated_at=timezone.now() - datetime.timedelta(hours=2),
        )

    def test_successful_return(self):
        call_command(
            'cleanup_stale_shipments',
            hours=1,
            stdout=StringIO(),
            stderr=StringIO(),
        )
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, SHIPMENT_RETURNED)

    def test_unexpected_save_error_propagates(self):
        with mock.patch(
            'apps.shipping.models.Shipment.save',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                call_command(
                    'cleanup_stale_shipments',
                    hours=1,
                    stdout=StringIO(),
                    stderr=StringIO(),
                )
