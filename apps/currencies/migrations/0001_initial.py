from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


CURRENCIES = (
    ("EUR", 978, 2),
    ("JPY", 392, 0),
    ("RUB", 643, 2),
    ("USD", 840, 2),
)


def seed_currencies(apps, schema_editor):
    Currency = apps.get_model("currencies", "Currency")
    Currency.objects.bulk_create(
        [
            Currency(
                code=code,
                numeric_code=numeric_code,
                minor_units=minor_units,
                is_active=True,
            )
            for code, numeric_code, minor_units in CURRENCIES
        ]
    )


def unseed_currencies(apps, schema_editor):
    Currency = apps.get_model("currencies", "Currency")
    Currency.objects.filter(code__in=[code for code, _, _ in CURRENCIES]).delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Currency",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        verbose_name="Создано",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Обновлено"),
                ),
                (
                    "code",
                    models.CharField(
                        db_index=True,
                        max_length=3,
                        unique=True,
                        verbose_name="ISO alphabetic code",
                    ),
                ),
                (
                    "numeric_code",
                    models.PositiveSmallIntegerField(
                        unique=True,
                        verbose_name="ISO numeric code",
                        validators=[MinValueValidator(1), MaxValueValidator(999)],
                    ),
                ),
                (
                    "minor_units",
                    models.PositiveSmallIntegerField(
                        help_text="Number of decimal places used by the currency.",
                        verbose_name="Minor units",
                        validators=[MinValueValidator(0), MaxValueValidator(6)],
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        db_index=True,
                        default=True,
                        verbose_name="Active",
                    ),
                ),
            ],
            options={
                "verbose_name": "Currency",
                "verbose_name_plural": "Currencies",
                "db_table": "currencies_currency",
                "ordering": ("code",),
            },
        ),
        migrations.AddConstraint(
            model_name="currency",
            constraint=models.CheckConstraint(
                condition=models.Q(code__regex=r"^[A-Z]{3}$"),
                name="currency_code_iso_alpha",
            ),
        ),
        migrations.AddConstraint(
            model_name="currency",
            constraint=models.CheckConstraint(
                condition=models.Q(numeric_code__gte=1)
                & models.Q(numeric_code__lte=999),
                name="currency_numeric_1_999",
            ),
        ),
        migrations.AddConstraint(
            model_name="currency",
            constraint=models.CheckConstraint(
                condition=models.Q(minor_units__gte=0)
                & models.Q(minor_units__lte=6),
                name="currency_minor_units_0_6",
            ),
        ),
        migrations.RunPython(seed_currencies, unseed_currencies),
    ]
