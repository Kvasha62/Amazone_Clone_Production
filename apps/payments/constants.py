# ────────────────────────────────────────────────────────────────────────
# apps/payments/constants.py — константы модуля платежей.
#
# Все магические числа и настройки платёжной системы в одном месте.
# Изменение лимита → правка одного файла → DRY.
#
# 📖 https://docs.djangoproject.com/en/stable/topics/settings/
# ────────────────────────────────────────────────────────────────────────

import re
from decimal import Decimal

# ────────────────────────────────────────────────────────────────────────
# СТАТУСЫ ПЛАТЕЖА
# ────────────────────────────────────────────────────────────────────────
#
# Жизненный цикл платежа:
#   PENDING   — создан, ожидает оплаты (пользователь на странице оплаты)
#   PROCESSING — платёж обрабатывается провайдером
#   SUCCEEDED  — оплата прошла успешно (Money received)
#   FAILED     — оплата отклонена (insufficient funds, fraud и т.д.)
#   CANCELLED  — платёж отменён (пользователь или система)
#   REFUNDED   — возврат средств (полный или частичный)
#
# FSM переходов:
#   PENDING → [PROCESSING, CANCELLED]
#   PROCESSING → [SUCCEEDED, FAILED, CANCELLED]
#   SUCCEEDED → [REFUNDED]
#   FAILED → []       (терминальный)
#   CANCELLED → []    (терминальный)
#   REFUNDED → []     (терминальный)

PAYMENT_STATUS_PENDING = 'pending'
PAYMENT_STATUS_PROCESSING = 'processing'
PAYMENT_STATUS_SUCCEEDED = 'succeeded'
PAYMENT_STATUS_FAILED = 'failed'
PAYMENT_STATUS_CANCELLED = 'cancelled'
PAYMENT_STATUS_REFUNDED = 'refunded'

# Допустимые переходы статусов платежа (FSM).
# Ключ — текущий статус, значение — список допустимых следующих статусов.
PAYMENT_STATUS_TRANSITIONS: dict[str, list[str]] = {
    PAYMENT_STATUS_PENDING: [
        PAYMENT_STATUS_PROCESSING,
        PAYMENT_STATUS_SUCCEEDED,  # Некоторые провайдеры мгновенно отвечают
        PAYMENT_STATUS_FAILED,     # Мгновенный отказ (невалидная карта)
        PAYMENT_STATUS_CANCELLED,
    ],
    PAYMENT_STATUS_PROCESSING: [
        PAYMENT_STATUS_SUCCEEDED,
        PAYMENT_STATUS_FAILED,
        PAYMENT_STATUS_CANCELLED,
    ],
    PAYMENT_STATUS_SUCCEEDED: [PAYMENT_STATUS_REFUNDED],
    PAYMENT_STATUS_FAILED: [],
    PAYMENT_STATUS_CANCELLED: [],
    PAYMENT_STATUS_REFUNDED: [],
}

# Терминальные статусы — из них нет переходов дальше.
PAYMENT_TERMINAL_STATUSES = frozenset({
    PAYMENT_STATUS_FAILED,
    PAYMENT_STATUS_CANCELLED,
    PAYMENT_STATUS_REFUNDED,
})

# ────────────────────────────────────────────────────────────────────────
# МЕТОДЫ ОПЛАТЫ
# ────────────────────────────────────────────────────────────────────────
# Поддерживаемые платёжные методы.
# В реальном проекте: карта, СБП, ЮMoney, SberPay и т.д.

PAYMENT_METHOD_CARD = 'card'
PAYMENT_METHOD_SBP = 'sbp'        # Система быстрых платежей
PAYMENT_METHOD_YOOMONEY = 'yoomoney'
PAYMENT_METHOD_SBERPAY = 'sberpay'
PAYMENT_METHOD_CASH = 'cash'       # Оплата при получении (наличные/карта курьеру)

PAYMENT_METHOD_CHOICES = (
    (PAYMENT_METHOD_CARD, 'Банковская карта'),
    (PAYMENT_METHOD_SBP, 'СБП'),
    (PAYMENT_METHOD_YOOMONEY, 'ЮMoney'),
    (PAYMENT_METHOD_SBERPAY, 'SberPay'),
    (PAYMENT_METHOD_CASH, 'При получении'),
)

# ────────────────────────────────────────────────────────────────────────
# ТИПЫ СОБЫТИЙ ПЛАТЕЖА (PaymentEvent)
# ────────────────────────────────────────────────────────────────────────
# Аудит-лог: каждое изменение состояния платежа фиксируется как событие.

PAYMENT_EVENT_CREATED = 'created'
PAYMENT_EVENT_STATUS_CHANGED = 'status_changed'
PAYMENT_EVENT_WEBHOOK_RECEIVED = 'webhook_received'
PAYMENT_EVENT_REFUND_INITIATED = 'refund_initiated'
PAYMENT_EVENT_REFUND_COMPLETED = 'refund_completed'
PAYMENT_EVENT_CANCELLED = 'cancelled'
PAYMENT_EVENT_CONFIRMED = 'confirmed'
PAYMENT_EVENT_CALLBACK_RECEIVED = 'callback_received'
PAYMENT_EVENT_ERROR = 'error'
# PROD-003: отказ провайдера выполнить возврат. Означает «возврат ещё не
# выполнен» — обязательство зафиксировано в Payment.refund_required_amount
# и будет повторено через retry_pending_refunds / команду.
PAYMENT_EVENT_REFUND_FAILED = 'refund_failed'
# PROD-003: платёж подтверждён, но подтверждение заказа не удалось
# (или заказ уже был в другом статусе). Событие делает расхождение
# платёж↔заказ наблюдаемым и доступным для реконсиляции.
PAYMENT_EVENT_ORDER_CONFIRM_FAILED = 'order_confirm_failed'

PAYMENT_EVENT_CHOICES = (
    (PAYMENT_EVENT_CREATED, 'Платёж создан'),
    (PAYMENT_EVENT_STATUS_CHANGED, 'Статус изменён'),
    (PAYMENT_EVENT_WEBHOOK_RECEIVED, 'Вебхук получен'),
    (PAYMENT_EVENT_REFUND_INITIATED, 'Возврат инициирован'),
    (PAYMENT_EVENT_REFUND_COMPLETED, 'Возврат завершён'),
    (PAYMENT_EVENT_REFUND_FAILED, 'Возврат не выполнен'),
    (PAYMENT_EVENT_CANCELLED, 'Платёж отменён'),
    (PAYMENT_EVENT_CONFIRMED, 'Платёж подтверждён'),
    (PAYMENT_EVENT_CALLBACK_RECEIVED, 'Callback получен'),
    (PAYMENT_EVENT_ORDER_CONFIRM_FAILED, 'Подтверждение заказа не удалось'),
    (PAYMENT_EVENT_ERROR, 'Ошибка'),
)

# ────────────────────────────────────────────────────────────────────────
# ЛИМИТЫ И НАСТРОЙКИ
# ────────────────────────────────────────────────────────────────────────

# Минимальная сумма платежа.
# Меньше 1₽ — бессмысленно (комиссия платёжки > сумма).
MIN_PAYMENT_AMOUNT = Decimal('1.00')

# Максимальная сумма платежа.
# Совпадает с MAX_ORDER_TOTAL — платёж не может превышать лимит заказа.
MAX_PAYMENT_AMOUNT = Decimal('99_999_999.99')

# Время жизни неоплачённого платежа (в часах).
# После этого — автоматически отменяется management command.
PAYMENT_PENDING_TTL_HOURS = 24

# Префикс номера платежа: PAY-000001.
PAYMENT_NUMBER_PREFIX = 'PAY'
PAYMENT_NUMBER_DIGITS = 6

# Максимальная длина note / description.
MAX_NOTE_LENGTH = 1000
MAX_EXTERNAL_ID_LENGTH = 200
MAX_PROVIDER_NAME_LENGTH = 50
MAX_REFUND_REASON_LENGTH = 500

# Throttling — лимит запросов к API платежей.
PAYMENT_USER_THROTTLE_RATE = '30/min'

# Имя провайдера по умолчанию (для тестов / mock).
DEFAULT_PAYMENT_PROVIDER = 'mock'

# ────────────────────────────────────────────────────────────────────────
# PAYMENT WEBHOOK — REPLAY PROTECTION (Issue #71 / API-01 F-6)
# ────────────────────────────────────────────────────────────────────────
#
# Транспортная защита webhook от replay-атак. HMAC-SHA256 теперь
# вычисляется НЕ только по raw body, а по канонической сборке:
#
#     signed_payload = timestamp || nonce || raw_body
#
# где timestamp/nonce — точные ASCII-строки из заголовков,
# raw_body — исходные байты request.body (без ре-сериализации).
#
# ДВА НЕЗАВИСИМЫХ УРОВНЯ ЗАЩИТЫ (не подменяют друг друга):
#   • Transport-level: timestamp + nonce + HMAC — повторная доставка
#     одного и того же подписанного запроса невозможна (freshness window
#     + одноразовый nonce).
#   • Business-level: уникальность Payment.external_id — повторное
#     бизнес-событие того же платежа идемпотентно.

# Имена HTTP-заголовков (контракт, см. docs/api/API_CONTRACT.md §11.3).
WEBHOOK_TIMESTAMP_HEADER = 'X-Webhook-Timestamp'
WEBHOOK_NONCE_HEADER = 'X-Webhook-Nonce'
WEBHOOK_SIGNATURE_HEADER = 'X-Webhook-Signature'

# Окно свежести timestamp: abs(servertime - webhook_timestamp) <= 300 с.
# Запросы старше 5 минут (и «слишком будущие» на > 5 минут) отклоняются.
WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS = 300

# Timestamp: Unix epoch, секунды, ASCII decimal integer (UTC).
# Без leading zeros (кроме одиночной '0'), не длиннее 20 цифр
# (защита от аномально длинных строк до int()).
WEBHOOK_TIMESTAMP_PATTERN = re.compile(r'(?:0|[1-9][0-9]{0,19})\Z')

# Nonce: непредсказуемый одноразовый идентификатор webhook.
# ASCII, [A-Za-z0-9_-], 1..128 символов — разумный потолок,
# произвольно огромные значения не принимаются.
WEBHOOK_NONCE_MAX_LENGTH = 128
WEBHOOK_NONCE_PATTERN = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')

# Signature: lowercase hex-дайджест HMAC-SHA256 (64 символа) —
# ровно то, что выдаёт hmac...hexdigest().
WEBHOOK_SIGNATURE_PATTERN = re.compile(r'[0-9a-f]{64}\Z')

# Retention nonce в БД. Nonce с webhook_timestamp=ts может быть
# повторно использован только пока (servertime - ts) <= tolerance,
# то есть пока ts >= servertime - tolerance. Удаление разрешено при
# ts < servertime - retention. retention = tolerance + 60 c запас —
# nonce гарантированно уже не может пройти проверку свежести.
WEBHOOK_NONCE_RETENTION_SECONDS = WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS + 60
