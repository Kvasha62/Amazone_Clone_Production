"""
F-17 regression tests for the health-check exception boundary.

The health endpoint intentionally reports a database outage as ``503
degraded``, but it must not swallow arbitrary programming/config failures by
converting them into the same status.
"""

from unittest import mock

from django.db import OperationalError
from django.test import RequestFactory, TestCase

from apps.core.health_urls import HealthCheckView


class HealthCheckExceptionBoundaryTests(TestCase):
    """Behavior-level coverage for the changed health boundary."""

    def test_database_ok_returns_200(self):
        resp = self.client.get('/api/v1/health/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['database'], 'ok')

    def test_database_error_returns_degraded_503(self):
        with mock.patch(
            'django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection',
            side_effect=OperationalError('database is down'),
        ):
            resp = self.client.get('/api/v1/health/')

        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()['database'], 'error')
        self.assertEqual(resp.json()['status'], 'degraded')

    def test_unexpected_error_is_not_swallowed(self):
        request = RequestFactory().get('/api/v1/health/')
        with mock.patch(
            'django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection',
            side_effect=RuntimeError('programming error'),
        ):
            with self.assertRaises(RuntimeError):
                HealthCheckView.as_view()(request)
