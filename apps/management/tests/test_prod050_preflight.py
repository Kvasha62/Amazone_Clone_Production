import json
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.catalog.models import Brand, Product
from apps.currencies.models import Currency
from apps.management.commands.prod050_preflight import FAIL_CLASSIFICATIONS, build_report
from apps.merchants.models import LegalEntity, Store


class Prod050PreflightTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        currency = Currency.objects.create(code="USD", numeric_code=840, minor_units=2)
        entity = LegalEntity.objects.create(
            legal_name="Example LLC", accounting_currency=currency
        )
        cls.store = Store.objects.create(
            legal_entity=entity, name="Only Store", slug="only-store"
        )
        brand = Brand.objects.create(name="Example Brand", slug="example-brand")
        cls.product = Product.objects.create(name="Example Product", brand=brand)

    def test_sole_store_and_matching_configuration_do_not_prove_ownership(self):
        report = build_report()
        product_finding = next(
            finding for finding in report["findings"]
            if finding["record_type"] == "Product"
        )
        self.assertEqual(product_finding["classification"], "UNPROVABLE")
        self.assertEqual(report["overall_result"], "FAIL")

    def test_fail_closed_classifications_include_all_blockers(self):
        self.assertEqual(
            FAIL_CLASSIFICATIONS,
            frozenset({"UNPROVABLE", "AMBIGUOUS", "CONFLICT"}),
        )

    def test_json_report_is_machine_readable_and_fail_exits_nonzero(self):
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command("prod050_preflight", format="json", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["overall_result"], "FAIL")
        self.assertNotIn("PASSWORD", report["database"])
