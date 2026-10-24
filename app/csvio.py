"""CSV export and import. Format: year,month,kind,category,name,amount"""

import csv
import io

from .credit import format_rate
from .db import Item
from .money import parse_pence, plain
from .structure import KINDS

HEADER = ["year", "month", "kind", "category", "name", "amount"]


def export_csv(items: list[Item]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADER)
    for i in items:
        writer.writerow([i.year, i.month, i.kind, i.category, i.name, plain(i.amount_pence)])
    return out.getvalue()


class ImportError_(ValueError):
    pass


def parse_csv(text: str) -> list[tuple[int, int, str, str, str, int]]:
    """Validate the whole file before anything is written."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = set(HEADER) - set(reader.fieldnames or [])
    if missing:
        raise ImportError_(f"Missing column(s): {', '.join(sorted(missing))}")
    rows = []
    for line, rec in enumerate(reader, start=2):
        try:
            year = int(rec["year"])
            month = int(rec["month"])
            kind = rec["kind"].strip().lower()
            name = rec["name"].strip()
            if not 1 <= month <= 12:
                raise ValueError("month must be 1-12")
            if kind not in KINDS:
                raise ValueError(f"kind must be one of {', '.join(KINDS)}")
            if not name:
                raise ValueError("name is empty")
            rows.append((year, month, kind, rec["category"].strip(), name,
                         parse_pence(rec["amount"])))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ImportError_(f"Line {line}: {exc}") from exc
    if not rows:
        raise ImportError_("The file has no rows.")
    return rows


DEBT_HEADER = ["name", "kind", "balance", "apr", "monthly_payment", "promo_apr",
               "promo_until", "balance_updated", "notes"]


def export_debts_csv(debts) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(DEBT_HEADER)
    for d in debts:
        writer.writerow([d.name, d.kind, plain(d.balance_pence), format_rate(d.apr_bp),
                         plain(d.payment_pence), format_rate(d.promo_apr_bp),
                         d.promo_until.isoformat() if d.promo_until else "",
                         d.updated_on.isoformat() if d.updated_on else "", d.notes])
    return out.getvalue()
