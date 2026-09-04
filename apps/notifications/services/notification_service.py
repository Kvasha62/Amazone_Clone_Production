# ────────────────────────────────────────────────────────────────────────
# apps/notifications/services/notification_service.py
#
# ОПЕРАЦИИ:
#   create()         — создать уведомление
#   mark_read()      — отметить прочитанным
#   mark_all_read()  — отметить все прочитанными
#   get_unread()     — получить непрочитанные
#   get_all()        — получить все для пользователя
#   send()           — «отправить» уведомление (stub)
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.notifications.constants import (
    CHANNEL_IN_APP,
    ORDER_STATUS_NOTIFICATION_TYPES,
    STATUS_PENDING,
    STATUS_READ,
    STATUS_SENT,
)
from apps.notifications.models import Notification

logger = logging.getLogger(__name__)


class NotificationService:
    """Бизнес-логика уведомлений."""

    @staticmethod
    def create(
        user,
        *,
        notification_type: str,
        title: str,
        body: str = '',
        channel: str = CHANNEL_IN_APP,
        related_object_type: str = '',
        related_object_id: int | None = None,
        action_url: str = '',
        send_immediately: bool = True,
    ) -> Notification:
        """
        Создаёт и (опционально) отправляет уведомление.

        ARGS:
            user: получатель
            notification_type: тип (order_created, payment_success, ...)
            title: заголовок
            body: текст
            channel: канал (in_app, email, push)
            related_object_type: тип связанного объекта (order, shipment)
            related_object_id: ID связанного объекта
            action_url: URL для перехода
            send_immediately: отправить сразу (stub)

        RETURNS:
            Notification
        """
        notif = Notification.objects.create(
            user=user,
            notification_type=notification_type,
            channel=channel,
            title=title,
            body=body,
            status=STATUS_PENDING,
            related_object_type=related_object_type,
            related_object_id=related_object_id,
            action_url=action_url,
        )

        if send_immediately:
            NotificationService._send(notif)

        logger.info(
            'notification_created',
            extra={
                'notif_id': notif.pk,
                'user_id': user.pk,
                'type': notification_type,
                'channel': channel,
            },
        )

        return notif

    @staticmethod
    def _send(notif: Notification) -> None:
        """
        «Отправляет» уведомление (stub).

        В production здесь:
          • channel=email → send_mail() / celery task
          • channel=push → Firebase Cloud Messaging
          • channel=in_app → уже сохранено в БД
        """
        notif.status = STATUS_SENT
        notif.sent_at = timezone.now()
        notif.save(update_fields=['status', 'sent_at', 'updated_at'])

    @staticmethod
    @transaction.atomic
    def mark_read(notification_id: int, user) -> Notification:
        """
        Отмечает уведомление как прочитанное.

        RAISES:
            NotFound если не найдено / чужое
        """
        from rest_framework.exceptions import NotFound

        try:
            notif = Notification.objects.select_for_update().get(
                pk=notification_id,
                user=user,
            )
        except Notification.DoesNotExist:
            raise NotFound('Уведомление не найдено.')

        if notif.read_at is None:
            notif.read_at = timezone.now()
            notif.status = STATUS_READ
            notif.save(update_fields=['read_at', 'status', 'updated_at'])

        return notif

    @staticmethod
    @transaction.atomic
    def mark_all_read(user) -> int:
        """
        Отмечает все непрочитанные уведомления пользователя.

        RETURNS:
            Количество отмеченных
        """
        now = timezone.now()
        count = Notification.objects.filter(
            user=user,
            read_at__isnull=True,
        ).update(
            read_at=now,
            status=STATUS_READ,
        )

        logger.info(
            'notifications_marked_read',
            extra={'user_id': user.pk, 'count': count},
        )

        return count

    @staticmethod
    def get_unread(user):
        """Возвращает QuerySet непрочитанных уведомлений."""
        return (
            Notification.objects
            .for_user(user)
            .unread()
            .select_related('user')
            .order_by('-created_at', 'pk')
        )

    @staticmethod
    def get_all(user):
        """Возвращает QuerySet всех уведомлений (paginated by the API layer)."""
        return (
            Notification.objects
            .for_user(user)
            .select_related('user')
            .order_by('-created_at', 'pk')
        )

    @staticmethod
    def get_unread_count(user) -> int:
        """Количество непрочитанных уведомлений."""
        return Notification.objects.for_user(user).unread().count()

    # ==============================================================
    # Удобные методы для конкретных событий
    # ==============================================================

    @staticmethod
    def notify_order_created(order) -> Notification:
        return NotificationService.create(
            user=order.user,
            notification_type='order_created',
            title=f'Заказ {order.order_number} создан',
            body=f'Ваш заказ {order.order_number} успешно оформлен.',
            related_object_type='order',
            related_object_id=order.pk,
        )

    @staticmethod
    def notify_order_status_changed(order, new_status: str) -> Notification | None:
        """Уведомление о смене статуса заказа.

        Тип уведомления берётся из ORDER_STATUS_NOTIFICATION_TYPES, а не
        конструируется как f'order_{new_status}': FSM заказа содержит
        состояния без уведомительного контракта («processing»), и создание
        для них строки с несуществующим типом нарушало бы choices модели.

        RETURNS:
            Notification — для статусов с уведомительным контрактом;
            None — для статусов без него (уведомление не создаётся).
        """
        notification_type = ORDER_STATUS_NOTIFICATION_TYPES.get(new_status)
        if notification_type is None:
            logger.debug(
                'notification_skipped_no_type',
                extra={'order_id': order.pk, 'order_status': new_status},
            )
            return None

        status_labels = {
            'confirmed': 'подтверждён',
            'shipped': 'отправлен',
            'delivered': 'доставлен',
            'cancelled': 'отменён',
            'processing': 'в обработке',
        }
        label = status_labels.get(new_status, new_status)
        return NotificationService.create(
            user=order.user,
            notification_type=notification_type,
            title=f'Заказ {order.order_number} {label}',
            body=f'Статус заказа изменён: {label}.',
            related_object_type='order',
            related_object_id=order.pk,
        )

    @staticmethod
    def notify_payment_success(order, payment) -> Notification:
        return NotificationService.create(
            user=order.user,
            notification_type='payment_success',
            title='Оплата прошла успешно',
            body=f'Оплата заказа {order.order_number} подтверждена.',
            related_object_type='order',
            related_object_id=order.pk,
        )
