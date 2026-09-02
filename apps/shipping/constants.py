# ────────────────────────────────────────────────────────────────────────
# apps/shipping/constants.py — константы модуля доставки.
#
# Все магические числа и строки вынесены в один файл для DRY.
# Если нужно изменить лимит — меняете в одном месте.
#
# 📖 https://docs.djangoproject.com/en/stable/ref/models/fields/#enumeration-types
#
# ЧТО БУДЕТ, ЕСЛИ УДАЛИТЬ ФАЙЛ:
#   • ImportErrors везде: SHIPMENT_STATUS_TRANSITIONS, MAX_WEIGHT_KG, ...
#   • Модели не смогут создать TextChoices
#   • Сервисы не смогут валидировать лимиты
# ────────────────────────────────────────────────────────────────────────

from decimal import Decimal

# ────────────────────────────────────────────────────────────────────────
# ТИПЫ СПОСОБОВ ДОСТАВКИ
# ────────────────────────────────────────────────────────────────────────

# Способ доставки — как посылка добирается до клиента.
#
# COURIER — курьерская доставка до двери.
#   • Самый дорогой, но удобный для клиента
#   • Используется для локальных доставок (в пределах города)
#   • Пример: Яндекс.Доставка, СДЭК «Посылка дверь-дверь»
#
# PICKUP — самовывоз из пункта выдачи / постамата.
#   • Бесплатный или дешёвый (ПВЗ уже существует)
#   • Клиент сам приезжает → нет расходов на «последнюю милю»
#   • Пример: СДЭК ПВЗ, Ozon ПВЗ, Wildberries постамат
#
# POST — почтовая доставка (Почта России).
#   • Для удалённых населённых пунктов (нет ПВЗ)
#   • Дешёвая, но медленная (7-30 дней)
#   • Покрывает 100% территории страны
#
# EXPRESS — экспресс-доставка (в течение дня / 1-2 часа).
#   • Самый дорогой способ
#   • Используется для срочных заказов
#   • Пример: Яндекс.Лавка, СберМаркет Express
SHIPPING_TYPE_COURIER = 'courier'
SHIPPING_TYPE_PICKUP = 'pickup'
SHIPPING_TYPE_POST = 'post'
SHIPPING_TYPE_EXPRESS = 'express'

SHIPPING_TYPE_CHOICES = (
    (SHIPPING_TYPE_COURIER, 'Курьер'),
    (SHIPPING_TYPE_PICKUP, 'Самовывоз'),
    (SHIPPING_TYPE_POST, 'Почта'),
    (SHIPPING_TYPE_EXPRESS, 'Экспресс'),
)

# Человекочитаемые названия для API-документации
SHIPPING_TYPE_LABELS = {
    SHIPPING_TYPE_COURIER: 'Курьерская доставка до двери',
    SHIPPING_TYPE_PICKUP: 'Самовывоз из пункта выдачи',
    SHIPPING_TYPE_POST: 'Почтовая доставка',
    SHIPPING_TYPE_EXPRESS: 'Экспресс-доставка',
}

# ────────────────────────────────────────────────────────────────────────
# СТАТУСЫ ОТПРАВЛЕНИЯ (Finite State Machine)
# ────────────────────────────────────────────────────────────────────────
# Жизненный цикл отправления:
#
#   PREPARING → IN_TRANSIT → OUT_FOR_DELIVERY → DELIVERED
#       ↓           ↓              ↓
#   RETURNED    RETURNED       FAILED
#                   ↓
#               RETURNED
#
# PREPARING — «Собирается». Заказ передан на склад, идёт сборка.
# IN_TRANSIT — «В пути». Посылка передана в службу доставки, едет.
# OUT_FOR_DELIVERY — «У курьера». Посылка выдана курьеру для доставки.
# DELIVERED — «Доставлено». Терминальный (успешный).
# FAILED — «Не доставлено». Курьер не смог вручить (нет дома, отказ).
#   Может перейти обратно в IN_TRANSIT (повторная попытка).
# RETURNED — «Возврат». Терминальный (неуспешный). Посылка вернулась на склад.

SHIPMENT_PREPARING = 'preparing'
SHIPMENT_IN_TRANSIT = 'in_transit'
SHIPMENT_OUT_FOR_DELIVERY = 'out_for_delivery'
SHIPMENT_DELIVERED = 'delivered'
SHIPMENT_FAILED = 'failed'
SHIPMENT_RETURNED = 'returned'

SHIPMENT_STATUS_CHOICES = (
    (SHIPMENT_PREPARING, 'Собирается'),
    (SHIPMENT_IN_TRANSIT, 'В пути'),
    (SHIPMENT_OUT_FOR_DELIVERY, 'У курьера'),
    (SHIPMENT_DELIVERED, 'Доставлено'),
    (SHIPMENT_FAILED, 'Не доставлено'),
    (SHIPMENT_RETURNED, 'Возврат'),
)

# Допустимые переходы статусов отправления.
# Ключ = текущий статус, значение = список допустимых следующих статусов.
#
# PREPARING  → [IN_TRANSIT, RETURNED]       — передан в доставку или сразу возврат
# IN_TRANSIT → [OUT_FOR_DELIVERY, FAILED, RETURNED] — выдан курьеру / потерян / возврат
# OUT_FOR_DELIVERY → [DELIVERED, FAILED]    — доставлен или не удалось
# FAILED     → [IN_TRANSIT, RETURNED]       — повторная попытка или возврат
# DELIVERED  → []                           — терминальный
# RETURNED   → []                           — терминальный
SHIPMENT_STATUS_TRANSITIONS = {
    SHIPMENT_PREPARING: [SHIPMENT_IN_TRANSIT, SHIPMENT_RETURNED],
    SHIPMENT_IN_TRANSIT: [SHIPMENT_OUT_FOR_DELIVERY, SHIPMENT_FAILED, SHIPMENT_RETURNED],
    SHIPMENT_OUT_FOR_DELIVERY: [SHIPMENT_DELIVERED, SHIPMENT_FAILED],
    SHIPMENT_FAILED: [SHIPMENT_IN_TRANSIT, SHIPMENT_RETURNED],
    SHIPMENT_DELIVERED: [],
    SHIPMENT_RETURNED: [],
}

# Терминальные статусы (дальнейшие переходы невозможны)
SHIPMENT_TERMINAL_STATUSES = frozenset({
    SHIPMENT_DELIVERED,
    SHIPMENT_RETURNED,
})

# ────────────────────────────────────────────────────────────────────────
# ЛИМИТЫ
# ────────────────────────────────────────────────────────────────────────

# Максимальный вес одной посылки (кг).
# 30 кг — стандартный лимит для большинства служб доставки.
# Если заказ тяжелее — разбивается на несколько отправлений.
MAX_WEIGHT_KG = Decimal('30.000')

# Максимальная стоимость доставки (руб.).
# Защита от опечаток: менеджер ввёл 999999₽ вместо 999₽.
MAX_SHIPPING_COST = Decimal('99_999.99')

# Максимальная длина трек-номера (символов).
# Треки разных служб: СДЭК ~16, Почта России ~14, DHL ~10.
MAX_TRACKING_NUMBER_LENGTH = 50

# Максимальная длина названия зоны / способа доставки
MAX_NAME_LENGTH = 200

# Максимальная длина кода зоны
MAX_ZONE_CODE_LENGTH = 50

# Максимальная длина адреса пункта выдачи
MAX_PICKUP_ADDRESS_LENGTH = 500

# ────────────────────────────────────────────────────────────────────────
# НАСТРОЙКИ БЕСПЛАТНОЙ ДОСТАВКИ
# ────────────────────────────────────────────────────────────────────────

# Сумма заказа, начиная с которой доставка бесплатная.
# При free_shipping_threshold = 5000, заказ на 5000₽+ → доставка 0₽.
# Decimal('0') = бесплатная доставка недоступна (всегда платная).
DEFAULT_FREE_SHIPPING_THRESHOLD = Decimal('5000.00')

# Цена доставки, когда для адреса не определена зона или в зоне нет
# активного способа доставки: тарифов нет → платить не за что.
# Это серверная константа расчёта (F-08): она не читается из запроса,
# поэтому клиент не может «выбрать» себе бесплатную доставку.
NO_DELIVERY_CHARGE = Decimal('0.00')

# ────────────────────────────────────────────────────────────────────────
# ПРЕФИКС ТРЕК-НОМЕРА
# ────────────────────────────────────────────────────────────────────────

# Внутренний трек-номер: SHP-{8 цифр}
# Формат: SHP-00000001
SHIPMENT_TRACKING_PREFIX = 'SHP'
SHIPMENT_TRACKING_DIGITS = 8
