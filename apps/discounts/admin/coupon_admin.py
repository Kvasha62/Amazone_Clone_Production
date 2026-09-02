# ────────────────────────────────────────────────────────────────────────
# apps/discounts/admin/coupon_admin.py — Django Admin для акций и купонов.
#
# PROD-004 (F-07) — Admin/domain boundary:
#   Coupon.times_used — денормализованный счётчик использований. Его
#   двигает только discounts-логика: DiscountService.register_usage()
#   (атомарный UPDATE ... WHERE times_used < max_total_uses) и
#   DiscountService.release_usage() (декремент вместе с удалением
#   CouponUsage). Прямая правка счётчика через Admin ломает связку
#   «счётчик ↔ строки CouponUsage» и лимит max_total_uses.
#   Конфигурация купона (код, тип/значение скидки, лимиты, период,
#   is_active) остаётся редактируемой — это административные данные.
#
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/
# ────────────────────────────────────────────────────────────────────────

from django.contrib import admin

from apps.core.admin_guards import ProtectedFieldsAdminMixin
from apps.discounts.models import Campaign, Coupon

# PROD-004 (F-07): бизнес-счётчик купона, закрытый для записи через Admin.
COUPON_ADMIN_PROTECTED_FIELDS = ('times_used',)


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'is_active', 'started_at', 'ended_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    ordering = ('-created_at',)


@admin.register(Coupon)
class CouponAdmin(ProtectedFieldsAdminMixin, admin.ModelAdmin):
    """Admin для купонов.

    PROD-004 (F-07): ``times_used`` — read-only (учёт использований
    ведёт DiscountService.register_usage() / release_usage()).
    Конфигурация купона остаётся редактируемой.
    """

    # ── PROD-004 (F-07): контракт protected-field guard'а ──
    protected_fields = COUPON_ADMIN_PROTECTED_FIELDS
    authoritative_path = (
        'DiscountService.register_usage() / release_usage()'
    )

    list_display = (
        'code', 'discount_type', 'discount_value',
        'is_active', 'times_used', 'max_total_uses',
        'started_at', 'ended_at',
    )
    list_filter = ('discount_type', 'is_active')
    search_fields = ('code', 'description')
    raw_id_fields = ('campaign',)
    list_per_page = 50
    ordering = ('-created_at',)
    readonly_fields = (
        # PROD-004 (F-07): счётчик использований — только чтение.
        'times_used',
    )
