# ────────────────────────────────────────────────────────────────────────
# apps/payments/admin/payment_admin.py — админка для платежей.
#
# ДВА ЭКРАНА:
#   1. PaymentAdmin — список платежей с inline-событиями
#   2. PaymentEventInline — события внутри платежа (TabularInline)
#
# НАСТРОЙКИ:
#   • list_display — основные колонки для быстрого обзора
#   • list_filter — фильтрация по статусу, методу, провайдеру
#   • search_fields — поиск по номеру, external_id
#   • readonly_fields — большинство полей неизменяемы (финансовые данные!)
#   • actions — отмена и возврат из админки
#
# 📖 https://docs.djangoproject.com/en/stable/ref/contrib/admin/
# ────────────────────────────────────────────────────────────────────────

from django.contrib import admin

from apps.payments.models import Payment, PaymentEvent


# ==============================================================
# INLINE: События платежа (TabularInline)
# ==============================================================
class PaymentEventInline(admin.TabularInline):
    """
    События платежа — отображаются внутри PaymentAdmin.

    TabularInline — компактная таблица (в отличие от StackedInline).
    Events — read-only: аудит-лог нельзя редактировать.
    """
    model = PaymentEvent
    extra = 0  # Не показываем пустые строки для добавления
    max_num = 0  # Запрещаем добавление через админку
    show_change_link = True

    # Только читаемые поля — аудит-лог неизменяем.
    fields = (
        'event_type', 'old_status', 'new_status',
        'performed_by', 'note', 'created_at',
    )
    readonly_fields = fields

    # Сортировка по времени создания (хронологический порядок).
    ordering = ('created_at',)


# ==============================================================
# PAYMENT ADMIN
# ==============================================================
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """
    Админка для платежей.

    ОСНОВНЫЕ КОЛОНКИ:
      • order_number — PAY-000001
      • order — ссылка на заказ
      • status — текущий статус
      • amount — сумма
      • method — метод оплаты
      • paid_at — когда оплачено

    READONLY:
      Финансовые данные (сумма, статус) — неизменяемы через админку.
      Изменение статуса — через сервис (PaymentService).
    """

    list_display = (
        'order_number',
        'order',
        'user',
        'status',
        'amount',
        'refund_amount',
        'refund_required_amount',
        'method',
        'provider',
        'paid_at',
        'created_at',
    )

    list_filter = (
        'status',
        'method',
        'provider',
    )

    search_fields = (
        'order_number',
        'external_id',
        'order__order_number',
        'user__email',
    )

    readonly_fields = (
        'order_number',
        'order',
        'user',
        'status',
        'amount',
        'refund_amount',
        'refund_required_amount',
        'method',
        'provider',
        'external_id',
        'paid_at',
        'cancelled_at',
        'refunded_at',
        'metadata',
        'created_at',
        'updated_at',
    )

    fields = (
        'order_number',
        'order',
        'user',
        'status',
        'amount',
        'refund_amount',
        'refund_required_amount',
        'method',
        'provider',
        'external_id',
        'note',
        'refund_reason',
        'paid_at',
        'cancelled_at',
        'refunded_at',
        'metadata',
        'created_at',
        'updated_at',
    )

    inlines = [PaymentEventInline]

    # Дата-навигация по created_at
    date_hierarchy = 'created_at'

    # Пагинация
    list_per_page = 50

    ordering = ('-created_at',)


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    """
    Отдельный экран для событий платежей (для аналитиков).
    """
    list_display = (
        'id',
        'payment',
        'event_type',
        'old_status',
        'new_status',
        'performed_by',
        'created_at',
    )

    list_filter = ('event_type',)

    search_fields = (
        'payment__order_number',
        'external_event_id',
    )

    readonly_fields = (
        'payment',
        'event_type',
        'old_status',
        'new_status',
        'payload',
        'external_event_id',
        'performed_by',
        'note',
        'created_at',
    )

    date_hierarchy = 'created_at'
    list_per_page = 100
    ordering = ('-created_at',)
