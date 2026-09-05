"""First-deployment merchant data contract (issue #110 / PROD-041).

Exactly one active LegalEntity, one Store and one active StoreMarket
(ADR-011 #1). Accounting Currency is RUB — the current single-currency
behaviour of the platform — and the market enables the currencies already
supported by pricing (RUB/USD/EUR) as payment currencies.
"""

from django.db import migrations


LEGAL_NAME = "Amazone Clone Ltd."
STORE_NAME = "Amazone Clone"
STORE_SLUG = "amazone-clone"
COUNTRY_CODE = "RU"
PAYMENT_CURRENCY_CODES = ("EUR", "RUB", "USD")
ACCOUNTING_CURRENCY_CODE = "RUB"


def seed_first_release(apps, schema_editor):
    Currency = apps.get_model("currencies", "Currency")
    LegalEntity = apps.get_model("merchants", "LegalEntity")
    Store = apps.get_model("merchants", "Store")
    StoreMarket = apps.get_model("merchants", "StoreMarket")

    accounting_currency = Currency.objects.get(code=ACCOUNTING_CURRENCY_CODE)
    entity = LegalEntity.objects.create(
        legal_name=LEGAL_NAME,
        accounting_currency=accounting_currency,
        is_active=True,
    )
    store = Store.objects.create(
        legal_entity=entity,
        name=STORE_NAME,
        slug=STORE_SLUG,
    )
    market = StoreMarket.objects.create(
        store=store,
        country_code=COUNTRY_CODE,
        is_active=True,
    )
    market.payment_currencies.set(
        Currency.objects.filter(code__in=PAYMENT_CURRENCY_CODES),
    )


def unseed_first_release(apps, schema_editor):
    StoreMarket = apps.get_model("merchants", "StoreMarket")
    Store = apps.get_model("merchants", "Store")
    LegalEntity = apps.get_model("merchants", "LegalEntity")

    StoreMarket.objects.filter(
        store__slug=STORE_SLUG,
        country_code=COUNTRY_CODE,
    ).delete()
    Store.objects.filter(slug=STORE_SLUG).delete()
    LegalEntity.objects.filter(legal_name=LEGAL_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("currencies", "0001_initial"),
        ("merchants", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_first_release, unseed_first_release),
    ]
