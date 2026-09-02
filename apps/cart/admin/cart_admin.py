# ────────────────────────────────────────────────────────────────────────
# apps/cart/admin/cart_admin.py — Django Admin для корзин.
#
# ДВА ADMIN-КЛАССА + ОДИН INLINE:
#   CartItemInline  — позиции внутри страницы корзины (TabularInline)
#   CartAdmin       — управление корзинами (/admin/cart/cart/)
#   CartItemAdmin   — управление позициями (/admin/cart/cartitem/)
#
# PROD-004 (N-05) — Admin/domain boundary:
#   CartItem.quantity / variant — бизнес-состояние корзины. Владелец —
#   CartService: add_item() и update_item_quantity() проверяют лимит
#   позиций, активность варианта и товара и остатки на складе под
#   select_for_update; remove_item() удаляет позицию. Прямая запись
#   количества или подмена варианта через Admin обошли бы эти проверки
#   (например, quantity=999 при stock=1).
#   Поэтому оба поля read-only на ОБОИХ Admin-поверхностях: и на
#   отдельной странице CartItemAdmin, и в CartItemInline (это второй
#   путь POST-записи тех же полей). Server-side слой для inline
#   подключается в CartAdmin.save_formset().
#
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#inlinemodeladmin-objects
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   /admin/cart/ — пусто, нет возможности управлять корзинами.
# ────────────────────────────────────────────────────────────────────────

# admin — модуль Django для административного интерфейса.
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#module-django.contrib.admin
from django.contrib import admin

# Guard-контракт PROD-004 (Admin не пишет бизнес-состояние).
from apps.core.admin_guards import (
    ProtectedFieldsAdminMixin,
    ProtectedFieldsInlineMixin,
    guard_inline_formsets,
)

# Cart, CartItem — модели корзины.
from apps.cart.models import Cart, CartItem

# PROD-004 (N-05): бизнес-поля позиции корзины, закрытые для Admin-записи.
# variant — идентичность бизнес-позиции (какой SKU в корзине),
# quantity — бизнес-количество.
CART_ITEM_ADMIN_PROTECTED_FIELDS = ('variant', 'quantity')


# ────────────────────────────────────────────────────────────────────────
# CartItemInline — позиции внутри страницы корзины
# ────────────────────────────────────────────────────────────────────────

# TabularInline — компактная таблица для отображения связанных объектов.
# Показывается внутри страницы CartAdmin.
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#tabularinline
class CartItemInline(ProtectedFieldsInlineMixin, admin.TabularInline):
    """Inline-отображение позиций корзины (read-only).

    PROD-004 (N-05): quantity и variant нельзя менять и здесь — inline
    это тот же Admin, только POST на страницу Cart. Позиции создаёт,
    меняет и удаляет CartService (API корзины).
    """
    # model — связанная модель для inline.
    model = CartItem
    # extra=0 — не показывать пустые строки для добавления.
    # Позиции добавляются через API, не через admin.
    extra = 0
    # PROD-004 (N-05): контракт protected-field guard'а.
    protected_fields = CART_ITEM_ADMIN_PROTECTED_FIELDS
    authoritative_path = (
        'CartService.add_item() / update_item_quantity() / remove_item()'
    )
    # readonly_fields — бизнес-поля (PROD-004) и системные поля.
    readonly_fields = (
        'variant', 'quantity', 'created_at', 'updated_at',
    )
    # fields — отображаемые колонки в inline-таблице.
    fields = ('variant', 'quantity', 'created_at', 'updated_at')

    def has_add_permission(self, request, obj=None):
        """Позицию создаёт CartService.add_item(), а не Admin-форма."""
        return False


# ────────────────────────────────────────────────────────────────────────
# CartAdmin — управление корзинами
# ────────────────────────────────────────────────────────────────────────

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    """
    Admin для корзин. Показывает:
      - пользователя / хэш сессии
      - активность
      - количество позиций
      - даты создания/обновления
    """
    # list_display — колонки в списке корзин.
    list_display = (
        'id',                       # PK
        'user',                     # Пользователь (FK, __str__)
        'session_key_hash_short',   # Кастомная — укороченный хэш
        'is_active',                # Активна (boolean)
        'items_count',              # Кастомная — количество позиций
        'created_at',               # Дата создания
        'updated_at',               # Дата обновления
    )
    # list_filter — фильтр по активности.
    list_filter = ('is_active',)
    # search_fields — поиск по username, email, хэшу.
    search_fields = ('user__username', 'user__email', 'session_key_hash')
    # autocomplete_fields — Select2 для user (тысячи пользователей).
    autocomplete_fields = ('user',)
    # readonly_fields — session_key_hash не редактируется (хэш).
    readonly_fields = ('session_key_hash', 'created_at', 'updated_at')
    # inlines — встраиваемые позиции внутри корзины.
    inlines = (CartItemInline,)
    # actions — массовые действия.
    actions = ('deactivate_selected',)

    def get_queryset(self, request):
        """
        Оптимизация: select_related(user) + prefetch_related(items).
        Без: N+1 запросов при отображении списка корзин.
        """
        qs = super().get_queryset(request)
        # select_related('user') — INNER JOIN к auth_user.
        # prefetch_related('items') — для items_count.
        return qs.select_related('user').prefetch_related('items')

    def save_formset(self, request, form, formset, change):
        """PROD-004 (N-05): server-side guard для CartItemInline.

        ``save_formset`` вызывается из ``_changeform_view`` ДО записи
        формсета, когда все формы уже провалидированы (то есть
        ``form.instance`` содержит связанные из POST данные). Здесь
        protected-поля позиции сравниваются со строкой в БД, и при
        расхождении сохраняется PermissionDenied — inline-POST не
        остаётся «второй дверью» в бизнес-состояние корзины, даже если
        readonly_fields когда-нибудь будет отредактирован.

        📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#django.contrib.admin.ModelAdmin.save_formset
        """
        guard_inline_formsets(self, request, formset)
        super().save_formset(request, form, formset, change)

    @admin.display(description='Сессия')
    def session_key_hash_short(self, obj: Cart):
        """
        Укороченный хэш сессии (первые 12 символов).
        Полный хэш = 64 символа — слишком длинный для колонки.
        """
        if not obj.session_key_hash:
            return '—'
        return f'{obj.session_key_hash[:12]}…'

    @admin.display(description='Позиций')
    def items_count(self, obj: Cart):
        """
        Количество позиций в корзине.

        hasattr(obj, '_prefetched_objects_cache') — проверяем
        был ли prefetch_related. Если да → items.all() из кэша (0 SQL).
        Если нет → items.count() — 1 SQL (оптимальнее .all()).
        """
        return len(obj.items.all()) if hasattr(obj, '_prefetched_objects_cache') else obj.items.count()

    @admin.action(description='Деактивировать выбранные корзины')
    def deactivate_selected(self, request, queryset):
        """
        Массовая деактивация корзин.
        .update(is_active=False) — один SQL:
        UPDATE cart_cart SET is_active = False WHERE id IN (...)
        """
        updated = queryset.update(is_active=False)
        self.message_user(request, f'Деактивировано {updated} корзин.')


# ────────────────────────────────────────────────────────────────────────
# CartItemAdmin — управление позициями корзин (отдельная страница)
# ────────────────────────────────────────────────────────────────────────

@admin.register(CartItem)
class CartItemAdmin(ProtectedFieldsAdminMixin, admin.ModelAdmin):
    """
    Admin для позиций корзин. Полезно для аналитики и отладки:
    какие товары добавляют, сколько штук, когда.

    PROD-004 (N-05): quantity и variant — read-only, создание позиции
    через Admin запрещено. Авторитетный путь — CartService
    (add_item / update_item_quantity / remove_item).
    """
    # ── PROD-004 (N-05): контракт protected-field guard'а ──
    protected_fields = CART_ITEM_ADMIN_PROTECTED_FIELDS
    authoritative_path = (
        'CartService.add_item() / update_item_quantity() / remove_item()'
    )

    list_display = (
        'id',       # PK
        'cart',     # Корзина (FK)
        'variant',  # Вариант товара (FK)
        'quantity', # Количество
        'created_at', # Дата добавления
    )
    # list_filter — фильтр по активности корзины.
    list_filter = ('cart__is_active',)
    # list_select_related — JOIN к cart и variant в списке.
    list_select_related = ('cart', 'variant')
    # autocomplete_fields — Select2 для cart.
    autocomplete_fields = ('cart',)
    # search_fields — поиск по SKU варианта и username.
    search_fields = ('variant__sku', 'cart__user__username')
    # readonly_fields — бизнес-поля (PROD-004) и системные поля.
    readonly_fields = ('variant', 'quantity', 'created_at', 'updated_at')
    # raw_id_fields — текстовое поле с ID (для миллионов записей).
    # autocomplete_fields может быть медленным при огромных таблицах.
    raw_id_fields = ('cart',)

    def has_add_permission(self, request):
        """Позицию создаёт CartService.add_item().

        Позиция без variant не существует (FK NOT NULL, уникальность
        cart+variant), а variant — защищённое бизнес-поле, поэтому
        Admin-форма создания в принципе не может быть «безопасной».
        """
        return False
