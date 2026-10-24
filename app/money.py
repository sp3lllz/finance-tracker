"""Amounts are stored as integer pence to avoid floating-point drift."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def parse_pence(value: str | None) -> int:
    """Parse user input like '£1,234.56', '12', '-3.5' into pence. Blank is 0."""
    if value is None:
        return 0
    text = value.strip().replace("£", "").replace(",", "").replace(" ", "")
    if text in ("", "-"):
        return 0
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Not an amount: {value!r}") from exc
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_gbp(pence: int) -> str:
    sign = "-" if pence < 0 else ""
    pounds, rem = divmod(abs(pence), 100)
    return f"{sign}£{pounds:,}.{rem:02d}"


def plain(pence: int) -> str:
    """Decimal string without currency symbol, for form inputs and CSV."""
    sign = "-" if pence < 0 else ""
    pounds, rem = divmod(abs(pence), 100)
    return f"{sign}{pounds}.{rem:02d}"
