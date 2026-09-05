import logging

from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import (
    build_paginated_response_data,
    ensure_deterministic_ordering,
    paginate_queryset,
    pagination_parameters,
)
from apps.core.serializers import PaginationResponseSerializer

from apps.discounts.models import Coupon
from apps.core.identifiers import order_reference_filters
from apps.discounts.serializers import (
    ApplyCouponInputSerializer,
    CouponListSerializer,
    PreviewDiscountInputSerializer,
    PreviewDiscountOutputSerializer,
    RemoveCouponInputSerializer,
)
from apps.discounts.services.discount_service import DiscountService
from apps.orders.models import Order
from apps.orders.services.order_service import OrderService

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
        summary='Список купонов (staff)',
        parameters=pagination_parameters(),
        responses={200: PaginationResponseSerializer},
    ),
)
class CouponListView(APIView):
    """GET /api/v1/discounts/coupons/ — список активных купонов (staff)."""
    permission_classes = (IsAdminUser,)

    def get(self, request):
        coupons = Coupon.objects.valid_now().with_campaign()
        # API-05: deterministic ordering with a stable pk tie-breaker.
        coupons = ensure_deterministic_ordering(coupons, ['-created_at'])
        page_items, meta = paginate_queryset(coupons, request)
        serializer = CouponListSerializer(page_items, many=True)
        return Response(
            build_paginated_response_data(request, serializer.data, meta),
        )


@extend_schema_view(
    post=extend_schema(
        summary='Применить купон',
        request=ApplyCouponInputSerializer,
        responses={200: 'Discount applied'},
    ),
)
class CouponApplyView(APIView):
    """POST /api/v1/discounts/apply/"""
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        input_ser = ApplyCouponInputSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)
        data = input_ser.validated_data

        try:
            order = Order.objects.get(
                user=request.user,
                **order_reference_filters(data),
            )
        except Order.DoesNotExist:
            raise NotFound('Заказ не найден.')

        order = OrderService.apply_coupon(
            order, data['code'], user=request.user,
        )

        return Response({
            # F-8 (#73): публичный идентификатор заказа в ответе.
            'order_number': order.order_number,
            # DEPRECATED: целочисленный PK, оставлен на окно совместимости.
            'order_id': order.pk,
            'discount': str(order.discount),
            'total': str(order.total),
        })


@extend_schema_view(
    post=extend_schema(
        summary='Снять скидку',
        request=RemoveCouponInputSerializer,
        responses={200: 'Discount removed'},
    ),
)
class CouponRemoveView(APIView):
    """POST /api/v1/discounts/remove/"""
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        input_ser = RemoveCouponInputSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)
        data = input_ser.validated_data

        try:
            order = Order.objects.get(
                user=request.user,
                **order_reference_filters(data),
            )
        except Order.DoesNotExist:
            raise NotFound('Заказ не найден.')

        order = OrderService.remove_coupon(order, user=request.user)

        return Response({
            # F-8 (#73): публичный идентификатор заказа в ответе.
            'order_number': order.order_number,
            # DEPRECATED: целочисленный PK, оставлен на окно совместимости.
            'order_id': order.pk,
            'discount': str(order.discount),
            'total': str(order.total),
        })


@extend_schema_view(
    post=extend_schema(
        summary='Превью скидки',
        request=PreviewDiscountInputSerializer,
        responses={200: PreviewDiscountOutputSerializer},
    ),
)
class CouponPreviewView(APIView):
    """POST /api/v1/discounts/preview/ — превью скидки без применения."""
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        input_ser = PreviewDiscountInputSerializer(data=request.data)
        input_ser.is_valid(raise_exception=True)
        data = input_ser.validated_data

        result = DiscountService.preview_discount(
            code=data['code'],
            order_amount=data['order_amount'],
        )

        return Response(PreviewDiscountOutputSerializer(result).data)
