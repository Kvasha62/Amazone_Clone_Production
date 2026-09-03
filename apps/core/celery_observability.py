"""Celery lifecycle logging and safe request-context propagation.

The hooks use Celery's existing signals only.  They do not inspect or log task
arguments/results and do not add a broker, event bus or external monitoring
service.  A request/correlation id is copied into Celery message headers when
one exists, then restored after the task so worker processes cannot leak
context between tasks.
"""

from __future__ import annotations

import contextvars
import logging
import time
from collections.abc import Mapping, MutableMapping
from typing import Any

from celery.signals import (
    before_task_publish,
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    task_success,
)

from apps.core.observability import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    get_correlation_id,
    get_request_id,
    new_request_id,
    normalize_identifier,
    reset_observability_context,
    safe_label,
    set_observability_context,
)

logger = logging.getLogger(__name__)

# A task is normally executed from one worker context at a time.  A
# ContextVar, rather than a module-level "current task", also remains correct
# for eager tests and alternative Celery pools that use execution contexts.
_TASK_CONTEXT = contextvars.ContextVar(
    "amazone_celery_task_context",
    default=None,
)


# ---------------------------------------------------------------------------
# Generic Celery request helpers
# ---------------------------------------------------------------------------


def _request_value(request: Any, name: str, default=None):
    if request is None:
        return default
    if isinstance(request, Mapping):
        return request.get(name, default)
    return getattr(request, name, default)


def _headers(request: Any):
    value = _request_value(request, "headers", {})
    return value if isinstance(value, Mapping) else {}


def _header(headers: Mapping, *names: str):
    for name in names:
        value = headers.get(name)
        if value is not None:
            return value
    return None


def _task_request(task: Any):
    return getattr(task, "request", None)


def _task_name(task: Any, request: Any = None, sender: Any = None) -> str:
    candidate = (
        getattr(task, "name", None)
        or _request_value(request, "task", None)
        or getattr(sender, "name", None)
        or sender
    )
    return safe_label(candidate)


def _task_id(task_id: Any, request: Any = None) -> str:
    candidate = task_id or _request_value(request, "id", None)
    return normalize_identifier(candidate) or "unknown"


def _queue(request: Any) -> str:
    delivery_info = _request_value(request, "delivery_info", {})
    if not isinstance(delivery_info, Mapping):
        delivery_info = {}
    return safe_label(
        delivery_info.get("queue") or delivery_info.get("routing_key"),
    )


def _parent_identifiers(request: Any) -> tuple[str | None, str]:
    """Get validated propagated ids, inheriting eager/current context safely."""

    headers = _headers(request)
    request_id = (
        normalize_identifier(
            _header(
                headers,
                "request_id",
                REQUEST_ID_HEADER,
                REQUEST_ID_HEADER.lower(),
            )
        )
        or normalize_identifier(_request_value(request, "request_id", None))
        or get_request_id()
    )
    correlation_id = (
        normalize_identifier(
            _header(
                headers,
                "correlation_id",
                CORRELATION_ID_HEADER,
                CORRELATION_ID_HEADER.lower(),
            )
        )
        # In eager mode the current request context is authoritative.  Celery
        # itself also has a ``correlation_id`` attribute, which usually equals
        # the task id and must not replace the parent HTTP correlation id.
        or get_correlation_id()
        or normalize_identifier(_request_value(request, "root_id", None))
        or normalize_identifier(_request_value(request, "correlation_id", None))
        or request_id
        or new_request_id()
    )
    return request_id, correlation_id


def _duration_ms(state: dict) -> float:
    return round(max(time.perf_counter() - state["started"], 0) * 1000, 3)


def _new_task_context(
    *,
    task: Any = None,
    sender: Any = None,
    request: Any = None,
    task_id: Any = None,
):
    request_id, correlation_id = _parent_identifiers(request)
    context_tokens = set_observability_context(
        request_id=request_id,
        correlation_id=correlation_id,
    )
    state = {
        "task_id": _task_id(task_id, request),
        "task_name": _task_name(task, request, sender),
        "queue": _queue(request),
        "started": time.perf_counter(),
        "request_id": request_id,
        "correlation_id": correlation_id,
        "context_tokens": context_tokens,
    }
    state["task_context_token"] = _TASK_CONTEXT.set(state)
    return state


def _current_or_new_context(
    *,
    task: Any = None,
    sender: Any = None,
    request: Any = None,
    task_id: Any = None,
):
    state = _TASK_CONTEXT.get()
    expected_id = _task_id(task_id, request)
    if state is not None and (
        task_id is None
        or state["task_id"] == expected_id
        or expected_id == "unknown"
    ):
        return state
    return _new_task_context(
        task=task,
        sender=sender,
        request=request,
        task_id=task_id,
    )


def _task_extra(state: dict, *, task_state: str, outcome: str, **extra) -> dict:
    payload = {
        "task_name": state["task_name"],
        "task_id": state["task_id"],
        "queue": state["queue"],
        "task_state": task_state,
        "outcome": outcome,
        "duration_ms": _duration_ms(state),
    }
    payload.update(extra)
    return payload


def _exception_type(exception: Any) -> str | None:
    if exception is None:
        return None
    return safe_label(type(exception).__name__, default="unknown")


def _exception_info(exception: Any, einfo: Any):
    """Return a logging tuple without ever logging task args or kwargs."""

    candidate = getattr(einfo, "exc_info", None) if einfo is not None else None
    if isinstance(candidate, tuple) and len(candidate) == 3:
        return candidate
    if exception is not None:
        return type(exception), exception, getattr(exception, "__traceback__", None)
    return None


def _cleanup_task_context() -> None:
    state = _TASK_CONTEXT.get()
    if state is None:
        return
    try:
        _TASK_CONTEXT.reset(state["task_context_token"])
    finally:
        reset_observability_context(state["context_tokens"])


# ---------------------------------------------------------------------------
# Context propagation
# ---------------------------------------------------------------------------


@before_task_publish.connect(weak=False)
def on_before_task_publish(
    sender=None,
    headers=None,
    body=None,
    **kwargs,
):
    """Copy only validated ids into a task header; never copy body/arguments."""

    if not isinstance(headers, MutableMapping):
        return

    request_id = get_request_id()
    correlation_id = get_correlation_id() or request_id
    if request_id:
        headers["request_id"] = request_id
    if correlation_id:
        headers["correlation_id"] = correlation_id


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------


@task_prerun.connect(weak=False)
def on_task_prerun(
    sender=None,
    task_id=None,
    task=None,
    args=None,
    kwargs=None,
    **signal_kwargs,
):
    task = task or sender
    request = _task_request(task)
    state = _new_task_context(
        task=task,
        sender=sender,
        request=request,
        task_id=task_id,
    )
    logger.info(
        "celery_task_started",
        extra=_task_extra(
            state,
            task_state="started",
            outcome="started",
            retry=False,
            attempt=_request_value(request, "retries", 0),
        ),
    )


@task_success.connect(weak=False)
def on_task_success(sender=None, result=None, **signal_kwargs):
    task = sender
    request = _task_request(task)
    state = _current_or_new_context(
        task=task,
        sender=sender,
        request=request,
        task_id=_request_value(request, "id", None),
    )
    logger.info(
        "celery_task_completed",
        extra=_task_extra(
            state,
            task_state="success",
            outcome="success",
            retry=False,
        ),
    )


@task_failure.connect(weak=False)
def on_task_failure(
    sender=None,
    task_id=None,
    exception=None,
    args=None,
    kwargs=None,
    traceback=None,
    einfo=None,
    **signal_kwargs,
):
    task = sender
    request = _task_request(task)
    state = _current_or_new_context(
        task=task,
        sender=sender,
        request=request,
        task_id=task_id,
    )
    extra = _task_extra(
        state,
        task_state="failure",
        outcome="failure",
        retry=False,
    )
    exception_type = _exception_type(exception)
    if exception_type:
        extra["exception_type"] = exception_type

    exc_info = _exception_info(exception, einfo)
    if exc_info is None:
        logger.error("celery_task_failed", extra=extra)
    else:
        logger.error("celery_task_failed", extra=extra, exc_info=exc_info)


@task_retry.connect(weak=False)
def on_task_retry(
    request=None,
    reason=None,
    einfo=None,
    **signal_kwargs,
):
    task = signal_kwargs.get("sender")
    state = _current_or_new_context(
        task=task,
        sender=task,
        request=request,
        task_id=_request_value(request, "id", None),
    )
    retry_number = _request_value(request, "retries", None)
    extra = _task_extra(
        state,
        task_state="retry",
        outcome="retry",
        retry=True,
        retry_count=retry_number if isinstance(retry_number, int) else 0,
    )
    exception_type = _exception_type(reason)
    if exception_type:
        extra["exception_type"] = exception_type
    # Retry is an expected control-flow outcome.  The type is useful for
    # diagnosis, but its reason/traceback and task arguments are intentionally
    # not emitted.
    logger.warning("celery_task_retry", extra=extra)


@task_postrun.connect(weak=False)
def on_task_postrun(
    sender=None,
    task_id=None,
    task=None,
    args=None,
    kwargs=None,
    retval=None,
    state=None,
    **signal_kwargs,
):
    """Clean context and cover terminal states without duplicate normal logs."""

    task = task or sender
    request = _task_request(task)
    current = _current_or_new_context(
        task=task,
        sender=sender,
        request=request,
        task_id=task_id,
    )

    normalized_state = str(state or "UNKNOWN").upper()
    if normalized_state not in {"SUCCESS", "FAILURE", "RETRY"}:
        logger.info(
            "celery_task_finished",
            extra=_task_extra(
                current,
                task_state=normalized_state.lower(),
                outcome=normalized_state.lower(),
                retry=normalized_state == "RETRY",
            ),
        )

    _cleanup_task_context()


__all__ = [
    "on_before_task_publish",
    "on_task_failure",
    "on_task_postrun",
    "on_task_prerun",
    "on_task_retry",
    "on_task_success",
]
