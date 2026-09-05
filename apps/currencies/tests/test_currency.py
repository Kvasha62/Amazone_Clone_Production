from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from apps.currencies.models import Currency
from apps.currencies.services import minor_unit_quantum, normalize_amount


class CurrencyModelTests(TestCase):
    def test_seeded_registry_contains_initial_currencies(self):
        self.assertEqual(
            set(Currency.objects.values_list("code", flat=True)),
            {"EUR", "JPY", "RUB", "USD"},
        )

    def test_zero_decimal_currency_is_first_class(self):
        currency = Currency.objects.get(code="JPY")
        self.assertEqual(currency.numeric_code, 392)
        self.assertEqual(currency.minor_units, 0)
        self.assertEqual(minor_unit_quantum(currency), Decimal("1"))
        self.assertEqual(normalize_amount(Decimal("100"), currency), Decimal("100"))

    def test_two_decimal_currency_quantum(self):
        currency = Currency.objects.get(code="EUR")
        self.assertEqual(minor_unit_quantum(currency), Decimal("0.01"))
        self.assertEqual(
            normalize_amount(Decimal("12.345"), currency),
            Decimal("12.35"),
        )

    def test_deactivated_currency_remains_queryable(self):
        currency = Currency.objects.get(code="EUR")
        currency.is_active = False
        currency.save(update_fields=["is_active", "updated_at"])

        persisted = Currency.objects.get(pk=currency.pk)
        self.assertFalse(persisted.is_active)
        self.assertEqual(persisted.code, "EUR")
        self.assertEqual(persisted.numeric_code, 978)
        self.assertEqual(persisted.minor_units, 2)

    def test_code_must_be_three_uppercase_letters(self):
        with self.assertRaises(IntegrityError):
            Currency.objects.create(code="eur", numeric_code=111, minor_units=2)

    def test_duplicate_iso_numeric_code_is_rejected(self):
        with self.assertRaises(IntegrityError):
            Currency.objects.create(code="ZZZ", numeric_code=978, minor_units=2)


class CurrencyPrecisionTests(TestCase):
    def test_float_amount_is_rejected(self):
        currency = Currency.objects.get(code="EUR")
        with self.assertRaises(TypeError):
            normalize_amount(12.34, currency)

    def test_negative_amount_is_rejected(self):
        currency = Currency.objects.get(code="EUR")
        with self.assertRaises(ValueError):
            normalize_amount(Decimal("-0.01"), currency)
