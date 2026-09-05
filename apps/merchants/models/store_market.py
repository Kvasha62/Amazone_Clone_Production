from django.db import models

from apps.core.models.base_model import BaseModel
from apps.merchants.models.store import Store


class StoreMarket(BaseModel):
    """First-release commercial configuration of a Store for one market.

    A StoreMarket carries only what is required to resolve the commercial
    market: sales country, active state and enabled payment currencies
    (issue #92 "Minimal StoreMarket scope", ADR-011 #6). Provider profiles
    and shipping-zone associations are deliberately out of scope for this
    slice (issue #110 non-goals).

    StoreMarket never owns Accounting Currency and never changes the
    monetary semantics of an Order (ADR-011 #7): ``payment_currencies``
    restrict what the buyer may pay with, while commercial calculation
    stays denominated in ``LegalEntity.accounting_currency``.
    """

    store = models.ForeignKey(
        Store,
        on_delete=models.PROTECT,
        related_name="markets",
        verbose_name="Store",
    )
    country_code = models.CharField(
        max_length=2,
        verbose_name="Country code",
        help_text="ISO 3166-1 alpha-2 sales geography of the market.",
    )
    payment_currencies = models.ManyToManyField(
        "currencies.Currency",
        related_name="store_markets",
        verbose_name="Enabled payment currencies",
        help_text="Currencies the buyer may pay with in this market.",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Active",
    )

    class Meta:
        db_table = "merchants_storemarket"
        verbose_name = "Store market"
        verbose_name_plural = "Store markets"
        ordering = ("country_code",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(country_code__regex=r"^[A-Z]{2}$"),
                name="storemarket_country_iso_alpha2",
            ),
            models.UniqueConstraint(
                fields=("store", "country_code"),
                name="storemarket_store_country_uniq",
            ),
            # First release ships exactly one *active* market per store
            # (ADR-011 #1) while staying extensible to one market per
            # country later (ADR-011 #4).
            models.UniqueConstraint(
                fields=("store", "is_active"),
                condition=models.Q(is_active=True),
                name="storemarket_active_per_store",
            ),
        ]

    def __str__(self):
        return f"{self.store.slug}:{self.country_code}"
