"""PROD-050 read-only ownership migration preflight.

The command applies the first-release bootstrap approved in ARCH-012 (#125).
That bootstrap is current migration evidence only; it is never historical proof.
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
FAIL_CLASSIFICATIONS = frozenset(
    {"UNPROVABLE", "AMBIGUOUS", "CONFLICT", "UNKNOWN"}
)


def _deny_writes(execute, sql, params, many, context):
    """Defense in depth: reject mutating SQL issued inside the preflight."""
    if _WRITE_SQL.match(sql):
        raise RuntimeError("PROD-050 preflight attempted a mutating SQL statement")
    return execute(sql, params, many, context)


def _finding(
    classification: str,
    record_type: str,
    record_id: Any,
    reason: str,
    **details,
):
    finding = {
        "classification": classification,
        "record_type": record_type,
        "record_id": str(record_id),
        "reason": reason,
    }
    finding.update(details)
    return finding


def _classify_merchant_foundation(
    active_entities: list[dict[str, Any]],
    all_entity_ids: set[int],
    stores: list[dict[str, Any]],
    markets: list[dict[str, Any]],
    market_payment_codes: dict[int, list[str]],
) -> tuple[list[dict[str, Any]], dict[str, Any], int | None, int | None]:
    """Classify persisted merchant rows using the ARCH-012 bootstrap contract."""
    findings: list[dict[str, Any]] = []
    active_entity_ids = [row["id"] for row in active_entities]

    if len(active_entities) != 1:
        findings.append(_finding(
            "CONFLICT" if len(active_entities) > 1 else "UNPROVABLE",
            "merchant_foundation",
            "active_legal_entities",
            f"Expected exactly one active LegalEntity; found {len(active_entities)}.",
            active_legal_entity_ids=active_entity_ids,
        ))
    for entity in active_entities:
        classification = "PROVEN" if entity["accounting_currency_id"] else "UNPROVABLE"
        reason = (
            "Active LegalEntity has an authoritative Accounting Currency."
            if entity["accounting_currency_id"]
            else "Active LegalEntity has no Accounting Currency."
        )
        findings.append(_finding(
            classification,
            "LegalEntity",
            entity["id"],
            reason,
            accounting_currency_id=entity["accounting_currency_id"],
        ))

    sole_active_entity_id = (
        active_entities[0]["id"]
        if len(active_entities) == 1 and active_entities[0]["accounting_currency_id"]
        else None
    )
    store_ids = [row["id"] for row in stores]
    exactly_one_store = len(stores) == 1

    for store in stores:
        entity_id = store["legal_entity_id"]
        if entity_id is None:
            classification = "UNPROVABLE"
            reason = "Store has no LegalEntity relationship."
        elif entity_id not in all_entity_ids:
            classification = "CONFLICT"
            reason = "Store references a LegalEntity that does not exist."
        elif sole_active_entity_id is None:
            classification = "UNPROVABLE"
            reason = "The single active LegalEntity is not proven."
        elif entity_id != sole_active_entity_id:
            classification = "CONFLICT"
            reason = "Store does not belong to the required single active LegalEntity."
        else:
            classification = "PROVEN"
            reason = "Store has exactly one valid relationship to the active LegalEntity."
        findings.append(_finding(
            classification,
            "Store",
            store["id"],
            reason,
            legal_entity_id=entity_id,
            relationship_result=classification,
        ))

    active_markets = [row for row in markets if row["is_active"]]
    bootstrap_store_id = stores[0]["id"] if exactly_one_store else None
    candidate_active_markets = [
        row for row in active_markets if row["store_id"] == bootstrap_store_id
    ] if bootstrap_store_id is not None else []
    alternative_active_markets = [
        row for row in active_markets if row["store_id"] != bootstrap_store_id
    ]

    for market in markets:
        payment_codes = market_payment_codes.get(market["id"], [])
        if market["store_id"] not in store_ids:
            classification = "CONFLICT"
            reason = "StoreMarket references a Store that does not exist."
        elif market["is_active"] and not payment_codes:
            classification = "UNPROVABLE"
            reason = "Active StoreMarket has no enabled payment currencies."
        elif market["is_active"]:
            classification = "PROVEN"
            reason = "Active StoreMarket has a valid Store and payment currencies."
        else:
            classification = "PROVEN"
            reason = "Inactive StoreMarket is reported and is not bootstrap evidence."
        findings.append(_finding(
            classification,
            "StoreMarket",
            market["id"],
            reason,
            store_id=market["store_id"],
            is_active=market["is_active"],
            payment_currencies=payment_codes,
            payment_currency_validation=(
                "PROVEN" if payment_codes or not market["is_active"] else "UNPROVABLE"
            ),
        ))

    store_relationship_proven = (
        exactly_one_store
        and sole_active_entity_id is not None
        and stores[0]["legal_entity_id"] == sole_active_entity_id
        and stores[0]["legal_entity_id"] in all_entity_ids
    )
    exactly_one_active_market = (
        len(candidate_active_markets) == 1 and not alternative_active_markets
    )
    market_configuration_valid = (
        exactly_one_active_market
        and bool(market_payment_codes.get(candidate_active_markets[0]["id"], []))
    )
    bootstrap_proven = (
        exactly_one_store
        and store_relationship_proven
        and exactly_one_active_market
        and market_configuration_valid
    )

    if bootstrap_proven:
        bootstrap_classification = "PROVEN"
        bootstrap_reason = (
            "ARCH-012 first-release bootstrap proven: exactly one persisted Store, "
            "valid active LegalEntity relationship, and exactly one valid active "
            "StoreMarket. This is not historical Store activity evidence."
        )
    elif len(stores) > 1 or len(candidate_active_markets) > 1 or alternative_active_markets:
        bootstrap_classification = "CONFLICT"
        bootstrap_reason = (
            "ARCH-012 bootstrap has alternative or conflicting Store/StoreMarket state."
        )
    else:
        bootstrap_classification = "UNPROVABLE"
        bootstrap_reason = (
            "ARCH-012 bootstrap conditions are incomplete or cannot be proven."
        )
    findings.append(_finding(
        bootstrap_classification,
        "merchant_foundation",
        "first_release_store_bootstrap",
        bootstrap_reason,
        store_id=bootstrap_store_id,
        exactly_one_persisted_store=exactly_one_store,
        exactly_one_active_store_market=exactly_one_active_market,
    ))

    foundation = {
        "total_store_count": len(stores),
        "persisted_store_ids": store_ids,
        "bootstrap_store_id": bootstrap_store_id,
        "exactly_one_persisted_store": exactly_one_store,
        "store_legal_entity_relationship_proven": store_relationship_proven,
        "active_store_market_count": len(candidate_active_markets),
        "active_store_market_ids": [row["id"] for row in candidate_active_markets],
        "alternative_active_store_market_ids": [
            row["id"] for row in alternative_active_markets
        ],
        "exactly_one_active_store_market": exactly_one_active_market,
        "payment_currency_configuration_valid": market_configuration_valid,
        "first_release_store_bootstrap": bootstrap_classification,
        "historical_store_activity_proven": False,
    }
    return (
        findings,
        foundation,
        bootstrap_store_id if bootstrap_proven else None,
        sole_active_entity_id if bootstrap_proven else None,
    )


def build_report() -> dict[str, Any]:
    """Read the current dataset and return deterministic fail-closed findings."""
    active_entities = list(
        LegalEntity.objects.filter(is_active=True).order_by("pk").values(
            "id", "accounting_currency_id"
        )
    )
    all_entity_ids = set(LegalEntity.objects.order_by("pk").values_list("id", flat=True))
    stores = list(Store.objects.order_by("pk").values("id", "legal_entity_id"))
    market_objects = list(
        StoreMarket.objects.prefetch_related("payment_currencies").order_by("pk")
    )
    markets = [
        {"id": market.pk, "store_id": market.store_id, "is_active": market.is_active}
        for market in market_objects
    ]
    market_payment_codes = {
        market.pk: sorted(currency.code for currency in market.payment_currencies.all())
        for market in market_objects
    }
    products = list(Product.objects.order_by("pk").values("pk", "uuid"))
    variants = list(ProductVariant.objects.order_by("pk").values("pk", "product_id"))
    prices = list(Price.objects.select_related("variant__product").order_by("pk"))
    histories = list(PriceHistory.objects.order_by("pk"))

    findings, foundation, bootstrap_store_id, active_entity_id = (
        _classify_merchant_foundation(
            active_entities, all_entity_ids, stores, markets, market_payment_codes
        )
    )

    # ARCH-012 explicitly approves this current first-release bootstrap path.
    # It does not prove historical Store activity or historical currency.
    product_classification: dict[int, str] = {}
    for product in products:
        if bootstrap_store_id is None:
            classification = "UNPROVABLE"
            reason = "Product ownership cannot be proven because ARCH-012 bootstrap failed."
        else:
            classification = "PROVEN"
            reason = (
                "Product ownership is proven only for first-release migration through "
                "the approved ARCH-012 sole-Store bootstrap; this is not historical evidence."
            )
        product_classification[product["pk"]] = classification
        findings.append(_finding(
            classification,
            "Product",
            product["uuid"],
            reason,
            product_id=product["pk"],
            store_id=bootstrap_store_id,
            ownership_evidence="ARCH-012_FIRST_RELEASE_BOOTSTRAP",
        ))

    variant_product_ids: dict[int, int] = {}
    for variant in variants:
        product_id = variant["product_id"]
        variant_product_ids[variant["pk"]] = product_id
        if product_id not in product_classification:
            classification = "UNPROVABLE"
            reason = "Variant has no reachable persisted Product."
        elif product_classification[product_id] != "PROVEN":
            classification = "UNPROVABLE"
            reason = "Variant ownership is unprovable because Product ownership is unprovable."
        else:
            classification = "PROVEN"
            reason = "Variant reaches exactly one Product with proven bootstrap ownership."
        findings.append(_finding(
            classification,
            "ProductVariant",
            variant["pk"],
            reason,
            product_id=product_id,
            store_id=bootstrap_store_id if classification == "PROVEN" else None,
        ))

    accounting_code = None
    if active_entity_id is not None:
        accounting_code = (
            LegalEntity.objects.filter(pk=active_entity_id)
            .values_list("accounting_currency__code", flat=True)
            .first()
        )
    for price in prices:
        product_id = variant_product_ids.get(price.variant_id)
        ownership = product_classification.get(product_id, "UNPROVABLE")
        if product_id is None or ownership != "PROVEN" or accounting_code is None:
            classification = "UNPROVABLE"
            reason = (
                "Price has no complete proven Product → Store → LegalEntity → "
                "Accounting Currency path; currency equality is not ownership evidence."
            )
        elif price.currency != accounting_code:
            classification = "CONFLICT"
            reason = (
                f"Legacy Price.currency {price.currency!r} conflicts with proven "
                f"Accounting Currency {accounting_code!r}."
            )
        else:
            classification = "MATCH"
            reason = "Legacy currency matches Accounting Currency through the proven path."
        findings.append(_finding(
            classification,
            "Price",
            price.pk,
            reason,
            variant_id=price.variant_id,
            product_id=product_id,
            product_ownership=ownership,
            store_id=bootstrap_store_id if ownership == "PROVEN" else None,
            legal_entity_id=active_entity_id if ownership == "PROVEN" else None,
            legacy_currency=price.currency,
            accounting_currency=accounting_code,
            comparison_result=classification,
        ))

    for history in histories:
        findings.append(_finding(
            "UNKNOWN",
            "PriceHistory",
            history.pk,
            "No record-specific authoritative historical-currency evidence exists; "
            "current Price, bootstrap Store, or merchant configuration is not proof.",
            variant_id=history.variant_id,
            historical_currency=None,
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
        "merchant_foundation": foundation,
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
            self.stdout.write("Merchant foundation:")
            for name, value in report["merchant_foundation"].items():
                self.stdout.write(f"  {name}: {value}")
            self.stdout.write("Classification counts:")
            for classification, count in report["classification_counts"].items():
                self.stdout.write(f"  {classification}: {count}")
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
