# ────────────────────────────────────────────────────────────────────────
# apps/users/tests/test_auth_contract.py — API-03 authentication lifecycle.
#
# Covers:
#   1. JWT Bearer mechanism and frozen token lifetimes.
#   2. Email-based login contract (case-insensitivity, inactive users).
#   3. Access-token behaviour (valid/missing/malformed/expired).
#   4. Refresh rotation and blacklist reuse prevention.
#   5. Logout endpoint and refresh-token revocation.
#   6. No server-side access-token blacklist.
#   7. Account deactivation.
#   8. Password change / password reset authentication lifecycle.
#
# API-04 (unified error body schema) is out of scope: assertions intentionally
# focus on deterministic HTTP status behaviour and token lifecycle, not exact
# error payload shape.
# ────────────────────────────────────────────────────────────────────────

from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from rest_framework import status
from rest_framework.test import APIClient

from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.users.models import User
from apps.users.services.user_service import UserService
from apps.users.tests.factories import UserTestCase


class AuthContractTestCase(UserTestCase):
    """Base helpers for API-03 authentication lifecycle tests."""

    password = 'TestPass123!'

    def setUp(self):
        # Django's setUpTestData keeps one in-memory user across the class; a
        # previous test may have changed its password/is_active in memory, so
        # reload the canonical DB state before each test.
        self.user.refresh_from_db()
        self.client = APIClient()
        self.login_url = reverse('users:login')
        self.refresh_url = reverse('users:refresh')
        self.logout_url = reverse('users:logout')
        self.change_password_url = reverse('users:change-password')
        self.me_url = reverse('users:me')
        self.password_reset_url = reverse('users:password-reset')
        self.password_reset_confirm_url = reverse('users:password-reset-confirm')

    # ── helpers ────────────────────────────────────────────────────────

    def _login(self, *, email=None, password=None, **extra):
        payload = {
            'email': email if email is not None else self.user.email,
            'password': password if password is not None else self.password,
        }
        payload.update(extra)
        return self.client.post(self.login_url, payload, format='json')

    def _get_tokens(self, user=None):
        self.client = APIClient()
        user = user or self.user
        resp = self._login(email=user.email, password=self.password)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp.data['access'], resp.data['refresh']

    def _authorize(self, access_token):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')

    def _deactivate(self, user=None):
        UserService.deactivate(user or self.user)

    def _reset_tokens(self, user=None):
        user = user or self.user
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        return uid, token


class TokenLifetimeContractTests(AuthContractTestCase):
    """API-03: freeze access (15 min) and refresh (7 days) lifetimes."""

    def test_settings_access_lifetime_is_15_minutes(self):
        self.assertEqual(
            settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'],
            timedelta(minutes=15),
        )

    def test_settings_refresh_lifetime_is_7_days(self):
        self.assertEqual(
            settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'],
            timedelta(days=7),
        )

    def test_issued_access_token_expires_after_15_minutes(self):
        token = AccessToken.for_user(self.user)
        self.assertEqual(
            token.payload['exp'] - token.payload['iat'],
            int(timedelta(minutes=15).total_seconds()),
        )

    def test_issued_refresh_token_expires_after_7_days(self):
        token = RefreshToken.for_user(self.user)
        self.assertEqual(
            token.payload['exp'] - token.payload['iat'],
            int(timedelta(days=7).total_seconds()),
        )

    def test_inactive_user_check_is_enabled_in_settings(self):
        self.assertTrue(settings.SIMPLE_JWT['CHECK_USER_IS_ACTIVE'])

    def test_password_change_does_not_enable_revoke_token(self):
        self.assertFalse(settings.SIMPLE_JWT['CHECK_REVOKE_TOKEN'])


class LoginContractTests(AuthContractTestCase):
    """API-03: email-based login is the only login identifier."""

    def test_valid_email_password_returns_access_and_refresh(self):
        resp = self._login()
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)

    def test_email_matching_is_case_insensitive(self):
        resp = self._login(email=self.user.email.upper())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)

    def test_invalid_password_is_auth_failure(self):
        resp = self._login(password='WrongPass123!')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unknown_email_is_auth_failure(self):
        resp = self._login(email='unknown@example.com')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_user_is_auth_failure(self):
        self._deactivate()
        resp = self._login()
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_username_cannot_be_used_as_login_identifier(self):
        resp = self.client.post(self.login_url, {
            'username': self.user.username,
            'password': self.password,
        }, format='json')
        # The login serializer requires `email`; username is not a recognized
        # login identifier. Status is deterministic even before the request is
        # authenticated against credentials.
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', resp.data)

    def test_missing_login_credentials_is_deterministic(self):
        resp = self.client.post(self.login_url, {'email': self.user.email}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', resp.data)


class AccessTokenContractTests(AuthContractTestCase):
    """API-03: access tokens are stateless bearer credentials."""

    def test_valid_access_token_succeeds_on_authenticated_endpoint(self):
        access, _ = self._get_tokens()
        self._authorize(access)
        resp = self.client.get(self.me_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_missing_access_token_is_auth_failure(self):
        resp = self.client.get(self.me_url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_malformed_access_token_is_auth_failure(self):
        self._authorize('not-a-real-jwt')
        resp = self.client.get(self.me_url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_expired_access_token_is_auth_failure(self):
        access = AccessToken.for_user(self.user)
        access.set_exp(from_time=timezone.now() - timedelta(minutes=1))
        self._authorize(str(access))
        resp = self.client.get(self.me_url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class RefreshRotationContractTests(AuthContractTestCase):
    """API-03: refresh rotation, blacklist and expiry."""

    def test_valid_refresh_returns_new_access_and_refresh(self):
        _, refresh = self._get_tokens()
        resp = self.client.post(self.refresh_url, {'refresh': refresh}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)

    def test_old_refresh_becomes_unusable_after_rotation(self):
        _, refresh = self._get_tokens()
        first = self.client.post(self.refresh_url, {'refresh': refresh}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        second = self.client.post(self.refresh_url, {'refresh': refresh}, format='json')
        self.assertEqual(second.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_blacklisted_refresh_cannot_be_reused(self):
        access, refresh = self._get_tokens()
        self._authorize(access)
        logout = self.client.post(self.logout_url, {'refresh': refresh}, format='json')
        self.assertEqual(logout.status_code, status.HTTP_200_OK)
        self.client.credentials()

        resp = self.client.post(self.refresh_url, {'refresh': refresh}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_expired_refresh_cannot_be_used(self):
        refresh = RefreshToken.for_user(self.user)
        refresh.set_exp(from_time=timezone.now() - timedelta(days=8))
        refresh.set_iat(from_time=timezone.now() - timedelta(days=8))

        resp = self.client.post(self.refresh_url, {'refresh': str(refresh)}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class LogoutContractTests(AuthContractTestCase):
    """API-03: POST /api/v1/auth/logout/ revokes refresh capability."""

    def test_logout_requires_authentication(self):
        _, refresh = self._get_tokens()
        self.client.credentials()
        resp = self.client.post(self.logout_url, {'refresh': refresh}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_with_valid_refresh_blacklists_it(self):
        access, refresh = self._get_tokens()
        jti = RefreshToken(refresh).payload['jti']

        self._authorize(access)
        resp = self.client.post(self.logout_url, {'refresh': refresh}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=jti).exists())

    def test_logout_does_not_blacklist_access_token(self):
        access, refresh = self._get_tokens()
        access_jti = AccessToken(access).payload['jti']

        self._authorize(access)
        resp = self.client.post(self.logout_url, {'refresh': refresh}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # No server-side access-token blacklist is introduced by API-03.
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
        self.assertFalse(BlacklistedToken.objects.filter(token__jti=access_jti).exists())

        # Access token remains valid until its 15-minute expiration.
        resp = self.client.get(self.me_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_logout_is_idempotent_for_blacklisted_refresh(self):
        access, refresh = self._get_tokens()
        self._authorize(access)

        first = self.client.post(self.logout_url, {'refresh': refresh}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        second = self.client.post(self.logout_url, {'refresh': refresh}, format='json')
        self.assertEqual(second.status_code, status.HTTP_200_OK)

    def test_logout_invalid_refresh_token_is_deterministic(self):
        access, _ = self._get_tokens()
        self._authorize(access)
        resp = self.client.post(self.logout_url, {'refresh': 'not-a-refresh-token'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_missing_refresh_is_400(self):
        access, _ = self._get_tokens()
        self._authorize(access)
        resp = self.client.post(self.logout_url, {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('refresh', resp.data)

    def test_logout_rejects_refresh_token_of_another_user(self):
        access, own_refresh = self._get_tokens()
        other = User.objects.create_user(
            username='logout_other',
            email='logout_other@example.com',
            password=self.password,
        )
        _, other_refresh = self._get_tokens(user=other)

        self._authorize(access)
        resp = self.client.post(
            self.logout_url,
            {'refresh': other_refresh},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

        # The other user's refresh remains usable; no cross-user revocation.
        self.client.credentials()
        resp = self.client.post(self.refresh_url, {'refresh': other_refresh}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class DeactivationContractTests(AuthContractTestCase):
    """API-03: deactivation stops new auth and existing-access behaviour."""

    def test_deactivated_user_cannot_login(self):
        self._get_tokens()
        self._deactivate()

        resp = self._login()
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_deactivated_user_cannot_refresh(self):
        _, refresh = self._get_tokens()
        self._deactivate()

        resp = self.client.post(self.refresh_url, {'refresh': refresh}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_existing_access_token_is_rejected_after_deactivation(self):
        access, _ = self._get_tokens()
        self._deactivate()

        self._authorize(access)
        resp = self.client.get(self.me_url)
        # SimpleJWT CHECK_USER_IS_ACTIVE is enabled, so an already-issued access
        # token cannot authenticate an inactive user. No access-token blacklist
        # is required for this behaviour.
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class ChangePasswordContractTests(AuthContractTestCase):
    """API-03: password-change authentication lifecycle."""

    def test_change_password_requires_authentication(self):
        self.client.credentials()
        resp = self.client.post(self.change_password_url, {
            'old_password': self.password,
            'new_password': 'NewPass789!',
            'new_password_confirm': 'NewPass789!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_change_password_requires_old_password(self):
        access, _ = self._get_tokens()
        self._authorize(access)
        resp = self.client.post(self.change_password_url, {
            'new_password': 'NewPass789!',
            'new_password_confirm': 'NewPass789!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('old_password', resp.data)

    def test_new_password_becomes_usable_for_login(self):
        access, _ = self._get_tokens()
        self._authorize(access)
        resp = self.client.post(self.change_password_url, {
            'old_password': self.password,
            'new_password': 'NewPass789!',
            'new_password_confirm': 'NewPass789!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()

        old_resp = self._login(password=self.password)
        self.assertEqual(old_resp.status_code, status.HTTP_401_UNAUTHORIZED)

        new_resp = self._login(password='NewPass789!')
        self.assertEqual(new_resp.status_code, status.HTTP_200_OK)

    def test_existing_access_token_remains_valid_after_password_change(self):
        access, _ = self._get_tokens()
        self._authorize(access)
        resp = self.client.post(self.change_password_url, {
            'old_password': self.password,
            'new_password': 'NewPass789!',
            'new_password_confirm': 'NewPass789!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()

        # Current implementation does not issue revoke-token hashes in JWTs, so
        # an already-issued access token remains valid until expiry.
        resp = self.client.get(self.me_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_existing_refresh_token_remains_valid_after_password_change(self):
        _, refresh = self._get_tokens()
        access, _ = self._get_tokens()
        self._authorize(access)
        resp = self.client.post(self.change_password_url, {
            'old_password': self.password,
            'new_password': 'NewPass789!',
            'new_password_confirm': 'NewPass789!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.client.credentials()

        # Current implementation does not invalidate refresh tokens on password
        # change; the only revocation mechanism is rotation/logout and expiry.
        resp = self.client.post(self.refresh_url, {'refresh': refresh}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class PasswordResetContractTests(AuthContractTestCase):
    """API-03: password-reset authentication lifecycle."""

    def test_reset_does_not_disclose_email_existence(self):
        with mock.patch(
            'apps.notifications.tasks.send_password_reset_email.delay',
        ):
            existing = self.client.post(
                self.password_reset_url,
                {'email': self.user.email},
                format='json',
            )
        unknown = self.client.post(
            self.password_reset_url,
            {'email': 'does-not-exist@example.com'},
            format='json',
        )
        self.assertEqual(existing.status_code, status.HTTP_200_OK)
        self.assertEqual(unknown.status_code, status.HTTP_200_OK)
        self.assertEqual(existing.data, unknown.data)

    def test_password_reset_changes_password(self):
        uid, token = self._reset_tokens()
        resp = self.client.post(self.password_reset_confirm_url, {
            'uid': uid,
            'token': token,
            'new_password': 'ResetPass789!',
            'new_password_confirm': 'ResetPass789!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()

        old_resp = self._login(password=self.password)
        self.assertEqual(old_resp.status_code, status.HTTP_401_UNAUTHORIZED)

        new_resp = self._login(password='ResetPass789!')
        self.assertEqual(new_resp.status_code, status.HTTP_200_OK)

    def test_existing_access_token_remains_valid_after_password_reset(self):
        access, _ = self._get_tokens()
        uid, token = self._reset_tokens()
        resp = self.client.post(self.password_reset_confirm_url, {
            'uid': uid,
            'token': token,
            'new_password': 'ResetPass789!',
            'new_password_confirm': 'ResetPass789!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()

        self._authorize(access)
        resp = self.client.get(self.me_url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_existing_refresh_token_remains_valid_after_password_reset(self):
        _, refresh = self._get_tokens()
        uid, token = self._reset_tokens()
        resp = self.client.post(self.password_reset_confirm_url, {
            'uid': uid,
            'token': token,
            'new_password': 'ResetPass789!',
            'new_password_confirm': 'ResetPass789!',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()

        resp = self.client.post(self.refresh_url, {'refresh': refresh}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
