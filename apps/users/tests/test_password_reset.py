# ────────────────────────────────────────────────────────────────────────
# apps/users/tests/testF/test_password_reset.py — password reset security tests.
#
# ПРОВЕРЯЕТ:
#   1. Reset request for existing user → correct response
#   2. Reset request for unknown email → does not reveal existence
#   3. Reset confirm with valid token → successfully changes password
#   4. Old password after reset → does not work
#   5. New password after reset → works
#   6. Invalid token → rejected
#   7. Expired/used token → rejected
#   8. Token does not appear in logs
#   9. Registration validation error does not log password
# ────────────────────────────────────────────────────────────────────────

import logging
from io import StringIO
from unittest import mock

from redis.exceptions import ConnectionError as RedisConnectionError

from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from rest_framework import status
from rest_framework.test import APIClient

from apps.users.api_views.password_reset_views import PasswordResetRequestView
from apps.users.models import User
from apps.orders.tests.factories import create_test_user


class PasswordResetRequestTests(TestCase):
    """Тесты POST /api/v1/auth/password-reset/."""

    def setUp(self):
        self.user = create_test_user(email='reset@example.com', password='OldPass123!')
        self.client = APIClient()
        self.url = reverse('users:password-reset')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
    def test_reset_request_existing_user(self):
        """Reset request for existing user → 200."""
        with mock.patch(
            'apps.notifications.tasks.send_password_reset_email.delay',
        ):
            resp = self.client.post(
                self.url, {'email': 'reset@example.com'}, format='json',
            )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('существует', resp.data['detail'])

    def test_reset_request_unknown_email(self):
        """Reset request for unknown email → same 200 (does not reveal existence)."""
        resp = self.client.post(self.url, {'email': 'nonexistent@example.com'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('существует', resp.data['detail'])

    def test_reset_request_invalid_email(self):
        """Invalid email format → 400."""
        resp = self.client.post(self.url, {'email': 'not-an-email'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_broker_failure_falls_back_to_sync(self):
        """F-17: expected Celery/Redis broker failures keep the sync fallback."""
        with mock.patch(
            'apps.notifications.tasks.send_password_reset_email.delay',
            side_effect=RedisConnectionError(
                'Error 111 connecting to broker. Connection refused.',
            ),
        ), mock.patch.object(
            PasswordResetRequestView,
            '_send_reset_email_sync',
        ) as sync_send:
            resp = self.client.post(
                self.url,
                {'email': 'reset@example.com'},
                format='json',
            )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        sync_send.assert_called_once()

    def test_unexpected_celery_error_not_masked(self):
        """F-17: arbitrary RuntimeError/programming failures must propagate."""
        with mock.patch(
            'apps.notifications.tasks.send_password_reset_email.delay',
            side_effect=RuntimeError('unexpected programming error'),
        ):
            resp = self.client.post(
                self.url,
                {'email': 'reset@example.com'},
                format='json',
            )
        self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(resp.data['error']['code'], 'server_error')
        self.assertNotIn('unexpected programming error', str(resp.data))
        self.assertNotIn('RuntimeError', str(resp.data))


class PasswordResetConfirmTests(TestCase):
    """Тесты POST /api/v1/auth/password-reset/confirm/."""

    def setUp(self):
        self.old_password = 'OldPass123!'
        self.new_password = 'NewPass456!'
        self.user = create_test_user(email='confirm@example.com', password=self.old_password)
        self.client = APIClient()
        self.url = reverse('users:password-reset-confirm')
        self.uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.token = default_token_generator.make_token(self.user)

    def _confirm_data(self, **overrides):
        data = {
            'uid': self.uid,
            'token': self.token,
            'new_password': self.new_password,
            'new_password_confirm': self.new_password,
        }
        data.update(overrides)
        return data

    def test_valid_token_changes_password(self):
        """Valid token → password successfully changed."""
        resp = self.client.post(self.url, self._confirm_data(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))

    def test_old_password_does_not_work_after_reset(self):
        """Old password → does not work after reset."""
        self.client.post(self.url, self._confirm_data(), format='json')
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password(self.old_password))

    def test_new_password_works_after_reset(self):
        """New password → works after reset."""
        self.client.post(self.url, self._confirm_data(), format='json')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))

    def _assert_reset_client_error(self, resp):
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        error = resp.data['error']
        self.assertEqual(error['code'], 'validation_error')
        self.assertIsInstance(error['message'], str)
        self.assertTrue(error['message'])
        self.assertIsInstance(error['details'], list)
        leaked = str(resp.data).lower()
        self.assertNotIn('traceback', leaked)
        self.assertNotIn('nameerror', leaked)
        self.assertNotIn('binascii', leaked)

    def test_invalid_token_rejected(self):
        """Invalid token → 400 canonical envelope."""
        resp = self.client.post(
            self.url,
            self._confirm_data(token='invalid-token-12345'),
            format='json',
        )
        self._assert_reset_client_error(resp)

    def test_used_token_rejected(self):
        """Used token (after successful reset) → 400 canonical envelope."""
        # First reset — succeeds
        self.client.post(self.url, self._confirm_data(), format='json')
        # Second reset with same token — fails
        resp = self.client.post(self.url, self._confirm_data(), format='json')
        self._assert_reset_client_error(resp)

    def test_invalid_uid_rejected(self):
        """Invalid uid → 400 canonical envelope."""
        resp = self.client.post(
            self.url,
            self._confirm_data(uid='invalid-uid'),
            format='json',
        )
        self._assert_reset_client_error(resp)

    def test_passwords_mismatch_rejected(self):
        """Passwords do not match → 400."""
        resp = self.client.post(
            self.url,
            self._confirm_data(new_password_confirm='DifferentPass789!'),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class PasswordResetLoggingTests(TestCase):
    """Тесты что чувствительные данные не попадают в логи."""

    def setUp(self):
        self.user = create_test_user(email='logtest@example.com', password='OldPass123!')
        self.client = APIClient()

    def test_token_not_in_logs(self):
        """Password reset token does NOT appear in log output."""
        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.INFO)
        logger = logging.getLogger('apps.users.api_views.password_reset_views')
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.INFO)

        try:
            url = reverse('users:password-reset')
            with mock.patch(
                'apps.notifications.tasks.send_password_reset_email.delay',
            ):
                self.client.post(
                    url, {'email': 'logtest@example.com'}, format='json',
                )

            log_output = log_stream.getvalue()
            # Token should NOT appear in logs
            # Generate the actual token to check
            token = default_token_generator.make_token(self.user)
            self.assertNotIn(token, log_output)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

    def test_registration_error_does_not_log_password(self):
        """Registration validation error does NOT log password in logs."""
        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setLevel(logging.WARNING)
        logger = logging.getLogger('apps.users.api_views.auth_views')
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.WARNING)

        try:
            url = reverse('users:register')
            self.client.post(url, {
                'email': 'bad',
                'username': 'test',
                'password': 'SecretPass123!',
                'password_confirm': 'DifferentPass456!',
            }, format='json')

            log_output = log_stream.getvalue()
            self.assertNotIn('SecretPass123!', log_output)
            self.assertNotIn('DifferentPass456!', log_output)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
