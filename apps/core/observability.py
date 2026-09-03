"""
Small, dependency-free observability primitives for the production runtime.

The application deliberately keeps observability inside the existing Django,
Gunicorn, Celery and Docker Compose stack.  This module provides the shared
request/task context and a conservative JSON formatter; it does not send data
to an external telemetry service.

Security properties:

* request and correlation identifiers are bounded and restricted to a safe
  character set before they can reach a log record or response header;
* request paths never include the query string;
* the formatter emits an allow-list of operational fields instead of copying
  arbitrary ``LogRecord`` attributes;
* messages and tracebacks are redacted for common credential/token forms.

The context is held in ``contextvars`` so it is isolated between concurrent
requests and can be restored when a Celery task is finished.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

# HTTP headers used by the edge and by the Celery message propagation hook.
REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"

# A request id is an operational identifier, not an arbitrary user string.
# 64 characters is enough for UUIDs and common proxy trace ids while keeping
# log lines and response headers bounded.
MAX_IDENTIFIER_LENGTH = 64
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

_REQUEST_ID = contextvars.ContextVar("amazone_request_id", default=None)
_CORRELATION_ID = contextvars.ContextVar("amazone_correlation_id", default=None)


# ---------------------------------------------------------------------------
# Context and safe identifier helpers
# ---------------------------------------------------------------------------


def new_request_id() -> str:
    """Return a fresh, non-secret identifier suitable for logs and headers."""

    return str(uuid.uuid4())


def normalize_identifier(value: Any) -> str | None:
    """Return ``value`` only when it is safe to use as an identifier.

    Incoming proxy headers are untrusted.  In particular, they must not be
    allowed to inject new log lines through CR/LF or consume unbounded memory.
    Invalid values are intentionally discarded rather than logged.
    """

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_IDENTIFIER_LENGTH:
        return None
    if not _IDENTIFIER_RE.fullmatch(candidate):
        return None
    return candidate


def safe_label(value: Any, *, default: str = "unknown") -> str:
    """Bound a trusted operational label such as a task name or queue name."""

    if not isinstance(value, str):
        return default
    candidate = value.strip()
    if not candidate or not _LABEL_RE.fullmatch(candidate):
        return default
    return candidate


def get_request_id() -> str | None:
    """Return the request id for the current execution context, if any."""

    return _REQUEST_ID.get()


# Explicit alias makes the intended use clear to callers and tests.
current_request_id = get_request_id


def get_correlation_id() -> str | None:
    """Return the current request/task correlation id, if any."""

    return _CORRELATION_ID.get()


current_correlation_id = get_correlation_id


def set_observability_context(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
):
    """Set context values and return reset tokens.

    The caller owns the returned tokens and must pass them to
    :func:`reset_observability_context` in a ``finally`` block.  Values are
    validated even when this helper is called outside the HTTP middleware.
    """

    safe_request_id = normalize_identifier(request_id)
    safe_correlation_id = normalize_identifier(correlation_id)
    if safe_correlation_id is None:
        safe_correlation_id = safe_request_id

    request_token = _REQUEST_ID.set(safe_request_id)
    correlation_token = _CORRELATION_ID.set(safe_correlation_id)
    return request_token, correlation_token


def reset_observability_context(tokens) -> None:
    """Restore a context previously returned by the setter."""

    if not tokens:
        return
    request_token, correlation_token = tokens
    # Reset in reverse order, as for nested context managers.
    _CORRELATION_ID.reset(correlation_token)
    _REQUEST_ID.reset(request_token)


def _header_value(headers: Any, *names: str) -> Any:
    """Read a header from Django's case-insensitive mapping or a plain dict."""

    if headers is None or not hasattr(headers, "get"):
        return None
    for name in names:
        value = headers.get(name)
        if value is not None:
            return value
    return None


def request_identifiers(request) -> tuple[str, str]:
    """Build safe request and correlation ids from an incoming Django request.

    ``X-Request-ID`` is the id of this HTTP request.  A valid
    ``X-Correlation-ID`` is retained when present so a caller can correlate
    several requests.  When no valid request id is supplied, a UUID is
    generated.  The raw invalid value is never included in a log message.
    """

    headers = getattr(request, "headers", None)
    request_value = _header_value(
        headers,
        REQUEST_ID_HEADER,
        REQUEST_ID_HEADER.lower(),
    )
    correlation_value = _header_value(
        headers,
        CORRELATION_ID_HEADER,
        CORRELATION_ID_HEADER.lower(),
    )

    # RequestFactory and a few WSGI adapters expose META even when a headers
    # wrapper is unavailable.
    meta = getattr(request, "META", {})
    if request_value is None:
        request_value = _header_value(
            meta,
            "HTTP_X_REQUEST_ID",
            "HTTP_X_REQUEST_ID".lower(),
        )
    if correlation_value is None:
        correlation_value = _header_value(
            meta,
            "HTTP_X_CORRELATION_ID",
            "HTTP_X_CORRELATION_ID".lower(),
        )

    safe_request_id = normalize_identifier(request_value) or new_request_id()
    safe_correlation_id = normalize_identifier(correlation_value) or safe_request_id
    return safe_request_id, safe_correlation_id


def safe_request_path(request) -> str:
    """Return a bounded path representation with no query string.

    Query parameters are intentionally excluded because they commonly contain
    reset tokens, access tokens, signatures, coupon codes, or other secrets.
    A few secret-like path components are redacted as a second defensive
    measure.  The function never reads ``request.body`` or arbitrary headers.
    """

    raw_path = getattr(request, "path_info", None) or getattr(request, "path", None) or "/"
    path = str(raw_path).split("?", 1)[0]
    path = _CONTROL_RE.sub("", path)
    path = re.sub(
        r"(?i)(/(?:token|uid|secret|signature|password)(?:/|=))[^/?#]+",
        r"\1[REDACTED]",
        path,
    )
    path = path[:512]
    return path or "/"


def safe_http_method(request) -> str:
    """Return an uppercase, bounded HTTP method for a lifecycle record."""

    method = re.sub(r"[^A-Za-z]", "", str(getattr(request, "method", "UNKNOWN")))
    return method.upper()[:16] or "UNKNOWN"


# ---------------------------------------------------------------------------
# Privacy-aware logging
# ---------------------------------------------------------------------------

# Existing domain logs use these fields.  Keeping an explicit allow-list
# preserves useful operational identifiers while excluding arbitrary values
# such as email, phone, body, payload, headers, IP and provider secrets.
SAFE_LOG_FIELDS = frozenset(
    {
        "address_id",
        "amount",
        "amount_after_discount",
        "attempt",
        "available",
        "cart_id",
        "changed_by",
        "count",
        "coupon_code",
        "available_quantity",
        "cancelled_by",
        "cancelled_count",
        "channel",
        "code",
        "confirmed_orders",
        "cost",
        "coupon_id",
        "currency",
        "database",
        "delta",
        "delivered_orders",
        "discount",
        "delivery_cost",
        "duration_ms",
        "event_type",
        "deleted_by",
        "helpful_no",
        "helpful_yes",
        "exception_type",
        "failed",
        "found",
        "image_id",
        "in_atomic_block",
        "is_active",
        "is_low_stock",
        "is_out_of_stock",
        "items_count",
        "item_id",
        "kind",
        "max_discount",
        "max_price",
        "max_shipping_cost",
        "merged_count",
        "method",
        "method_id",
        "movements_count",
        "min_price",
        "movement_id",
        "moved_count",
        "new_order_status",
        "new_status",
        "new_total",
        "notif_id",
        "notification_id",
        "old_status",
        "order_id",
        "order_number",
        "order_status",
        "order_total",
        "outcome",
        "payment_id",
        "payment_number",
        "period_end",
        "period_start",
        "price",
        "product_id",
        "product_uuid",
        "provider",
        "qty_after",
        "qty_before",
        "quantity",
        "quantity_after",
        "quantity_before",
        "queue",
        "rating",
        "raw_cost",
        "refund_amount",
        "removed_discount",
        "reviews_count",
        "refund_pending_amount",
        "refund_required_amount",
        "removed_count",
        "reserved",
        "reserved_quantity",
        "retry",
        "retry_count",
        "review_id",
        "shipment_id",
        "shipment_status",
        "shipping_cost",
        "shipping_method_id",
        "shipping_type",
        "source",
        "status",
        "status_code",
        "stock_id",
        "task_id",
        "task_name",
        "task_state",
        "threshold",
        "total",
        "total_items_sold",
        "total_orders",
        "total_refunded",
        "total_revenue",
        "type",
        "user_id",
        "uuid",
        "variant_id",
        "variant_sku",
        "vendor",
        "verified",
        "view_id",
        "vote",
        "weight_kg",
        "wishlist_id",
        "wishlist_item_id",
        "zone_code",
    }
)

# Message text is retained as an event name for the existing application
# logs, but common accidental secret forms are removed before serialization.
_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
)
_AUTH_RE = re.compile(
    r"(?i)\b(Bearer\s+)([A-Za-z0-9._~+/=-]+)"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|uid|password|passwd|secret|signature|authorization|"
    r"access_token|refresh_token|api_key)=)[^&#\s]+"
)
_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|authorization|api[_-]?key|signature|uid)"
    r"(\s*[:=]\s*)[^\s,;]+"
)


def redact_text(value: Any) -> str:
    """Redact common credentials/PII without making logs unusable."""

    text = str(value)
    text = _JWT_RE.sub("[REDACTED_JWT]", text)
    text = _AUTH_RE.sub(r"\1[REDACTED]", text)
    text = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    text = _KEY_VALUE_SECRET_RE.sub(r"\1\2[REDACTED]", text)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    return text


def _json_value(value: Any) -> Any:
    """Convert an allow-listed value to a JSON-safe, redacted value."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value)


class RequestContextFilter(logging.Filter):
    """Add request/correlation context to records handled by production logs."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        if getattr(record, "request_id", None) is None:
            record.request_id = get_request_id()
        if getattr(record, "correlation_id", None) is None:
            record.correlation_id = get_correlation_id() or get_request_id()
        return True


class JSONFormatter(logging.Formatter):
    """Compact JSON-lines formatter with a privacy-preserving field allow-list."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None) or get_request_id()
        correlation_id = (
            getattr(record, "correlation_id", None)
            or get_correlation_id()
            or request_id
        )
        message = redact_text(record.getMessage())

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            # Keep both names: ``event`` is convenient for structured log
            # consumers, while ``message`` is familiar in Docker log viewers.
            "event": message,
            "message": message,
            "request_id": request_id or "-",
            "correlation_id": correlation_id or "-",
        }

        for field in SAFE_LOG_FIELDS:
            if hasattr(record, field):
                value = getattr(record, field)
                if value is not None:
                    payload[field] = _json_value(value)

        if record.exc_info:
            # ``formatException`` includes the traceback needed to diagnose an
            # unexpected failure.  It is redacted before it leaves the process.
            payload["exception"] = redact_text(self.formatException(record.exc_info))

        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=redact_text,
        )


# A LogRecord factory makes the context available to custom handlers and to
# tests using ``assertLogs`` as well as to the production handler filter.  Do
# not pass request_id/correlation_id in ``extra`` at call sites: the factory
# owns those reserved fields and prevents accidental overwrite errors.

def _install_context_record_factory() -> None:
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_amazone_observability_factory", False):
        return

    def context_record_factory(*args, **kwargs):
        record = current_factory(*args, **kwargs)
        record.request_id = get_request_id()
        record.correlation_id = get_correlation_id() or get_request_id()
        return record

    context_record_factory._amazone_observability_factory = True
    logging.setLogRecordFactory(context_record_factory)


_install_context_record_factory()


__all__ = [
    "CORRELATION_ID_HEADER",
    "JSONFormatter",
    "MAX_IDENTIFIER_LENGTH",
    "REQUEST_ID_HEADER",
    "RequestContextFilter",
    "SAFE_LOG_FIELDS",
    "current_correlation_id",
    "current_request_id",
    "get_correlation_id",
    "get_request_id",
    "new_request_id",
    "normalize_identifier",
    "redact_text",
    "request_identifiers",
    "reset_observability_context",
    "safe_http_method",
    "safe_label",
    "safe_request_path",
    "set_observability_context",
]
