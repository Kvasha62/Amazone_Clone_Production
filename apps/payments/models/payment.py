# ────────────────────────────────────────────────────────────────────────
# apps/payments/models/payment.py — модель платежа.
#
# БИЗНЕС-ТРЕБОВАНИЯ:
#   • Платёж привязан к заказу (Order) — один заказ может иметь
#     несколько платежей (например, неудачная попытка + успешная)
#   • Сумма платежа может не совпадать с total заказа (частичная оплата,
#     скидка, купон, но обычно — совпадает)
#   • Статусная машина: PENDING → PROCESSING → SUCCEEDED / FAILED / CANCELLED
#   • SUCCEEDED может перейти в REFUNDED (возврат)
#   • external_id — ID платежа во внешней платёжной системе (ЮKassa, Stripe)
#   • provider — платёжный провайдер (mock, yookassa, stripe)
#
# АРХИТЕКТУРНОЕ РЕШЕНИЕ — ПЛАТЕЖ ОТДЕЛЬНО ОТ ЗАКАЗА:
#   Почему не просто поле Order.payment_status:
#     1) Один заказ → несколько попыток оплаты (card declined → retry)
#     2) Частичный возврат (REFUNDED, но не на всю сумму)
#     3) Аудит: полная история всех платежных операций
#     4) Разные провайдеры: первый платёж через карту, второй через СБП
#
# 📖 https://docs.djangoproject.com/en/stable/ref/models/fields/
# 📖 https://docs.djangoproject.com/en/stable/ref/models/constraints/
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base_model import BaseModel
from apps.payments.constants import (
    MAX_EXTERNAL_ID_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_PAYMENT_AMOUNT,
    MAX_PROVIDER_NAME_LENGTH,
    MAX_REFUND_REASON_LENGTH,
    MIN_PAYMENT_AMOUNT,
    PAYMENT_METHOD_CHOICES,
    PAYMENT_NUMBER_DIGITS,
    PAYMENT_NUMBER_PREFIX,
    PAYMENT_STATUS_CANCELLED,
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_PENDING,
    PAYMENT_STATUS_PROCESSING,
    PAYMENT_STATUS_REFUNDED,
    PAYMENT_STATUS_SUCCEEDED,
    PAYMENT_TERMINAL_STATUSES,
    PAYMENT_STATUS_TRANSITIONS,
)
from apps.payments.managers.payment_manager import PaymentManager


# ==============================================================
# СТАТУСЫ ПЛАТЕЖА (TextChoices — type-safe Enum)
# ==============================================================
class PaymentStatus(models.TextChoices):
    """
    Статусы платежа — конечный автомат (FSM).

    ЖИЗНЕННЫЙ ЦИКЛ:
        PENDING → PROCESSING → SUCCEEDED → REFUNDED
           ↓          ↓
        CANCELLED  FAILED / CANCELLED

    Правила переходов валидируются в PaymentService:
      • FAILED, CANCELLED, REFUNDED — терминальные (финальные) статусы
      • Переход «назад» невозможен
      • Только SUCCEEDED может перейти в REFUNDED
    """
    PENDING = PAYMENT_STATUS_PENDING, 'Ожидает оплаты'
    PROCESSING = PAYMENT_STATUS_PROCESSING, 'В обработке'
    SUCCEEDED = PAYMENT_STATUS_SUCCEEDED, 'Успешно оплачен'
    FAILED = PAYMENT_STATUS_FAILED, 'Ошибка оплаты'
    CANCELLED = PAYMENT_STATUS_CANCELLED, 'Отменён'
    REFUNDED = PAYMENT_STATUS_REFUNDED, 'Возврат средств'


# ==============================================================
# МОДЕЛЬ ПЛАТЕЖА
# ==============================================================
class Payment(BaseModel):
    """
    Платёж за заказ.

    АРХИТЕКТУРНЫЕ РЕШЕНИЯ:
      1. Сумма — DecimalField (не float — точность до копеек)
      2. external_id — ID во внешней платёжной системе
      3. Статус — FSM (Finite State Machine)
      4. provider — платёжный провайдер (mock/yookassa/stripe)
      5. refund_amount — сколько возвращено (0 если без возврата)

    СВЯЗИ:
      • Order (FK) — к какому заказу относится платёж
      • User (FK) — кто платил (денормализация для быстрого поиска)
      • PaymentEvent (reverse FK) — история событий платежа
    """

    # Пользовательский менеджер с QuerySet-методами:
    #   Payment.objects.for_user(user).succeeded()
    #   Payment.objects.for_order(order).active()
    objects = PaymentManager()

    # ──────────────────────────────────────────────────────────────
    # Заказ, к которому относится платёж
    # ──────────────────────────────────────────────────────────────
    # on_delete=PROTECT — нельзя удалить заказ с платежами!
    # Сначала нужно вернуть деньги → потом удалять заказ.
    # related_name='payments' → order.payments.all()
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='payments',
        verbose_name='Заказ',
    )

    # ──────────────────────────────────────────────────────────────
    # Пользователь, который оплачивает
    # ──────────────────────────────────────────────────────────────
    # Денормализация: user есть в Order, но частые запросы
    # «все платежи пользователя» требуют JOIN без этого поля.
    # on_delete=PROTECT — нельзя удалить пользователя с платежами.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='payments',
        verbose_name='Плательщик',
    )

    # ──────────────────────────────────────────────────────────────
    # Номер платежа (публичный идентификатор)
    # ──────────────────────────────────────────────────────────────
    # Формат: PAY-000001, PAY-000123 и т.д.
    # unique=True → UniqueConstraint + B-tree индекс.
    # editable=False — генерируется автоматически.
    payment_number = models.CharField(
        verbose_name='Номер платежа',
        max_length=20,
        unique=True,
        editable=False,
        blank=True,
    )

    # Внутренний числовой счётчик для генерации payment_number.
    # Аналог Order._order_number_seq — для быстрого MAX().
    _payment_number_seq = models.PositiveBigIntegerField(
        editable=False,
        db_index=True,
        default=0,
    )

    # ──────────────────────────────────────────────────────────────
    # Статус платежа
    # ──────────────────────────────────────────────────────────────
    status = models.CharField(
        verbose_name='Статус',
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
    )

    # ──────────────────────────────────────────────────────────────
    # Метод оплаты
    # ──────────────────────────────────────────────────────────────
    method = models.CharField(
        verbose_name='Метод оплаты',
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        db_index=True,
    )

    # ──────────────────────────────────────────────────────────────
    # Суммы (Decimal — точность до копеек)
    # ──────────────────────────────────────────────────────────────
    # amount — сумма платежа (сколько списали/попытались списать).
    # Обычно совпадает с order.total, но может отличаться
    # (например, частичная оплата, скидка платёжной системы).
    amount = models.DecimalField(
        verbose_name='Сумма платежа',
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(MIN_PAYMENT_AMOUNT)],
        db_index=True,
    )

    # refund_amount — сколько средств возвращено.
    # 0 = без возврата. При частичном возврате < amount.
    # При полном возврате == amount.
    refund_amount = models.DecimalField(
        verbose_name='Сумма возврата',
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    # refund_required_amount — сколько средств ЕЩЁ должно быть возвращено
    # (накопительное обязательство, PROD-003).
    #
    # PROD-003: если возврат провалился (отказ провайдера, сбой БД), сюда
    # записывается сумма, которую необходимо вернуть. Пока
    # refund_amount < refund_required_amount, платёж остаётся SUCCEEDED,
    # но имеет явное retryable-обязательство: его подхватывает
    # PaymentService.retry_pending_refunds() и команда
    # `python manage.py retry_pending_refunds`.
    # Обычные успешные возвраты НЕ создают обязательства (0 = нет долга).
    refund_required_amount = models.DecimalField(
        verbose_name='Сумма возврата к исполнению',
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0'))],
    )

    # ──────────────────────────────────────────────────────────────
    # Данные внешней платёжной системы
    # ──────────────────────────────────────────────────────────────
    # external_id — ID платежа у провайдера (ЮKassa payment_id,
    # Stripe payment_intent_id). Используется для:
    #   • Реконсиляции (сверка с платёжной системой)
    #   • Повторных запросов (status check)
    #   • Вебхуков (webhook передаёт этот ID)
    #
    # F-15 / PROD-014 — инвариант уникальности:
    #   Непустой external_id ГЛОБАЛЬНО уникален — это авторитетный
    #   контракт кода, а не redesign: вебхук-корреляция (ADR-004) ищет
    #   платёж только по external_id (QuerySet.with_external_id, без
    #   provider), сериал вебхука вообще не передаёт provider. Дубль
    #   идентификатора сделал бы выборку .first() неоднозначной.
    #   Инвариант защищён частичным уникальным индексом на уровне БД
    #   (UniqueConstraint payment_external_id_unique, условие
    #   external_id != ''): платёж без назначенного провайдером ID
    #   (blank, default='') может существовать во многих экземплярах —
    #   несколько попыток оплаты на один заказ сохранены.
    #   Замена обычного db_index на частичный уникальный индекс не
    #   ухудшает продакшн-путь: корреляция всегда ищет конкретный
    #   непустой ID, который удовлетворяет предикату индекса.
    external_id = models.CharField(
        verbose_name='Внешний ID',
        max_length=MAX_EXTERNAL_ID_LENGTH,
        blank=True,
        default='',
    )

    # provider — платёжный провайдер.
    # 'mock' — для тестов (имитация без реальной платёжки).
    # 'yookassa', 'stripe', 'tinkoff' — реальные провайдеры.
    provider = models.CharField(
        verbose_name='Платёжный провайдер',
        max_length=MAX_PROVIDER_NAME_LENGTH,
        db_index=True,
    )

    # ──────────────────────────────────────────────────────────────
    # Таймстампы платежа
    # ──────────────────────────────────────────────────────────────
    # paid_at — когда платёж успешно завершён.
    # null если ещё не оплачен (PENDING/PROCESSING).
    paid_at = models.DateTimeField(
        verbose_name='Дата оплаты',
        null=True,
        blank=True,
        db_index=True,
    )

    # cancelled_at — когда платёж отменён.
    cancelled_at = models.DateTimeField(
        verbose_name='Дата отмены',
        null=True,
        blank=True,
    )

    # refunded_at — когда выполнен возврат средств.
    refunded_at = models.DateTimeField(
        verbose_name='Дата возврата',
        null=True,
        blank=True,
    )

    # ──────────────────────────────────────────────────────────────
    # Дополнительные данные
    # ──────────────────────────────────────────────────────────────
    # note — комментарий к платежу (для менеджеров/админов).
    note = models.TextField(
        verbose_name='Комментарий',
        blank=True,
        default='',
        max_length=MAX_NOTE_LENGTH,
    )

    # refund_reason — причина возврата. Заполняется при REFUNDED.
    refund_reason = models.TextField(
        verbose_name='Причина возврата',
        blank=True,
        default='',
        max_length=MAX_REFUND_REASON_LENGTH,
    )

    # metadata — JSON-поле для хранения дополнительных данных
    # от платёжного провайдера (например, last4 карты, банк и т.д.).
    # В PostgreSQL — JSONField, в SQLite — TextField (эмуляция).
    metadata = models.JSONField(
        verbose_name='Метаданные',
        blank=True,
        default=dict,
    )

    class Meta:
        verbose_name = 'Платёж'
        verbose_name_plural = 'Платежи'
        ordering = ('-created_at',)

        indexes = [
            # Составной индекс (user, status) — для запросов:
            # «Все успешные платежи пользователя»
            models.Index(
                fields=['user', 'status'],
                name='payments_user_status_idx',
            ),
            # Составной индекс (order, status) — для запросов:
            # «Все платежи заказа со статусом SUCCEEDED»
            models.Index(
                fields=['order', 'status'],
                name='payments_order_status_idx',
            ),
            # Индекс по status — для аналитики:
            # «Сколько платежей в статусе PENDING?»
            models.Index(
                fields=['status'],
                name='payments_status_idx',
            ),
        ]

        constraints = [
            # CheckConstraint: amount ≥ 0
            models.CheckConstraint(
        condition=models.Q(amount__gte=Decimal('0')),
                name='payment_amount_non_negative',
            ),
            # CheckConstraint: refund_amount ≥ 0
            models.CheckConstraint(
        condition=models.Q(refund_amount__gte=Decimal('0')),
                name='payment_refund_non_negative',
            ),
            # CheckConstraint: refund_amount ≤ amount
            # Нельзя вернуть больше чем было оплачено.
            models.CheckConstraint(
        condition=models.Q(refund_amount__lte=models.F('amount')),
                name='payment_refund_lte_amount',
            ),
            # PROD-003: обязательство возврата не может превышать сумму
            # платежа (накопительное обязательство ≤ amount).
            models.CheckConstraint(
                condition=models.Q(
                    refund_required_amount__lte=models.F('amount'),
                ),
                name='payment_refund_required_lte_amount',
            ),
            # F-15 / PROD-014: непустой external_id глобально уникален
            # (контракт вебхук-корреляции ADR-004 — поиск только по
            # external_id, provider в выборке не участвует). Условие
            # исключает blank-значения (default=''): платежи без
            # назначенного провайдером ID не конкурируют между собой,
            # несколько попыток оплаты на заказ сохранены.
            models.UniqueConstraint(
                fields=['external_id'],
                condition=~models.Q(external_id=''),
                name='payment_external_id_unique',
            ),
        ]

    def __str__(self):
        return (
            f'{self.payment_number} ({self.get_status_display()}) — '
            f'{self.amount}₽'
        )

    @property
    def is_terminal(self) -> bool:
        """True если платёж в терминальном статусе."""
        return self.status in PAYMENT_TERMINAL_STATUSES

    @property
    def is_paid(self) -> bool:
        """True если платёж успешно завершён."""
        return self.status == PAYMENT_STATUS_SUCCEEDED

    @property
    def is_refundable(self) -> bool:
        """True если платёж можно вернуть (SUCCEEDED)."""
        return self.status == PAYMENT_STATUS_SUCCEEDED

    @property
    def refund_pending_amount(self) -> Decimal:
        """Сумма возврата, которая ещё не исполнена (PROD-003).

        > 0 → платёж имеет retryable-обязательство возврата:
        retry_pending_refunds() должен довести refund_amount до
        refund_required_amount.
        """
        pending = self.refund_required_amount - self.refund_amount
        return pending if pending > 0 else Decimal('0.00')

    def save(self, *args, **kwargs):
        """
        Переопределённый save() — авто-генерация payment_number.
        Аналог Order.save() — генерируем PAY-000001.
        """
        if not self.payment_number:
            max_seq = Payment.objects.aggregate(
                max_seq=models.Max('_payment_number_seq'),
            )['max_seq'] or 0
            self._payment_number_seq = max_seq + 1
            self.payment_number = (
                f'{PAYMENT_NUMBER_PREFIX}-'
                f'{self._payment_number_seq:0{PAYMENT_NUMBER_DIGITS}d}'
            )
        super().save(*args, **kwargs)
