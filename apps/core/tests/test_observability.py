"""Behavior-level coverage for PROD-027 / F-19 observability."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase

from apps.core import celery_observability
from apps.core.middleware import RequestCorrelationMiddleware
from apps.core.observability import (
    CORRELATION_ID_HEADER,
    JSONFormatter,
    get_correlation_id,
    get_request_id,
    reset_observability_context,
    set_observability_context,
)


class RequestObservabilityTests(SimpleTestCase):
    """AC-1, AC-2, AC-3 and AC-4 — safe HTTP context and lifecycle logs."""

    def setUp(self):
        self.factory = RequestFactory()

    def _run(self, request, view):
        middleware = RequestCorrelationMiddleware(view)
        return middleware(request)

    def test_valid_id_is_correlated_and_returned_without_query_string(self):
        seen = {}

        def view(request):
            seen["request_id"] = request.request_id
            seen["correlation_id"] = request.correlation_id
            return JsonResponse({"ok": True})

        request = self.factory.get(
            "/api/v1/catalog/products/?token=do-not-log",
            HTTP_X_REQUEST_ID="edge-123_abc",
        )
        with self.assertLogs("apps.core.middleware", level="INFO") as captured:
            response = self._run(request, view)

        self.assertEqual(response["X-Request-ID"], "edge-123_abc")
        self.assertEqual(response[CORRELATION_ID_HEADER], "edge-123_abc")
        self.assertEqual(seen["request_id"], "edge-123_abc")
        self.assertEqual(seen["correlation_id"], "edge-123_abc")
        lifecycle = next(
            record for record in captured.records if record.getMessage() == "http_request"
        )
        self.assertEqual(lifecycle.request_id, "edge-123_abc")
        self.assertEqual(lifecycle.correlation_id, "edge-123_abc")
        self.assertEqual(lifecycle.method, "GET")
        self.assertEqual(lifecycle.path, "/api/v1/catalog/products/")
        self.assertEqual(lifecycle.status_code, 200)
        self.assertGreaterEqual(lifecycle.duration_ms, 0)
        self.assertNotIn("do-not-log", lifecycle.path)
        self.assertIsNone(get_request_id())
        self.assertIsNone(get_correlation_id())

    def test_invalid_or_unbounded_incoming_id_is_replaced(self):
        def view(request):
            return JsonResponse({"request_id": request.request_id})

        invalid = "bad\r\nX-Forged: yes"
        request = self.factory.get(
            "/api/v1/health/?access_token=secret",
            HTTP_X_REQUEST_ID=invalid,
        )
        response = self._run(request, view)

        generated = response["X-Request-ID"]
        self.assertNotEqual(generated, invalid)
        self.assertRegex(generated, r"^[0-9a-f-]{36}$")
        self.assertEqual(response[CORRELATION_ID_HEADER], generated)
        self.assertNotIn("secret", response.content.decode())

    def test_expected_http_error_is_visible_by_status_not_error_traceback(self):
        def view(request):
            return JsonResponse({"detail": "invalid"}, status=400)

        request = self.factory.post("/api/v1/orders/")
        with self.assertLogs("apps.core.middleware", level="INFO") as captured:
            response = self._run(request, view)

        self.assertEqual(response.status_code, 400)
        messages = [record.getMessage() for record in captured.records]
        self.assertNotIn("http_request_exception", messages)
        lifecycle = next(record for record in captured.records if record.getMessage() == "http_request")
        self.assertEqual(lifecycle.status_code, 400)
        self.assertFalse(any(record.levelno >= logging.ERROR for record in captured.records))

    def test_expected_api_exception_is_not_error_logged(self):
        from rest_framework.exceptions import ValidationError

        def view(request):
            raise ValidationError({"detail": "invalid input"})

        request = self.factory.post("/api/v1/orders/")
        with self.assertLogs("apps.core.middleware", level="INFO") as captured:
            with self.assertRaises(ValidationError):
                self._run(request, view)

        messages = [record.getMessage() for record in captured.records]
        self.assertNotIn("http_request_exception", messages)
        lifecycle = next(record for record in captured.records if record.getMessage() == "http_request")
        self.assertEqual(lifecycle.status_code, 400)

    def test_unexpected_exception_has_traceback_and_is_reraised(self):
        def view(request):
            raise RuntimeError("unexpected failure")

        request = self.factory.get("/api/v1/orders/?password=never-log")
        with self.assertLogs("apps.core.middleware", level="INFO") as captured:
            with self.assertRaises(RuntimeError):
                self._run(request, view)

        failure = next(
            record
            for record in captured.records
            if record.getMessage() == "http_request_exception"
        )
        self.assertEqual(failure.status_code, 500)
        self.assertIsNotNone(failure.exc_info)
        self.assertEqual(failure.request_id, failure.correlation_id)
        lifecycle = next(record for record in captured.records if record.getMessage() == "http_request")
        self.assertEqual(lifecycle.status_code, 500)
        self.assertIsNone(get_request_id())
        self.assertIsNone(get_correlation_id())

    def test_application_log_created_inside_request_has_same_context(self):
        application_logger = logging.getLogger("apps.core.tests.application")

        def view(request):
            application_logger.info("application_event")
            return JsonResponse({"ok": True})

        request = self.factory.get("/api/v1/catalog/products/")
        with self.assertLogs("apps.core.tests.application", level="INFO") as captured:
            self._run(request, view)

        self.assertEqual(len(captured.records), 1)
        self.assertIsNotNone(captured.records[0].request_id)
        self.assertEqual(
            captured.records[0].request_id,
            captured.records[0].correlation_id,
        )


class StructuredLogPrivacyTests(SimpleTestCase):
    """AC-8 — formatter does not emit credentials or arbitrary PII fields."""

    def test_json_formatter_redacts_message_and_drops_non_allowlisted_fields(self):
        record = logging.LogRecord(
            name="apps.core.tests",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Bearer eyJheader.payload.signature email=user@example.com "
            "?token=reset-secret",
            args=(),
            exc_info=None,
        )
        record.user_id = 7
        record.email = "user@example.com"
        record.authorization = "Bearer another-secret"

        payload = json.loads(JSONFormatter().format(record))

        self.assertEqual(payload["user_id"], 7)
        self.assertNotIn("email", payload)
        self.assertNotIn("authorization", payload)
        self.assertNotIn("reset-secret", payload["message"])
        self.assertNotIn("user@example.com", payload["message"])
        self.assertNotIn("eyJheader.payload.signature", payload["message"])

    def test_traceback_is_kept_for_diagnosis_but_redacted(self):
        try:
            raise RuntimeError("token=trace-secret")
        except RuntimeError:
            record = logging.LogRecord(
                name="apps.core.tests",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="unexpected",
                args=(),
                exc_info=__import__("sys").exc_info(),
            )

        payload = json.loads(JSONFormatter().format(record))
        self.assertIn("exception", payload)
        self.assertNotIn("trace-secret", payload["exception"])


class CeleryObservabilityTests(SimpleTestCase):
    """AC-5 — lifecycle, safe metadata and correlation propagation."""

    def _task(self, *, headers=None, retries=0, queue="orders"):
        request = SimpleNamespace(
            id="11111111-1111-4111-8111-111111111111",
            task="apps.orders.tasks.reconcile",
            headers=headers or {},
            delivery_info={"routing_key": queue},
            retries=retries,
        )
        return SimpleNamespace(
            name="apps.orders.tasks.reconcile",
            request=request,
        )

    def test_started_and_completed_include_safe_identity_and_duration(self):
        task = self._task(headers={"correlation_id": "parent-correlation"})
        with self.assertLogs("apps.core.celery_observability", level="INFO") as captured:
            celery_observability.on_task_prerun(
                sender=task,
                task_id=task.request.id,
                task=task,
                args=("password=secret",),
                kwargs={"token": "secret"},
            )
            celery_observability.on_task_success(sender=task, result={"secret": "value"})
            celery_observability.on_task_postrun(
                sender=task,
                task_id=task.request.id,
                task=task,
                state="SUCCESS",
            )

        self.assertEqual(
            [record.getMessage() for record in captured.records],
            ["celery_task_started", "celery_task_completed"],
        )
        started, completed = captured.records
        for record in (started, completed):
            self.assertEqual(record.task_id, task.request.id)
            self.assertEqual(record.task_name, "apps.orders.tasks.reconcile")
            self.assertEqual(record.queue, "orders")
            self.assertGreaterEqual(record.duration_ms, 0)
            self.assertEqual(record.correlation_id, "parent-correlation")
            self.assertNotIn("secret", record.getMessage())
        self.assertIsNone(get_correlation_id())

    def test_failure_has_traceback_without_task_arguments(self):
        task = self._task()
        error = ValueError("database unavailable")
        try:
            raise error
        except ValueError:
            with self.assertLogs("apps.core.celery_observability", level="INFO") as captured:
                celery_observability.on_task_prerun(
                    sender=task,
                    task_id=task.request.id,
                    task=task,
                    args=("do-not-log",),
                    kwargs={"password": "do-not-log"},
                )
                celery_observability.on_task_failure(
                    sender=task,
                    task_id=task.request.id,
                    exception=error,
                    args=("do-not-log",),
                    kwargs={"password": "do-not-log"},
                    einfo=None,
                )
                celery_observability.on_task_postrun(
                    sender=task,
                    task_id=task.request.id,
                    task=task,
                    state="FAILURE",
                )

        failure = next(record for record in captured.records if record.getMessage() == "celery_task_failed")
        self.assertEqual(failure.exception_type, "ValueError")
        self.assertIsNotNone(failure.exc_info)
        self.assertNotIn("do-not-log", " ".join(captured.output))

    def test_retry_is_a_warning_and_does_not_log_reason_or_arguments(self):
        task = self._task(retries=2)
        reason = RuntimeError("password=retry-secret")
        with self.assertLogs("apps.core.celery_observability", level="INFO") as captured:
            celery_observability.on_task_prerun(
                sender=task,
                task_id=task.request.id,
                task=task,
                args=(),
                kwargs={},
            )
            celery_observability.on_task_retry(
                request=task.request,
                reason=reason,
                einfo=None,
                sender=task,
            )
            celery_observability.on_task_postrun(
                sender=task,
                task_id=task.request.id,
                task=task,
                state="RETRY",
            )

        retry_record = next(record for record in captured.records if record.getMessage() == "celery_task_retry")
        self.assertEqual(retry_record.levelno, logging.WARNING)
        self.assertEqual(retry_record.retry_count, 2)
        self.assertEqual(retry_record.exception_type, "RuntimeError")
        self.assertNotIn("retry-secret", " ".join(captured.output))

    def test_publish_hook_copies_only_context_ids(self):
        tokens = set_observability_context(
            request_id="request-123",
            correlation_id="correlation-456",
        )
        try:
            headers = {}
            celery_observability.on_before_task_publish(
                sender="apps.orders.tasks.reconcile",
                headers=headers,
                body=(("password", "must-not-be-copied"),),
            )
        finally:
            reset_observability_context(tokens)

        self.assertEqual(headers, {
            "request_id": "request-123",
            "correlation_id": "correlation-456",
        })
        self.assertNotIn("password", headers)
