from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from apps.currencies.models import Currency


MAX_SUPPORTED_MINOR_UNITS = 6


def minor_unit_quantum(currency: Currency) -> Decimal:
    """Return the Decimal quantum for a currency's minor-unit precision."""
    if not 0 <= currency.minor_units <= MAX_SUPPORTED_MINOR_UNITS:
        raise ValueError("Unsupported currency minor-unit precision")
    return Decimal(1).scaleb(-currency.minor_units)


def normalize_amount(amount: Decimal, currency: Currency) -> Decimal:
    """Normalize a monetary Decimal to the currency's minor-unit precision.

    Decimal is deliberately required: converting binary floating-point values
    here would make financial facts non-deterministic.
    """
    if not isinstance(amount, Decimal):
        raise TypeError("Monetary amounts must be Decimal instances")
    if not amount.is_finite():
        raise ValueError("Monetary amount must be finite")
    if amount < 0:
        raise ValueError("Monetary amount must not be negative")

    try:
        return amount.quantize(minor_unit_quantum(currency), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Monetary amount cannot be normalized") from exc
