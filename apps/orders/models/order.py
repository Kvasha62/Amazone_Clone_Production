# ────────────────────────────────────────────────────────────────────────
# apps/orders/models/order.py — модель заказа (Order).
#
# БИЗНЕС-ТРЕБОВАНИЯ:
#   • Заказ создаётся из корзины (cart → order — односторонняя связь)
#   • Адрес доставки КОПИРУЕТСЯ из Address (snapshot) — не FK!
#     Если пользователь изменит адрес — данные заказа не поменяются.
#   • Номер заказа — auto-generated: ORD-000001
#   • Статусная машина: PENDING → CONFIRMED → PROCESSING → SHIPPED → DELIVERED
#   • Любой статус кроме DELIVERED может перейти в CANCELLED
#   • Суммы хранятся как DecimalField (не float — точность до копеек)
#
# АДРЕС КАК SNAPSHOT (не FK):
#   Пользователь меняет адрес в профиле → заказы сохраняют старый адрес.
#   Это критично для юридической корректности (чеки, возвраты, споры).
#   Представьте: пользователь изменил город → все его заказы «переехали» — баг!
#
# 📖 https://docs.djangoproject.com/en/stable/ref/models/fields/#decimalfield
# 📖 https://docs.djangoproject.com/en/stable/ref/models/constraints/
# 📖 https://martinfowler.com/eaaCatalog/finiteStateMachine.html
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • Таблица orders_order не создастся → OrderItem не сможет ссылаться
#   • Все сервисы, views, сериализаторы заказов → ImportError
# ────────────────────────────────────────────────────────────────────────

import logging
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import connections, models, router
from django.db.models import Q

from apps.core.models.base_model import BaseModel
from apps.orders.managers.order_manager import OrderManager

logger = logging.getLogger(__name__)


# ==============================================================
# СТАТУСЫ ЗАКАЗА (TextChoices — type-safe Enum)
# ==============================================================
# TextChoices — Django-аналог Python Enum с человекочитаемыми значениями.
# Преимущества:
#   • values() — для choices в ModelField
#   • .PENDING — доступ через атрибут класса (type-safe)
#   • .label — человекочитаемое название
# 📖 https://docs.djangoproject.com/en/stable/ref/models/fields/#enumeration-types
class OrderStatus(models.TextChoices):
    """
    Статусы заказа — конечный автомат (Finite State Machine).

    ЖИЗНЕННЫЙ ЦИКЛ:
        PENDING → CONFIRMED → PROCESSING → SHIPPED → DELIVERED
           ↓          ↓            ↓
        CANCELLED  CANCELLED   CANCELLED

    Правила переходов валидируются в OrderService:
      • CANCELLED и DELIVERED — терминальные (финальные) статусы
      • Переход «назад» невозможен (PROCESSING → CONFIRMED = ошибка)
      • Переход в CANCELLED возможен из любого не-терминального статуса

    📖 https://en.wikipedia.org/wiki/Finite-state_machine
    """

    # PENDING — «Ожидает оплаты». Начальный статус при создании.
    # Заказ создан, но не подтверждён. Сток ещё не зарезервирован
    # (или зарезервирован временно — зависит от inventory strategy).
    PENDING = 'pending', 'Ожидает оплаты'

    # CONFIRMED — «Подтверждён». Пользователь оплатил заказ.
    # После оплаты платёжной системой → OrderService.confirm().
    CONFIRMED = 'confirmed', 'Подтверждён'

    # PROCESSING — «В обработке». Склад начал сборку.
    # Заказ передан в WMS (Warehouse Management System).
    PROCESSING = 'processing', 'В обработке'

    # SHIPPED — «Отправлен». Заказ передан в службу доставки.
    # Трек-номер заполнен. Пользователь получил уведомление.
    SHIPPED = 'shipped', 'Отправлен'

    # DELIVERED — «Доставлен». Терминальный статус (успешный).
    # Дальнейшие переходы невозможны (кроме REFUNDED через dispute).
    DELIVERED = 'delivered', 'Доставлен'

    # CANCELLED — «Отменён». Терминальный статус (неуспешный).
    # Причина отмены хранится в cancellation_reason.
    # Сток освобождён. Возврат средств инициирован (если была оплата).
    CANCELLED = 'cancelled', 'Отменён'


# ==============================================================
# ДОПУСТИМЫЕ ПЕРЕХОДЫ СТАТУСОВ
# ==============================================================
# Словарь: { текущий_статус: [список допустимых следующих статусов] }
# Используется в OrderService.transition_status() для валидации.
#
# НЕ ВКЛЮЧАЕМ transition_map в TextChoices — это бизнес-логика,
# а не данные модели. Константа живёт рядом с моделью для удобства.
#
# Схема переходов:
#   PENDING    → [CONFIRMED, CANCELLED]
#   CONFIRMED  → [PROCESSING, CANCELLED]
#   PROCESSING → [SHIPPED, CANCELLED]
#   SHIPPED    → [DELIVERED, CANCELLED]
#   DELIVERED  → []  (терминальный)
#   CANCELLED  → []  (терминальный)
#
# ПОЧЕМУ CANCELLED возможен из SHIPPED:
#   Иногда посылка теряется или повреждена → отмена после отправки.
#   Это редкий кейс, но он реален (возврат через курьерскую службу).
ORDER_STATUS_TRANSITIONS: dict[str, list[str]] = {
    OrderStatus.PENDING: [OrderStatus.CONFIRMED, OrderStatus.CANCELLED],
    OrderStatus.CONFIRMED: [OrderStatus.PROCESSING, OrderStatus.CANCELLED],
    OrderStatus.PROCESSING: [OrderStatus.SHIPPED, OrderStatus.CANCELLED],
    OrderStatus.SHIPPED: [OrderStatus.DELIVERED, OrderStatus.CANCELLED],
    OrderStatus.DELIVERED: [],
    OrderStatus.CANCELLED: [],
}


# =============================================================
# ВЫДАЧА НОМЕРА ЗАКАЗА (F-13 / PROD-010)
# =============================================================
# Имя PostgreSQL SEQUENCE. Создаётся миграцией
# apps/orders/migrations/0003_order_number_sequence.py и выставляется
# из MAX(_order_number_seq), чтобы существующие заказы не пересекались
# с новыми номерами.
ORDER_NUMBER_SEQUENCE = 'orders_order_number_seq'


def format_order_number(sequence_value: int) -> str:
    """
    Форматирует числовое значение в публичный номер заказа.

    ФОРМАТ: {ORDER_NUMBER_PREFIX}-{6 цифр, zero-padded}
    ПРИМЕР: ORD-000001, ORD-000123, ORD-999999, ORD-1000000

    ПОЧЕМУ НЕ UUID:
      • ORD-000123 → легко диктовать по телефону
      • QR-код из ORD-000123 короче чем из UUID
      • Печатается на чеках, этикетках, накладных

    ПОЧЕМУ НЕ ПРОСТО PK (id):
      • id=1 раскрывает количество заказов (конкурентная разведка)
      • Формат ORD-000123 — профессиональный, распознаваемый
    """
    from apps.orders.constants import ORDER_NUMBER_DIGITS, ORDER_NUMBER_PREFIX

    return f'{ORDER_NUMBER_PREFIX}-{sequence_value:0{ORDER_NUMBER_DIGITS}d}'


def allocate_order_number(*, using: str | None = None) -> tuple[int, str]:
    """
    Выдаёт следующий номер заказа: (числовая часть, 'ORD-000123').

    МЕХАНИЗМ (F-13 / PROD-010):
      PostgreSQL SEQUENCE — ``SELECT nextval('orders_order_number_seq')``.
      nextval() атомарен на стороне СУБД: каждый вызов получает собственное
      значение без чтения таблицы, без блокировок приложения и без
      read-then-increment. Параллельные создания заказа физически не могут
      получить один номер, поэтому retry на IntegrityError не нужен.

    ПОЧЕМУ НЕ MAX()+1 (прежняя схема):
      ``SELECT MAX(_order_number_seq) → +1 → INSERT`` — это
      read-then-increment: две параллельные транзакции читали один MAX и
      вставляли один номер. UNIQUE(order_number) не пропускал дубль, но
      превращал гонку в ошибку: повторный INSERT внутри той же транзакции
      невозможен (она aborted), поэтому конкурентный checkout падал.

    СЕМАНТИКА ОТКАТА (документированное поведение SEQUENCE):
      nextval() НЕ транзакционен — откат транзакции расходует значение,
      номер не переиспользуется. В нумерации возможны gaps; гарантируются
      уникальность и монотонность. Gapless-нумерация архитектурой не
      требуется (см. ADR-005).

    NON-POSTGRESQL BACKENDS (dev-режим, например SQLite):
      SEQUENCE отсутствуют, поэтому используется fallback MAX()+1 с
      предупреждением в логе. Это НЕ production-путь: production-СУБД
      проекта — PostgreSQL (CI и docker-compose.prod), а UNIQUE(order_number)
      остаётся последним рубежом уникальности на любом бэкенде.

    📖 https://www.postgresql.org/docs/current/functions-sequence.html
    """
    connection = (
        connections[using] if using
        else connections[router.db_for_write(Order)]
    )
    sequence_value = _next_order_number_sequence(connection)
    return sequence_value, format_order_number(sequence_value)


def _next_order_number_sequence(connection) -> int:
    """Возвращает следующее числовое значение номера заказа."""
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            # Имя SEQUENCE передаётся параметром (nextval принимает его как
            # regclass) — конкатенации SQL нет.
            cursor.execute('SELECT nextval(%s)', [ORDER_NUMBER_SEQUENCE])
            return int(cursor.fetchone()[0])

    # Fallback для бэкендов без SEQUENCE (SQLite — dev-режим, не production).
    logger.warning(
        'order_number_sequence_unsupported_backend',
        extra={'vendor': connection.vendor},
    )
    max_seq = Order.objects.using(connection.alias).aggregate(
        max_seq=models.Max('_order_number_seq'),
    )['max_seq'] or 0
    return max_seq + 1


# ==============================================================
# МОДЕЛЬ ЗАКАЗА
# ==============================================================
class Order(BaseModel):
    """
    Заказ пользователя.

    АРХИТЕКТУРНЫЕ РЕШЕНИЯ:
      1. Адрес — SNAPSHOT (копия полей), не FK к Address
         → данные заказа неизменны при изменении адреса пользователем
      2. Статус — TextChoices + transition map (FSM)
         → валидация переходов на уровне сервиса
      3. Суммы — DecimalField (не float)
         → точность до копеек (0.01₽)
      4. Номер — auto-generated (ORD-000001)
         → скрывает PK, удобен для поддержки

    СВЯЗИ:
      • User (FK) — кто оформил заказ
      • OrderItem (reverse FK) — позиции заказа
      • Cart (опциональный FK) — из какой корзины создан

    📖 https://docs.djangoproject.com/en/stable/topics/db/models/
    """

    # objects = OrderManager() — подменяет стандартный менеджер Django.
    # Подмешивает методы из OrderQuerySet:
    #   Order.objects.for_user(user)
    #   Order.objects.with_items()
    #   Order.objects.pending()
    # 📖 https://docs.djangoproject.com/en/stable/topics/db/managers/#django.db.models.Manager.from_queryset
    objects = OrderManager()

    # ──────────────────────────────────────────────────────────────
    # Статус заказа
    # ──────────────────────────────────────────────────────────────
    status = models.CharField(
        verbose_name='Статус',
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
        db_index=True,
    )

    # ──────────────────────────────────────────────────────────────
    # Владелец заказа
    # ──────────────────────────────────────────────────────────────
    # user — FK к модели пользователя.
    # on_delete=PROTECT — нельзя удалить пользователя с заказами!
    #   Альтернатива (SET_NULL): пользователь «удалён», заказы остаются.
    #   Но PROTECT честнее: заставляет сначала архивировать/анонимизировать.
    #
    # related_name='orders' → user.orders.all() — все заказы пользователя.
    # settings.AUTH_USER_MODEL — 'users.User' (lazy reference).
    # 📖 https://docs.djangoproject.com/en/stable/ref/settings/#auth-user-model
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='orders',
        verbose_name='Пользователь',
    )

    # ──────────────────────────────────────────────────────────────
    # Номер заказа (публичный идентификатор)
    # ──────────────────────────────────────────────────────────────
    # unique=True → UniqueConstraint + B-tree индекс автоматически.
    # editable=False — нельзя изменить через admin/form (авто-генерация).
    # blank=True — разрешаем пустое значение при инициализации
    #   (save() заполнит его автоматически).
    #
    # ПОЧЕМУ НЕ db_index=True: unique=True уже создаёт индекс.
    order_number = models.CharField(
        verbose_name='Номер заказа',
        max_length=20,
        unique=True,
        editable=False,
        blank=True,
    )

    # Внутренний числовой счётчик номера заказа.
    # Храним отдельно от order_number, чтобы не парсить строку 'ORD-000123'
    # при разборе номера (аналитика, интеграции, отладка).
    # Значение выдаёт PostgreSQL SEQUENCE (см. allocate_order_number()).
    _order_number_seq = models.PositiveBigIntegerField(
        editable=False,
        db_index=True,
        default=0,
    )

    # ──────────────────────────────────────────────────────────────
    # Ссылка на корзину (опциональная)
    # ──────────────────────────────────────────────────────────────
    # cart — ссылка на корзину, из которой создан заказ.
    # null=True, blank=True — заказ может быть создан вручную (admin, phone).
    # on_delete=SET_NULL — корзина может быть удалена/деактивирована
    #   после оформления заказа. Заказ остаётся.
    #
    # ПОЧЕМУ НЕ CASCADE: удаление корзины ≠ удаление заказа.
    #   Корзина — временный объект, заказ — постоянный.
    #   CASCADE → удаление корзины удалит заказ — катастрофа!
    cart = models.ForeignKey(
        'cart.Cart',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
        verbose_name='Корзина (источник)',
    )

    # ──────────────────────────────────────────────────────────────
    # SNAPSHOT АДРЕСА ДОСТАВКИ (копия полей из Address)
    # ──────────────────────────────────────────────────────────────
    # ПОЧЕМУ КОПИЯ, А НЕ FK:
    #   Пользователь может:
    #     1. Изменить улицу в адресе → если FK, все заказы изменятся
    #     2. Удалить адрес → если CASCADE, заказы удалятся
    #     3. Добавить новый дефолтный → старый адрес «отвалится»
    #   Копия полей = snapshot на момент оформления → неизменность данных.
    #
    # Это стандартный паттерн e-commerce: «Order Address Snapshot».
    # Amazon, Ozon, Wildberries — все копируют адрес в заказ.
    #
    # 📖 https://martinfowler.com/eaaDev/EventSourcing.html (аналог: immutable events)

    recipient_name = models.CharField(
        verbose_name='ФИО получателя',
        max_length=200,
    )
    country = models.CharField(
        verbose_name='Страна',
        max_length=100,
        default='Россия',
    )
    region = models.CharField(
        verbose_name='Регион / область',
        max_length=100,
        blank=True,
        default='',
    )
    city = models.CharField(
        verbose_name='Город',
        max_length=100,
    )
    street = models.CharField(
        verbose_name='Улица, дом, квартира',
        max_length=300,
    )
    postal_code = models.CharField(
        verbose_name='Почтовый индекс',
        max_length=20,
        blank=True,
        default='',
    )

    # ──────────────────────────────────────────────────────────────
    # Суммы заказа (Decimal — точность до копеек)
    # ──────────────────────────────────────────────────────────────
    # subtotal — сумма цен за товары (без доставки).
    # = Σ(order_item.unit_price × order_item.quantity)
    #
    # max_digits=12, decimal_places=2 → до 99 999 999 999.99
    # MinValueValidator(0) — сумма не может быть отрицательной.
    #
    # ПОЧЕМУ НЕ ВЫЧИСЛЯЕМОЕ ПОЛЕ:
    #   Вычисляемые поля (generated column) пересчитываются при каждом запросе.
    #   Но сумма заказа — историческая величина: если цену товара изменят
    #   после оформления → subtotal НЕ должен измениться!
    #   Поэтому храним «замороженную» сумму в DecimalField.
    #
    # 📖 https://docs.python.org/3/library/decimal.html
    subtotal = models.DecimalField(
        verbose_name='Сумма товаров',
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )

    # delivery_cost — стоимость доставки.
    # 0 = бесплатная доставка (подарок, акция, самовывоз).
    # null/blank не допускаются — доставка всегда имеет стоимость (даже 0).
    delivery_cost = models.DecimalField(
        verbose_name='Стоимость доставки',
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0'))],
    )

    # discount — сумма скидки (купон, промокод).
    # 0 = без скидки. Может увеличиваться при применении купона.
    # В будующих итерациях: связать с apps.discounts (Coupon model).
    discount = models.DecimalField(
        verbose_name='Скидка',
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0'))],
    )

    # total — итоговая сумма = subtotal + delivery_cost - discount.
    # РАСЧЁТ: всегда пересчитывается в OrderService перед save().
    # total ДОЛЖЕН быть ≥ MIN_ORDER_TOTAL (валидация в сервисе).
    #
    # ПОЧЕМУ НЕ PROPERTY:
    #   Property вычисляется на лету → нельзя проиндексировать.
    #   Нужен индекс для аналитики: «заказы с total > 10000₽».
    #   Хранимое поле + индекс → быстрый поиск.
    total = models.DecimalField(
        verbose_name='Итого',
        max_digits=12,
        decimal_places=2,
        db_index=True,
        validators=[MinValueValidator(Decimal('0'))],
    )

    # ──────────────────────────────────────────────────────────────
    # Примечания и причины отмены
    # ──────────────────────────────────────────────────────────────
    notes = models.TextField(
        verbose_name='Примечания к заказу',
        blank=True,
        default='',
        help_text='Комментарий пользователя к заказу.',
    )

    # cancellation_reason — причина отмены. Заполняется только при CANCELLED.
    # blank=True, default='' — для не-отменённых заказов поле пустое.
    # choices — список предопределённых причин (из constants.py).
    # ВАЛИДАЦИЯ: в OrderService.cancel() проверяем что reason не пустой.
    cancellation_reason = models.CharField(
        verbose_name='Причина отмены',
        max_length=30,
        blank=True,
        default='',
    )

    # cancelled_at — точное время отмены. null если заказ не отменён.
    # Зачем: аналитика «сколько времени прошло от создания до отмены».
    # db_index=True — частый запрос: «отменённые за последний месяц».
    cancelled_at = models.DateTimeField(
        verbose_name='Дата отмены',
        null=True,
        blank=True,
        db_index=True,
    )

    # confirmed_at — точное время подтверждения (оплаты).
    # null если заказ ещё не подтверждён (PENDING).
    confirmed_at = models.DateTimeField(
        verbose_name='Дата подтверждения',
        null=True,
        blank=True,
        db_index=True,
    )

    # delivered_at — точное время доставки. Терминальный таймстамп.
    # null если заказ ещё не доставлен.
    delivered_at = models.DateTimeField(
        verbose_name='Дата доставки',
        null=True,
        blank=True,
        db_index=True,
    )

    class Meta:
        verbose_name = 'Заказ'
        verbose_name_plural = 'Заказы'
        # По умолчанию — новые заказы первыми (по дате создания DESC).
        ordering = ('-created_at',)

        indexes = [
            # Составной индекс (user, status) — для запросов:
            #   «Все активные заказы пользователя»:
            #   Order.objects.filter(user=X, status__in=['pending', 'confirmed', ...])
            # 📖 https://www.postgresql.org/docs/current/indexes-multicolumn.html
            models.Index(
                fields=['user', 'status'],
                name='orders_user_status_idx',
            ),
            # Индекс по status — для админки и аналитики:
            #   «Сколько заказов в статусе PENDING?»
            #   Order.objects.filter(status='pending').count()
            models.Index(
                fields=['status'],
                name='orders_status_idx',
            ),
            # Составной индекс (status, created_at) — для management command
            # cleanup_stale_orders:
            #   Order.objects.filter(
            #       status='pending',
            #       created_at__lt=now() - 24h
            #   )
            models.Index(
                fields=['status', 'created_at'],
                name='orders_status_created_idx',
            ),
        ]

        constraints = [
            # ── CheckConstraint: total ≥ 0 ──
            # Защита от отрицательного total (баг в расчётах).
            # 📖 https://docs.djangoproject.com/en/stable/ref/models/constraints/#checkconstraint
            models.CheckConstraint(
        condition=Q(total__gte=Decimal('0')),
                name='order_total_non_negative',
            ),
            # ── CheckConstraint: subtotal ≥ 0 ──
            models.CheckConstraint(
        condition=Q(subtotal__gte=Decimal('0')),
                name='order_subtotal_non_negative',
            ),
            # ── CheckConstraint: delivery_cost ≥ 0 ──
            models.CheckConstraint(
        condition=Q(delivery_cost__gte=Decimal('0')),
                name='order_delivery_cost_non_negative',
            ),
            # ── CheckConstraint: discount ≥ 0 ──
            models.CheckConstraint(
        condition=Q(discount__gte=Decimal('0')),
                name='order_discount_non_negative',
            ),
        ]

    def __str__(self):
        """
        «ORD-000123 (pending) — ivan@example.com»

        Используется в:
          • Django Admin (список заказов)
          • shell: Order.objects.first() → «ORD-000001 (pending) — ...»
          • Логи: logger.info(f'Order: {order}')

        self.user_id — FK id (integer), без дополнительного SQL.
        self.user — доступ к объекту → SQL-запрос (N+1!).
        Поэтому используем user_id для проверки, getattr — для имени.
        """
        user_str = getattr(self.user, 'email', f'user#{self.user_id}')
        return f'{self.order_number} ({self.get_status_display()}) — {user_str}'

    @property
    def is_terminal(self) -> bool:
        """
        True если заказ в терминальном статусе (DELIVERED или CANCELLED).
        Терминальный = нельзя изменить, перейти в другой статус.
        """
        return self.status in (OrderStatus.DELIVERED, OrderStatus.CANCELLED)

    @property
    def full_address(self) -> str:
        """
        Полный адрес в одну строку: «Россия, Москва, ул. Тестовая, д. 1».

        Удобно для:
          • Печатных форм (накладные, чеки)
          • Email-уведомлений
          • API-ответа (одна строка вместо 5 полей)

        Почтовый индекс добавляется к улице без отдельной запятой:
          «ул. Тестовая, д. 1 (123456)» а не «ул. Тестовая, д. 1, (123456)»
        """
        parts = [self.country]
        if self.region:
            parts.append(self.region)
        parts.append(self.city)
        # Индекс склеиваем с улицей — без лишней запятой
        street_part = self.street
        if self.postal_code:
            street_part = f'{self.street} ({self.postal_code})'
        parts.append(street_part)
        return ', '.join(parts)

    def recalculate_total(self) -> None:
        """
        Пересчитывает subtotal и total на основе OrderItem.

        ВЫЗЫВАЕТСЯ:
          • При создании заказа (OrderService.create_from_cart)
          • При отмене (subtotal не меняется, но discount может)

        АЛГОРИТМ:
          subtotal = Σ(item.unit_price × item.quantity)
          total = subtotal + delivery_cost - discount

        НЕ сохраняет — вызывающий код должен вызвать save() сам.
        Причина: атомарность (recalculate + save в одной транзакции).

        📖 https://docs.djangoproject.com/en/stable/topics/db/transactions/
        """
        # aggregate() → один SQL-запрос вместо перебора items в Python.
        # SUM(unit_price * quantity) → точный Decimal-результат.
        from django.db.models import Sum, F

        result = self.items.aggregate(
            subtotal=Sum(F('unit_price') * F('quantity')),
        )
        self.subtotal = result['subtotal'] or Decimal('0.00')
        self.total = self.subtotal + self.delivery_cost - self.discount

    def save(self, *args, **kwargs):
        """
        Переопределённый save() — выдача order_number и _order_number_seq.

        АЛГОРИТМ (F-13 / PROD-010):
          Если order_number пустой:
            1. Взять следующее значение PostgreSQL SEQUENCE
               (allocate_order_number() → nextval('orders_order_number_seq'))
            2. Установить _order_number_seq = полученное значение
            3. Сформировать order_number: ORD-000001

        ПОЧЕМУ ЭТО БЕЗОПАСНО ПРИ КОНКУРЕНТНОМ СОЗДАНИИ:
          Номер выдаёт СУБД, а не приложение: nextval() атомарен, поэтому
          две параллельные транзакции не могут получить одно значение.
          Прежняя схема «MAX(_order_number_seq) + 1» была
          read-then-increment: параллельные заказы читали один MAX, второй
          INSERT падал на UNIQUE(order_number), и оформление завершалось
          ошибкой. UNIQUE(order_number) сохранён как последний рубеж
          инварианта уникальности.

        ПОЧЕМУ В save(), А НЕ В default=callable:
          default=func вызывается для КАЖДОГО create() — но func не может
          установить _order_number_seq на экземпляре (у неё нет доступа к self).
          save() имеет доступ к self и заполняет оба поля согласованно.

        ОДИН ПУТЬ ВЫДАЧИ:
          Номер выдается здесь для всех созданий заказа — OrderService,
          admin add, management commands, фабрики. Явно заданный
          order_number не перезаписывается.

        📖 https://docs.djangoproject.com/en/stable/ref/models/instances/#overriding-model-methods
        """
        if not self.order_number:
            self._order_number_seq, self.order_number = allocate_order_number(
                using=kwargs.get('using'),
            )
        super().save(*args, **kwargs)
