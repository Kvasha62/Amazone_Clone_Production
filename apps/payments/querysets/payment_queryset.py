# ────────────────────────────────────────────────────────────────────────
# apps/payments/querysets/payment_queryset.py — QuerySet для платежей.
#
# Кастомные методы фильтрации и аннотации.
# Подмешиваются в PaymentManager через from_queryset().
#
# ПАТТЕРН «Custom QuerySet»:
#   Вместо написания Manager-методов для каждого фильтра,
#   пишем chainable-методы в QuerySet:
#     Payment.objects.for_order(order).succeeded()
#     Payment.objects.for_user(user).pending()
#
# 📖 https://docs.djangoproject.com/en/stable/topics/db/managers/#calling-custom-queryset-methods-from-the-manager
# ────────────────────────────────────────────────────────────────────────

from django.db import models, transaction
from django.db.models import Q, Sum

from apps.payments.constants import (
    PAYMENT_STATUS_CANCELLED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_PENDING,
    PAYMENT_STATUS_PROCESSING,
    PAYMENT_STATUS_REFUNDED,
    PAYMENT_STATUS_SUCCEEDED,
    PAYMENT_TERMINAL_STATUSES,
)


class PaymentQuerySet(models.QuerySet):
    """
    Кастомный QuerySet для Payment с chainable-методами фильтрации.
    """

    # ──────────────────────────────────────────────────────────────
    # Фильтрация по статусу
    # ──────────────────────────────────────────────────────────────

    def pending(self):
        """Платежи в статусе PENDING (ожидают оплаты)."""
        return self.filter(status=PAYMENT_STATUS_PENDING)

    def processing(self):
        """Платежи в статусе PROCESSING (обрабатываются провайдером)."""
        return self.filter(status=PAYMENT_STATUS_PROCESSING)

    def succeeded(self):
        """Успешно завершённые платежи."""
        return self.filter(status=PAYMENT_STATUS_SUCCEEDED)

    def failed(self):
        """Неудачные платежи."""
        return self.filter(status=PAYMENT_STATUS_FAILED)

    def cancelled(self):
        """Отменённые платежи."""
        return self.filter(status=PAYMENT_STATUS_CANCELLED)

    def refunded(self):
        """Платежи с возвратом."""
        return self.filter(status=PAYMENT_STATUS_REFUNDED)

    def terminal(self):
        """Платежи в терминальных статусах (не могут измениться)."""
        return self.filter(status__in=PAYMENT_TERMINAL_STATUSES)

    def active(self):
        """
        Платежи в НЕтерминальных статусах (могут ещё измениться).
        PENDING, PROCESSING, SUCCEEDED.
        """
        return self.exclude(status__in=PAYMENT_TERMINAL_STATUSES)

    # ──────────────────────────────────────────────────────────────
    # Фильтрация по связям
    # ──────────────────────────────────────────────────────────────

    def for_order(self, order):
        """Все платежи для конкретного заказа."""
        return self.filter(order=order)

    def for_user(self, user):
        """Все платежи пользователя."""
        return self.filter(user=user)

    def for_provider(self, provider: str):
        """Платежи через конкретного провайдера."""
        return self.filter(provider=provider)

    def with_external_id(self, external_id: str):
        """
        Платёж по внешнему ID (от провайдера).

        Авторитетный путь вебхук-корреляции (ADR-004): ищет ТОЛЬКО по
        external_id, без provider — непустой external_id глобально
        уникален (UniqueConstraint payment_external_id_unique, F-15),
        поэтому выборка всегда содержит не более одной строки.
        """
        return self.filter(external_id=external_id)

    # ──────────────────────────────────────────────────────────────
    # Оптимизация запросов
    # ──────────────────────────────────────────────────────────────

    def with_order(self):
        """Подтягивает заказ через JOIN (select_related)."""
        return self.select_related('order')

    def with_user(self):
        """Подтягивает пользователя через JOIN (select_related)."""
        return self.select_related('user')

    def with_events(self):
        """Подтягивает события платежа (prefetch_related)."""
        return self.prefetch_related('events')

    # ──────────────────────────────────────────────────────────────
    # Агрегация
    # ──────────────────────────────────────────────────────────────

    def total_paid(self) -> models.QuerySet:
        """
        Аннотирует total_paid — сумму всех успешных платежей.
        Полезно для аналитики: «сколько денег получил от пользователя».
        """
        return self.filter(
            status=PAYMENT_STATUS_SUCCEEDED,
        ).aggregate(
            total=Sum('amount'),
        )['total'] or 0
