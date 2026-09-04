"""
Canonical API v1 error envelope (API-04).

Public ``/api/v1/`` resource endpoints return a single JSON shape for every
client-visible failure.  HTTP status semantics are unchanged: this module
normalizes representation, not business rules.

The envelope never includes exception class names, tracebacks, SQL, tokens,
passwords, or other internals.
"""

from __future__ import annotations

import logging
from typing import Any

from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    MethodNotAllowed,
    NotAcceptable,
    NotAuthenticated,
    NotFound,
    ParseError,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.observability import get_request_id, redact_text, safe_http_method, safe_request_path

logger = logging.getLogger(__name__)

# Stable machine-readable codes.  Clients MUST branch on HTTP status and
# ``error.code`` / ``error.details[].field``, never on free-text messages.
CODE_VALIDATION = "validation_error"
CODE_PARSE = "parse_error"
CODE_AUTHENTICATION = "authentication_failed"
CODE_NOT_AUTHENTICATED = "not_authenticated"
CODE_PERMISSION = "permission_denied"
CODE_NOT_FOUND = "not_found"
CODE_METHOD = "method_not_allowed"
CODE_NOT_ACCEPTABLE = "not_acceptable"
CODE_THROTTLED = "throttled"
CODE_SERVER = "server_error"
CODE_BAD_GATEWAY = "bad_gateway"

_STATUS_DEFAULT_CODE = {
    status.HTTP_400_BAD_REQUEST: CODE_VALIDATION,
    status.HTTP_401_UNAUTHORIZED: CODE_AUTHENTICATION,
    status.HTTP_403_FORBIDDEN: CODE_PERMISSION,
    status.HTTP_404_NOT_FOUND: CODE_NOT_FOUND,
    status.HTTP_405_METHOD_NOT_ALLOWED: CODE_METHOD,
    status.HTTP_406_NOT_ACCEPTABLE: CODE_NOT_ACCEPTABLE,
    status.HTTP_429_TOO_MANY_REQUESTS: CODE_THROTTLED,
    status.HTTP_500_INTERNAL_SERVER_ERROR: CODE_SERVER,
    status.HTTP_502_BAD_GATEWAY: CODE_BAD_GATEWAY,
}

_SAFE_DEFAULT_MESSAGES = {
    CODE_VALIDATION: "Запрос содержит некорректные данные.",
    CODE_PARSE: "Тело запроса не удалось разобрать.",
    CODE_AUTHENTICATION: "Ошибка аутентификации.",
    CODE_NOT_AUTHENTICATED: "Требуется аутентификация.",
    CODE_PERMISSION: "Недостаточно прав.",
    CODE_NOT_FOUND: "Ресурс не найден.",
    CODE_METHOD: "Метод не поддерживается.",
    CODE_NOT_ACCEPTABLE: "Запрошенный формат ответа недоступен.",
    CODE_THROTTLED: "Слишком много запросов.",
    CODE_SERVER: "Внутренняя ошибка сервера.",
    CODE_BAD_GATEWAY: "Временный сбой обработки запроса.",
}

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "password",
        "password_confirm",
        "old_password",
        "new_password",
        "new_password_confirm",
        "refresh",
        "access",
        "token",
        "uid",
        "authorization",
        "secret",
        "signature",
    }
)


class BadGateway(APIException):
    """Provider-retry signal used by the payment webhook (existing 502)."""

    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = CODE_BAD_GATEWAY
    default_detail = _SAFE_DEFAULT_MESSAGES[CODE_BAD_GATEWAY]


def error_envelope(
    *,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build the canonical JSON body."""

    rid = request_id if request_id is not None else get_request_id()
    payload: dict[str, Any] = {
        "error": {
            "code": str(code),
            "message": redact_text(message),
            "details": details or [],
        }
    }
    if rid:
        payload["request_id"] = rid
    return payload


def flatten_error_details(data: Any, prefix: str | None = None) -> list[dict[str, Any]]:
    """Turn DRF's nested ErrorDetail structures into a stable list.

    Nested serializer errors use dotted / indexed field paths
    (``items[0].quantity``).  ``non_field_errors`` become details with
    ``field: null``.
    """

    items: list[dict[str, Any]] = []
    if data is None:
        return items

    if isinstance(data, dict):
        for key, value in data.items():
            if key in ("detail", "non_field_errors"):
                child_prefix = prefix
            elif prefix:
                child_prefix = f"{prefix}.{key}"
            else:
                child_prefix = str(key)
            items.extend(flatten_error_details(value, child_prefix))
        return items

    if isinstance(data, list):
        if data and all(not isinstance(item, (dict, list)) for item in data):
            for item in data:
                items.append(_detail_item(item, prefix))
            return items
        for index, item in enumerate(data):
            if isinstance(item, (dict, list)):
                child = f"{prefix}[{index}]" if prefix else f"[{index}]"
                items.extend(flatten_error_details(item, child))
            else:
                items.append(_detail_item(item, prefix))
        return items

    items.append(_detail_item(data, prefix))
    return items


def _detail_item(value: Any, field: str | None) -> dict[str, Any]:
    code = getattr(value, "code", None) or "invalid"
    message = redact_text(str(value))
    if field in _SENSITIVE_FIELD_NAMES:
        # Keep the field name (clients need it) but never echo the submitted
        # secret back through the message.
        message = redact_text(message)
    return {
        "field": field,
        "code": str(code),
        "message": message,
    }


def _code_for_exception(exc: Exception, http_status: int) -> str:
    if isinstance(exc, ValidationError):
        return CODE_VALIDATION
    if isinstance(exc, ParseError):
        return CODE_PARSE
    if isinstance(exc, NotAuthenticated):
        return CODE_NOT_AUTHENTICATED
    if isinstance(exc, AuthenticationFailed):
        return CODE_AUTHENTICATION
    if isinstance(exc, PermissionDenied):
        return CODE_PERMISSION
    if isinstance(exc, NotFound):
        return CODE_NOT_FOUND
    if isinstance(exc, MethodNotAllowed):
        return CODE_METHOD
    if isinstance(exc, NotAcceptable):
        return CODE_NOT_ACCEPTABLE
    if isinstance(exc, Throttled):
        return CODE_THROTTLED
    if isinstance(exc, BadGateway):
        return CODE_BAD_GATEWAY
    default_code = getattr(exc, "default_code", None)
    if default_code in _SAFE_DEFAULT_MESSAGES or default_code in (
        CODE_AUTHENTICATION,
        "token_not_valid",
        "no_active_account",
    ):
        if default_code in ("token_not_valid", "no_active_account", "authentication_failed"):
            return CODE_AUTHENTICATION
        return str(default_code)
    return _STATUS_DEFAULT_CODE.get(http_status, CODE_SERVER)


def _message_for(code: str, details: list[dict[str, Any]], fallback: str | None) -> str:
    default = _SAFE_DEFAULT_MESSAGES.get(code, _SAFE_DEFAULT_MESSAGES[CODE_SERVER])
    if code in (CODE_SERVER,):
        return default
    if fallback:
        text = redact_text(str(fallback)).strip()
        # Nested dict/list stringification is not a client message.
        if text and not text.startswith("{") and not text.startswith("["):
            return text
    if len(details) == 1 and details[0].get("message"):
        return details[0]["message"]
    return default


def _fallback_message_from_exc(exc: Exception) -> str | None:
    detail = getattr(exc, "detail", None)
    if detail is None:
        return None
    if isinstance(detail, (dict, list)):
        return None
    return str(detail)


def exception_handler(exc, context):
    """DRF exception handler that always emits the canonical envelope."""

    request = context.get("request") if isinstance(context, dict) else None

    if not isinstance(exc, APIException):
        from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
        from django.http import Http404

        if isinstance(exc, Http404):
            exc = NotFound(str(exc) or _SAFE_DEFAULT_MESSAGES[CODE_NOT_FOUND])
        elif isinstance(exc, DjangoPermissionDenied):
            exc = PermissionDenied(str(exc) or _SAFE_DEFAULT_MESSAGES[CODE_PERMISSION])
        else:
            return _unhandled(exc, request)

    response = drf_exception_handler(exc, context)
    if response is None:
        return _unhandled(exc, request)

    http_status = response.status_code
    code = _code_for_exception(exc, http_status)
    details = flatten_error_details(response.data)
    message = _message_for(code, details, _fallback_message_from_exc(exc))
    body = error_envelope(code=code, message=message, details=details)
    response.data = body
    return response


def _unhandled(exc: Exception, request) -> Response:
    extra = {
        "exception_type": type(exc).__name__,
        "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
    }
    if request is not None:
        extra["method"] = safe_http_method(request)
        extra["path"] = safe_request_path(request)
    logger.exception("api_unhandled_exception", extra=extra)
    body = error_envelope(
        code=CODE_SERVER,
        message=_SAFE_DEFAULT_MESSAGES[CODE_SERVER],
        details=[],
    )
    return Response(body, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


__all__ = [
    "BadGateway",
    "CODE_AUTHENTICATION",
    "CODE_BAD_GATEWAY",
    "CODE_METHOD",
    "CODE_NOT_ACCEPTABLE",
    "CODE_NOT_AUTHENTICATED",
    "CODE_NOT_FOUND",
    "CODE_PARSE",
    "CODE_PERMISSION",
    "CODE_SERVER",
    "CODE_THROTTLED",
    "CODE_VALIDATION",
    "error_envelope",
    "exception_handler",
    "flatten_error_details",
]
