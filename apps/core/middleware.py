"""HTTP request correlation and lifecycle logging middleware."""

from __future__ import annotations

import logging
import time

from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404
from rest_framework.exceptions import APIException

from apps.core.observability import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    request_identifiers,
    reset_observability_context,
    safe_http_method,
    safe_request_path,
    set_observability_context,
)

logger = logging.getLogger(__name__)


class RequestCorrelationMiddleware:
    """Attach a safe id to each request and emit one lifecycle record.

    The middleware deliberately logs only method, path (without query
    parameters), status and elapsed time.  It does not inspect request bodies,
    authorization headers, cookies or user objects.  Exceptions that escape
    Django's normal exception conversion are logged with traceback and then
    re-raised so this middleware cannot change the application's error
    semantics.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _extra(request, *, status_code: int, duration_ms: float) -> dict:
        """Build the small, allow-listed HTTP lifecycle context."""

        return {
            "method": safe_http_method(request),
            "path": safe_request_path(request),
            "status_code": status_code,
            "duration_ms": duration_ms,
        }

    @staticmethod
    def _status_code(response) -> int:
        """Read a response status without allowing telemetry to break a view."""

        try:
            return int(getattr(response, "status_code", 500))
        except (TypeError, ValueError):
            return 500

    @staticmethod
    def _expected_exception_status(exception) -> int:
        """Map a normal framework/business exception to its HTTP status."""

        status_code = getattr(exception, "status_code", None)
        if status_code is not None:
            try:
                return int(status_code)
            except (TypeError, ValueError):
                pass
        if isinstance(exception, Http404):
            return 404
        if isinstance(exception, PermissionDenied):
            return 403
        # SuspiciousOperation is handled by Django's security logger.
        return 400

    def __call__(self, request):
        request_id, correlation_id = request_identifiers(request)
        context_tokens = set_observability_context(
            request_id=request_id,
            correlation_id=correlation_id,
        )

        # Expose the already-validated values to application code without
        # exposing the original headers.  This is useful for an application
        # event that needs to link itself to the current request.
        request.request_id = request_id
        request.correlation_id = correlation_id

        started = time.perf_counter()
        response = None
        expected_exception_status = None

        try:
            response = self.get_response(request)
            return response
        except (APIException, Http404, PermissionDenied, SuspiciousOperation) as exc:
            # DRF/Django normally turn these expected business/security
            # exceptions into responses before they reach this boundary.  The
            # explicit classification also keeps direct middleware use quiet.
            expected_exception_status = self._expected_exception_status(exc)
            raise
        except Exception:
            # This is an intentional exception boundary for observability:
            # unexpected failures remain visible with a traceback and are not
            # converted, swallowed or mistaken for business validation errors.
            duration_ms = round(max(time.perf_counter() - started, 0) * 1000, 3)
            logger.exception(
                "http_request_exception",
                extra=self._extra(
                    request,
                    status_code=500,
                    duration_ms=duration_ms,
                ),
            )
            raise
        finally:
            duration_ms = round(max(time.perf_counter() - started, 0) * 1000, 3)
            status_code = self._status_code(response)
            if response is None and expected_exception_status is not None:
                status_code = expected_exception_status

            if response is not None:
                # Additive response headers make correlation available to an
                # API client and to the edge without changing the JSON API.
                response[REQUEST_ID_HEADER] = request_id
                response[CORRELATION_ID_HEADER] = correlation_id

            logger.info(
                "http_request",
                extra=self._extra(
                    request,
                    status_code=status_code,
                    duration_ms=duration_ms,
                ),
            )
            reset_observability_context(context_tokens)


__all__ = ["RequestCorrelationMiddleware"]
