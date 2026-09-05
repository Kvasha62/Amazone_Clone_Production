# ────────────────────────────────────────────────────────────────────────
# apps/shipping/admin/shipping_admin.py — админка для моделей доставки.
#
# Регистрирует ShippingZone, ShippingMethod, Shipment в Django Admin.
#
# PROD-004 (F-06) — Admin/domain boundary:
#   Shipment.status — FSM-состояние отправления. Его меняет только
#   ShippingService.transition_status(): она валидирует
#   SHIPMENT_STATUS_TRANSITIONS, держит select_for_update, выставляет
#   shipped_at / delivered_at и синхронизирует статус заказа
#   (_sync_order_status → OrderService). Прямая запись статуса через
#   Admin обошла бы FSM и рассинхронизировала Order.
#   shipped_at / delivered_at защищаются вместе со статусом: их пишет
#   тот же переход, это часть того же жизненного цикла, а не настройка.
#   Трек-номер и примечания — операционные данные и остаются
#   редактируемыми (для трека есть и сервисный путь
#   ShippingService.update_tracking()).
#
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/
# ────────────────────────────────────────────────────────────────────────

from django.contrib import admin

from apps.core.admin_guards import ProtectedFieldsAdminMixin
from apps.shipping.models import Shipment, ShippingMethod, ShippingZone

# PROD-004 (F-06): бизнес-поля отправления, закрытые для записи через Admin.
SHIPMENT_ADMIN_PROTECTED_FIELDS = ('status', 'shipped_at', 'delivered_at')


@admin.register(ShippingZone)
class ShippingZoneAdmin(admin.ModelAdmin):
    """Админка для зон доставки."""

    list_display = ('id', 'name', 'zone_code', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'zone_code')
    ordering = ('name',)
    list_per_page = 50


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    """Админка для способов доставки."""

    list_display = (
        'id', 'name', 'shipping_type', 'zone',
        'base_price', 'price_per_kg',
        'free_shipping_threshold', 'is_active',
        'estimated_days_min', 'estimated_days_max',
    )
    list_filter = ('shipping_type', 'is_active', 'zone')
    search_fields = ('name',)
    raw_id_fields = ('zone',)
    list_per_page = 50
    ordering = ('sort_order', 'base_price')


@admin.register(Shipment)
class ShipmentAdmin(ProtectedFieldsAdminMixin, admin.ModelAdmin):
    """Админка для отправлений.

    PROD-004 (F-06): статус и переходные таймстампы — read-only
    (авторитетный путь — ShippingService.transition_status(), API
    PATCH /api/v1/shipping/shipments/{id}/status/). Трек-номер,
    примечания и вес остаются административными полями.
    """

    # ── PROD-004 (F-06): контракт protected-field guard'а ──
    protected_fields = SHIPMENT_ADMIN_PROTECTED_FIELDS
    authoritative_path = (
        'ShippingService.transition_status() '
        '(API PATCH /api/v1/shipping/shipments/{id}/status/)'
    )

    list_display = (
        'id', 'shipment_number', 'internal_tracking', 'tracking_number',
        'order', 'status', 'shipping_cost',
        'shipped_at', 'delivered_at',
    )
    list_filter = ('status', 'method__shipping_type')
    search_fields = (
        'shipment_number', 'internal_tracking', 'tracking_number',
        'order__order_number',
    )
    raw_id_fields = ('order', 'user', 'method')
    list_per_page = 50
    ordering = ('-created_at',)
    readonly_fields = (
        'shipment_number', '_shipment_number_seq',
        'internal_tracking', '_tracking_seq',
        # PROD-004 (F-06): жизненный цикл отправления — только чтение.
        'status', 'shipped_at', 'delivered_at',
    )
