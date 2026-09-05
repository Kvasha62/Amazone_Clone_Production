from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models.base_model import BaseModel


class LegalEntity(BaseModel):
    """Legal owner of storefronts and the sole owner of Accounting Currency.

    ``accounting_currency`` is the source of truth for internal commercial
    calculations (ADR-008 §1). It is immutable once the entity exists: a new
    accounting currency requires a new LegalEntity, so historical financial
    facts can never be rewritten or reinterpreted by merchant configuration
    (ADR-008 §1, ADR-011 #8).

    ``is_active`` is soft state: archiving keeps ownership history intact.
    The partial unique constraint keeps exactly one active LegalEntity, while
    the schema stays extensible to multiple archived entities later
    (ADR-011 #1, #4).
    """

    #: Value of ``accounting_currency_id`` as loaded from the database.
    #: Used by :meth:`save` to reject in-place accounting-currency rewrites.
    _LOADED_CURRENCY_ATTR = "_loaded_accounting_currency_id"

    legal_name = models.CharField(
        max_length=255,
        unique=True,
        verbose_name="Legal name",
    )
    accounting_currency = models.ForeignKey(
        "currencies.Currency",
        on_delete=models.PROTECT,
        related_name="legal_entities",
        verbose_name="Accounting currency",
        help_text="Immutable after creation; a new currency requires a new LegalEntity.",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Active",
    )

    class Meta:
        db_table = "merchants_legalentity"
        verbose_name = "Legal entity"
        verbose_name_plural = "Legal entities"
        ordering = ("legal_name",)
        constraints = [
            # First release ships exactly one *active* LegalEntity (ADR-011 #1).
            # A partial unique index on the constant ``is_active = true``
            # enforces "single active row" while remaining extensible to
            # multiple archived entities (ADR-011 #4).
            models.UniqueConstraint(
                fields=("is_active",),
                condition=models.Q(is_active=True),
                name="legalentity_single_active",
            ),
        ]

    @classmethod
    def from_db(cls, db, field_names, values):
        """Remember the persisted accounting currency for immutability checks."""
        instance = super().from_db(db, field_names, values)
        setattr(
            instance,
            cls._LOADED_CURRENCY_ATTR,
            instance.accounting_currency_id,
        )
        return instance

    def save(self, *args, **kwargs):
        """Defense-in-depth guard against accounting-currency rewrites.

        ``MerchantsService`` is the authoritative mutation path, but direct
        ORM saves are also rejected so the domain invariant cannot be
        bypassed by any other context (issue #110: explicit ownership
        boundaries in models and services).
        """
        loaded_currency_id = getattr(self, self._LOADED_CURRENCY_ATTR, None)
        if (
            loaded_currency_id is not None
            and self.accounting_currency_id != loaded_currency_id
        ):
            raise ValidationError(
                "Accounting currency is immutable once a LegalEntity exists. "
                "Create a new LegalEntity for a new accounting currency "
                "(ADR-008)."
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return self.legal_name
