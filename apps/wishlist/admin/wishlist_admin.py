# ────────────────────────────────────────────────────────────────────────
# apps/wishlist/admin/wishlist_admin.py — Django Admin для избранного.
#
# PROD-023 (F-23) — Admin/domain boundary:
#   Wishlist.items_count — денормализованный счётчик позиций. Его
#   двигает только WishlistService: add_item() (F('items_count') + 1),
#   remove_item() / move_to_cart() (Greatest(F('items_count') - n, 0))
#   и clear() (items_count=0). Прямая правка счётчика через Admin
#   рассогласовывает «счётчик ↔ фактические WishlistItem».
#   Поле user остаётся редактируемым — это административная привязка
#   списка к владельцу, не business-state счётчик.
#
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/
# ────────────────────────────────────────────────────────────────────────

from django.contrib import admin

from apps.core.admin_guards import ProtectedFieldsAdminMixin
from apps.wishlist.models import Wishlist, WishlistItem

# PROD-023 (F-23): бизнес-счётчик избранного, закрытый для записи через Admin.
WISHLIST_ADMIN_PROTECTED_FIELDS = ('items_count',)


@admin.register(Wishlist)
class WishlistAdmin(ProtectedFieldsAdminMixin, admin.ModelAdmin):
    """Admin для списков желаний.

    PROD-023 (F-23): ``items_count`` — read-only (учёт позиций ведёт
    WishlistService.add_item() / remove_item() / move_to_cart() / clear()).
    Привязка ``user`` остаётся редактируемой.
    """

    # ── PROD-023 (F-23): контракт protected-field guard'а ──
    protected_fields = WISHLIST_ADMIN_PROTECTED_FIELDS
    authoritative_path = (
        'WishlistService.add_item() / remove_item() / '
        'move_to_cart() / clear()'
    )

    list_display = ('id', 'user', 'items_count', 'created_at', 'updated_at')
    raw_id_fields = ('user',)
    ordering = ('-created_at',)
    readonly_fields = (
        # PROD-023 (F-23): счётчик позиций — только чтение.
        'items_count',
    )


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'wishlist', 'variant', 'note', 'created_at')
    raw_id_fields = ('wishlist', 'variant')
    ordering = ('-created_at',)
