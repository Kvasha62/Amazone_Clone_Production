import json
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.catalog.models import Brand, Product, ProductVariant
from apps.currencies.models import Currency
from apps.management.commands.prod050_preflight import (
    FAIL_CLASSIFICATIONS,
    _deny_writes,
    build_report,
)
from apps.merchants.models import LegalEntity, Store, StoreMarket
from apps.pricing.models import Price, PriceHistory


class Prod050PreflightTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.currency = Currency.objects.create(
            code="USD", numeric_code=840, minor_units=2
        )
        cls.entity = LegalEntity.objects.create(
            legal_name="Example LLC", accounting_currency=cls.currency
        )
        cls.store = Store.objects.create(
            legal_entity=cls.entity, name="Only Store", slug="only-store"
        )
        cls.market = StoreMarket.objects.create(
            store=cls.store, country_code="US", is_active=True
        )
        cls.market.payment_currencies.add(cls.currency)
        brand = Brand.objects.create(name="Example Brand", slug="example-brand")
        cls.product = Product.objects.create(name="Example Product", brand=brand)
        cls.variant_matching = ProductVariant.objects.create(
            product=cls.product, sku="MATCHING-SKU"
        )
        cls.variant_conflicting = ProductVariant.objects.create(
            product=cls.product, sku="CONFLICTING-SKU"
        )
        cls.matching_price = Price.objects.create(
            variant=cls.variant_matching, price="10.00", currency="USD"
        )
        cls.conflicting_price = Price.objects.create(
            variant=cls.variant_conflicting, price="20.00", currency="EUR"
        )
        cls.matching_history = PriceHistory.objects.create(
            variant=cls.variant_matching, old_price="9.00", new_price="10.00"
        )
        cls.conflicting_history = PriceHistory.objects.create(
            variant=cls.variant_conflicting, old_price="19.00", new_price="20.00"
        )

    @staticmethod
    def _findings(report, record_type):
        return [
            finding for finding in report["findings"]
            if finding["record_type"] == record_type
        ]

    @staticmethod
    def _snapshot():
        models = (
            LegalEntity, Store, StoreMarket, Product, ProductVariant, Price,
            PriceHistory,
        )
        snapshot = {
            model._meta.label: list(
                model.objects.order_by("pk").values().iterator()
            )
            for model in models
        }
        snapshot["StoreMarket.payment_currencies"] = list(
            StoreMarket.payment_currencies.through.objects.order_by("pk")
            .values().iterator()
        )
        return snapshot

    def test_matching_price_and_sole_store_do_not_prove_product_ownership(self):
        report = build_report()
        product_finding = self._findings(report, "Product")[0]
        matching_price = next(
            finding for finding in self._findings(report, "Price")
            if finding["record_id"] == str(self.matching_price.pk)
        )

        self.assertEqual(product_finding["classification"], "UNPROVABLE")
        self.assertEqual(matching_price["classification"], "UNPROVABLE")
        self.assertIn("not ownership evidence", matching_price["reason"])
        self.assertEqual(report["overall_result"], "FAIL")

    def test_every_relevant_persisted_record_is_individually_reported(self):
        report = build_report()
        expected_counts = {
            "LegalEntity": 1,
            "Store": 1,
            "StoreMarket": 1,
            "Product": 1,
            "ProductVariant": 2,
            "Price": 2,
            "PriceHistory": 2,
        }
        for record_type, expected in expected_counts.items():
            self.assertEqual(len(self._findings(report, record_type)), expected)

        self.assertEqual(
            {item["record_id"] for item in self._findings(report, "Price")},
            {str(self.matching_price.pk), str(self.conflicting_price.pk)},
        )
        self.assertEqual(
            sum(report["classification_counts"].values()),
            len(report["findings"]),
        )

    def test_price_history_is_unknown_without_historical_evidence(self):
        report = build_report()
        histories = self._findings(report, "PriceHistory")

        self.assertEqual({item["classification"] for item in histories}, {"UNKNOWN"})
        self.assertEqual(report["overall_result"], "FAIL")

    def test_current_price_and_accounting_currency_are_not_historical_evidence(self):
        report = build_report()
        findings_by_id = {
            item["record_id"]: item for item in self._findings(report, "PriceHistory")
        }

        # One current Price matches and one conflicts with current Accounting
        # Currency. Neither current-state fact proves historical currency.
        self.assertEqual(
            findings_by_id[str(self.matching_history.pk)]["classification"],
            "UNKNOWN",
        )
        self.assertEqual(
            findings_by_id[str(self.conflicting_history.pk)]["classification"],
            "UNKNOWN",
        )

    def test_command_is_read_only_for_all_relevant_records(self):
        before = self._snapshot()
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("prod050_preflight", format="json", stdout=output)
        after = self._snapshot()

        self.assertEqual(after, before)

    def test_sql_write_guard_rejects_insert_update_and_delete(self):
        calls = []

        def execute(sql, params, many, context):
            calls.append(sql)
            return "executed"

        for sql in (
            "INSERT INTO example VALUES (1)",
            "UPDATE example SET value = 1",
            "DELETE FROM example",
        ):
            with self.subTest(sql=sql), self.assertRaisesRegex(
                RuntimeError, "mutating SQL"
            ):
                _deny_writes(execute, sql, None, False, {})
        self.assertEqual(calls, [])
        self.assertEqual(
            _deny_writes(execute, "SELECT 1", None, False, {}), "executed"
        )

    def test_fail_closed_classifications_include_all_blockers(self):
        self.assertEqual(
            FAIL_CLASSIFICATIONS,
            frozenset({"UNPROVABLE", "AMBIGUOUS", "CONFLICT", "UNKNOWN"}),
        )

    def test_json_report_contains_all_records_and_fail_exits_nonzero(self):
        output = StringIO()
        with self.assertRaises(CommandError) as raised:
            call_command("prod050_preflight", format="json", stdout=output)
        report = json.loads(output.getvalue())

        self.assertEqual(raised.exception.returncode, 1)
        self.assertEqual(report["overall_result"], "FAIL")
        self.assertEqual(len(self._findings(report, "Price")), 2)
        self.assertEqual(len(self._findings(report, "PriceHistory")), 2)
        self.assertNotIn("PASSWORD", report["database"])

    def test_human_report_contains_same_summary_information(self):
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("prod050_preflight", format="human", stdout=output)
        rendered = output.getvalue()

        self.assertIn("Audit timestamp:", rendered)
        self.assertIn("Database:", rendered)
        self.assertIn("Rows examined:", rendered)
        self.assertIn("Classification counts:", rendered)
        self.assertIn("[UNKNOWN] PriceHistory", rendered)
        self.assertIn("Overall result: FAIL", rendered)
