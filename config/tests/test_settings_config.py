"""
Behavior-level tests for the PROD-007 / F-11 production configuration contract.

These tests exercise ``config.settings._build_config`` directly so they cover
both safe and unsafe configuration states without having to boot the whole
application. They assert the acceptance criteria from Issue #12:

  AC-1  SECRET_KEY must be provided and non-placeholder in production.
  AC-2  DEBUG must be explicit; production cannot default to True.
  AC-3  ALLOWED_HOSTS must be explicit and never "*" in production.
  AC-4  CORS must not be silently permissive in production.
  AC-5  Boolean / list settings are parsed deterministically.
  AC-6  Development / test configuration remains usable.
  AC-8  Focused coverage of the unsafe-vs-safe states.
"""

import os

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config import settings


def _environ(**overrides):
    """Copy ``os.environ`` and apply overrides (``None`` removes a key)."""
    env = dict(os.environ)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


class ParseBoolTests(SimpleTestCase):
    """AC-5 — deterministic boolean parsing, no truthiness inference."""

    def test_true_tokens(self):
        for token in ("true", "True", "TRUE", "1", "yes", "on", "y"):
            with self.subTest(token=token):
                self.assertTrue(settings._parse_bool("X", token))

    def test_false_tokens(self):
        for token in ("false", "False", "FALSE", "0", "no", "off", "n"):
            with self.subTest(token=token):
                self.assertFalse(settings._parse_bool("X", token))

    def test_missing_required_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            settings._parse_bool("X", None)

    def test_invalid_value_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            settings._parse_bool("X", "maybe")

    def test_default_is_used_when_absent(self):
        self.assertTrue(settings._parse_bool("X", None, default=True))
        self.assertFalse(settings._parse_bool("X", None, default=False))

    def test_empty_string_is_invalid(self):
        with self.assertRaises(ImproperlyConfigured):
            settings._parse_bool("X", "")


class ParseHostListTests(SimpleTestCase):
    """AC-5 — deterministic list parsing."""

    def test_missing_required_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            settings._parse_host_list("HOSTS", None)

    def test_empty_value_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            settings._parse_host_list("HOSTS", " , , ")

    def test_wildcard_rejected_when_disallowed(self):
        with self.assertRaises(ImproperlyConfigured):
            settings._parse_host_list("HOSTS", "*", allow_wildcard=False)

    def test_strips_and_splits(self):
        self.assertEqual(
            settings._parse_host_list("HOSTS", " a , b ,c", default=["x"]),
            ["a", "b", "c"],
        )

    def test_wildcard_allowed_by_default(self):
        self.assertEqual(settings._parse_host_list("HOSTS", "*"), ["*"])


class ProductionConfigTests(SimpleTestCase):
    """AC-1, AC-2, AC-3, AC-4 — production must fail closed."""

    def _production(self, **overrides):
        return settings._build_config(
            _environ(DJANGO_DEBUG="False", **overrides)
        )

    def test_missing_secret_key_fails(self):
        with self.assertRaises(ImproperlyConfigured):
            self._production(DJANGO_SECRET_KEY=None)

    def test_insecure_placeholder_secret_fails(self):
        with self.assertRaises(ImproperlyConfigured):
            self._production(DJANGO_SECRET_KEY="django-insecure-abc123")

    def test_missing_allowed_hosts_fails(self):
        with self.assertRaises(ImproperlyConfigured):
            self._production(
                DJANGO_SECRET_KEY="prod-secret",
                DJANGO_ALLOWED_HOSTS=None,
            )

    def test_wildcard_allowed_hosts_fails(self):
        with self.assertRaises(ImproperlyConfigured):
            self._production(
                DJANGO_SECRET_KEY="prod-secret",
                DJANGO_ALLOWED_HOSTS="*",
            )

    def test_permissive_cors_fails(self):
        with self.assertRaises(ImproperlyConfigured):
            self._production(
                DJANGO_SECRET_KEY="prod-secret",
                DJANGO_ALLOWED_HOSTS="example.com",
                CORS_ALLOW_ALL_ORIGINS="True",
            )

    def test_debug_cannot_default_to_true(self):
        # The production path is reached only with an explicit False; it never
        # yields DEBUG=True. Verifying an explicit-False production config.
        cfg = self._production(
            DJANGO_SECRET_KEY="prod-secret",
            DJANGO_ALLOWED_HOSTS="example.com",
        )
        self.assertFalse(cfg["DEBUG"])

    def test_valid_production_config_loads(self):
        cfg = self._production(
            DJANGO_SECRET_KEY="prod-secret-key",
            DJANGO_ALLOWED_HOSTS="example.com,www.example.com",
            CORS_ALLOW_ALL_ORIGINS="False",
            CORS_ALLOWED_ORIGINS="https://example.com",
        )
        self.assertFalse(cfg["DEBUG"])
        self.assertEqual(cfg["SECRET_KEY"], "prod-secret-key")
        self.assertEqual(
            cfg["ALLOWED_HOSTS"], ["example.com", "www.example.com"]
        )
        self.assertFalse(cfg["CORS_ALLOW_ALL_ORIGINS"])
        self.assertEqual(cfg["CORS_ALLOWED_ORIGINS"], ["https://example.com"])

    def test_production_cors_defaults_to_not_permissive(self):
        cfg = self._production(
            DJANGO_SECRET_KEY="prod-secret",
            DJANGO_ALLOWED_HOSTS="example.com",
        )
        self.assertFalse(cfg["CORS_ALLOW_ALL_ORIGINS"])
        self.assertEqual(cfg["CORS_ALLOWED_ORIGINS"], [])


class DevelopmentConfigTests(SimpleTestCase):
    """AC-6 — development / test path stays usable and explicit."""

    def _development(self, **overrides):
        # Start from a clean development environment: drop any production
        # vars inherited from the process so the development DEFAULTS are
        # actually exercised (e.g. ALLOWED_HOSTS -> ["*"], CORS -> permissive).
        base = {
            "DJANGO_DEBUG": "True",
            "DJANGO_SECRET_KEY": None,
            "DJANGO_ALLOWED_HOSTS": None,
            "CORS_ALLOW_ALL_ORIGINS": None,
            "CORS_ALLOWED_ORIGINS": None,
        }
        base.update(overrides)
        return settings._build_config(_environ(**base))

    def test_dev_uses_permissive_defaults_when_explicit(self):
        cfg = self._development(DJANGO_SECRET_KEY=None)
        self.assertTrue(cfg["DEBUG"])
        self.assertEqual(cfg["ALLOWED_HOSTS"], ["*"])
        self.assertTrue(cfg["CORS_ALLOW_ALL_ORIGINS"])

    def test_dev_explicit_secret_is_used(self):
        cfg = self._development(DJANGO_SECRET_KEY="my-dev-key")
        self.assertEqual(cfg["SECRET_KEY"], "my-dev-key")

    def test_dev_falls_back_to_localhost_cors(self):
        cfg = self._development(CORS_ALLOWED_ORIGINS=None)
        self.assertIn("http://localhost:3000", cfg["CORS_ALLOWED_ORIGINS"])

    def test_dev_wildcard_allowed_hosts_accepted(self):
        cfg = self._development(DJANGO_ALLOWED_HOSTS="*")
        self.assertEqual(cfg["ALLOWED_HOSTS"], ["*"])


class DebugRequiredTests(SimpleTestCase):
    """AC-2 — DEBUG must be explicit; no silent default."""

    def test_missing_debug_fails(self):
        with self.assertRaises(ImproperlyConfigured):
            settings._build_config(_environ(DJANGO_DEBUG=None))

    def test_invalid_debug_fails(self):
        with self.assertRaises(ImproperlyConfigured):
            settings._build_config(_environ(DJANGO_DEBUG="sometimes"))
