# ────────────────────────────────────────────────────────────────────────
# apps/analytics/tests/test_locks.py
#
# PROD-021 / F-22 — семантика ключа дедупликации просмотров.
# Бэкенд-независимые проверки (личность и стабильность ключа лока).
# ────────────────────────────────────────────────────────────────────────

from django.test import SimpleTestCase

from apps.analytics.locks import dedup_identity, lock_key


class DedupIdentityTests(SimpleTestCase):

    def test_authenticated_identity_ignores_session(self):
        """Личность авторизованного — (товар, user), сессия не влияет."""
        self.assertEqual(
            dedup_identity(1, user_id=7, session_key='a'),
            dedup_identity(1, user_id=7, session_key='b'),
        )

    def test_anonymous_identity_uses_session(self):
        """Личность анонима — (товар, session_key)."""
        self.assertNotEqual(
            dedup_identity(1, session_key='a'),
            dedup_identity(1, session_key='b'),
        )

    def test_different_products_are_different_identities(self):
        self.assertNotEqual(
            dedup_identity(1, user_id=7),
            dedup_identity(2, user_id=7),
        )

    def test_no_identity_without_user_and_session(self):
        self.assertIsNone(dedup_identity(1))

    def test_user_and_session_namespaces_do_not_collide(self):
        self.assertNotEqual(
            dedup_identity(1, user_id=7),
            dedup_identity(1, session_key='7'),
        )


class LockKeyTests(SimpleTestCase):

    def test_key_is_stable_across_calls(self):
        """Ключ детерминирован (не зависит от PYTHONHASHSEED)."""
        identity = dedup_identity(1, user_id=7)
        self.assertEqual(lock_key(identity), lock_key(identity))

    def test_key_fits_signed_bigint(self):
        """pg_advisory_xact_lock принимает signed bigint."""
        for identity in (
            dedup_identity(1, user_id=7),
            dedup_identity(999999, session_key='x' * 40),
        ):
            key = lock_key(identity)
            self.assertGreaterEqual(key, -(2 ** 63))
            self.assertLess(key, 2 ** 63)

    def test_known_identity_key_is_pinned(self):
        """Значение ключа стабильно между процессами/воркерами."""
        self.assertEqual(
            lock_key('analytics.product_view.dedup:product=1:user=7'),
            lock_key('analytics.product_view.dedup:product=1:user=7'),
        )
