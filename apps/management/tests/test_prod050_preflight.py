import json
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from apps.catalog.models import Brand, Product, ProductVariant
from apps.currencies.models import Currency
from apps.management.commands.prod050_preflight import (
    FAIL_CLASSIFICATIONS,
    _classify_merchant_foundation,
    _deny_writes,
    build_report,
)
from apps.merchants.models import LegalEntity, Store, StoreMarket
from apps.pricing.models import Price, PriceHistory


class MerchantFoundationClassificationTests(SimpleTestCase):
    entity = [{"id": 1, "accounting_currency_id": 1}]
    entity_ids = {1}
    store = [{"id": 10, "legal_entity_id": 1}]
    active_market = [{"id": 20, "store_id": 10, "is_active": True}]
    payments = {20: ["USD"]}

    def classify(self, stores=None, markets=None, payments=None, entities=None):
        return _classify_merchant_foundation(
            self.entity if entities is None else entities,
            self.entity_ids,
            self.store if stores is None else stores,
            self.active_market if markets is None else markets,
            self.payments if payments is None else payments,
        )

    def test_exactly_one_store_and_valid_market_proves_bootstrap(self):
        _, foundation, store_id, entity_id = self.classify()
        self.assertEqual(foundation["first_release_store_bootstrap"], "PROVEN")
        self.assertTrue(foundation["exactly_one_persisted_store"])
        self.assertTrue(foundation["exactly_one_active_store_market"])
        self.assertEqual((store_id, entity_id), (10, 1))

    def test_zero_stores_fails(self):
        findings, foundation, store_id, _ = self.classify(
            stores=[], markets=[], payments={}
        )
        self.assertEqual(foundation["first_release_store_bootstrap"], "UNPROVABLE")
        self.assertIsNone(store_id)
        self.assertTrue(any(item["classification"] in FAIL_CLASSIFICATIONS for item in findings))

    def test_multiple_stores_conflict(self):
        stores = self.store + [{"id": 11, "legal_entity_id": 1}]
        _, foundation, store_id, _ = self.classify(stores=stores)
        self.assertEqual(foundation["first_release_store_bootstrap"], "CONFLICT")
        self.assertIsNone(store_id)

    def test_invalid_store_legal_entity_relationship_fails(self):
        stores = [{"id": 10, "legal_entity_id": 999}]
        findings, foundation, store_id, _ = self.classify(stores=stores)
        store_finding = next(item for item in findings if item["record_type"] == "Store")
        self.assertEqual(store_finding["classification"], "CONFLICT")
        self.assertFalse(foundation["store_legal_entity_relationship_proven"])
        self.assertIsNone(store_id)

    def test_zero_active_markets_fails(self):
        inactive = [{"id": 21, "store_id": 10, "is_active": False}]
        _, foundation, store_id, _ = self.classify(markets=inactive, payments={})
        self.assertEqual(foundation["active_store_market_count"], 0)
        self.assertEqual(foundation["first_release_store_bootstrap"], "UNPROVABLE")
        self.assertIsNone(store_id)

    def test_multiple_active_markets_conflict(self):
        markets = self.active_market + [{"id": 21, "store_id": 10, "is_active": True}]
        _, foundation, store_id, _ = self.classify(
            markets=markets, payments={20: ["USD"], 21: ["USD"]}
        )
        self.assertEqual(foundation["active_store_market_count"], 2)
        self.assertEqual(foundation["first_release_store_bootstrap"], "CONFLICT")
        self.assertIsNone(store_id)

    def test_active_market_without_payment_currency_fails(self):
        findings, foundation, store_id, _ = self.classify(payments={20: []})
        market = next(item for item in findings if item["record_type"] == "StoreMarket")
        self.assertEqual(market["classification"], "UNPROVABLE")
        self.assertFalse(foundation["payment_currency_configuration_valid"])
        self.assertIsNone(store_id)

    def test_inactive_market_does_not_count_as_active_or_conflict(self):
        markets = self.active_market + [{"id": 21, "store_id": 10, "is_active": False}]
        findings, foundation, store_id, _ = self.classify(
            markets=markets, payments={20: ["USD"]}
        )
        inactive = next(item for item in findings if item["record_id"] == "21")
        self.assertEqual(inactive["classification"], "PROVEN")
        self.assertEqual(foundation["active_store_market_ids"], [20])
        self.assertEqual(store_id, 10)


class Prod050PreflightTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.currency = Currency.objects.create(code="USD", numeric_code=840, minor_units=2)
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
        return [item for item in report["findings"] if item["record_type"] == record_type]

    @staticmethod
    def _snapshot():
        models = (LegalEntity, Store, StoreMarket, Product, ProductVariant, Price, PriceHistory)
        snapshot = {
            model._meta.label: list(model.objects.order_by("pk").values().iterator())
            for model in models
        }
        snapshot["StoreMarket.payment_currencies"] = list(
            StoreMarket.payment_currencies.through.objects.order_by("pk").values().iterator()
        )
        return snapshot

    def test_bootstrap_proves_current_product_path_but_not_historical_activity(self):
        report = build_report()
        product = self._findings(report, "Product")[0]
        self.assertEqual(report["merchant_foundation"]["first_release_store_bootstrap"], "PROVEN")
        self.assertFalse(report["merchant_foundation"]["historical_store_activity_proven"])
        self.assertEqual(product["classification"], "PROVEN")
        self.assertEqual(product["store_id"], self.store.pk)
        self.assertIn("not historical evidence", product["reason"])

    def test_every_relevant_persisted_record_is_individually_reported(self):
        report = build_report()
        expected = {
            "LegalEntity": 1, "Store": 1, "StoreMarket": 1, "Product": 1,
            "ProductVariant": 2, "Price": 2, "PriceHistory": 2,
        }
        for record_type, count in expected.items():
            self.assertEqual(len(self._findings(report, record_type)), count)
        self.assertEqual(sum(report["classification_counts"].values()), len(report["findings"]))

    def test_every_price_has_complete_classification_details(self):
        report = build_report()
        prices = {item["record_id"]: item for item in self._findings(report, "Price")}
        self.assertEqual(set(prices), {str(self.matching_price.pk), str(self.conflicting_price.pk)})
        self.assertEqual(prices[str(self.matching_price.pk)]["classification"], "MATCH")
        self.assertEqual(prices[str(self.conflicting_price.pk)]["classification"], "CONFLICT")
        for item in prices.values():
            for key in (
                "variant_id", "product_id", "product_ownership", "store_id",
                "legal_entity_id", "legacy_currency", "accounting_currency",
                "comparison_result",
            ):
                self.assertIn(key, item)

    def test_price_history_remains_unknown_without_historical_evidence(self):
        report = build_report()
        histories = self._findings(report, "PriceHistory")
        self.assertEqual({item["classification"] for item in histories}, {"UNKNOWN"})
        self.assertEqual(report["overall_result"], "FAIL")

    def test_current_state_and_bootstrap_are_not_historical_evidence(self):
        report = build_report()
        histories = {item["record_id"]: item for item in self._findings(report, "PriceHistory")}
        self.assertEqual(histories[str(self.matching_history.pk)]["classification"], "UNKNOWN")
        self.assertEqual(histories[str(self.conflicting_history.pk)]["classification"], "UNKNOWN")

    def test_command_is_read_only_for_all_relevant_records(self):
        before = self._snapshot()
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("prod050_preflight", format="json", stdout=output)
        self.assertEqual(self._snapshot(), before)

    def test_sql_write_guard_rejects_all_prohibited_categories(self):
        calls = []

        def execute(sql, params, many, context):
            calls.append(sql)
            return "executed"

        statements = (
            "INSERT INTO x VALUES (1)", "UPDATE x SET y = 1", "DELETE FROM x",
            "MERGE INTO x USING y ON true WHEN MATCHED THEN DELETE", "ALTER TABLE x ADD y int",
            "CREATE TABLE x (id int)", "DROP TABLE x", "TRUNCATE x", "REINDEX TABLE x",
            "GRANT SELECT ON x TO y", "REVOKE SELECT ON x FROM y", "COMMENT ON TABLE x IS 'x'",
            "VACUUM x", "CALL procedure()", "COPY x FROM '/tmp/x'",
        )
        for sql in statements:
            with self.subTest(sql=sql), self.assertRaisesRegex(RuntimeError, "mutating SQL"):
                _deny_writes(execute, sql, None, False, {})
        self.assertEqual(calls, [])
        self.assertEqual(_deny_writes(execute, "SELECT 1", None, False, {}), "executed")

    def test_fail_closed_classifications_include_all_blockers(self):
        self.assertEqual(
            FAIL_CLASSIFICATIONS,
            frozenset({"UNPROVABLE", "AMBIGUOUS", "CONFLICT", "UNKNOWN"}),
        )

    def test_json_report_is_complete_and_fail_exits_nonzero(self):
        output = StringIO()
        with self.assertRaises(CommandError) as raised:
            call_command("prod050_preflight", format="json", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(raised.exception.returncode, 1)
        self.assertEqual(report["overall_result"], "FAIL")
        self.assertEqual(len(self._findings(report, "Price")), 2)
        self.assertIn("merchant_foundation", report)
        self.assertNotIn("PASSWORD", report["database"])

    def test_human_report_contains_same_summary_information(self):
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("prod050_preflight", format="human", stdout=output)
        rendered = output.getvalue()
        for text in (
            "Audit timestamp:", "Database:", "Rows examined:", "Merchant foundation:",
            "Classification counts:", "[UNKNOWN] PriceHistory", "Overall result: FAIL",
        ):
            self.assertIn(text, rendered)
