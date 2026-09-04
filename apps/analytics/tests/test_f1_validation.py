# ────────────────────────────────────────────────────────────────────────
# Regression tests for API-01/F-1 — Issue #66
#
# ``limit`` on analytics endpoints previously used bare ``int(...)`` and
# raised ``ValueError`` → 500 for non-numeric input.  ``metric`` and
# ``period`` were free-form strings silently coerced by the service.
#
# These tests prove:
#   * non-numeric / empty / float-like limit → 400 canonical envelope
#   * zero / negative limit → 400
#   * valid numeric limit → 200 and correct service behaviour
#   * missing optional limit → 200 (default)
#   * invalid metric / period → 400 canonical envelope
#   * valid metric / period → 200
#   * envelope never leaks ``ValueError``/``Traceback``
#   * existing valid behaviour is preserved
# ────────────────────────────────────────────────────────────────────────

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.catalog.tests.factories import CatalogTestCase
from apps.orders.tests.factories import create_test_user
from apps.analytics.tests.factories import create_test_delivered_order_with_items, create_test_view

from decimal import Decimal


class AnalyticsF1Base(CatalogTestCase):
    def setUp(self):
        self.admin = create_test_user(is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        create_test_delivered_order_with_items(
            self.admin, self.variant_128, quantity=2, unit_price=Decimal('1000.00')
        )
        create_test_view(self.product, session_key='f1-sess')

    def assert_validation_400(self, resp, field: str):
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, msg=resp.content[:500])
        # Must be JSON with canonical API-04 envelope
        self.assertTrue(resp['Content-Type'].startswith('application/json'))
        body = resp.json()
        self.assertIn('error', body)
        error = body['error']
        self.assertEqual(error['code'], 'validation_error')
        self.assertIsInstance(error['message'], str)
        self.assertTrue(error['message'])
        self.assertIsInstance(error['details'], list)
        self.assertTrue(len(error['details']) > 0)
        # At least one detail points to the expected field
        fields = {d.get('field') for d in error['details']}
        self.assertIn(field, fields, msg=f'expected field {field!r} in {fields} body={body}')
        # Must not leak internals
        leaked = str(body).lower()
        for token in ('traceback', 'valueerror', 'exception'):
            self.assertNotIn(token, leaked, msg=f'leaked {token!r} in {body}')
        # Must not be 500 server_error
        self.assertNotEqual(error['code'], 'server_error')
        return body

    def assert_success_200(self, resp):
        self.assertEqual(resp.status_code, status.HTTP_200_OK, msg=resp.content[:800])
        # No leak
        if resp['Content-Type'].startswith('application/json'):
            try:
                body = resp.json()
                leaked = str(body).lower()
                for token in ('traceback', 'valueerror'):
                    self.assertNotIn(token, leaked)
                # Must not be error envelope on success
                if isinstance(body, dict):
                    self.assertNotIn('error', body, msg=f'unexpected error envelope in success: {body}')
            except Exception:
                pass


class AnalyticsLimitValidationTests(AnalyticsF1Base):
    """Every analytics endpoint that accepts ``limit`` must validate it."""

    # We test all four limit-bearing endpoints
    LIMIT_ENDPOINTS = [
        ('analytics:top-products', 'limit'),
        ('analytics:top-categories', 'limit'),
        ('analytics:top-customers', 'limit'),
        ('analytics:most-viewed', 'limit'),
    ]

    def test_limit_non_numeric_returns_400(self):
        for url_name, field in self.LIMIT_ENDPOINTS:
            url = reverse(url_name)
            with self.subTest(endpoint=url_name, value='abc'):
                resp = self.client.get(url, {'limit': 'abc'})
                self.assert_validation_400(resp, field)

    def test_limit_float_string_returns_400(self):
        for url_name, field in self.LIMIT_ENDPOINTS:
            url = reverse(url_name)
            with self.subTest(endpoint=url_name, value='10.5'):
                resp = self.client.get(url, {'limit': '10.5'})
                self.assert_validation_400(resp, field)

    def test_limit_empty_string_returns_400(self):
        for url_name, field in self.LIMIT_ENDPOINTS:
            url = reverse(url_name)
            with self.subTest(endpoint=url_name):
                resp = self.client.get(url, {'limit': ''})
                self.assert_validation_400(resp, field)

    def test_limit_zero_returns_400(self):
        for url_name, field in self.LIMIT_ENDPOINTS:
            url = reverse(url_name)
            with self.subTest(endpoint=url_name, value='0'):
                resp = self.client.get(url, {'limit': '0'})
                self.assert_validation_400(resp, field)

    def test_limit_negative_returns_400(self):
        for url_name, field in self.LIMIT_ENDPOINTS:
            url = reverse(url_name)
            with self.subTest(endpoint=url_name, value='-1'):
                resp = self.client.get(url, {'limit': '-1'})
                self.assert_validation_400(resp, field)
                resp2 = self.client.get(url, {'limit': '-100'})
                self.assert_validation_400(resp2, field)

    def test_limit_valid_returns_200(self):
        for url_name, _ in self.LIMIT_ENDPOINTS:
            url = reverse(url_name)
            with self.subTest(endpoint=url_name, value='5'):
                resp = self.client.get(url, {'limit': '5'})
                self.assert_success_200(resp)
                self.assertIsInstance(resp.data, list)
            with self.subTest(endpoint=url_name, value='1'):
                resp = self.client.get(url, {'limit': '1'})
                self.assert_success_200(resp)

    def test_limit_missing_returns_200_with_default(self):
        for url_name, _ in self.LIMIT_ENDPOINTS:
            url = reverse(url_name)
            with self.subTest(endpoint=url_name):
                resp = self.client.get(url)
                self.assert_success_200(resp)
                self.assertIsInstance(resp.data, list)

    def test_limit_with_spaces_and_valid_number(self):
        url = reverse('analytics:top-products')
        resp = self.client.get(url, {'limit': ' 5 '})
        # str.strip() should allow it
        self.assert_success_200(resp)

    def test_limit_boundary_one_is_valid(self):
        url = reverse('analytics:top-products')
        resp = self.client.get(url, {'limit': '1'})
        self.assert_success_200(resp)

    def test_limit_does_not_leak_internal_details(self):
        url = reverse('analytics:top-products')
        resp = self.client.get(url, {'limit': 'abc'})
        body = self.assert_validation_400(resp, 'limit')
        # Explicitly check no ValueError text
        self.assertNotIn('ValueError', str(body))
        self.assertNotIn('Traceback', str(body))


class AnalyticsMetricValidationTests(AnalyticsF1Base):
    """``metric`` on top-products must be validated."""

    def test_metric_invalid_returns_400(self):
        url = reverse('analytics:top-products')
        for bad in ('invalid', 'Revenue', 'REVENUE', 'foo', '', '   ', 'quantity '):
            # 'quantity ' with trailing space after strip becomes 'quantity' -> valid, so skip that nuance
            # we test truly invalid
            if bad.strip() in ('revenue', 'quantity'):
                continue
            with self.subTest(metric=bad):
                resp = self.client.get(url, {'metric': bad})
                self.assert_validation_400(resp, 'metric')

    def test_metric_valid_revenue(self):
        url = reverse('analytics:top-products')
        resp = self.client.get(url, {'metric': 'revenue'})
        self.assert_success_200(resp)

    def test_metric_valid_quantity(self):
        url = reverse('analytics:top-products')
        resp = self.client.get(url, {'metric': 'quantity'})
        self.assert_success_200(resp)

    def test_metric_missing_defaults_to_revenue(self):
        url = reverse('analytics:top-products')
        resp = self.client.get(url)
        self.assert_success_200(resp)

    def test_metric_no_leak(self):
        url = reverse('analytics:top-products')
        resp = self.client.get(url, {'metric': 'bad'})
        body = self.assert_validation_400(resp, 'metric')
        self.assertNotIn('Traceback', str(body))


class AnalyticsPeriodValidationTests(AnalyticsF1Base):
    """``period`` on sales/timeline must be validated."""

    def test_period_invalid_returns_400(self):
        url = reverse('analytics:sales-timeline')
        for bad in ('invalid', 'Daily', 'DAILY', 'foo', 'weekly ', ''):
            # strip will make 'weekly ' -> 'weekly' valid, so adjust
            if bad.strip() in ('hourly', 'daily', 'weekly', 'monthly'):
                continue
            with self.subTest(period=bad):
                resp = self.client.get(url, {'period': bad})
                self.assert_validation_400(resp, 'period')

    def test_period_valid_values(self):
        url = reverse('analytics:sales-timeline')
        for good in ('daily', 'weekly', 'monthly', 'hourly'):
            with self.subTest(period=good):
                resp = self.client.get(url, {'period': good})
                self.assert_success_200(resp)
                self.assertIn('timeline', resp.data)

    def test_period_missing_defaults_to_daily(self):
        url = reverse('analytics:sales-timeline')
        resp = self.client.get(url)
        self.assert_success_200(resp)
        self.assertIn('timeline', resp.data)

    def test_period_no_leak(self):
        url = reverse('analytics:sales-timeline')
        resp = self.client.get(url, {'period': 'bad'})
        body = self.assert_validation_400(resp, 'period')
        self.assertNotIn('Traceback', str(body))


class AnalyticsDaysValidationStillWorksTests(AnalyticsF1Base):
    """Ensure existing ``days`` validation (via serializer) still returns 400 envelope."""

    def test_days_invalid_returns_400(self):
        url = reverse('analytics:sales-summary')
        resp = self.client.get(url, {'days': 'abc'})
        self.assert_validation_400(resp, 'days')

    def test_days_zero_returns_400(self):
        url = reverse('analytics:sales-summary')
        resp = self.client.get(url, {'days': '0'})
        self.assert_validation_400(resp, 'days')

    def test_days_valid(self):
        url = reverse('analytics:sales-summary')
        resp = self.client.get(url, {'days': '7'})
        self.assert_success_200(resp)
