# ────────────────────────────────────────────────────────────────────────
# apps/orders/admin/order_admin.py — конфигурация Django Admin для заказов.
#
# НАЗНАЧЕНИЕ:
#   • OrderAdmin — управление заказами (список, фильтры, действия)
#   • OrderItemInline — редактирование позиций внутри заказа (inline)
#
# ФУНКЦИИ ADMIN:
#   • Просмотр списка заказов с фильтрами по статусу/дате
#   • Просмотр деталей заказа с позициями
#   • Массовые действия: подтвердить, отменить
#   • Поиск по номеру заказа и email пользователя
#
# PROD-004 (F-03) — Admin/domain boundary:
#   Order.status — бизнес-состояние (FSM). Единственный авторитетный путь
#   изменения — OrderService.transition_status() / confirm() / cancel(),
#   которые держат select_for_update, валидируют ORDER_STATUS_TRANSITIONS
#   и двигают склад/платежи (_handle_inventory_transition).
#   Поэтому status в Admin — read-only: второй путь записи обошёл бы
#   FSM, блокировки и инвентарную координацию. Существующие массовые
#   действия (confirm_selected / cancel_selected) сохранены — они
#   вызывают ровно этот сервисный путь, а не пишут поле напрямую.
#
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#inlinemodeladmin-objects
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • Заказы не отображаются в Django Admin
#   • Управление заказами только через API
# ────────────────────────────────────────────────────────────────────────

from django.contrib import admin

from apps.core.admin_guards import ProtectedFieldsAdminMixin
from apps.orders.models import Order, OrderItem
from apps.orders.models.order import OrderStatus

# PROD-004 (F-03): бизнес-поля заказа, закрытые для записи через Admin.
ORDER_ADMIN_PROTECTED_FIELDS = ('status',)


# ==============================================================
# INLINE — позиции заказа внутри заказа
# ==============================================================
# Inline — позволяет редактировать связанные модели на одной странице.
# OrderItemInline показывает позиции заказа прямо в форме Order.
#
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#inlinemodeladmin-objects

class OrderItemInline(admin.TabularInline):
    """
    Табличное отображение позиций заказа внутри формы Order.

    TabularInline — компактное отображение (таблица).
    Альтернатива: StackedInline — каждая позиция как «карточка».
    Для OrderItem (6-7 полей) табличный формат удобнее.

    readonly_fields — поля только для чтения (нельзя редактировать).
    Причина: позиции заказа — immutable (snapshot на момент оформления).
    Нельзя менять цену/количество после оформления!
    """
    model = OrderItem
    extra = 0  # Не показывать пустые строки для новых позиций
    # Все поля только для чтения — заказ immutable
    readonly_fields = (
        'variant',
        'product_name',
        'sku',
        'unit_price',
        'quantity',
        'total_price_display',
        'created_at',
    )
    # Поля для отображения в таблице
    fields = (
        'product_name',
        'sku',
        'unit_price',
        'quantity',
        'total_price_display',
    )
    # Запретить добавление/удаление позиций через admin
    can_delete = False
    # Максимальное количество форм = количеству существующих позиций
    max_num = 0

    # Вычисляемое поле для total_price
    @admin.display(description='Сумма')
    def total_price_display(self, obj):
        """Показывает total_price (unit_price × quantity)."""
        return obj.total_price


# ==============================================================
# ADMIN — управление заказами
# ==============================================================

@admin.register(Order)
class OrderAdmin(ProtectedFieldsAdminMixin, admin.ModelAdmin):
    """
    Конфигурация Django Admin для модели Order.

    СПИСОК ЗАКАЗОВ (list_display):
      • order_number — номер заказа (ORD-000001)
      • user — пользователь (email)
      • status — статус с цветовой индикацией
      • total — итоговая сумма
      • created_at — дата создания

    ФИЛЬТРЫ (list_filter):
      • status — по статусу (pending, confirmed, ...)
      • created_at — по дате (today, last 7 days, ...)

    ПОИСК (search_fields):
      • order_number — по номеру заказа
      • user__email — по email пользователя

    ДЕЙСТВИЯ (actions):
      • confirm_selected — подтвердить выбранные
      • cancel_selected — отменить выбранные

    PROD-004 (F-03) — СТАТУС:
      • status — read-only (бизнес-состояние, FSM)
      • переходы — только OrderService (действия выше + API)

    📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/#modeladmin-objects
    """

    # ── PROD-004 (F-03): контракт protected-field guard'а ──
    protected_fields = ORDER_ADMIN_PROTECTED_FIELDS
    authoritative_path = (
        'OrderService.confirm() / cancel() / transition_status()'
    )

    # ── Отображение в списке ──
    list_display = (
        'order_number',
        'user_email',
        'status',
        'total',
        'items_count',
        'created_at',
    )

    # ── Фильтры в боковой панели ──
    list_filter = (
        'status',
        'created_at',
    )

    # ── Поиск ──
    search_fields = (
        'order_number',
        'user__email',
        'recipient_name',
    )

    # ── Поля только для чтения (immutable order) ──
    readonly_fields = (
        # PROD-004 (F-03): статус — бизнес-состояние. Меняется только
        # через OrderService (см. действия ниже); в Admin — только чтение
        # и фильтрация (list_filter = ('status', ...)).
        'status',
        'order_number',
        'user',
        'cart',
        'subtotal',
        'delivery_cost',
        'discount',
        'total',
        'recipient_name',
        'country',
        'region',
        'city',
        'street',
        'postal_code',
        'full_address_display',
        'created_at',
        'updated_at',
        'cancelled_at',
        'confirmed_at',
        'delivered_at',
    )

    # ── Inline-позиции ──
    inlines = (OrderItemInline,)

    # ── Сортировка по умолчанию ──
    ordering = ('-created_at',)

    # ── Количество элементов на страницу ──
    list_per_page = 50

    # ── Аннотация items_count ──
    # Переопределяем queryset для добавления аннотации
    def get_queryset(self, request):
        from django.db.models import Count
        qs = super().get_queryset(request)
        return qs.annotate(_items_count=Count('items'))

    # ── Кастомные колонки ──
    @admin.display(description='Email', ordering='user__email')
    def user_email(self, obj):
        """Email пользователя (из FK)."""
        return getattr(obj.user, 'email', '—')

    @admin.display(description='Кол-во позиций', ordering='_items_count')
    def items_count(self, obj):
        """Количество позиций в заказе (из annotation)."""
        return getattr(obj, '_items_count', 0)

    @admin.display(description='Полный адрес')
    def full_address_display(self, obj):
        """Полный адрес в одну строку."""
        return obj.full_address

    # ── Массовые действия ──
    @admin.action(description='Подтвердить выбранные заказы')
    def confirm_selected(self, request, queryset):
        """
        Массовое подтверждение заказов.
        Переводит из PENDING → CONFIRMED.
        """
        from apps.orders.services.order_service import OrderService
        confirmed = 0
        for order in queryset.filter(status=OrderStatus.PENDING):
            try:
                OrderService.confirm(order, user=request.user)
                confirmed += 1
            except Exception as e:
                self.message_user(
                    request,
                    f'Ошибка для {order.order_number}: {e}',
                    level='ERROR',
                )
        self.message_user(
            request,
            f'Подтверждено {confirmed} заказов.',
        )

    @admin.action(description='Отменить выбранные заказы')
    def cancel_selected(self, request, queryset):
        """
        Массовая отмена заказов.

        PROD-004: это единственная Admin-операция, меняющая статус, — и она
        идёт через OrderService.cancel() (освобождение купона, release
        склада, возврат платежа), а не через запись поля.

        ⚠️ Багфикс PROD-004: раньше сюда передавалось
        reason='cancelled_by_admin', которого НЕТ в CANCELLATION_REASONS.
        OrderService.cancel() валидирует причину и бросал ValidationError,
        то есть действие молча отменяло 0 заказов (ошибка тонула в
        except Exception → message_user). Теперь используется валидная
        доменная причина 'other'; кто отменил — видно из user и из
        сообщения действия.
        """
        from apps.orders.services.order_service import OrderService
        cancelled = 0
        for order in queryset.exclude(
            status__in=[OrderStatus.DELIVERED, OrderStatus.CANCELLED],
        ):
            try:
                OrderService.cancel(
                    order,
                    reason='other',
                    user=request.user,
                )
                cancelled += 1
            except Exception as e:
                self.message_user(
                    request,
                    f'Ошибка для {order.order_number}: {e}',
                    level='ERROR',
                )
        self.message_user(
            request,
            f'Отменено {cancelled} заказов.',
        )

    actions = ('confirm_selected', 'cancel_selected')
