from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models.base_model import BaseModel


class Currency(BaseModel):
    """Authoritative ISO-4217 currency registry entry.

    ``code`` and ``numeric_code`` identify the ISO currency; ``minor_units``
    defines the smallest persisted monetary unit. Deactivation is soft so
    historical financial records can continue referencing the currency.
    """

    code = models.CharField(
        max_length=3,
        unique=True,
        db_index=True,
        verbose_name="ISO alphabetic code",
    )
    numeric_code = models.PositiveSmallIntegerField(
        unique=True,
        verbose_name="ISO numeric code",
        validators=[MinValueValidator(1), MaxValueValidator(999)],
    )
    minor_units = models.PositiveSmallIntegerField(
        verbose_name="Minor units",
        validators=[MinValueValidator(0), MaxValueValidator(6)],
        help_text="Number of decimal places used by the currency.",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Active",
    )

    class Meta:
        db_table = "currencies_currency"
        verbose_name = "Currency"
        verbose_name_plural = "Currencies"
        ordering = ("code",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(code__regex=r"^[A-Z]{3}$"),
                name="currency_code_iso_alpha",
            ),
            models.CheckConstraint(
                condition=models.Q(numeric_code__gte=1)
                & models.Q(numeric_code__lte=999),
                name="currency_numeric_1_999",
            ),
            models.CheckConstraint(
                condition=models.Q(minor_units__gte=0)
                & models.Q(minor_units__lte=6),
                name="currency_minor_units_0_6",
            ),
        ]

    def __str__(self):
        return self.code
