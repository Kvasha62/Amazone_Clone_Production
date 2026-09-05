"""F-9: frozen public health payload and explicit OpenAPI contract.

The original F-17 exception-boundary regressions remain in test_health.py.
"""

from unittest import mock

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import Error, OperationalError
from django.test import RequestFactory, TestCase, override_settings
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema
from rest_framework.response import Response

from apps.core.health_urls import HealthCheckView


HEALTH_URL = '/api/v1/health/'
ENSURE_CONNECTION = (
    'django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection'
)


class HealthCheckContractTests(TestCase):
    def assert_health_payload(self, response, *, degraded=False, version=None):
        self.assertIsInstance(response, Response)
        self.assertEqual(response.status_code, 503 if degraded else 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertEqual(response.json(), {
            'status': 'degraded' if degraded else 'ok',
            'version': (
                settings.SPECTACULAR_SETTINGS['VERSION']
                if version is None else version
            ),
            'database': 'error' if degraded else 'ok',
        })
        self.assertNotIn('error', response.json())

    def test_unauthenticated_get_returns_success_contract(self):
        self.assert_health_payload(self.client.get(HEALTH_URL))

    def test_database_errors_return_degraded_contract(self):
        for error in (Error, OperationalError):
            with self.subTest(error=error.__name__):
                with mock.patch(
                    ENSURE_CONNECTION, side_effect=error('database unavailable'),
                ) as ensure_connection:
                    response = self.client.get(HEALTH_URL)
                ensure_connection.assert_called_once_with()
                self.assert_health_payload(response, degraded=True)

    def test_both_responses_follow_the_configured_version(self):
        version = '9.8.7-health-contract'
        with override_settings(SPECTACULAR_SETTINGS={
            **settings.SPECTACULAR_SETTINGS,
            'VERSION': version,
        }):
            with mock.patch(
                ENSURE_CONNECTION,
                side_effect=[None, OperationalError('database unavailable')],
            ):
                self.assert_health_payload(
                    self.client.get(HEALTH_URL), version=version,
                )
                self.assert_health_payload(
                    self.client.get(HEALTH_URL), degraded=True, version=version,
                )

    def test_health_does_not_parse_jwt_even_when_database_is_down(self):
        with mock.patch(
            'rest_framework_simplejwt.authentication.JWTAuthentication.authenticate',
            side_effect=AssertionError('Health must not authenticate JWTs'),
        ) as authenticate:
            with mock.patch(
                ENSURE_CONNECTION,
                side_effect=[None, OperationalError('database unavailable')],
            ):
                for degraded in (False, True):
                    with self.subTest(degraded=degraded):
                        response = self.client.get(
                            HEALTH_URL, HTTP_AUTHORIZATION='Bearer not-a-jwt',
                        )
                        self.assert_health_payload(response, degraded=degraded)
            authenticate.assert_not_called()

    def test_health_does_not_inherit_global_throttles(self):
        # Test settings disable throttle rates, so explicitly fail if either
        # default throttle is invoked rather than relying on those rates.
        with mock.patch(
            'rest_framework.throttling.AnonRateThrottle.allow_request',
            side_effect=AssertionError('Health must not be throttled'),
        ) as anon_throttle, mock.patch(
            'rest_framework.throttling.UserRateThrottle.allow_request',
            side_effect=AssertionError('Health must not be throttled'),
        ) as user_throttle:
            self.assert_health_payload(self.client.get(HEALTH_URL))
        anon_throttle.assert_not_called()
        user_throttle.assert_not_called()

    def test_repeated_get_checks_connectivity_without_writes(self):
        with self.assertNumQueries(0):
            with mock.patch(ENSURE_CONNECTION) as ensure_connection:
                first = self.client.get(HEALTH_URL)
                second = self.client.get(HEALTH_URL)
        self.assertEqual(ensure_connection.call_count, 2)
        self.assert_health_payload(first)
        self.assertEqual(first.json(), second.json())

    def test_write_methods_are_not_allowed(self):
        with mock.patch(ENSURE_CONNECTION) as ensure_connection:
            for method in ('post', 'put', 'patch', 'delete'):
                with self.subTest(method=method):
                    response = getattr(self.client, method)(HEALTH_URL)
                    self.assertEqual(response.status_code, 405)
        ensure_connection.assert_not_called()

    def test_unexpected_exceptions_propagate_from_drf_dispatch(self):
        for error in (RuntimeError, TypeError, AttributeError, ImproperlyConfigured):
            with self.subTest(error=error.__name__):
                request = RequestFactory().get(HEALTH_URL)
                with mock.patch(
                    ENSURE_CONNECTION, side_effect=error('unexpected failure'),
                ):
                    with self.assertRaisesMessage(error, 'unexpected failure'):
                        HealthCheckView.as_view()(request)

    def test_runtime_error_propagates_through_the_routed_endpoint(self):
        with mock.patch(
            ENSURE_CONNECTION, side_effect=RuntimeError('unexpected failure'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'unexpected failure'):
                self.client.get(HEALTH_URL)


class HealthCheckOpenAPIContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Use the real root URLconf: an isolated test-only route would not
        # detect accidental omission from the application's generated schema.
        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def resolve_schema(self, schema):
        if '$ref' in schema:
            target = self.schema
            for part in schema['$ref'].removeprefix('#/').split('/'):
                target = target[part]
            return self.resolve_schema(target)
        if 'allOf' in schema:
            resolved = {key: value for key, value in schema.items() if key != 'allOf'}
            for part in schema['allOf']:
                resolved.update(self.resolve_schema(part))
            return resolved
        return schema

    def test_generated_openapi_is_valid(self):
        validate_schema(self.schema)

    def test_health_is_documented_as_public_get_without_inputs(self):
        self.assertIn(HEALTH_URL, self.schema['paths'])
        path = self.schema['paths'][HEALTH_URL]
        self.assertEqual(set(path), {'get'})
        operation = path['get']
        self.assertEqual(operation.get('security', []), [])
        self.assertNotIn('requestBody', operation)
        self.assertEqual(operation.get('parameters', []), [])

    def test_both_response_schemas_match_the_frozen_payload(self):
        responses = self.schema['paths'][HEALTH_URL]['get']['responses']
        self.assertEqual(set(responses), {'200', '503'})
        for code, expected_status, expected_database in (
            ('200', 'ok', 'ok'),
            ('503', 'degraded', 'error'),
        ):
            with self.subTest(code=code):
                content = responses[code]['content']
                self.assertEqual(set(content), {'application/json'})
                schema = self.resolve_schema(content['application/json']['schema'])
                self.assertEqual(schema['type'], 'object')
                fields = {'status', 'version', 'database'}
                self.assertEqual(set(schema['required']), fields)
                self.assertEqual(set(schema['properties']), fields)
                properties = schema['properties']
                self.assertNotIn('error', properties)
                self.assertEqual(properties['version']['type'], 'string')
                for name, value in (
                    ('status', expected_status), ('database', expected_database),
                ):
                    field = self.resolve_schema(properties[name])
                    self.assertEqual(field['type'], 'string')
                    self.assertEqual(field['enum'], [value])

    def test_openapi_and_health_share_the_configured_version(self):
        self.assertEqual(
            self.schema['info']['version'], settings.SPECTACULAR_SETTINGS['VERSION'],
        )
        self.assertEqual(
            self.client.get(HEALTH_URL).json()['version'],
            self.schema['info']['version'],
        )
