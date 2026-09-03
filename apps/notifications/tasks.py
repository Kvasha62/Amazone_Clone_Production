# ────────────────────────────────────────────────────────────────────────
# apps/notifications/tasks.py — Celery-задачи для уведомлений.
#
# Асинхронная отправка:
#   - Email-подтверждение заказа
#   - Email о доставке
#   - Промокод / скидка
#   - Ответ на отзыв
#   - Брошенная корзина
# ────────────────────────────────────────────────────────────────────────

import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='apps.notifications.tasks.send_email_notification')
def send_email_notification(notification_id: int):
    """
    Асинхронная отправка email-уведомления.

    Args:
        notification_id: PK объекта Notification

    Логика:
      1. Загрузить Notification из БД
      2. Проверить канал == 'email'
      3. Рендерить шаблон письма
      4. Отправить через django.core.mail
      5. Обновить status → 'sent', sent_at → now()
    """
    from apps.notifications.models import Notification
    from django.utils import timezone

    try:
        notif = Notification.objects.select_related('user').get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.warning('Notification %s не найдена', notification_id)
        return

    if notif.channel != 'email':
        logger.debug('Notification %s: канал не email, пропускаем', notification_id)
        return

    if notif.user and notif.user.email:
        # TODO: Рендеринг HTML-шаблона и реальная отправка
        # Сейчас — заглушка (console backend). Адрес получателя, тема и
        # текст письма являются PII и никогда не попадают в production log.
        logger.info(
            'notification_email_delivery_started',
            extra={'notification_id': notif.pk, 'channel': notif.channel},
        )

    notif.status = 'sent'
    notif.sent_at = timezone.now()
    notif.save(update_fields=['status', 'sent_at', 'updated_at'])


@shared_task(name='apps.notifications.tasks.send_order_confirmation')
def send_order_confirmation(order_id: int):
    """
    Отправка email-подтверждения заказа.
    Создаёт Notification + вызывает send_email_notification.
    """
    from apps.orders.models import Order
    from apps.notifications.models import Notification

    try:
        order = Order.objects.select_related('user').get(pk=order_id)
    except Order.DoesNotExist:
        logger.warning('Order %s не найден', order_id)
        return

    notif = Notification.objects.create(
        user=order.user,
        notification_type='order_confirmed',
        channel='email',
        title=f'Заказ {order.order_number} подтверждён',
        body=f'Ваш заказ {order.order_number} на сумму {order.total} ₽ подтверждён и передан в обработку.',
        status='pending',
        related_object_type='order',
        related_object_id=order.pk,
    )

    send_email_notification.delay(notif.pk)


@shared_task(name='apps.notifications.tasks.send_order_shipped')
def send_order_shipped(order_id: int):
    """Отправка email о отправке заказа."""
    from apps.orders.models import Order
    from apps.notifications.models import Notification

    try:
        order = Order.objects.select_related('user').get(pk=order_id)
    except Order.DoesNotExist:
        return

    notif = Notification.objects.create(
        user=order.user,
        notification_type='order_shipped',
        channel='email',
        title=f'Заказ {order.order_number} отправлен',
        body=f'Ваш заказ {order.order_number} передан в службу доставки.',
        status='pending',
        related_object_type='order',
        related_object_id=order.pk,
    )

    send_email_notification.delay(notif.pk)


@shared_task(name='apps.notifications.tasks.send_password_reset_email')
def send_password_reset_email(user_id: int, uid: str, token: str):
    """
    Асинхронная отправка password reset email.

    Args:
        user_id: PK пользователя
        uid: base64-кодировка PK (для ссылки)
        token: Django PasswordResetTokenGenerator token

    🔴 Token передаётся как аргумент задачи, но НИКОГДА не логируется.
    Token хранится только в Celery message (Redis broker),
    который должен быть защищён отдельно.
    """
    from django.core.mail import send_mail
    from django.conf import settings
    from apps.users.models import User

    try:
        user = User.objects.get(pk=user_id, is_active=True)
    except User.DoesNotExist:
        # 🔴 Не логируем подробности — не раскрываем информацию
        return

    # Формируем ссылку сброса (frontend route)
    reset_url = (
        f"{getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')}"
        f"/forgot-password?uid={uid}&token={token}"
    )

    send_mail(
        subject='Сброс пароля — Amazone Clone',
        message=(
            f'Здравствуйте, {user.get_full_name() or user.username}!\n\n'
            f'Вы запросили сброс пароля.\n'
            f'Перейдите по ссылке для установки нового пароля:\n'
            f'{reset_url}\n\n'
            f'Если вы не запрашивали сброс пароля, проигнорируйте это письмо.\n'
            f'Ссылка действительна 3 дня.'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@amazone-clone.local'),
        recipient_list=[user.email],
        fail_silently=True,
    )

    # 🔴 НЕ логируем token, uid, или ссылку
    logger.info('Password reset email sent for user %s', user_id)
