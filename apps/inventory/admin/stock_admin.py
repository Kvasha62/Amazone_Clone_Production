# ────────────────────────────────────────────────────────────────────────
# apps/inventory/admin/stock_admin.py — Django Admin для склада.
#
# PROD-004 (F-04) — Admin/domain boundary:
#   Stock.quantity / reserved_quantity — бизнес-счётчики склада. Их
#   единственный владелец — InventoryService (restock / adjust_stock /
#   reserve_stock / release_stock / commit_stock): каждый путь держит
#   select_for_update, проверяет CheckConstraint-инварианты и пишет
#   аудит-строку StockMovement. Stock.variant — идентичность строки:
#   перенос остатка на другой SKU меняет складское состояние того же
#   класса, что и правка quantity (без StockMovement и без блокировки).
#   Поэтому все три поля в Admin read-only, а строки Stock создаёт
#   InventoryService.get_or_create_stock().
#   Остаётся редактируемым low_stock_threshold — операционная настройка
#   уведомлений, не бизнес-состояние.
#
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/
# ────────────────────────────────────────────────────────────────────────

from django.contrib import admin
from django.core.exceptions import PermissionDenied

from apps.core.admin_guards import ProtectedFieldsAdminMixin
from apps.inventory.models import Stock, StockMovement

# PROD-004 (F-04): бизнес-поля склада, закрытые для записи через Admin.
STOCK_ADMIN_PROTECTED_FIELDS = ('variant', 'quantity', 'reserved_quantity')


class StockMovementInline(admin.TabularInline):
    """Inline для движений внутри Stock.

    PROD-004: StockMovement — аудит складских движений, строки создаёт
    только InventoryService. Добавление «своих» движений через Admin
    исказило бы аудит, поэтому inline полностью read-only.
    """
    model = StockMovement
    extra = 0
    readonly_fields = (
        'kind', 'delta', 'quantity_before', 'quantity_after',
        'order', 'performed_by', 'note', 'created_at',
    )
    can_delete = False
    max_num = 0
    fields = (
        'kind', 'delta', 'quantity_before', 'quantity_after',
        'note', 'created_at',
    )

    def has_add_permission(self, request, obj=None):
        """Аудит-строки создаёт InventoryService, а не Admin."""
        return False


@admin.register(Stock)
class StockAdmin(ProtectedFieldsAdminMixin, admin.ModelAdmin):
    """Admin для Stock — остатки на складе.

    PROD-004 (F-04): количество и резерв — read-only (бизнес-счётчики),
    строки создаются InventoryService.get_or_create_stock(). Операционная
    настройка low_stock_threshold остаётся редактируемой.
    """

    # ── PROD-004 (F-04): контракт protected-field guard'а ──
    protected_fields = STOCK_ADMIN_PROTECTED_FIELDS
    authoritative_path = (
        'InventoryService.restock() / adjust_stock() / reserve_stock() '
        '/ release_stock() / commit_stock()'
    )

    list_display = (
        'variant_sku', 'quantity', 'reserved_quantity',
        'available_display', 'is_low_stock', 'updated_at',
    )
    list_filter = ('quantity',)
    search_fields = ('variant__sku', 'variant__product__name')
    readonly_fields = (
        'variant', 'quantity', 'reserved_quantity', 'created_at', 'updated_at',
    )
    ordering = ('-updated_at',)
    list_per_page = 50
    inlines = (StockMovementInline,)

    def has_add_permission(self, request):
        """Строки Stock создаёт InventoryService.get_or_create_stock().

        Создание строки через Admin невозможно и по protected-field
        правилу (variant/quantity/reserved_quantity обязаны равняться
        дефолтам модели), поэтому кнопка «Добавить» не предлагается.
        """
        return False

    # ── PROD-032 / F-25: удаление Stock через Admin запрещено ────────
    # StockMovement.stock использует on_delete=CASCADE, поэтому удаление
    # Stock уничтожило бы append-only аудит-историю StockMovement.
    # Блокируем все административные пути удаления: одиночное, массовое
    # (bulk action) и любой вызов delete_model / delete_queryset.

    def has_delete_permission(self, request, obj=None):
        return False

    def delete_model(self, request, obj):
        raise PermissionDenied(
            'Удаление Stock через Admin запрещено (PROD-032 / F-25): '
            'это уничтожило бы аудит-историю StockMovement.'
        )

    def delete_queryset(self, request, queryset):
        raise PermissionDenied(
            'Массовое удаление Stock через Admin запрещено '
            '(PROD-032 / F-25): это уничтожило бы аудит-историю StockMovement.'
        )

    @admin.display(description='SKU', ordering='variant__sku')
    def variant_sku(self, obj):
        return getattr(obj.variant, 'sku', '—')

    @admin.display(description='Доступно')
    def available_display(self, obj):
        return obj.available_quantity


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    """Admin для StockMovement — аудит всех движений."""

    list_display = (
        'stock_variant', 'kind', 'delta',
        'quantity_before', 'quantity_after',
        'order_number', 'created_at',
    )
    list_filter = ('kind',)
    search_fields = ('stock__variant__sku', 'note')
    readonly_fields = (
        'stock', 'kind', 'delta', 'quantity_before', 'quantity_after',
        'order', 'performed_by', 'note', 'created_at',
    )
    ordering = ('-created_at',)
    list_per_page = 50

    # ── PROD-032 / F-25: удаление StockMovement через Admin запрещено ──
    # StockMovement — append-only аудиторский журнал складских движений.
    # Удаление записи нарушило бы целостность аудита и идемпотентность
    # парных движений (RESERVE/RELEASE, RESERVE/OUT).
    # Блокируем все административные пути удаления: одиночное, массовое
    # (bulk action) и любой вызов delete_model / delete_queryset.

    def has_delete_permission(self, request, obj=None):
        return False

    def delete_model(self, request, obj):
        raise PermissionDenied(
            'Удаление StockMovement через Admin запрещено (PROD-032 / F-25): '
            'StockMovement — append-only аудиторский журнал.'
        )

    def delete_queryset(self, request, queryset):
        raise PermissionDenied(
            'Массовое удаление StockMovement через Admin запрещено '
            '(PROD-032 / F-25): StockMovement — append-only аудиторский журнал.'
        )

    @admin.display(description='Вариант')
    def stock_variant(self, obj):
        return str(obj.stock)

    @admin.display(description='Заказ')
    def order_number(self, obj):
        return getattr(obj.order, 'order_number', '—')
