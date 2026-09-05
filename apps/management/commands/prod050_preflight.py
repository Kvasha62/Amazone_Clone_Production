"""PROD-050 read-only ownership migration preflight.

This command deliberately does not infer historical ownership or currency facts
from current configuration.  It only reads and classifies persisted rows.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from contextlib import nullcontext
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.catalog.models import Product, ProductVariant
from apps.merchants.models import LegalEntity, Store, StoreMarket
from apps.pricing.models import Price, PriceHistory


_WRITE_SQL = re.compile(
    r"^\s*(?:/\*.*?\*/\s*)*(INSERT|UPDATE|DELETE|MERGE|ALTER|CREATE|DROP|"
    r"TRUNCATE|REINDEX|GRANT|REVOKE|COMMENT|VACUUM|CALL|COPY\s+[^\n]+\s+FROM)",
    re.IGNORECASE | re.DOTALL,
)
FAIL_CLASSIFICATIONS = frozenset({"UNPROVABLE", "AMBIGUOUS", "CONFLICT"})


def _deny_writes(execute, sql, params, many, context):
    """Defense in depth: reject mutating SQL issued inside the preflight."""
    if _WRITE_SQL.match(sql):
        raise RuntimeError("PROD-050 preflight attempted a mutating SQL statement")
    return execute(sql, params, many, context)


def _finding(classification: str, record_type: str, record_id: Any, reason: str):
    return {
        "classification": classification,
        "record_type": record_type,
        "record_id": str(record_id),
        "reason": reason,
    }


def build_report() -> dict[str, Any]:
    """Read the current dataset and return deterministic fail-closed findings."""
    findings: list[dict[str, str]] = []

    active_entities = list(
        LegalEntity.objects.filter(is_active=True)
        .select_related("accounting_currency")
        .order_by("pk")
    )
    stores = list(Store.objects.select_related("legal_entity").order_by("pk"))
    markets = list(StoreMarket.objects.prefetch_related("payment_currencies").order_by("pk"))
    products = list(Product.objects.order_by("pk").values("pk", "uuid"))
    variants = list(ProductVariant.objects.order_by("pk").values("pk", "product_id"))
    prices = list(
        Price.objects.select_related("variant__product").order_by("pk")
    )
    histories = list(
        PriceHistory.objects.select_related("variant__product").order_by("pk")
    )

    if len(active_entities) != 1:
        findings.append(_finding(
            "CONFLICT" if len(active_entities) > 1 else "UNPROVABLE",
            "merchant_foundation", "active_legal_entities",
            f"Expected exactly one active LegalEntity; found {len(active_entities)}.",
        ))
    elif not active_entities[0].accounting_currency_id:
        findings.append(_finding(
            "UNPROVABLE", "LegalEntity", active_entities[0].pk,
            "Active LegalEntity has no Accounting Currency.",
        ))

    if len(stores) != 1:
        findings.append(_finding(
            "CONFLICT" if len(stores) > 1 else "UNPROVABLE",
            "merchant_foundation", "stores",
            f"Expected exactly one first-release Store; found {len(stores)}.",
        ))

    for market in markets:
        payment_codes = [currency.code for currency in market.payment_currencies.all()]
        if market.is_active and not payment_codes:
            findings.append(_finding(
                "UNPROVABLE", "StoreMarket", market.pk,
                "Active StoreMarket has no enabled payment currencies.",
            ))

    # The current schema contains no approved, record-specific Product→Store
    # evidence. Current Store count, StoreMarket membership, and matching Price
    # currency are configuration/compatibility facts, never ownership proof.
    product_classification: dict[int, str] = {}
    for product in products:
        product_classification[product["pk"]] = "UNPROVABLE"
        findings.append(_finding(
            "UNPROVABLE", "Product", product["uuid"],
            "No approved record-specific Product→Store ownership evidence exists; "
            "a sole current Store is not ownership proof.",
        ))

    for variant in variants:
        if variant["product_id"] not in product_classification:
            findings.append(_finding(
                "UNPROVABLE", "ProductVariant", variant["pk"],
                "Variant has no reachable Product.",
            ))
        elif product_classification[variant["product_id"]] != "PROVEN":
            findings.append(_finding(
                "UNPROVABLE", "ProductVariant", variant["pk"],
                "Variant owner is unprovable because Product ownership is unprovable.",
            ))

    accounting_code = (
        active_entities[0].accounting_currency.code
        if len(active_entities) == 1 and active_entities[0].accounting_currency_id
        else None
    )
    for price in prices:
        # Currency equality is reported only as compatibility. It cannot upgrade
        # the ownership path to PROVEN.
        if price.variant_id is None or price.variant.product_id not in product_classification:
            classification = "UNPROVABLE"
            reason = "Price has no complete Product ownership path."
        elif product_classification[price.variant.product_id] != "PROVEN":
            classification = "UNPROVABLE"
            reason = (
                "Price ownership path is unprovable; Price.currency equality, "
                "StoreMarket membership, and payment currencies are not ownership evidence."
            )
        elif accounting_code is None:
            classification = "UNPROVABLE"
            reason = "Authoritative Accounting Currency is unavailable."
        elif price.currency != accounting_code:
            classification = "CONFLICT"
            reason = (
                f"Legacy Price.currency {price.currency!r} conflicts with proven "
                f"Accounting Currency {accounting_code!r}."
            )
        else:
            classification = "MATCH"
            reason = "Legacy currency matches Accounting Currency through a proven owner."
        if classification != "MATCH":
            findings.append(_finding(classification, "Price", price.pk, reason))

    for history in histories:
        findings.append(_finding(
            "UNPROVABLE", "PriceHistory", history.pk,
            "No record-specific authoritative historical-currency evidence exists; "
            "current Price or merchant configuration is not historical proof.",
        ))

    counts = Counter(item["classification"] for item in findings)
    overall = "FAIL" if any(
        item["classification"] in FAIL_CLASSIFICATIONS for item in findings
    ) else "PASS"
    return {
        "audit_timestamp": timezone.now().isoformat(),
        "database": {"alias": connection.alias, "vendor": connection.vendor},
        "rows_examined": {
            "active_legal_entities": len(active_entities),
            "stores": len(stores),
            "store_markets": len(markets),
            "products": len(products),
            "product_variants": len(variants),
            "prices": len(prices),
            "price_histories": len(histories),
        },
        "classification_counts": dict(sorted(counts.items())),
        "findings": findings,
        "overall_result": overall,
    }


class Command(BaseCommand):
    help = "Run the strictly read-only PROD-050 ownership migration preflight."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format", choices=("human", "json"), default="human",
            help="Report format (default: human).",
        )

    def handle(self, *args, **options):
        atomic = transaction.atomic() if connection.vendor == "postgresql" else nullcontext()
        with connection.execute_wrapper(_deny_writes), atomic:
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            report = build_report()

        if options["format"] == "json":
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        else:
            self.stdout.write("PROD-050 READ-ONLY PREFLIGHT")
            self.stdout.write(f"Audit timestamp: {report['audit_timestamp']}")
            self.stdout.write(
                f"Database: {report['database']['vendor']} / {report['database']['alias']}"
            )
            self.stdout.write("Rows examined:")
            for name, count in report["rows_examined"].items():
                self.stdout.write(f"  {name}: {count}")
            self.stdout.write("Findings:")
            for finding in report["findings"]:
                self.stdout.write(
                    "  [{classification}] {record_type} {record_id}: {reason}".format(
                        **finding
                    )
                )
            self.stdout.write(f"Overall result: {report['overall_result']}")

        if report["overall_result"] == "FAIL":
            raise CommandError("PROD-050 preflight result: FAIL", returncode=1)
