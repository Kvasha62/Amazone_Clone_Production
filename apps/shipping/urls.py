# ────────────────────────────────────────────────────────────────────────
# apps/shipping/urls.py — URL-маршруты для API доставки.
#
# ПОДКЛЮЧЕНИЕ В config/urls.py:
#   path('api/v1/shipping/', include('apps.shipping.urls'))
#
# ПОЛНЫЙ СПИСОК ЭНДПОИНТОВ:
#   GET    /api/v1/shipping/methods/                     — способы доставки
#   POST   /api/v1/shipping/calculate/                   — расчёт стоимости
#   GET    /api/v1/shipping/shipments/                   — список отправлений
#   POST   /api/v1/shipping/shipments/                   — создать (staff)
#   GET    /api/v1/shipping/shipments/{shipment}/            — детали
#   PATCH  /api/v1/shipping/shipments/{shipment}/status/      — статус (staff)
#   POST   /api/v1/shipping/shipments/{shipment}/tracking/    — трек-номер (staff)
#
# ИДЕНТИФИКАТОР ОТПРАВЛЕНИЯ (F-8, issue #73):
#   {shipment} — публичный internal_tracking (SHP-00000001).
#   Числовой PK всё ещё принимается тем же маршрутом ради обратной
#   совместимости, но считается устаревшим.
#   GET    /api/v1/shipping/track/{tracking}/            — отслеживание (public)
#
# 📖 https://docs.djangoproject.com/en/stable/topics/http/urls/
# ────────────────────────────────────────────────────────────────────────

from django.urls import path

from apps.shipping.api_views import (
    ShipmentCreateView,
    ShipmentDetailView,
    ShipmentListView,
    ShipmentStatusView,
    ShipmentTrackingByCodeView,
    ShipmentTrackingView,
    ShippingCostView,
    ShippingMethodListView,
)

app_name = 'shipping'

urlpatterns = [
    # ── Способы доставки ──
    path('methods/', ShippingMethodListView.as_view(), name='method-list'),
    path('calculate/', ShippingCostView.as_view(), name='calculate'),

    # ── Отправления ──
    path('shipments/', ShipmentListView.as_view(), name='shipment-list'),
    path('shipments/create/', ShipmentCreateView.as_view(), name='shipment-create'),
    # <str:shipment> принимает публичный SHP-номер и (deprecated) числовой PK.
    path(
        'shipments/<str:shipment>/',
        ShipmentDetailView.as_view(),
        name='shipment-detail',
    ),
    path(
        'shipments/<str:shipment>/status/',
        ShipmentStatusView.as_view(),
        name='shipment-status',
    ),
    path(
        'shipments/<str:shipment>/tracking/',
        ShipmentTrackingView.as_view(),
        name='shipment-tracking',
    ),

    # ── Публичное отслеживание ──
    path(
        'track/<str:tracking>/',
        ShipmentTrackingByCodeView.as_view(),
        name='track-by-code',
    ),
]
