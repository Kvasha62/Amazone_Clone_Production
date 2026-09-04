# ────────────────────────────────────────────────────────────────────────
# apps/payments/tests/test_webhook_security.py — транспортная защита
# payment webhook: HMAC + timestamp + nonce + replay (Issue #71 / F-6).
#
# ПРОВЕРЯЕТ:
#   HMAC:
#     1.  valid timestamp + nonce + body + signature → accepted
#     2.  wrong signature → rejected
#     3.  modified body → rejected
#     4.  wrong secret → rejected
#     5.  missing secret → rejected
#     6.  missing signature → rejected
#   Timestamp:
#     7.  missing timestamp → rejected
#     8.  malformed timestamp → rejected
#     9.  non-integer timestamp → rejected
#     10. stale timestamp (> 5 мин) → rejected
#     11. future timestamp (> 5 мин) → rejected
#     12. timestamp на границе окна (±300 с) → accepted
#     13. timestamp за границей окна → rejected
#   Nonce:
#     14. missing nonce → rejected
#     15. malformed/oversized nonce → rejected
#     16. first use of nonce → accepted
#     17. second use of same nonce → rejected (replay)
#     18. same nonce + changed body/signature → rejected
#     19. same nonce + different external_id → rejected (transport layer)
#   Business idempotency:
#     20. same external_id + different valid nonce → бизнес-идемпотентность
#   Failure durability:
#     21–24. nonce зафиксирован → бизнес упал → повтор того же nonce → rejected
#   Concurrency:
#     25–28. два параллельных запроса с одним nonce → ровно один прошёл
#   Security leakage:
#     29–33. в rejection нет secret/signature/nonce/traceback, и replay
#            неотличим от прочих webhook-auth сбоев
#
# ПОДПИСЬ: единый помощник apps/payments/tests/webhook_helpers.py —
# подпись считается производственной функцией
# compute_webhook_signature (каноническая сборка ts || nonce || body).
# ────────────────────────────────────────────────────────────────────────

import json
import threading
import time
import uuid
from unittest import mock

from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from apps.orders.tests.factories import create_test_order, create_test_user
from apps.payments.models import PaymentEvent, PaymentWebhookNonce
from apps.payments.services.payment_service import PaymentService
from apps.payments.tests.factories import create_test_payment
from apps.payments.tests.webhook_helpers import (
    OMIT,
    WEBHOOK_SECRET,
    build_webhook_request,
    post_signed_webhook,
)


class WebhookBaseTests(TestCase):
    """Общий setup для webhook-тестов + сборка payload."""

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, status='processing',
        )
        self.client = APIClient()
        self.url = reverse('payments:payment-webhook')

    def _webhook_data(self, **overrides):
        """Стандартный webhook payload."""
        data = {
            'external_id': self.payment.external_id,
            'event_type': 'payment.succeeded',
            'status': 'succeeded',
        }
        data.update(overrides)
        return data


# ════════════════════════════════════════════════════════════════════════
# HMAC
# ════════════════════════════════════════════════════════════════════════

class WebhookHMACSignatureTests(WebhookBaseTests):
    """HMAC-SHA256 подпись: secret, timestamp || nonce || raw body."""

    # ── 1. Valid timestamp + nonce + body + signature → accepted ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_valid_signature_accepted(self):
        """Полностью валидный запрос (ts + nonce + подпись) → 200."""
        resp = post_signed_webhook(self.client, self.url, self._webhook_data())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    # ── 2. Wrong signature → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_wrong_signature_rejected(self):
        """Подпись не совпадает с HMAC(ts || nonce || body) → 403."""
        data = self._webhook_data()
        body, headers = build_webhook_request(data)
        # Правильного формата (64 hex), но чужого значения подпись.
        wrong_sig = '0' * 64
        resp = self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_WEBHOOK_TIMESTAMP=headers['HTTP_X_WEBHOOK_TIMESTAMP'],
            HTTP_X_WEBHOOK_NONCE=headers['HTTP_X_WEBHOOK_NONCE'],
            HTTP_X_WEBHOOK_SIGNATURE=wrong_sig,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_signature_from_another_payload_rejected(self):
        """Подпись, посчитанная по ЧУЖОМУ payload → 403."""
        from apps.payments.services.webhook_security import (
            compute_webhook_signature,
        )

        data = self._webhook_data()
        body = json.dumps(data).encode('utf-8')
        other_body = json.dumps({'foo': 'bar'}).encode('utf-8')
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        wrong_sig = compute_webhook_signature(WEBHOOK_SECRET, ts, nonce, other_body)
        resp = self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_WEBHOOK_TIMESTAMP=ts,
            HTTP_X_WEBHOOK_NONCE=nonce,
            HTTP_X_WEBHOOK_SIGNATURE=wrong_sig,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 3. Modified body → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_modified_body_rejected(self):
        """Подпись по исходному body, body изменён → 403.

        Заголовки (ts/nonce/signature) — валидный набор для
        ИСХОДНОГО тела; тело в запросе — другое → HMAC mismatch.
        """
        data = self._webhook_data()
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        # Валидные заголовки + подпись, посчитанная по data…
        _, headers = build_webhook_request(data, timestamp=ts, nonce=nonce)
        # …а отправляем ИЗМЕНЁННОЕ тело.
        modified = self._webhook_data(status='failed')
        resp = self.client.post(
            self.url,
            data=json.dumps(modified).encode('utf-8'),
            content_type='application/json',
            **headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 4. Wrong secret → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_wrong_secret_rejected(self):
        """Подпись посчитана с другим secret → 403."""
        data = self._webhook_data()
        resp = post_signed_webhook(
            self.client, self.url, data,
            secret='wrong-secret-key!!!!!!!!!!!',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 5. Missing secret → rejected (fail-closed) ──

    @override_settings(PAYMENT_WEBHOOK_SECRET='')
    def test_no_secret_configured_rejected(self):
        """PAYMENT_WEBHOOK_SECRET пуст → все webhook отклоняются (403)."""
        data = self._webhook_data()
        # Подпись «правильная» для какого-то секрета — всё равно 403.
        resp = post_signed_webhook(
            self.client, self.url, data, secret='some-secret',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 6. Missing signature → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_missing_signature_rejected(self):
        """Нет X-Webhook-Signature (но ts/nonce есть) → 403."""
        data = self._webhook_data()
        resp = post_signed_webhook(self.client, self.url, data, signature=OMIT)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── Валидная подпись + succeeded → платёж реально меняется ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_valid_signature_succeeded_transitions_payment(self):
        """Валидный webhook + status=succeeded → платёж SUCCEEDED."""
        data = self._webhook_data(status='succeeded')
        resp = post_signed_webhook(self.client, self.url, data)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'succeeded')
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'succeeded')

    # ── Не подписанный succeeded → платёж НЕ меняется ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_unsigned_succeeded_does_not_transition_payment(self):
        """Webhook без подписи (status=succeeded) → платёж PROCESSING."""
        data = self._webhook_data(status='succeeded')
        resp = post_signed_webhook(self.client, self.url, data, signature=OMIT)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'processing')


# ════════════════════════════════════════════════════════════════════════
# TIMESTAMP
# ════════════════════════════════════════════════════════════════════════

class WebhookTimestampTests(WebhookBaseTests):
    """X-Webhook-Timestamp: формат, свежесть (±300 с)."""

    # ── 7. Missing timestamp → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_missing_timestamp_rejected(self):
        """Нет X-Webhook-Timestamp (nonce + подпись есть) → 403."""
        data = self._webhook_data()
        resp = post_signed_webhook(self.client, self.url, data, timestamp=OMIT)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 8. Malformed timestamp → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_malformed_timestamp_rejected(self):
        """Не-цифровые/специальные форматы timestamp → 403."""
        data = self._webhook_data()
        for bad_ts in (
            'abc',                 # не числа
            '12.5',                # не integer
            '+123',                # со знаком
            '-123',                # отрицательный
            '1e5',                 # экспонента
            '1 234',               # пробел
            '',                    # пустой
            '01234567890123456789',  # leading zero (20 цифр)
            '9' * 21,              # слишком длинный (21 цифра)
            '123\n',               # управление
        ):
            with self.subTest(ts=bad_ts):
                resp = post_signed_webhook(
                    self.client, self.url, data, timestamp=bad_ts,
                )
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 9. Non-integer timestamp → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_non_integer_timestamp_rejected(self):
        """Дробный/нечисловой timestamp → 403."""
        data = self._webhook_data()
        now = int(time.time())
        for bad_ts in (str(now) + '.5', 'now', '0x1A2B'):
            with self.subTest(ts=bad_ts):
                resp = post_signed_webhook(
                    self.client, self.url, data, timestamp=bad_ts,
                )
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 10. Stale timestamp (> 5 мин) → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_stale_timestamp_rejected(self):
        """timestamp = now - 301 с → 403 (старше окна свежести)."""
        data = self._webhook_data()
        stale_ts = str(int(time.time()) - 301)
        resp = post_signed_webhook(
            self.client, self.url, data, timestamp=stale_ts,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 11. Future timestamp (> 5 мин) → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_future_timestamp_rejected(self):
        """timestamp = now + 302 с → 403 («слишком будущий»).

        +302, а не +301: серверное время растёт, и на границе
        секунды |now - ts| могло бы упасть ровно в 300 (граница
        окна). +302 гарантирует отклонение без race.
        """
        data = self._webhook_data()
        future_ts = str(int(time.time()) + 302)
        resp = post_signed_webhook(
            self.client, self.url, data, timestamp=future_ts,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 12. Timestamp exactly inside allowed boundary → accepted ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_timestamp_at_future_boundary_accepted(self):
        """timestamp = now + 300 с (граница окна) → 200.

        Берём «будущую» границу: серверное время только растёт,
        поэтому abs(now - ts) <= 300 гарантированно сохранится
        (тест детерминирован, без race на секундных границах).
        """
        data = self._webhook_data()
        boundary_ts = str(int(time.time()) + 300)
        resp = post_signed_webhook(
            self.client, self.url, data, timestamp=boundary_ts,
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    # ── 13. Timestamp outside boundary → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_timestamp_outside_boundary_rejected(self):
        """timestamp за границей окна (now + 310 с) → 403."""
        data = self._webhook_data()
        outside_ts = str(int(time.time()) + 310)
        resp = post_signed_webhook(
            self.client, self.url, data, timestamp=outside_ts,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ════════════════════════════════════════════════════════════════════════
# NONCE
# ════════════════════════════════════════════════════════════════════════

class WebhookNonceTests(WebhookBaseTests):
    """X-Webhook-Nonce: формат/длина, одноразовость (replay)."""

    # ── 14. Missing nonce → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_missing_nonce_rejected(self):
        """Нет X-Webhook-Nonce (timestamp + подпись есть) → 403."""
        data = self._webhook_data()
        resp = post_signed_webhook(self.client, self.url, data, nonce=OMIT)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 15. Malformed/oversized nonce → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_malformed_or_oversized_nonce_rejected(self):
        """Недопустимый формат/длина nonce → 403."""
        data = self._webhook_data()
        for bad_nonce in (
            'x' * 129,             # oversized (> 128)
            'nonce with space',    # пробел
            'non/ce',              # недопустимый символ
            'non.ce',              # точка
            'юникод-nonce',        # не ASCII
            '',                    # пустой
        ):
            with self.subTest(nonce=bad_nonce[:16]):
                resp = post_signed_webhook(
                    self.client, self.url, data, nonce=bad_nonce,
                )
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 16. First use of nonce → accepted ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_first_use_of_nonce_accepted(self):
        """Свежий nonce → 200, nonce зафиксирован в БД."""
        data = self._webhook_data()
        nonce = uuid.uuid4().hex
        resp = post_signed_webhook(self.client, self.url, data, nonce=nonce)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(
            PaymentWebhookNonce.objects.filter(nonce=nonce).exists(),
        )

    # ── 17. Second use of same nonce → rejected (replay) ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_second_use_of_same_nonce_rejected(self):
        """Повтор ТОГО ЖЕ запроса (ts/nonce/подпись) → 403 (replay)."""
        data = self._webhook_data()
        nonce = uuid.uuid4().hex
        ts = str(int(time.time()))

        first = post_signed_webhook(
            self.client, self.url, data, timestamp=ts, nonce=nonce,
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        # Тот же заголовок-набор → повтор → отклонение.
        second = post_signed_webhook(
            self.client, self.url, data, timestamp=ts, nonce=nonce,
        )
        self.assertEqual(second.status_code, status.HTTP_403_FORBIDDEN)

    # ── 18. Same nonce with changed body/signature → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_same_nonce_changed_body_rejected(self):
        """Тот же nonce, ДРУГОЕ тело (и подпись под него) → 403.

        Nonce одноразов: даже с валидной подписью под новое тело
        повторный nonce отклоняется transport-слоем.
        """
        data = self._webhook_data()
        nonce = uuid.uuid4().hex
        ts = str(int(time.time()))

        first = post_signed_webhook(
            self.client, self.url, data, timestamp=ts, nonce=nonce,
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        # Новое тело (status=failed) + ВЕРНАЯ подпись под новое тело,
        # но тот же nonce → replay → 403.
        modified = self._webhook_data(status='failed')
        second = post_signed_webhook(
            self.client, self.url, modified, timestamp=ts, nonce=nonce,
        )
        self.assertEqual(second.status_code, status.HTTP_403_FORBIDDEN)

    # ── 19. Same nonce with different external_id → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_same_nonce_different_external_id_rejected(self):
        """Тот же nonce, другой external_id → 403 (transport layer).

        Транспортная защита проверяется ДО бизнес-логики: смена
        external_id не «обнуляет» использованный nonce.
        """
        from apps.payments.tests.factories import create_test_payment

        other_payment = create_test_payment(
            self.order, self.user, status='processing',
        )
        nonce = uuid.uuid4().hex
        ts = str(int(time.time()))

        # 1-й webhook: external_id=A (payment), nonce N → 200.
        data_a = self._webhook_data(external_id=self.payment.external_id)
        first = post_signed_webhook(
            self.client, self.url, data_a, timestamp=ts, nonce=nonce,
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        # 2-й webhook: external_id=B (other_payment), тот же nonce N,
        # верная подпись → replay → 403 (НЕ доходит до business).
        data_b = self._webhook_data(external_id=other_payment.external_id)
        second = post_signed_webhook(
            self.client, self.url, data_b, timestamp=ts, nonce=nonce,
        )
        self.assertEqual(second.status_code, status.HTTP_403_FORBIDDEN)

        # Бизнес второго платежа не затронут transport-отказом.
        other_payment.refresh_from_db()
        self.assertEqual(other_payment.status, 'processing')


# ════════════════════════════════════════════════════════════════════════
# BUSINESS IDEMPOTENCY (Payment.external_id)
# ════════════════════════════════════════════════════════════════════════

class WebhookBusinessIdempotencyTests(WebhookBaseTests):
    """Transport replay-защита НЕ подменяет бизнес-идемпотентность."""

    # ── 20. same external_id + different valid nonce → идемпотентно ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_same_external_id_different_nonce_stays_idempotent(self):
        """Тот же external_id, РАЗНЫЕ валидные nonce → бизнес идемпотентен.

        Это легитимная повторная ДОСТАВКА (провайдер переслал событие
        с новым nonce), а не replay: transport-защита пропускает, а
        Payment.external_id обеспечивает идемпотентную обработку.
        """
        data = self._webhook_data(status='succeeded')

        # 1-я доставка — новый nonce.
        resp1 = post_signed_webhook(self.client, self.url, data)
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)

        # 2-я доставка того же события — ДРУГОЙ nonce (та же подпись-схема).
        resp2 = post_signed_webhook(self.client, self.url, data)
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)

        # Платёж один раз SUCCEEDED, бизнес-состояние не «сломалось».
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'succeeded')
        self.assertEqual(self.payment.external_id, data['external_id'])


# ════════════════════════════════════════════════════════════════════════
# FAILURE DURABILITY (claim не откатывается с бизнес-транзакцией)
# ════════════════════════════════════════════════════════════════════════

class WebhookFailureDurabilityTests(WebhookBaseTests):
    """Nonce claim переживает сбой бизнес-обработки."""

    # ── 21–24. claim → бизнес упал → retry → rejected ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_nonce_kept_when_business_processing_raises(self):
        """21–24: nonce зафиксирован, business выбросил ошибку,
        повтор того же nonce → rejected, платёж не изменился."""
        data = self._webhook_data()
        nonce = uuid.uuid4().hex
        ts = str(int(time.time()))

        # 21: валидный nonce → claim. Бизнес выбрасывает RuntimeError.
        with mock.patch.object(
            PaymentService, 'handle_webhook',
            side_effect=RuntimeError('boom'),
        ):
            first = post_signed_webhook(
                self.client, self.url, data, timestamp=ts, nonce=nonce,
            )
        # Бизнес упал → 500 (не 200).
        self.assertEqual(first.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Nonce УЖЕ зафиксирован (claim в durable-транзакции).
        self.assertTrue(
            PaymentWebhookNonce.objects.filter(nonce=nonce).exists(),
        )

        # 23–24: повтор того же nonce → replay → 403.
        second = post_signed_webhook(
            self.client, self.url, data, timestamp=ts, nonce=nonce,
        )
        self.assertEqual(second.status_code, status.HTTP_403_FORBIDDEN)

        # Платёж так и не изменился (бизнес не выполнялся).
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'processing')

    # ── Bonus: 502-сценарий (сбой подтверждения заказа) ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_nonce_kept_after_502_order_confirm_failure(self):
        """Реальный production-сценарий: 502 (order confirm failed) →
        nonce остаётся использованным; повтор того же nonce → 403;
        легитимная повторная доставка с НОВЫМ nonce после восстановления
        → 200."""
        from apps.inventory.services.inventory_service import InventoryService
        from apps.payments.tests.test_order_confirmation_recovery import (
            _make_order_with_item,
        )

        order, variant = _make_order_with_item(quantity=5, stock_quantity=2)
        payment = create_test_payment(
            order, order.user, status='processing',
        )
        data = {
            'external_id': payment.external_id,
            'event_type': 'payment.succeeded',
            'status': 'succeeded',
        }
        nonce1 = uuid.uuid4().hex
        ts = str(int(time.time()))

        # 1-я попытка: не хватает стока → 502, бизнес откатился.
        first = post_signed_webhook(
            self.client, self.url, data, timestamp=ts, nonce=nonce1,
        )
        self.assertEqual(first.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertTrue(
            PaymentWebhookNonce.objects.filter(nonce=nonce1).exists(),
        )

        # Повтор ТОГО ЖЕ nonce → replay → 403.
        retry = post_signed_webhook(
            self.client, self.url, data, timestamp=ts, nonce=nonce1,
        )
        self.assertEqual(retry.status_code, status.HTTP_403_FORBIDDEN)

        # Восстановление стока + повторная доставка с НОВЫМ nonce → 200.
        InventoryService.restock(variant, 100)
        nonce2 = uuid.uuid4().hex
        second = post_signed_webhook(
            self.client, self.url, data, timestamp=ts, nonce=nonce2,
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'succeeded')


# ════════════════════════════════════════════════════════════════════════
# CONCURRENCY (race-safe claim)
# ════════════════════════════════════════════════════════════════════════

class WebhookNonceConcurrencyTests(TransactionTestCase):
    """Параллельные запросы с одним nonce → ровно один выигрывает.

    TransactionTestCase (а не TestCase): данные закоммичены, поэтому
    фоновые потоки (с собственными DB-соединениями) видят платёж и
    гоняются за INSERT nonce по-настоящему. Это проверяет race-safe
    фиксацию (UNIQUE-индекс + IntegrityError), а не «exists → create».
    """

    databases = {'default'}

    def setUp(self):
        self.user = create_test_user()
        self.order = create_test_order(self.user)
        self.payment = create_test_payment(
            self.order, self.user, status='processing',
        )
        self.url = reverse('payments:payment-webhook')

    # ── 25–28. два конкурентных запроса с одним nonce ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_concurrent_same_nonce_only_one_wins(self):
        """25–28: два параллельных валидных запроса с одним nonce.
        Ровно один → 200, другой → 403; бизнес-операция — один раз."""
        data = {
            'external_id': self.payment.external_id,
            'event_type': 'payment.succeeded',
            'status': 'succeeded',
        }
        nonce = uuid.uuid4().hex
        ts = str(int(time.time()))
        body, headers = build_webhook_request(data, timestamp=ts, nonce=nonce)

        results: list[int] = []
        barrier = threading.Barrier(2)

        def worker():
            from django.db import connections

            client = APIClient()
            try:
                barrier.wait()  # синхронизировать старт
                resp = client.post(
                    self.url, data=body, content_type='application/json',
                    **headers,
                )
                results.append(resp.status_code)
            finally:
                # DB-соединение потока (thread-local) держит session на
                # тестовой БД и ломает teardown; закрываем в своём потоке.
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        for t in threads:
            self.assertFalse(t.is_alive(), 'worker thread hung')

        # 25–27: ровно один прошёл, второй отклонён.
        self.assertEqual(sorted(results), [
            status.HTTP_200_OK,
            status.HTTP_403_FORBIDDEN,
        ])

        # 28: бизнес-операция выполнилась ровно один раз.
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'succeeded')
        webhook_events = PaymentEvent.objects.filter(
            payment=self.payment,
            event_type='webhook_received',
        ).count()
        self.assertEqual(webhook_events, 1)
        # Nonce зафиксирован ровно один раз.
        self.assertEqual(
            PaymentWebhookNonce.objects.filter(nonce=nonce).count(), 1,
        )


# ════════════════════════════════════════════════════════════════════════
# SECURITY LEAKAGE (rejection не раскрывает причин/данных)
# ════════════════════════════════════════════════════════════════════════

class WebhookSecurityLeakTests(WebhookBaseTests):
    """Security rejection: без secret/signature/nonce/traceback."""

    # ── 29–31. rejection не содержит secret/signature/nonce ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_rejection_contains_no_sensitive_data(self):
        """29–31: body 403 не содержит secret, signature, nonce."""
        data = self._webhook_data()
        nonce = uuid.uuid4().hex
        stale_ts = str(int(time.time()) - 301)
        # Запрос с stale-timestamp (валидная подпись, но «просрочен»).
        body, headers = build_webhook_request(
            data, timestamp=stale_ts, nonce=nonce,
        )
        resp = self.client.post(
            self.url, data=body, content_type='application/json',
            **headers,
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        text = json.dumps(resp.data, ensure_ascii=False)
        # 29: нет secret.
        self.assertNotIn(WEBHOOK_SECRET, text)
        # 30: нет signature.
        self.assertNotIn(headers['HTTP_X_WEBHOOK_SIGNATURE'], text)
        # 31: нет nonce.
        self.assertNotIn(nonce, text)

    # ── 32. rejection не содержит traceback ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_rejection_contains_no_traceback(self):
        """32: body 403 — чистый canonical envelope, без traceback."""
        data = self._webhook_data()
        resp = post_signed_webhook(self.client, self.url, data, signature=OMIT)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        text = json.dumps(resp.data, ensure_ascii=False).lower()
        self.assertNotIn('traceback', text)
        self.assertNotIn('exception', text)
        self.assertNotIn('internal server error', text)
        # Canonical envelope: есть error.code.
        self.assertEqual(resp.data['error']['code'], 'permission_denied')

    # ── 33. replay неотличим от прочих webhook-auth сбоев ──

    @override_settings(PAYMENT_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_replay_indistinguishable_from_other_auth_failures(self):
        """33: envelope replay-отказа == envelope любого другого
        webhook-auth отказа (код/сообщение/details идентичны)."""
        data = self._webhook_data()
        nonce = uuid.uuid4().hex
        ts = str(int(time.time()))

        # 1) валидный запрос → nonce потреблён.
        first = post_signed_webhook(
            self.client, self.url, data, timestamp=ts, nonce=nonce,
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        # 2) replay того же nonce → 403 (envelope A).
        replay = post_signed_webhook(
            self.client, self.url, data, timestamp=ts, nonce=nonce,
        )
        self.assertEqual(replay.status_code, status.HTTP_403_FORBIDDEN)

        # 3) другой тип сбоя (неверная подпись, свежий nonce) → 403 (B).
        other = post_signed_webhook(
            self.client, self.url, data, signature='0' * 64,
        )
        self.assertEqual(other.status_code, status.HTTP_403_FORBIDDEN)

        # Для клиента A и B ИДЕНТИЧНЫ (кроме request_id).
        self.assertEqual(replay.data['error'], other.data['error'])
        self.assertEqual(replay.data['error']['code'], 'permission_denied')
