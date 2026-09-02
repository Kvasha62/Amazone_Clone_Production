# ────────────────────────────────────────────────────────────────────────
# apps/pricing/admin/price_admin.py — Django Admin для цен и истории.
#
# PROD-004 (F-05) — Admin/domain boundary:
#   Цена варианта — целиком владение PricingService: set_price() держит
#   select_for_update на Product, валидирует границы, пишет PriceHistory
#   и обновляет денормализованные Product.min_price / max_price через
#   CatalogService.set_product_prices(). Запись price / sale_price (или
#   перепривязка variant / смена currency — они определяют, ЧЬЯ это цена
#   и в какой валюте) через Admin обошла бы историю, пересчёт границ и
#   блокировки. Поэтому страница Price в Admin — инспекция; авторитетный
#   путь — PricingService (API: POST /api/v1/pricing/variants/{id}/price/
#   и POST /api/v1/pricing/prices/bulk/).
#
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/
# ────────────────────────────────────────────────────────────────────────

from django.contrib import admin

from apps.core.admin_guards import ProtectedFieldsAdminMixin
from apps.pricing.models import Price, PriceHistory

# PROD-004 (F-05): бизнес-поля цены, закрытые для записи через Admin.
PRICE_ADMIN_PROTECTED_FIELDS = ('variant', 'price', 'sale_price', 'currency')


@admin.register(Price)
class PriceAdmin(ProtectedFieldsAdminMixin, admin.ModelAdmin):
    """
    Admin для актуальных цен вариантов.
    Показывает: вариант, цена, скидка, эффективная цена, % скидки.

    PROD-004 (F-05): все бизнес-поля — read-only; создание цены через
    Admin запрещено (любая цена требует price, у которого нет дефолта).
    Авторитетный путь — PricingService.set_price() / remove_price().
    """

    # ── PROD-004 (F-05): контракт protected-field guard'а ──
    protected_fields = PRICE_ADMIN_PROTECTED_FIELDS
    authoritative_path = 'PricingService.set_price() / remove_price()'

    list_display = (
        'id', 'variant', 'price', 'sale_price',
        'effective_price_display', 'discount_percent_display',
        'currency', 'updated_at',
    )
    list_filter = ('currency',)
    # Двойной select_related: variant → product — без N+1.
    list_select_related = ('variant', 'variant__product')
    search_fields = ('variant__sku', 'variant__product__name')
    readonly_fields = (
        'variant', 'price', 'sale_price', 'currency', 'created_at', 'updated_at',
    )
    # raw_id_fields — текстовое поле для variant (тысячи записей).
    raw_id_fields = ('variant',)

    def has_add_permission(self, request):
        """Цену создаёт PricingService.set_price().

        Admin-форма создания не может не содержать price (поле без
        дефолта), то есть любое добавление строки означало бы запись
        бизнес-значения в обход сервиса и в обход пересчёта границ.
        """
        return False

    @admin.display(description='Эффект. цена')
    def effective_price_display(self, obj):
        """Показывает эффективную цену (sale или base)."""
        return f'{obj.effective_price:.2f}'

    @admin.display(description='Скидка %')
    def discount_percent_display(self, obj):
        """Показывает % скидки или —."""
        pct = obj.discount_percent
        return f'{pct}%' if pct is not None else '—'


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    """
    Admin для истории изменений цен (read-only аудит).
    """
    list_display = (
        'id', 'variant', 'old_price', 'new_price',
        'old_sale_price', 'new_sale_price',
        'changed_by', 'created_at',
    )
    list_select_related = ('variant', 'changed_by')
    readonly_fields = ('created_at', 'updated_at')
    search_fields = ('variant__sku',)
    raw_id_fields = ('variant', 'changed_by')
