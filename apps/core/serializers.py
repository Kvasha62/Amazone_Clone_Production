"""Shared API v1 serializers.

The paginated response serializer describes the API-05 canonical collection
envelope. It is intentionally generic over item payloads so it can be reused
by every paginated ``/api/v1/`` endpoint; each endpoint still serializes its
own item objects before they are placed in ``results``.
"""

from rest_framework import serializers


class PaginationResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    next = serializers.CharField(allow_null=True, required=False)
    previous = serializers.CharField(allow_null=True, required=False)
    results = serializers.ListField(child=serializers.JSONField())
