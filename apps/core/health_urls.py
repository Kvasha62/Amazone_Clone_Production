"""Public API v1 database health check (F-9)."""

from django.conf import settings
from django.db import Error, connection
from django.urls import path
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler as drf_exception_handler


class HealthSuccessSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=('ok',))
    version = serializers.CharField()
    database = serializers.ChoiceField(choices=('ok',))


class HealthDegradedSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=('degraded',))
    version = serializers.CharField()
    database = serializers.ChoiceField(choices=('error',))


class HealthCheckView(APIView):
    """Check only database connectivity; never mask non-database failures."""

    permission_classes = (AllowAny,)
    # Preserve the operational endpoint's independence from JWT and throttling.
    authentication_classes = ()
    throttle_classes = ()

    def get_exception_handler(self):
        # Health is outside the common API error envelope. DRF's default
        # handler leaves unexpected exceptions unhandled, as the Django view did.
        return drf_exception_handler

    @extend_schema(
        summary='Database health',
        description=(
            'Public, read-only database connection check. No authentication, '
            'request body or parameters. Version comes from '
            'SPECTACULAR_SETTINGS["VERSION"], shared with OpenAPI info.version. '
            'Only django.db.Error produces 503; unexpected errors propagate. '
            'The success/degraded payloads are excluded from the common API '
            'error envelope.'
        ),
        request=None,
        parameters=[],
        auth=[],
        responses={
            200: OpenApiResponse(
                response=HealthSuccessSerializer,
                description='Database connection is available.',
            ),
            503: OpenApiResponse(
                response=HealthDegradedSerializer,
                description='Database connection failed with django.db.Error.',
            ),
        },
    )
    def get(self, request):
        db_ok = True
        try:
            connection.ensure_connection()
        except Error:
            # Only database errors become degraded, never programming or
            # configuration errors (the existing F-17 exception boundary).
            db_ok = False

        return Response(
            {
                'status': 'ok' if db_ok else 'degraded',
                'version': settings.SPECTACULAR_SETTINGS['VERSION'],
                'database': 'ok' if db_ok else 'error',
            },
            status=status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


urlpatterns = [
    path('', HealthCheckView.as_view(), name='health'),
]
