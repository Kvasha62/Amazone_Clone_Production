# ────────────────────────────────────────────────────────────────────────
# apps/payments/tests/webhook_helpers.py — EДИНЫЙ помощник для тестов
# payment webhook (Issue #71 / API-01 F-6).
#
# ПОЧЕМУ ОДИН МОДУЛЬ:
#   Подпись webhook вычисляется производственной функцией
#   apps.payments.services.webhook_security.compute_webhook_signature
#   (каноническая сборка timestamp || nonce || raw_body). Тесты
#   ИМПОРТИРУЮТ эту функцию — дублирование HMAC-конструкции в тестах
#   запрещено: canonical payload в тестах побайтово идентичен
#   production.
#
# ИСПОЛЬЗОВАНИЕ (все тестовые файлы):
#   from apps.payments.tests.webhook_helpers import (
#       WEBHOOK_SECRET, OMIT, build_webhook_request, post_signed_webhook,
#   )
#
#   # Валидный запрос (timestamp «сейчас», свежий nonce, верная подпись):
#   resp = post_signed_webhook(client, url, data)
#
#   # Явный контроль параметров (replay, stale, wrong secret …):
#   body, headers = build_webhook_request(data, timestamp=ts, nonce=nonce)
#   resp = client.post(url, data=body, content_type='application/json',
#                      **headers)
#
# НЕГАТИВНЫЕ СЦЕНАРИИ (overrides в build_webhook_request):
#   timestamp=OMIT    — без X-Webhook-Timestamp
#   nonce=OMIT        — без X-Webhook-Nonce
#   signature=OMIT    — без X-Webhook-Signature
#   signature='dead…' — своя подпись (wrong secret / чужой body)
#   timestamp='abc'   — malformed timestamp (нестроковое значение
#                       передаётся как есть, str()-ится)
#   nonce='x' * 200   — oversized/malformed nonce
#   timestamp=str(int(time.time()) - 301) — stale (> 5 минут)
# ────────────────────────────────────────────────────────────────────────

import json
import time
import uuid

from apps.payments.services.webhook_security import compute_webhook_signature

# Секрет по умолчанию для тестов webhook (не секрет: test-only).
WEBHOOK_SECRET = 'test-webhook-secret-key-32bytes!!'

# Sentinel: «заголовок не отправлять».
# (По умолчанию — валидное значение: timestamp=«сейчас»,
# nonce=uuid4().hex, signature=production-функция.)
OMIT = object()


def _json_bytes(data: dict) -> bytes:
    """Сериализует данные в JSON-байты (детерминированно)."""
    return json.dumps(data).encode('utf-8')


def build_webhook_request(
    data: dict | bytes,
    *,
    secret: str = WEBHOOK_SECRET,
    timestamp: str | int = None,
    nonce: str = None,
    signature: str = None,
) -> tuple[bytes, dict]:
    """
    Строит webhook-запрос: (raw_body, headers).

    headers — kwargs для client.post():
      HTTP_X_WEBHOOK_TIMESTAMP / HTTP_X_WEBHOOK_NONCE /
      HTTP_X_WEBHOOK_SIGNATURE.

    АРГУМЕНТЫ:
      data        — dict (сериализуется в JSON-байты) либо готовый
                    raw body (bytes). Подписываются именно эти байты.
      secret      — секрет для подписи (default: WEBHOOK_SECRET).
      timestamp   — int/str epoch seconds. None (default) → «сейчас».
                    OMIT → заголовок не отправляется.
      nonce       — str. None (default) → новый uuid4().hex.
                    OMIT → заголовок не отправляется.
      signature   — str. None (default) → вычисляется через
                    production-функцию compute_webhook_signature по
                    канону (ts || nonce || raw_body). Передайте свою
                    для негативных сценариев (wrong secret, чужой
                    body); OMIT → заголовок не отправляется.
    """
    body = _json_bytes(data) if isinstance(data, dict) else bytes(data)

    # Фактические значения заголовков (None → сгенерировать default;
    # OMIT → не отправляем).
    ts_str = None if timestamp is None else str(timestamp)
    if ts_str is None:
        ts_str = str(int(time.time()))
    ts_omitted = timestamp is OMIT

    nz = None if nonce is None else str(nonce)
    if nz is None:
        nz = uuid.uuid4().hex
    nonce_omitted = nonce is OMIT

    headers: dict = {}
    if not ts_omitted:
        headers['HTTP_X_WEBHOOK_TIMESTAMP'] = ts_str
    if not nonce_omitted:
        headers['HTTP_X_WEBHOOK_NONCE'] = nz

    # Подпись: None (default) → вычисляется через production-функцию
    # по канону (ts_str || nz || body — точные строки заголовков,
    # которые увидит сервер); str → используется как есть;
    # OMIT → заголовок не отправляется.
    #
    # production вычисляет подпись только ПОСЛЕ форматных проверок
    # ts/nonce (обе должны быть ASCII). Если в негативном сценарии
    # nonce/timestamp не-ASCII, production отклонит запрос форматной
    # проверкой ДО подписи — поэтому подпись считать бессмысленно:
    # просто не отправляем заголовок (форматная проверка сработает).
    if signature is not OMIT:
        if signature is None:
            try:
                ts_str.encode('ascii')
                nz.encode('ascii')
                signature = compute_webhook_signature(secret, ts_str, nz, body)
            except UnicodeEncodeError:
                signature = OMIT  # отклонится форматной проверкой
        if signature is not OMIT:
            headers['HTTP_X_WEBHOOK_SIGNATURE'] = str(signature)

    return body, headers


def post_signed_webhook(
    client,
    url: str,
    data: dict | bytes,
    *,
    secret: str = WEBHOOK_SECRET,
    timestamp: str | int = None,
    nonce: str = None,
    signature: str = None,
    content_type: str = 'application/json',
):
    """
    POST webhook с валидной подписью (или заданными overrides).

    По умолчанию — полностью валидный запрос: свежий timestamp,
    свежий nonce, подпись = production-функция. Возвращает Response.
    """
    body, headers = build_webhook_request(
        data,
        secret=secret,
        timestamp=timestamp,
        nonce=nonce,
        signature=signature,
    )
    return client.post(
        url,
        data=body,
        content_type=content_type,
        **headers,
    )
