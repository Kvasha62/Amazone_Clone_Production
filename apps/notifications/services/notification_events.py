# ────────────────────────────────────────────────────────────────────────
# apps/notifications/services/notification_events.py
#
# PROD-025 / F-18 — подключение бизнес-событий к уведомлениям.
#
# ЗАЧЕМ ЭТОТ МОДУЛЬ:
#   Уведомления (NotificationService) и Celery-задачи (tasks.py) уже
#   реализованы, но бизнес-события, которые должны их порождать, не были
#   к ним подключены. Здесь живёт ТОЧКА ПОДКЛЮЧЕНИЯ: ровно один модуль,
#   который знает, какое доменное событие какое уведомление порождает.
#
# КТО ВЫЗЫВАЕТ:
#   Только авторитетные пути, владеющие бизнес-событием:
#     • OrderService.create_from_cart()  → order_created()
#     • OrderService.transition_status() → order_status_changed()
#     • OrderService.cancel()            → order_status_changed(CANCELLED)
#     • PaymentService.confirm_payment() → payment_succeeded()
#
# СЕМАНТИКА ТРАНЗАКЦИЙ (AC-6):
#   Работа планируется через transaction.on_commit(): транзакция, которая
#   откатилась, НЕ оставляет уведомления, и Celery-задача видит только
#   зафиксированные строки (иначе воркер мог бы начать работу до COMMIT
#   и не найти заказ). Вне транзакции on_commit выполняет колбэк сразу
#   (поведение Django).
#
# СЕМАНТИКА ОШИБОК:
#   Колбэки регистрируются с robust=True: сбой доставки уведомления
#   НЕ «тонет» молча (Django пишет ERROR с traceback в логгер
#   django.db.backends.base) и при этом не превращает уже зафиксированный
#   бизнес-результат (созданный/подтверждённый заказ) в ошибку 500.
#   Повторная доставка/ручная реконсиляция — отдельная задача (N-02),
#   здесь она не вводится.
#
# ЧЕГО ЗДЕСЬ НЕТ:
#   Ни шины событий, ни outbox, ни нового «event layer». Только вызовы
#   существующих методов NotificationService и существующих Celery-задач.
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging

from django.db import transaction

from apps.notifications import tasks as notification_tasks
from apps.notifications.constants import ORDER_STATUS_NOTIFICATION_TYPES
from apps.notifications.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class NotificationEvents:
    """Подключение доменных событий к bounded context уведомлений."""

    # ==============================================================
    # ЗАКАЗЫ
    # ==============================================================

    @staticmethod
    def order_created(order) -> None:
        """Заказ создан → in-app уведомление `order_created`.

        Вызывается из OrderService.create_from_cart() — единственного
        авторитетного пути создания заказа.
        """

        def emit_order_created() -> None:
            NotificationService.notify_order_created(order)

        transaction.on_commit(emit_order_created, robust=True)

    @staticmethod
    def order_status_changed(order, new_status: str) -> None:
        """Статус заказа изменён → in-app уведомление (+ email-задача).

        Вызывается из OrderService.transition_status() и
        OrderService.cancel() — авторитетных путей смены статуса.

        Статусы без уведомительного контракта (ORDER_STATUS_NOTIFICATION_TYPES,
        например «processing») не порождают ни уведомления, ни задачи.
        """
        if new_status not in ORDER_STATUS_NOTIFICATION_TYPES:
            return

        def emit_order_status_changed() -> None:
            NotificationService.notify_order_status_changed(order, new_status)

        transaction.on_commit(emit_order_status_changed, robust=True)

        email_task = NotificationEvents._email_task_for_status(new_status)
        if email_task is None:
            return

        order_id = order.pk

        def emit_order_status_email() -> None:
            email_task.delay(order_id)

        transaction.on_commit(emit_order_status_email, robust=True)

    # ==============================================================
    # ПЛАТЕЖИ
    # ==============================================================

    @staticmethod
    def payment_succeeded(order, payment) -> None:
        """Оплата подтверждена → in-app уведомление `payment_success`.

        Вызывается из PaymentService.confirm_payment() ПОСЛЕ ветки
        идемпотентного возврата («платёж уже SUCCEEDED»): повторная
        доставка вебхука не создаёт дубль уведомления (AC-7).
        """
        payment_id = payment.pk

        def emit_payment_success() -> None:
            NotificationService.notify_payment_success(order, payment)
            logger.debug(
                'notification_payment_success_emitted',
                extra={'order_id': order.pk, 'payment_id': payment_id},
            )

        transaction.on_commit(emit_payment_success, robust=True)

    # ==============================================================
    # ВНУТРЕННЕЕ
    # ==============================================================

    @staticmethod
    def _email_task_for_status(new_status: str):
        """Существующая email-задача для статуса заказа (или None).

        Только те статусы, для которых в tasks.py уже реализована задача:
          • confirmed → send_order_confirmation()
          • shipped   → send_order_shipped()
        """
        if new_status == 'confirmed':
            return notification_tasks.send_order_confirmation
        if new_status == 'shipped':
            return notification_tasks.send_order_shipped
        return None
