"""
API v1 canonical collection/pagination contract (API-05).

All paginated ``/api/v1/`` collection endpoints return the same envelope::

    {
        "count": int,            # total number of items in the queryset
        "page": int,             # requested 1-based page
        "page_size": int,        # effective page size
        "total_pages": int,      # ceil(count / page_size); 0 when empty
        "next": url | null,
        "previous": url | null,
        "results": [...]
    }

Semantics:

* ``page`` defaults to 1 and must be an integer >= 1.
* ``page_size`` defaults to 20 and must be an integer between 1 and 100.
* A page beyond the last page is a valid observable state: HTTP 200 with an
  empty ``results`` list (no 404 is invented).
* Invalid pagination parameters raise ``ValidationError`` so the API-04
  handler emits the canonical error envelope.
* Callers MUST apply deterministic ordering to the queryset before calling
  ``paginate_queryset``. This module does not silently add ordering.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from django.http import QueryDict
from rest_framework.exceptions import ValidationError

try:
    from drf_spectacular.types import OpenApiTypes
    from drf_spectacular.utils import OpenApiParameter

    _PAGE_PARAM = OpenApiParameter(
        name='page',
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        required=False,
        description='1-based page number. Default 1. Integer >= 1.',
    )
    _PAGE_SIZE_PARAM = OpenApiParameter(
        name='page_size',
        type=OpenApiTypes.INT,
        location=OpenApiParameter.QUERY,
        required=False,
        description='Items per page. Default 20. Integer 1-100.',
    )
except ImportError:  # pragma: no cover - drf-spectacular is installed in CI
    _PAGE_PARAM = None
    _PAGE_SIZE_PARAM = None

DEFAULT_PAGE_SIZE = 20
MIN_PAGE_SIZE = 1
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class PaginationMeta:
    """Metadata shared by every canonical paginated response."""

    page: int
    page_size: int
    count: int
    total_pages: int


def pagination_parameters() -> list:
    """OpenAPI query parameters for the canonical pagination contract."""
    if _PAGE_PARAM is None:
        return []
    return [_PAGE_PARAM, _PAGE_SIZE_PARAM]


def _parse_query_int(request, name: str, default: int, *, minimum: int, maximum: int | None = None) -> int:
    """Parse a single integer query parameter deterministically.

    Repeated query parameters use the last occurrence (DRF QueryDict semantics).
    """
    raw = request.query_params.get(name)
    if raw is None:
        return default
    # Defensive: QueryDict.get already returns the last value, but be explicit.
    if isinstance(raw, (list, tuple)):
        raw = raw[-1]
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValidationError({name: 'Должно быть целым числом.'})
    if value < minimum:
        raise ValidationError({name: f'Должно быть не меньше {minimum}.'})
    if maximum is not None and value > maximum:
        raise ValidationError({name: f'Должно быть не больше {maximum}.'})
    return value


def parse_pagination(request, *, default_page_size: int = DEFAULT_PAGE_SIZE):
    """Return ``(page, page_size)`` after applying the API-05 bounds."""
    page = _parse_query_int(
        request, 'page', 1, minimum=1,
    )
    page_size = _parse_query_int(
        request, 'page_size', default_page_size,
        minimum=MIN_PAGE_SIZE,
        maximum=MAX_PAGE_SIZE,
    )
    return page, page_size


def paginate_queryset(queryset, request, *, default_page_size: int = DEFAULT_PAGE_SIZE):
    """Slice a deterministically ordered queryset and return (items, metadata).

    The returned ``items`` is a plain list of the current page. ``page`` may
    exceed ``total_pages``; in that case ``items`` is empty and HTTP status
    remains 200 (the pagination contract treats this as empty results).
    """
    page, page_size = parse_pagination(request, default_page_size=default_page_size)

    if hasattr(queryset, 'count'):
        count = queryset.count()
    else:
        count = len(queryset)

    total_pages = math.ceil(count / page_size) if count > 0 else 0
    start = (page - 1) * page_size
    end = start + page_size

    if hasattr(queryset, '__getitem__'):
        page_items = list(queryset[start:end])
    else:
        page_items = list(queryset)[start:end]

    meta = PaginationMeta(
        page=page,
        page_size=page_size,
        count=count,
        total_pages=total_pages,
    )
    return page_items, meta


def _page_url(request, page: int, page_size: int) -> str:
    params: QueryDict = request.query_params.copy()
    params['page'] = str(page)
    params['page_size'] = str(page_size)
    base = request.build_absolute_uri(request.path)
    query = params.urlencode()
    if not query:
        return base
    return f'{base}?{query}'


def build_paginated_response_data(
    request,
    results: Iterable,
    meta: PaginationMeta,
) -> dict:
    """Build the canonical collection response body."""
    results = list(results)

    if meta.total_pages == 0:
        next_url = None
        previous_url = None
    else:
        next_url = (
            _page_url(request, meta.page + 1, meta.page_size)
            if meta.page < meta.total_pages
            else None
        )
        previous_url = None
        if meta.page > 1:
            previous_page = meta.page - 1
            if previous_page > meta.total_pages:
                previous_page = meta.total_pages
            previous_url = _page_url(request, previous_page, meta.page_size)

    return {
        'count': meta.count,
        'page': meta.page,
        'page_size': meta.page_size,
        'total_pages': meta.total_pages,
        'next': next_url,
        'previous': previous_url,
        'results': results,
    }


def ensure_deterministic_ordering(queryset, ordering_fields):
    """Return ``queryset`` ordered by ``ordering_fields`` plus ``pk``.

    The primary ordering fields are preserved; ``pk`` is the deterministic
    tie-breaker so page boundaries are stable even when two rows share the
    same value (e.g. ``created_at``).
    """
    fields = list(ordering_fields)
    if 'pk' not in fields:
        fields.append('pk')
    return queryset.order_by(*fields)
