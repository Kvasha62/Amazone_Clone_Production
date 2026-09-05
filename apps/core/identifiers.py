"""Cross-context identifier contract for API v1 (API-01 / F-8, issue #73).

Background
----------
API-01 documented gap **G-23** ("identifier drift across bounded contexts"):

* catalog product payloads serialize ``id`` as the product **UUID**, while
  ``id`` means an integer PK everywhere else;
* order URLs use the public ``order_number`` (``ORD-000001``) but
  ``POST /payments/``, ``POST /discounts/apply|remove/`` and
  ``POST /shipping/shipments/create/`` took the integer ``order_id``;
* shipment routes exposed the raw integer PK in the path, and the resource had
  no public identifier at all — only the internal ``internal_tracking`` field;
* reviews accepted both ``product_id`` (int PK) and ``product_uuid``;
* payment stored its ``PAY-`` number in a field named ``order_number``.

Frozen contract (F-8)
---------------------
1. **Order** — the public identifier is ``order_number`` (``ORD-000001``)
   in *paths and request bodies alike*.  The integer PK is internal.
2. **Shipment** — the public identifier is ``shipment_number``
   (``SHP-00000001``), a dedicated immutable column.  ``internal_tracking`` is
   internal and never serialized publicly; ``tracking_number`` is the
   **external** carrier code.  The integer PK is internal.
3. **Payment** — identity is ``payment_number`` (``PAY-000001``); the order
   reference is ``order_number``; ``external_id`` is provider-side only.
4. **Product** — the public identifier is the **UUID**.  Catalog payloads keep
   serializing it as ``id`` (that is normative, not a bug), and reviews
   reference products by ``product_id``, *typed as UUID* — there is no
   competing ``product_uuid`` field.
5. **One deprecation window, for ``order_id`` only.**  That request field keeps
   working but is deprecated; passing it together with ``order_number`` is a
   ``400``.  No other legacy integer reference is honoured: an integer shipment
   path segment (``404``) and an integer ``product_id`` (``400``) were never
   public identifiers, so accepting them would recreate the enumerable second
   addressing scheme this contract removes.  Responses always echo the public
   identifier.

This module holds the shared primitives so every bounded context resolves
those identifiers identically.
"""

from __future__ import annotations

import re
import uuid as uuid_module

from rest_framework import serializers

# ``ORD-`` + zero-padded sequence (apps.orders.constants.ORDER_NUMBER_PREFIX /
# ORDER_NUMBER_DIGITS).  Kept as a permissive "one or more digits" pattern so a
# sequence that grows past the padding width still matches.
ORDER_NUMBER_RE = re.compile(r'^ORD-[0-9]+$')

# ``SHP-`` + zero-padded sequence (apps.shipping.constants).
SHIPMENT_NUMBER_RE = re.compile(r'^SHP-[0-9]+$')

# Строго ASCII-цифры. НЕ ``str.isdigit()``: тот пропускает не-ASCII цифры
# (``'٤٢'.isdigit()`` → True, ``int('٤٢')`` → 42), из-за чего арабо-индийские
# цифры резолвились бы в тот же PK, что и ASCII-запись — лишний, ничем не
# оправданный способ адресовать один и тот же объект. Хуже того, ``isdigit()``
# пропускает и надстрочные цифры (``'²'``), на которых ``int()`` уже падает
# с ``ValueError`` → 500 вместо канонического 404.
ASCII_DIGITS_RE = re.compile(r'^[0-9]+$')

# Верхняя граница PostgreSQL ``bigint`` (Django BigAutoField). Значение выше
# не может существовать в БД, а psycopg отвергает его на уровне протокола
# (``NumericValueOutOfRange``) — то есть 500 вместо 404. Отсекаем заранее.
MAX_BIGINT_PK = 9223372036854775807

# Максимальная длина публичных идентификаторов — совпадает с max_length
# соответствующих полей модели (Order.order_number, Shipment.shipment_number,
# Payment.payment_number).
PUBLIC_IDENTIFIER_MAX_LENGTH = 20


def is_order_number(value: object) -> bool:
    """True, если ``value`` выглядит как публичный номер заказа ``ORD-000001``."""
    return bool(ORDER_NUMBER_RE.match(str(value or '')))


def is_shipment_number(value: object) -> bool:
    """True, если ``value`` выглядит как публичный номер отправления ``SHP-00000001``."""
    return bool(SHIPMENT_NUMBER_RE.match(str(value or '')))


def parse_legacy_pk(value: object) -> int | None:
    """Возвращает целочисленный PK из устаревшего строкового идентификатора.

    Принимает ТОЛЬКО строку из ASCII-цифр (``'42'`` → ``42``). Всё остальное —
    пустая строка, отрицательное число, не-ASCII цифры, надстрочные знаки,
    любой мусор — даёт ``None``; вызывающий код обязан превратить это в
    канонический 404, а не в 500.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= MAX_BIGINT_PK else None
    if not ASCII_DIGITS_RE.match(str(value or '')):
        return None
    parsed = int(value)
    if parsed <= 0 or parsed > MAX_BIGINT_PK:
        return None
    return parsed


def parse_uuid(value: object) -> uuid_module.UUID | None:
    """Возвращает ``UUID`` или ``None``, если значение не UUID.

    Не бросает исключение: вызывающий код сам решает, что делать с
    некорректным идентификатором (404 или 400).
    """
    if isinstance(value, uuid_module.UUID):
        return value
    try:
        return uuid_module.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class OrderReferenceSerializerMixin(serializers.Serializer):
    """Ссылка на заказ в теле запроса.

    КОНТРАКТ (F-8):
      • ``order_number`` — канонический публичный идентификатор (``ORD-000001``);
      • ``order_id`` — устаревший целочисленный PK, принимается ради
        обратной совместимости.

    Ровно одно из двух полей обязано присутствовать; указание обоих — 400,
    чтобы не было неоднозначности «какой из них выиграл».
    """

    order_number = serializers.CharField(
        required=False,
        max_length=PUBLIC_IDENTIFIER_MAX_LENGTH,
        help_text='Публичный номер заказа (ORD-000001). Канонический идентификатор.',
    )
    order_id = serializers.IntegerField(
        required=False,
        help_text='DEPRECATED: целочисленный PK заказа. Используйте order_number.',
    )

    def validate(self, data):
        data = super().validate(data)
        order_number = data.get('order_number')
        order_id = data.get('order_id')

        if order_number and order_id:
            raise serializers.ValidationError(
                'Укажите либо order_number, либо order_id (устар.), но не оба.',
            )
        if not order_number and not order_id:
            raise serializers.ValidationError(
                {'order_number': 'Обязательное поле.'},
            )
        if order_number and not is_order_number(order_number):
            raise serializers.ValidationError(
                {'order_number': 'Некорректный номер заказа. Формат: ORD-000001.'},
            )
        return data


def order_reference_filters(validated_data: dict) -> dict:
    """Строит ORM-фильтр по ссылке на заказ из провалидированных данных.

    ``{'order_number': 'ORD-000001'}`` → ``{'order_number': 'ORD-000001'}``
    ``{'order_id': 7}``                → ``{'pk': 7}``
    """
    order_number = validated_data.get('order_number')
    if order_number:
        return {'order_number': order_number}
    return {'pk': validated_data['order_id']}
