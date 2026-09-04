# ────────────────────────────────────────────────────────────────────────
# apps/payments/services/webhook_security.py — транспортная защита
# payment webhook (Issue #71 / API-01 F-6).
#
# РЕШАЕМАЯ УЯЗВИМОСТЬ (replay):
#   До изменений подпись вычислялась только по request.body.
#   Корректно подписанный перехваченный запрос можно было повторять
#   неограниченное время. Теперь подпись покрывает timestamp и nonce:
#
#       signed_payload = timestamp || nonce || raw_body
#
#   • timestamp (X-Webhook-Timestamp) — Unix epoch, секунды, UTC;
#     окно свежести abs(now - timestamp) <= 300 с;
#   • nonce (X-Webhook-Nonce) — одноразовый; сервер фиксирует его в
#     PaymentWebhookNonce в отдельной (durable) транзакции ДО
#     бизнес-обработки;
#   • signature (X-Webhook-Signature) — lowercase hex
#     HMAC-SHA256(secret, signed_payload), сравнение timing-safe.
#
# КРИТИЧЕСКОЕ ТРЕБОВАНИЕ (durable claim):
#   Фиксация nonce не должна откатываться вместе с бизнес-транзакцией.
#   claim_webhook_nonce() выполняется в СОБСТВЕННОЙ транзакции
#   (transaction.atomic без внешнего atomic-блока — view не открывает
#   транзакцию), т.е.:
#
#       транзакция A: claim nonce          → COMMIT
#       транзакция B: PaymentService.handle_webhook() (свой atomic)
#
#   Если B откатывается (сбой обработки), nonce ОСТАЁТСЯ
#   использованным: повтор того же webhook → REJECT (replay).
#
# 🔴 НИКОГДА не логируем secret, signature, nonce или timestamp.
# ────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import hashlib
import hmac
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.payments.constants import (
    WEBHOOK_NONCE_PATTERN,
    WEBHOOK_SIGNATURE_PATTERN,
    WEBHOOK_TIMESTAMP_PATTERN,
    WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS,
)
from apps.payments.models import PaymentWebhookNonce

logger = logging.getLogger(__name__)


# ==============================================================\
# ВАЛИДАЦИЯ ЗАГОЛОВКОВ
# ==============================================================\

def is_valid_webhook_timestamp(value: str) -> bool:
    """
    Валиден ли X-Webhook-Timestamp по ФОРМАТУ.

    Формат: ASCII decimal integer — Unix epoch, секунды (UTC),
    без leading zeros (кроме одиночной '0'), не длиннее 20 цифр.
    """
    if not isinstance(value, str):
        return False
    return WEBHOOK_TIMESTAMP_PATTERN.fullmatch(value) is not None


def is_valid_webhook_nonce(value: str) -> bool:
    """
    Валиден ли X-Webhook-Nonce по формату/длине.

    Формат: [A-Za-z0-9_-], 1..128 символов. Произвольно огромные
    или не-ASCII значения не принимаются.
    """
    if not isinstance(value, str):
        return False
    return WEBHOOK_NONCE_PATTERN.fullmatch(value) is not None


def is_valid_webhook_signature_format(value: str) -> bool:
    """
    Валидна ли X-Webhook-Signature по формату.

    Формат: lowercase hex-дайджест HMAC-SHA256 — ровно 64 символа
    [0-9a-f]. (Точное сравнение значения — за сравнение HMAC.)
    """
    if not isinstance(value, str):
        return False
    return WEBHOOK_SIGNATURE_PATTERN.fullmatch(value) is not None


def is_fresh_webhook_timestamp(
    webhook_timestamp: int,
    *,
    now_epoch: int | None = None,
) -> bool:
    """
    Свеж ли timestamp в пределах окна (Issue #71).

    Окно: abs(server_now - webhook_timestamp) <= 300 секунд.
    И просроченные (> 5 минут назад), и «слишком будущие»
    (> 5 минут вперёд) запросы отклоняются.
    """
    if now_epoch is None:
        now_epoch = int(timezone.now().timestamp())
    return abs(now_epoch - webhook_timestamp) <= WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS


# ==============================================================\
# ПОДПИСЬ (canonical payload)
# ==============================================================\

def compute_webhook_signature(
    secret: str,
    timestamp: str,
    nonce: str,
    raw_body: bytes,
) -> str:
    """
    Вычисляет X-Webhook-Signature — ЭТО ЕДИНСТВЕННЫЙ источник
    канонической сборки подписываемых данных.

    signed_payload =
        timestamp.encode('ascii')   (точная строка заголовка)
      + nonce.encode('ascii')       (точная строка заголовка)
      + raw_body                    (исходные байты request.body)

    🔴 raw_body используется как есть: без JSON re-serialization,
    normalization или изменения whitespace. НЕ подписываем
    request.data / сериализованный JSON — только raw bytes.

    Результат — lowercase hex (hmac SHA-256 hexdigest).
    Тестовые помощники обязаны вызывать именно эту функцию
    (см. apps/payments/tests/webhook_helpers.py), чтобы подпись
    в тестах была побайтово идентична production.
    """
    signed_payload = (
        timestamp.encode('ascii') + nonce.encode('ascii') + raw_body
    )
    return hmac.new(
        secret.encode('utf-8'),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()


# ==============================================================\
# АТОМАРНАЯ (race-safe) ФИКСАЦИЯ NONCE
# ==============================================================\

def claim_webhook_nonce(nonce: str, webhook_timestamp: int) -> bool:
    """
    Атомарно фиксирует nonce в собственной durable-транзакции.

    ВОЗВРАЩАЕТ:
      True  — nonce зафиксирован впервые (этот запрос владеет nonce)
      False — nonce уже использован (replay) → запрос отклоняется

    RACE-SAFETY:
      Фиксация — INSERT с нарушением UNIQUE-индекса как детектором
      дубля. Недостаточно «if exists() → reject; create()» — такой
      код уязвим при параллельных запросах (оба запроса видят
      «не существует» и оба создают). Здесь уникальность
      гарантирована БД: ровно один INSERT коммитится, все остальные
      получают IntegrityError.

    DURABILITY (не откатывается с бизнес-транзакцией):
      Блок transaction.atomic() — это ОТДЕЛЬНАЯ транзакция A
      (view не открывает внешний atomic-блок, поэтому в production
      блок — реальная автономная транзакция, коммитится сразу).
      Бизнес-обработка (PaymentService.handle_webhook) идёт потом
      в своей транзакции B. Если B откатывается — nonce остаётся
      использованным, повтор webhook отклоняется.

    ВАЖНО (PostgreSQL): try/except ОБНИМАЕТ атомарный блок, а не
      наоборот. Нарушение constraint прерывает текущую PG-транзакцию;
      если ловить IntegrityError ВНУТРИ блока, соединение останется в
      aborted-состоянии. Здесь исключение уходит из блока — Django
      откатывает savepoint/транзакцию и чистит соединение, затем мы
      перехватываем ошибку и возвращаем False.

    🔴 Не логируем значение nonce.
    """
    try:
        with transaction.atomic():
            PaymentWebhookNonce.objects.create(
                nonce=nonce,
                webhook_timestamp=webhook_timestamp,
            )
    except IntegrityError:
        # Nonce уже существует — повторное использование (replay).
        # Не логируем сам nonce (см. модуль-комментарий).
        return False
    return True
