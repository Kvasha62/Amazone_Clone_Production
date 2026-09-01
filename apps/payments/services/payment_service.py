# ────────────────────────────────────────────────────────────────────────
# apps/payments/services/payment_service.py — бизнес-логика платежей.
#
# АРХИТЕКТУРНЫЙ ПРИНЦИП «Service Layer»:
#   View → сериализатор (валидация) → сервис (бизнес-логика) → ORM (SQL)
#
# ОПЕРАЦИИ:
#   create_payment()     — создать платёж для заказа
#   process_payment()    — перевести в PROCESSING (отправка провайдеру)
#   confirm_payment()    — подтвердить оплату (webhook/callback)
#   fail_payment()       — отметить как FAILED
#   cancel_payment()     — отменить платёж
#   refund_payment()     — оформить возврат средств
#   handle_webhook()     — обработать вебхук от провайдера
#
# БЕЗОПАСНОСТЬ КОНКУРЕНТНОГО ДОСТУПА:
#   Все mutating-методы используют select_for_update() и transaction.atomic.
#
# 📖 https://martinfowler.com/eaaCatalog/serviceLayer.html
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone

from rest_framework.exceptions import NotFound, ValidationError

from apps.orders.models.order import OrderStatus
from apps.payments.constants import (
    DEFAULT_PAYMENT_PROVIDER,
    MAX_PAYMENT_AMOUNT,
    MIN_PAYMENT_AMOUNT,
    PAYMENT_EVENT_CANCELLED,
    PAYMENT_EVENT_CONFIRMED,
    PAYMENT_EVENT_CREATED,
    PAYMENT_EVENT_ERROR,
    PAYMENT_EVENT_REFUND_COMPLETED,
    PAYMENT_EVENT_REFUND_INITIATED,
    PAYMENT_EVENT_REFUND_FAILED,
    PAYMENT_EVENT_ORDER_CONFIRM_FAILED,
    PAYMENT_EVENT_STATUS_CHANGED,
    PAYMENT_EVENT_WEBHOOK_RECEIVED,
    PAYMENT_METHOD_CARD,
    PAYMENT_STATUS_CANCELLED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_PENDING,
    PAYMENT_STATUS_PROCESSING,
    PAYMENT_STATUS_REFUNDED,
    PAYMENT_STATUS_SUCCEEDED,
    PAYMENT_STATUS_TRANSITIONS,
)
from apps.payments.exceptions import OrderConfirmationError
from apps.payments.models import Payment, PaymentEvent

logger = logging.getLogger(__name__)

# PROD-003: выделенный alias соединения для durable audit-записей,
# которые должны пережить откат/аборт основной транзакции
# (например, фиксация обязательства возврата после ошибки БД внутри
# OrderService.cancel()). Регистрируется лениво; настройки копируются
# из соединения default.
_PAYMENTS_AUDIT_ALIAS = 'payments_audit'


def _payments_audit_connection():
    """Зарегистрировать (если нужно) и вернуть audit-соединение."""
    from django.db import connections

    if _PAYMENTS_AUDIT_ALIAS not in connections.databases:
        connections.databases[_PAYMENTS_AUDIT_ALIAS] = dict(
            connections.databases['default'],
        )
    return connections[_PAYMENTS_AUDIT_ALIAS]


class PaymentService:
    """
    Бизнес-логика платежей.

    Все mutating-методы обёрнуты в transaction.atomic и используют
    select_for_update() для исключения race conditions.
    """

    # ==============================================================
    # СОЗДАНИЕ ПЛАТЕЖА
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def create_payment(
        order,
        user,
        amount: Decimal,
        method: str = PAYMENT_METHOD_CARD,
        provider: str = DEFAULT_PAYMENT_PROVIDER,
        note: str = '',
    ) -> Payment:
        """
        Создаёт новый платёж для заказа.

        АЛГОРИТМ:
          1. Проверить что заказ принадлежит пользователю
          2. Проверить что заказ в статусе PENDING (можно оплатить)
          3. Проверить сумму (в пределах лимитов)
          4. Создать Payment (PENDING)
          5. Создать PaymentEvent(CREATED)

        ЗАЩИТА ОТ:
          • Оплата чужого заказа (ownership check)
          • Повторная оплата уже оплаченного заказа
          • Слишком маленькая/большая сумма

        📖 https://docs.djangoproject.com/en/stable/topics/db/transactions/
        """
        # ── Проверка ownership ──
        if order.user_id != user.pk:
            raise NotFound('Заказ не найден.')

        # ── Проверка статуса заказа ──
        # Оплачивать можно только PENDING-заказы.
        # CONFIRMED → уже оплачен. CANCELLED → нельзя оплатить.
        if order.status != OrderStatus.PENDING:
            raise ValidationError({
                'detail': (
                    f'Нельзя создать платёж для заказа в статусе '
                    f'«{order.get_status_display()}». '
                    f'Допускается только «Ожидает оплаты».'
                ),
            })

        # ── Проверка суммы ──
        if amount < MIN_PAYMENT_AMOUNT:
            raise ValidationError({
                'amount': (
                    f'Минимальная сумма платежа — {MIN_PAYMENT_AMOUNT}₽.'
                ),
            })
        if amount > MAX_PAYMENT_AMOUNT:
            raise ValidationError({
                'amount': (
                    f'Максимальная сумма платежа — {MAX_PAYMENT_AMOUNT}₽.'
                ),
            })

        # ── Проверка: сумма платежа должна совпадать с суммой заказа ──
        # 🔴 КРИТИЧЕСКАЯ БЕЗОПАСНОСТЬ: без этой проверки злоумышленник
        # может создать платёж на 1₽ для заказа на 100000₽ → товар за 1₽.
        # 📖 https://owasp.org/www-community/attacks/Business_Logic_Vulnerabilities
        if amount != order.total:
            raise ValidationError({
                'amount': (
                    f'Сумма платежа ({amount}₽) не совпадает с суммой заказа '
                    f'({order.total}₽). Оплатите полную сумму.'
                ),
            })

        # ── Проверка: нет ли уже успешного платежа ──
        existing_paid = Payment.objects.filter(
            order=order,
            status=PAYMENT_STATUS_SUCCEEDED,
        ).exists()
        if existing_paid:
            raise ValidationError({
                'detail': 'Заказ уже оплачен.',
            })

        # ── Создание платежа ──
        payment = Payment(
            order=order,
            user=user,
            amount=amount,
            method=method,
            provider=provider,
            status=PAYMENT_STATUS_PENDING,
            note=note,
            # Генерируем mock external_id (в реальном проекте —
            # от провайдера после API-вызова)
            external_id=f'mock_{uuid.uuid4().hex[:16]}',
        )
        payment.save()

        # ── Аудит: создаём событие ──
        PaymentEvent.objects.create(
            payment=payment,
            event_type=PAYMENT_EVENT_CREATED,
            new_status=PAYMENT_STATUS_PENDING,
            performed_by=user,
            note=f'Платёж {payment.order_number} создан для заказа {order.order_number}',
        )

        logger.info(
            'payment_created',
            extra={
                'payment_id': payment.pk,
                'payment_number': payment.order_number,
                'order_id': order.pk,
                'amount': str(amount),
                'method': method,
            },
        )

        return payment

    # ==============================================================
    # ОБРАБОТКА ПЛАТЕЖА (переход в PROCESSING)
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def process_payment(payment: Payment, *, user=None) -> Payment:
        """
        Переводит платёж в PROCESSING (отправка провайдеру).

        В реальном проекте здесь — вызов API платёжного провайдера:
          yookassa.create_payment(amount, ...)

        Для mock — просто меняем статус.
        """
        payment = Payment.objects.select_for_update().get(pk=payment.pk)

        if payment.status != PAYMENT_STATUS_PENDING:
            raise ValidationError({
                'detail': (
                    f'Нельзя обработать платёж в статусе '
                    f'«{payment.get_status_display()}».'
                ),
            })

        old_status = payment.status
        payment.status = PAYMENT_STATUS_PROCESSING
        payment.save(update_fields=['status', 'updated_at'])

        PaymentEvent.objects.create(
            payment=payment,
            event_type=PAYMENT_EVENT_STATUS_CHANGED,
            old_status=old_status,
            new_status=PAYMENT_STATUS_PROCESSING,
            performed_by=user,
        )

        logger.info(
            'payment_processing',
            extra={'payment_id': payment.pk},
        )

        return payment

    # ==============================================================
    # ПОДТВЕРЖДЕНИЕ ОПЛАТЫ (webhook / callback от провайдера)
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def confirm_payment(
        payment: Payment,
        *,
        external_id: str = '',
        payload: dict | None = None,
    ) -> Payment:
        """
        Подтверждает успешную оплату.

        ВЫЗЫВАЕТСЯ ПРИ:
          • Вебхук от платёжного провайдера (YooKassa: payment.succeeded)
          • Callback при возврате пользователя на сайт
          • Ручное подтверждение (admin)

        ПОСЛЕДСТВИЯ:
          • Payment.status → SUCCEEDED
          • Payment.paid_at → now()
          • Order.status → CONFIRMED (через OrderService.confirm)

        АЛГОРИТМ:
          1. select_for_update — блокировка
          2. Валидация перехода (PROCESSING → SUCCEEDED)
          3. Обновление статуса и paid_at
          4. Создание PaymentEvent
          5. Подтверждение заказа (OrderService.confirm)

        📖 https://docs.djangoproject.com/en/stable/topics/db/transactions/
        """
        payment = Payment.objects.select_for_update().get(pk=payment.pk)

        # Валидация перехода: допускаем PROCESSING и PENDING
        # (некоторые провайдеры мгновенно отвечают SUCCEEDED)
        allowed = PAYMENT_STATUS_TRANSITIONS.get(payment.status, [])
        if PAYMENT_STATUS_SUCCEEDED not in allowed:
            # Особый случай: если уже SUCCEEDED — идемпотентно возвращаем.
            # PROD-003: перед возвратом пробуем «залечить» расхождение
            # платёж↔заказ (SUCCEEDED при PENDING-заказе) — повторная
            # доставка вебхука самовосстанавливает состояние.
            if payment.status == PAYMENT_STATUS_SUCCEEDED:
                logger.info(
                    'payment_already_confirmed',
                    extra={'payment_id': payment.pk},
                )
                PaymentService._reconcile_order_confirmation(payment)
                return payment
            raise ValidationError({
                'detail': (
                    f'Нельзя подтвердить платёж в статусе '
                    f'«{payment.get_status_display()}».'
                ),
            })

        old_status = payment.status
        payment.status = PAYMENT_STATUS_SUCCEEDED
        payment.paid_at = timezone.now()

        if external_id:
            payment.external_id = external_id
        if payload:
            payment.metadata.update(payload)

        payment.save(update_fields=[
            'status', 'paid_at', 'external_id', 'metadata', 'updated_at',
        ])

        # Аудит
        PaymentEvent.objects.create(
            payment=payment,
            event_type=PAYMENT_EVENT_CONFIRMED,
            old_status=old_status,
            new_status=PAYMENT_STATUS_SUCCEEDED,
            payload=payload or {},
            external_event_id=external_id,
        )

        # ── Подтверждаем заказ ──
        from apps.orders.services.order_service import OrderService
        from rest_framework.exceptions import ValidationError as DRFValidationError
        from django.db import DatabaseError

        try:
            OrderService.confirm(payment.order)
        except (DRFValidationError, DatabaseError) as exc:
            # PROD-003: сбой подтверждения заказа больше не молчаливый.
            # Классифицируем по актуальному статусу заказа:
            order_status = PaymentService._fresh_order_status(payment.order_id)
            db_error = isinstance(exc, DatabaseError) or order_status is None

            if db_error or order_status == OrderStatus.PENDING:
                # Резервирование стока не удалось (или сбой БД): платёж
                # ОБЯЗАН откатиться вместе с транзакцией — денежное
                # состояние не уходит вперёд без зарезервированного стока.
                # Обработчик (PaymentWebhookView) фиксирует событие
                # order_confirm_failed и возвращает 502 — провайдер
                # повторит запрос.
                raise OrderConfirmationError(
                    str(exc),
                    order_status=order_status,
                    db_error=db_error,
                )

            if order_status in (OrderStatus.DELIVERED, OrderStatus.CANCELLED):
                # Заказ уже завершён: SUCCEEDED был бы неконсистентен.
                # Откат + обработка на уровне вебхука (закрытие платежа).
                raise OrderConfirmationError(
                    str(exc),
                    order_status=order_status,
                    db_error=False,
                )

            # Заказ перешёл в ненарушающее состояние другим путём
            # (staff/admin подтвердил заранее): SUCCEEDED консистентен.
            # Фиксируем событие — расхождение должно быть наблюдаемым.
            PaymentEvent.objects.create(
                payment=payment,
                event_type=PAYMENT_EVENT_ORDER_CONFIRM_FAILED,
                old_status=old_status,
                new_status=PAYMENT_STATUS_SUCCEEDED,
                payload={'error': str(exc), 'order_status': order_status},
                note=(
                    'Платёж подтверждён; заказ уже находится в статусе, '
                    'отличном от PENDING.'
                ),
            )
            logger.error(
                'payment_confirmed_order_already_advanced',
                extra={
                    'payment_id': payment.pk,
                    'order_id': payment.order_id,
                    'order_status': order_status,
                    'error': str(exc),
                },
            )
            return payment

        logger.info(
            'payment_confirmed',
            extra={
                'payment_id': payment.pk,
                'order_id': payment.order_id,
                'amount': str(payment.amount),
            },
        )

        return payment

    # ==============================================================
    # ОШИБКА ОПЛАТЫ
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def fail_payment(
        payment: Payment,
        *,
        payload: dict | None = None,
        note: str = '',
    ) -> Payment:
        """
        Отмечает платёж как FAILED (оплата отклонена).

        ВЫЗЫВАЕТСЯ ПРИ:
          • Вебхук от провайдера: payment.failed / payment.rejected
          • Таймаут при обработке
          • Fraud detection
        """
        payment = Payment.objects.select_for_update().get(pk=payment.pk)

        allowed = PAYMENT_STATUS_TRANSITIONS.get(payment.status, [])
        if PAYMENT_STATUS_FAILED not in allowed:
            raise ValidationError({
                'detail': (
                    f'Нельзя отметить как FAILED платёж в статусе '
                    f'«{payment.get_status_display()}».'
                ),
            })

        old_status = payment.status
        payment.status = PAYMENT_STATUS_FAILED
        if note:
            payment.note = note
        if payload:
            payment.metadata.update(payload)
        payment.save(update_fields=['status', 'note', 'metadata', 'updated_at'])

        PaymentEvent.objects.create(
            payment=payment,
            event_type=PAYMENT_EVENT_ERROR,
            old_status=old_status,
            new_status=PAYMENT_STATUS_FAILED,
            payload=payload or {},
            note=note or 'Оплата отклонена',
        )

        logger.info(
            'payment_failed',
            extra={
                'payment_id': payment.pk,
                'note': note,
            },
        )

        return payment

    # ==============================================================
    # ОТМЕНА ПЛАТЕЖА
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def cancel_payment(
        payment: Payment,
        *,
        user=None,
        note: str = '',
    ) -> Payment:
        """
        Отменяет платёж.

        ВЫЗЫВАЕТСЯ ПРИ:
          • Пользователь нажал «Отменить» на странице оплаты
          • Таймаут неоплачённого платежа (management command)
          • Админ отменил вручную
        """
        payment = Payment.objects.select_for_update().get(pk=payment.pk)

        allowed = PAYMENT_STATUS_TRANSITIONS.get(payment.status, [])
        if PAYMENT_STATUS_CANCELLED not in allowed:
            raise ValidationError({
                'detail': (
                    f'Нельзя отменить платёж в статусе '
                    f'«{payment.get_status_display()}».'
                ),
            })

        old_status = payment.status
        payment.status = PAYMENT_STATUS_CANCELLED
        payment.cancelled_at = timezone.now()
        if note:
            payment.note = note
        payment.save(update_fields=[
            'status', 'cancelled_at', 'note', 'updated_at',
        ])

        PaymentEvent.objects.create(
            payment=payment,
            event_type=PAYMENT_EVENT_CANCELLED,
            old_status=old_status,
            new_status=PAYMENT_STATUS_CANCELLED,
            performed_by=user,
            note=note or 'Платёж отменён',
        )

        logger.info(
            'payment_cancelled',
            extra={
                'payment_id': payment.pk,
                'cancelled_by': getattr(user, 'pk', None),
            },
        )

        return payment

    # ==============================================================
    # ВОЗВРАТ СРЕДСТВ
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def refund_payment(
        payment: Payment,
        *,
        amount: Decimal | None = None,
        reason: str = '',
        user=None,
    ) -> Payment:
        """
        Оформляет возврат средств (PROD-003: fail-safe).

        АЛГОРИТМ:
          1. Проверить что платёж SUCCEEDED (блокировка строки)
          2. Определить сумму возврата (вся или частичная)
          3. Зафиксировать намерение (PaymentEvent refund_initiated)
          4. Вызвать провайдера (_settle_refund; mock исполняет сразу)
          5. При отказе провайдера — ЗАФИКСИРОВАТЬ retryable-обязательство
             refund_required_amount + PaymentEvent(refund_failed)

        Сбой исполнения возврата НЕ выбрасывается наружу и НЕ теряется:
        платёж остаётся SUCCEEDED с refund_pending_amount > 0, а
        retry_pending_refunds() / команда `retry_pending_refunds`
        доведут возврат до конца. ValidationError по-прежнему
        выбрасывается для некорректных вызовов (программные ошибки).

        ПОЧЕМУ ПОДДЕРЖИВАЕМ ЧАСТИЧНЫЙ ВОЗВРАТ:
          • Возврат одной позиции из заказа (не всего заказа)
          • Частичный refund при повреждении товара
        """
        payment = Payment.objects.select_for_update().get(pk=payment.pk)

        if payment.status != PAYMENT_STATUS_SUCCEEDED:
            raise ValidationError({
                'detail': (
                    f'Возврат возможен только для оплаченного платежа. '
                    f'Текущий статус: «{payment.get_status_display()}».'
                ),
            })

        # Сумма возврата: если не указана — полная (amount)
        refund_amount = amount if amount is not None else payment.amount

        if refund_amount <= 0:
            raise ValidationError({
                'amount': 'Сумма возврата должна быть > 0.',
            })

        new_total_refund = payment.refund_amount + refund_amount
        if new_total_refund > payment.amount:
            raise ValidationError({
                'amount': (
                    f'Сумма возврата ({new_total_refund}₽) превышает '
                    f'сумму платежа ({payment.amount}₽).'
                ),
            })

        old_status = payment.status
        payment.refund_reason = reason
        payment.save(update_fields=['refund_reason', 'updated_at'])

        # Аудит: намерение возврата фиксируется ДО вызова провайдера.
        PaymentEvent.objects.create(
            payment=payment,
            event_type=PAYMENT_EVENT_REFUND_INITIATED,
            old_status=old_status,
            new_status=old_status,
            performed_by=user,
            payload={'refund_amount': str(refund_amount)},
            note=reason or f'Возврат {refund_amount}₽',
        )

        try:
            # Savepoint: сбой исполнения возврата не должен откатывать
            # уже зафиксированное намерение.
            with transaction.atomic():
                PaymentService._settle_refund(
                    payment,
                    refund_amount,
                    reason=reason,
                    user=user,
                )
        except Exception as exc:
            # PROD-003: провайдер не исполнил возврат. Фиксируем
            # retryable-обязательство (refund_required_amount) и событие
            # refund_failed — НИКАКОЙ молчаливой потери.
            payment.refund_required_amount = max(
                payment.refund_required_amount,
                new_total_refund,
            )
            payment.save(update_fields=[
                'refund_required_amount',
                'updated_at',
            ])
            PaymentEvent.objects.create(
                payment=payment,
                event_type=PAYMENT_EVENT_REFUND_FAILED,
                old_status=old_status,
                new_status=old_status,
                performed_by=user,
                payload={
                    'error': str(exc),
                    'refund_amount': str(refund_amount),
                    'refund_required_amount': str(
                        payment.refund_required_amount,
                    ),
                },
                note=(
                    'Провайдер не исполнил возврат — зафиксировано '
                    'обязательство повторной попытки.'
                ),
            )
            logger.error(
                'payment_refund_settle_failed',
                extra={
                    'payment_id': payment.pk,
                    'refund_amount': str(refund_amount),
                    'refund_required_amount': str(
                        payment.refund_required_amount,
                    ),
                    'error': str(exc),
                },
            )
            # Вернуть экземпляр к актуальному состоянию БД: исполнение
            # возврата откатилось, обязательство — зафиксировано.
            payment.refresh_from_db()

        logger.info(
            'payment_refunded',
            extra={
                'payment_id': payment.pk,
                'refund_amount': str(refund_amount),
                'total_refunded': str(payment.refund_amount),
                'refund_required_amount': str(
                    payment.refund_required_amount,
                ),
                'new_status': payment.status,
            },
        )

        return payment

    @staticmethod
    def _settle_refund(
        payment: Payment,
        settled_amount: Decimal,
        *,
        reason: str = '',
        user=None,
    ) -> Payment:
        """Применить подтверждённый провайдером возврат (PROD-003).

        Вызывается ИЗНУТРИ transaction.atomic при удерживаемой блокировке
        строки Payment. В mock-интеграции возврат исполняется сразу;
        при подключении реального провайдера здесь будет вызов его API.

        Статус REFUNDED выставляется только когда refund_amount покрыл
        ВСЮ сумму платежа (существующий контракт).
        """
        payment.refund_amount = payment.refund_amount + settled_amount
        payment.refund_reason = reason or payment.refund_reason
        if payment.refund_amount >= payment.amount:
            payment.status = PAYMENT_STATUS_REFUNDED
            payment.refunded_at = timezone.now()

        payment.save(update_fields=[
            'status',
            'refund_amount',
            'refund_reason',
            'refunded_at',
            'updated_at',
        ])

        event_type = (
            PAYMENT_EVENT_REFUND_COMPLETED
            if payment.status == PAYMENT_STATUS_REFUNDED
            else PAYMENT_EVENT_REFUND_INITIATED
        )
        PaymentEvent.objects.create(
            payment=payment,
            event_type=event_type,
            old_status=PAYMENT_STATUS_SUCCEEDED,
            new_status=payment.status,
            performed_by=user,
            payload={
                'refund_amount': str(settled_amount),
                'total_refunded': str(payment.refund_amount),
            },
            note=reason or f'Возврат {settled_amount}₽',
        )
        return payment

    @staticmethod
    def retry_pending_refunds(
        payment_ids: list[int] | None = None,
    ) -> dict:
        """Исполнить все незакрытые обязательства возвратов (PROD-003).

        Находит SUCCEEDED-платежи с refund_required_amount >
        refund_amount и доводит refund_amount до обязательства.
        Каждый платёж обрабатывается в собственной транзакции с
        блокировкой строки; повторный запуск безопасен (идемпотентен).

        Возвращает статистику: {'found', 'settled', 'failed'}.
        """
        qs = (
            Payment.objects
            .filter(status=PAYMENT_STATUS_SUCCEEDED)
            .filter(refund_required_amount__gt=models.F('refund_amount'))
        )
        if payment_ids:
            qs = qs.filter(pk__in=payment_ids)

        stats = {'found': qs.count(), 'settled': 0, 'failed': 0}
        for payment in list(qs.order_by('pk')):
            try:
                with transaction.atomic():
                    locked = (
                        Payment.objects
                        .select_for_update()
                        .get(pk=payment.pk)
                    )
                    if locked.status != PAYMENT_STATUS_SUCCEEDED:
                        continue
                    remaining = (
                        locked.refund_required_amount - locked.refund_amount
                    )
                    if remaining <= 0:
                        continue
                    PaymentService._settle_refund(locked, remaining)
                stats['settled'] += 1
            except Exception as exc:
                # Не молчаливо: статистика + ERROR-лог; обязательство
                # остаётся в БД и будет повторено следующим запуском.
                stats['failed'] += 1
                logger.error(
                    'refund_retry_failed',
                    extra={
                        'payment_id': payment.pk,
                        'error': str(exc),
                    },
                )
        return stats

    @staticmethod
    def _record_refund_obligation(
        payment: Payment,
        *,
        reason: str = '',
        error: str = '',
        user_id=None,
        using: str | None = None,
    ) -> bool:
        """Ядро фиксации обязательства возврата (PROD-003).

        Вызывается для УЖЕ заблокированного платежа. Устанавливает
        refund_required_amount = amount и создаёт PaymentEvent
        (refund_failed). Идемпотентно: если обязательство уже покрывает
        оставшуюся сумму, ничего не меняется и событие не дублируется.
        `using` — alias соединения (None = default).
        """
        remaining = payment.amount - payment.refund_amount
        if remaining <= 0:
            # Долга нет — состояние уже консистентно.
            return True
        if payment.refund_required_amount >= remaining:
            # Обязательство уже зафиксировано — идемпотентный
            # повторный вызов не создаёт дублирующих событий.
            return True
        requirement = payment.amount
        payment_manager = (
            Payment.objects.using(using) if using else Payment.objects
        )
        event_manager = (
            PaymentEvent.objects.using(using) if using else PaymentEvent.objects
        )
        payment_manager.filter(pk=payment.pk).update(
            refund_required_amount=requirement,
        )
        event_manager.create(
            payment=payment,
            event_type=PAYMENT_EVENT_REFUND_FAILED,
            old_status=PAYMENT_STATUS_SUCCEEDED,
            new_status=PAYMENT_STATUS_SUCCEEDED,
            performed_by_id=user_id,
            payload={
                'error': error,
                'refund_required_amount': str(requirement),
            },
            note=(
                reason or
                'Возврат не выполнен — зафиксировано обязательство '
                'повторной попытки.'
            ),
        )
        return True

    @staticmethod
    def record_refund_failure(
        payment: Payment,
        *,
        reason: str = '',
        error: str = '',
        user_id=None,
    ) -> bool:
        """Зафиксировать обязательство возврата в собственной транзакции.

        Для вызовов из здорового контекста (реконсиляция, команды):
        метод сам блокирует строку платежа и пишет через default-
        соединение. Возвращает True, если обязательство зафиксировано
        (или уже было зафиксировано / долга нет).
        """
        with transaction.atomic():
            locked = (
                Payment.objects
                .select_for_update()
                .get(pk=payment.pk)
            )
            if locked.status != PAYMENT_STATUS_SUCCEEDED:
                return False
            return PaymentService._record_refund_obligation(
                locked,
                reason=reason,
                error=error,
                user_id=user_id,
            )

    @staticmethod
    def record_refund_failure_durable(
        payment_id: int,
        *,
        reason: str = '',
        error: str = '',
        user_id=None,
    ) -> bool:
        """Зафиксировать обязательство возврата DURABLE (PROD-003).

        Пишет через выделенное audit-соединение, поэтому запись
        переживает откат/аборт основной транзакции (типовой сценарий —
        ошибка БД внутри OrderService.cancel(), когда обычная запись
        была бы откачена вместе с транзакцией).
        """
        _payments_audit_connection()
        alias = _PAYMENTS_AUDIT_ALIAS
        try:
            with transaction.atomic(using=alias):
                payment = (
                    Payment.objects.using(alias)
                    .select_for_update()
                    .get(pk=payment_id)
                )
                if payment.status != PAYMENT_STATUS_SUCCEEDED:
                    return False
                return PaymentService._record_refund_obligation(
                    payment,
                    reason=reason,
                    error=error,
                    user_id=user_id,
                    using=alias,
                )
        except Payment.DoesNotExist:
            return False
        except Exception as exc:
            # НЕ молчаливо: durable-запись — последняя линия обороны,
            # её провал обязан оставить критический след в логе.
            logger.critical(
                'record_refund_failure_durable_failed',
                extra={'payment_id': payment_id, 'error': str(exc)},
            )
            return False

    @staticmethod
    def _fresh_order_status(order_id: int) -> str | None:
        """Свежий статус заказа; None, если прочитать не удалось.

        Используется для классификации сбоя подтверждения заказа:
        статус читается отдельным запросом (в том числе когда
        транзакция-носитель уже не может выполнять запросы).
        """
        from apps.orders.models import Order

        try:
            return Order.objects.only('status').get(pk=order_id).status
        except Exception:  # noqa: BLE001 — probe; caller classifies.
            return None

    @staticmethod
    def _reconcile_order_confirmation(payment: Payment) -> None:
        """Залечить расхождение «SUCCEEDED платёж + PENDING заказ».

        Вызывается из идемпотентной ветки confirm_payment (повторная
        доставка вебхука) и из команды реконсиляции. Сбой повторного
        подтверждения фиксируется событием order_confirm_failed —
        расхождение остаётся наблюдаемым.
        """
        from apps.orders.services.order_service import OrderService

        order_status = PaymentService._fresh_order_status(payment.order_id)
        if order_status != OrderStatus.PENDING:
            return
        try:
            OrderService.confirm(payment.order)
            logger.info(
                'payment_order_reconciled',
                extra={
                    'payment_id': payment.pk,
                    'order_id': payment.order_id,
                },
            )
        except Exception as exc:  # noqa: BLE001 — фиксируем, не теряем.
            logger.error(
                'payment_reconcile_failed',
                extra={
                    'payment_id': payment.pk,
                    'order_id': payment.order_id,
                    'error': str(exc),
                },
            )
            PaymentEvent.objects.create(
                payment=payment,
                event_type=PAYMENT_EVENT_ORDER_CONFIRM_FAILED,
                payload={'error': str(exc), 'phase': 'reconcile'},
                note='Реконсиляция подтверждения заказа не удалась.',
            )

    @staticmethod
    def reconcile_succeeded_payment(payment: Payment) -> str:
        """Реконсиляция SUCCEEDED-платежа с его заказом (PROD-003).

        Точка восстановления для команды `reconcile_order_coordination`
        и ручной разборки. Возвращает:
          'skipped'         — платёж не SUCCEEDED;
          'ok'              — платёж и заказ консистентны;
          'confirmed'       — заказ был PENDING и подтверждён;
          'confirm_failed'  — подтверждение снова не удалось (событие есть);
          'refund_required' — заказ отменён: обязательство возврата
                              зафиксировано, подхватит retry_pending_refunds.
        """
        from apps.orders.services.order_service import OrderService

        payment = Payment.objects.get(pk=payment.pk)
        if payment.status != PAYMENT_STATUS_SUCCEEDED:
            return 'skipped'

        order_status = PaymentService._fresh_order_status(payment.order_id)
        if order_status == OrderStatus.PENDING:
            try:
                with transaction.atomic():
                    OrderService.confirm(payment.order)
                return 'confirmed'
            except Exception as exc:  # noqa: BLE001 — фиксируем, не теряем.
                logger.error(
                    'reconcile_confirm_failed',
                    extra={
                        'payment_id': payment.pk,
                        'error': str(exc),
                    },
                )
                PaymentEvent.objects.create(
                    payment=payment,
                    event_type=PAYMENT_EVENT_ORDER_CONFIRM_FAILED,
                    payload={'error': str(exc), 'phase': 'reconcile'},
                    note='Реконсиляция: подтверждение заказа не удалось.',
                )
                return 'confirm_failed'

        if order_status == OrderStatus.CANCELLED:
            PaymentService.record_refund_failure(
                payment,
                reason='Заказ отменён после оплаты — требуется возврат.',
                error='order_cancelled_after_payment',
                user_id=None,
            )
            return 'refund_required'

        return 'ok'

    # ==============================================================
    # ОБРАБОТКА ВЕБХУКА
    # ==============================================================

    @staticmethod
    @transaction.atomic
    def handle_webhook(
        *,
        external_id: str,
        event_type: str,
        status: str,
        payload: dict | None = None,
    ) -> Payment | None:
        """
        Обрабатывает вебхук от платёжного провайдера.

        ВЫЗЫВАЕТСЯ ПРИ:
          • POST /api/v1/payments/webhook/ (внешний запрос от провайдера)

        АЛГОРИТМ:
          1. Найти платёж по external_id
          2. Записать PaymentEvent(WEBHOOK_RECEIVED)
          3. Обработать статус:
             - succeeded → confirm_payment()
             - failed → fail_payment()
             - cancelled → cancel_payment()
          4. Вернуть обновлённый платёж

        ИДЕМПОТЕНТНОСТЬ:
          Если вебхук пришёл дважды — обрабатываем корректно:
          • Уже SUCCEEDED → возвращаем без ошибки
          • Для каждого вебхука создаётся PaymentEvent

        📖 https://en.wikipedia.org/wiki/Idempotence
        """
        # ── Ищем платёж по external_id ──
        try:
            payment = Payment.objects.with_external_id(external_id).first()
        except Exception:
            payment = None

        if payment is None:
            logger.warning(
                'webhook_payment_not_found',
                extra={'external_id': external_id},
            )
            return None

        # ── Записываем вебхук в аудит-лог ──
        PaymentEvent.objects.create(
            payment=payment,
            event_type=PAYMENT_EVENT_WEBHOOK_RECEIVED,
            payload=payload or {},
            external_event_id=external_id,
            note=f'Webhook: event={event_type}, status={status}',
        )

        # ── Обрабатываем статус ──
        if status == PAYMENT_STATUS_SUCCEEDED:
            payment = PaymentService.confirm_payment(
                payment,
                external_id=external_id,
                payload=payload,
            )
        elif status == PAYMENT_STATUS_FAILED:
            payment = PaymentService.fail_payment(
                payment,
                payload=payload,
            )
        elif status == PAYMENT_STATUS_CANCELLED:
            payment = PaymentService.cancel_payment(
                payment,
                note='Отменён провайдером (webhook)',
            )
        else:
            logger.warning(
                'webhook_unknown_status',
                extra={
                    'external_id': external_id,
                    'status': status,
                },
            )

        return payment

    # ==============================================================
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ==============================================================

    @staticmethod
    def get_payment_by_number(payment_number: str) -> Payment:
        """
        Возвращает платёж по номеру (PAY-000001).
        Бросает NotFound если не найден.
        """
        try:
            return Payment.objects.get(order_number=payment_number)
        except Payment.DoesNotExist:
            raise NotFound('Платёж не найден.')

    @staticmethod
    def get_payment_for_order_check(order, user) -> Payment:
        """
        Возвращает платёж для проверки ownership.
        """
        try:
            payment = Payment.objects.get(order=order, user=user)
        except Payment.DoesNotExist:
            raise NotFound('Платёж не найден.')
        return payment
