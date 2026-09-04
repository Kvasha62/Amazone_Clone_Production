import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import (
    build_paginated_response_data,
    paginate_queryset,
    pagination_parameters,
)
from apps.core.serializers import PaginationResponseSerializer
from apps.notifications.serializers import NotificationSerializer
from apps.notifications.services.notification_service import NotificationService

try:
    from drf_spectacular.utils import extend_schema, extend_schema_view
except ImportError:
    def extend_schema(**kwargs):
        def decorator(func): return func
        return decorator
    def extend_schema_view(**kwargs):
        def decorator(cls): return cls
        return decorator

logger = logging.getLogger(__name__)


@extend_schema_view(
    get=extend_schema(
        summary='Все уведомления пользователя',
        parameters=pagination_parameters(),
        responses={200: PaginationResponseSerializer},
    ),
)
class NotificationListView(APIView):
    """GET /api/v1/notifications/"""
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        notifications = NotificationService.get_all(request.user)
        page_items, meta = paginate_queryset(notifications, request)
        serializer = NotificationSerializer(page_items, many=True)
        return Response(
            build_paginated_response_data(request, serializer.data, meta),
        )


@extend_schema_view(
    get=extend_schema(
        summary='Непрочитанные уведомления',
        parameters=pagination_parameters(),
        responses={200: PaginationResponseSerializer},
    ),
)
class NotificationUnreadListView(APIView):
    """GET /api/v1/notifications/unread/"""
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        notifications = NotificationService.get_unread(request.user)
        page_items, meta = paginate_queryset(notifications, request)
        serializer = NotificationSerializer(page_items, many=True)
        return Response(
            build_paginated_response_data(request, serializer.data, meta),
        )


@extend_schema_view(
    get=extend_schema(summary='Количество непрочитанных'),
)
class NotificationUnreadCountView(APIView):
    """GET /api/v1/notifications/unread-count/"""
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        count = NotificationService.get_unread_count(request.user)
        return Response({'unread_count': count})


@extend_schema_view(
    post=extend_schema(summary='Отметить прочитанным'),
)
class NotificationMarkReadView(APIView):
    """POST /api/v1/notifications/{id}/read/"""
    permission_classes = (IsAuthenticated,)

    def post(self, request, pk):
        notif = NotificationService.mark_read(pk, request.user)
        serializer = NotificationSerializer(notif)
        return Response(serializer.data)


@extend_schema_view(
    post=extend_schema(summary='Отметить все прочитанными'),
)
class NotificationMarkAllReadView(APIView):
    """POST /api/v1/notifications/read-all/"""
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        count = NotificationService.mark_all_read(request.user)
        return Response({'marked': count})
