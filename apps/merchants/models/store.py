from django.db import models

from apps.core.models.base_model import BaseModel
from apps.merchants.models.legal_entity import LegalEntity


class Store(BaseModel):
    """Commercial storefront/channel owned by exactly one LegalEntity.

    Ownership is explicit through the required ``legal_entity`` foreign key
    (ADR-011 #5). The store itself owns no monetary semantics: Accounting
    Currency belongs to the LegalEntity only, and StoreMarket never changes
    the monetary meaning of an Order (ADR-011 #7).
    """

    legal_entity = models.ForeignKey(
        LegalEntity,
        on_delete=models.PROTECT,
        related_name="stores",
        verbose_name="Legal entity",
    )
    name = models.CharField(
        max_length=255,
        verbose_name="Name",
    )
    slug = models.SlugField(
        max_length=100,
        unique=True,
        verbose_name="Slug",
        help_text="Stable public channel identifier.",
    )

    class Meta:
        db_table = "merchants_store"
        verbose_name = "Store"
        verbose_name_plural = "Stores"
        ordering = ("slug",)
        constraints = [
            models.UniqueConstraint(
                fields=("legal_entity", "name"),
                name="store_entity_name_unique",
            ),
        ]

    def __str__(self):
        return self.name
