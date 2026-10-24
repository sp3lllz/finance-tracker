import datetime as dt
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .credit import Debt
from .structure import BILL, BLANK_MONTH, INCOME, KINDS, PLANNED

SCHEMA = """
CREATE TABLE IF NOT EXISTS months (
    year  INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    PRIMARY KEY (year, month)
);
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY,
    year         INTEGER NOT NULL,
    month        INTEGER NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('income', 'bill', 'planned')),
    category     TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL,
    amount_pence INTEGER NOT NULL DEFAULT 0,
    position     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (year, month) REFERENCES months (year, month) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS items_by_month ON items (year, month, kind, position);
CREATE TABLE IF NOT EXISTS debts (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'other',
    balance_pence INTEGER NOT NULL DEFAULT 0,
    apr_bp        INTEGER NOT NULL DEFAULT 0,
    payment_pence INTEGER NOT NULL DEFAULT 0,
    promo_apr_bp  INTEGER,
    promo_until   TEXT,
    notes         TEXT NOT NULL DEFAULT '',
    updated_on    TEXT NOT NULL DEFAULT (date('now'))
);
"""


@dataclass
class Item:
    id: int
    year: int
    month: int
    kind: str
    category: str
    name: str
    amount_pence: int
    position: int


@dataclass
class MonthTotals:
    income: int = 0
    bills: int = 0
    planned: int = 0

    @property
    def after_bills(self) -> int:
        return self.income - self.bills

    @property
    def after_spending(self) -> int:
        return self.income - self.bills - self.planned

    @property
    def outgoings(self) -> int:
        return self.bills + self.planned


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- months -----------------------------------------------------------

    def month_exists(self, year: int, month: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM months WHERE year = ? AND month = ?", (year, month)
            ).fetchone()
        return row is not None

    def previous_month(self, year: int, month: int) -> tuple[int, int] | None:
        """Most recent existing month strictly before (year, month)."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT year, month FROM months WHERE year * 12 + month < ? "
                "ORDER BY year DESC, month DESC LIMIT 1",
                (year * 12 + month,),
            ).fetchone()
        return (row["year"], row["month"]) if row else None

    def years(self) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT DISTINCT year FROM months ORDER BY year").fetchall()
        return [r["year"] for r in rows]

    def create_blank_month(self, year: int, month: int) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO months (year, month) VALUES (?, ?)", (year, month))
            for position, (kind, category, name) in enumerate(BLANK_MONTH):
                conn.execute(
                    "INSERT INTO items (year, month, kind, category, name, amount_pence, position) "
                    "VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (year, month, kind, category, name, position),
                )

    def copy_month(self, src: tuple[int, int], dest: tuple[int, int]) -> None:
        """Start `dest` with the income and recurring bills of `src`.

        Planned spending is one-off, so it is not carried over.
        """
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO months (year, month) VALUES (?, ?)", dest)
            conn.execute(
                "INSERT INTO items (year, month, kind, category, name, amount_pence, position) "
                "SELECT ?, ?, kind, category, name, amount_pence, position FROM items "
                "WHERE year = ? AND month = ? AND kind IN (?, ?)",
                (*dest, *src, INCOME, BILL),
            )

    def delete_month(self, year: int, month: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM items WHERE year = ? AND month = ?", (year, month))
            conn.execute("DELETE FROM months WHERE year = ? AND month = ?", (year, month))

    # --- items ------------------------------------------------------------

    def items(self, year: int, month: int) -> list[Item]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM items WHERE year = ? AND month = ? ORDER BY position, id",
                (year, month),
            ).fetchall()
        return [Item(**dict(r)) for r in rows]

    def all_items(self) -> list[Item]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM items ORDER BY year, month, position, id"
            ).fetchall()
        return [Item(**dict(r)) for r in rows]

    def get_item(self, item_id: int) -> Item | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return Item(**dict(row)) if row else None

    def add_item(self, year: int, month: int, kind: str, category: str, name: str,
                 amount_pence: int) -> None:
        if kind not in KINDS:
            raise ValueError(f"Unknown kind {kind!r}")
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO months (year, month) VALUES (?, ?)", (year, month))
            (position,) = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM items WHERE year = ? AND month = ?",
                (year, month),
            ).fetchone()
            conn.execute(
                "INSERT INTO items (year, month, kind, category, name, amount_pence, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (year, month, kind, category, name, amount_pence, position),
            )

    def update_item(self, item_id: int, name: str, amount_pence: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE items SET name = ?, amount_pence = ? WHERE id = ?",
                (name, amount_pence, item_id),
            )

    def delete_item(self, item_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))

    def replace_months(self, rows: list[tuple[int, int, str, str, str, int]]) -> int:
        """Replace every month that appears in `rows` with exactly those rows.

        Runs in one transaction, so a bad import leaves the database untouched.
        """
        months = sorted({(r[0], r[1]) for r in rows})
        with self.connect() as conn:
            for year, month in months:
                conn.execute("DELETE FROM items WHERE year = ? AND month = ?", (year, month))
                conn.execute("INSERT OR IGNORE INTO months (year, month) VALUES (?, ?)", (year, month))
            for position, (year, month, kind, category, name, pence) in enumerate(rows):
                conn.execute(
                    "INSERT INTO items (year, month, kind, category, name, amount_pence, position) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (year, month, kind, category, name, pence, position),
                )
        return len(months)

    # --- debts ------------------------------------------------------------

    DEBT_FIELDS = ("name", "kind", "balance_pence", "apr_bp", "payment_pence",
                   "promo_apr_bp", "promo_until", "notes")

    @staticmethod
    def _debt(row: sqlite3.Row) -> Debt:
        data = {k: row[k] for k in ("id", *Database.DEBT_FIELDS)}
        data["promo_until"] = dt.date.fromisoformat(row["promo_until"]) if row["promo_until"] else None
        return Debt(**data, updated_on=dt.date.fromisoformat(row["updated_on"]))

    def debts(self) -> list[Debt]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM debts ORDER BY apr_bp DESC, name").fetchall()
        return [self._debt(r) for r in rows]

    def get_debt(self, debt_id: int) -> Debt | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
        return self._debt(row) if row else None

    def save_debt(self, values: dict, debt_id: int | None = None) -> None:
        values = dict(values)
        if values.get("promo_until"):
            values["promo_until"] = values["promo_until"].isoformat()
        cols = self.DEBT_FIELDS
        params = [values.get(c) for c in cols]
        with self.connect() as conn:
            if debt_id is None:
                conn.execute(f"INSERT INTO debts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             params)
            else:
                sets = ", ".join(f"{c} = ?" for c in cols)
                conn.execute(f"UPDATE debts SET {sets}, updated_on = date('now') WHERE id = ?",
                             (*params, debt_id))

    def delete_debt(self, debt_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM debts WHERE id = ?", (debt_id,))

    # --- totals -----------------------------------------------------------

    def year_totals(self, year: int) -> dict[int, MonthTotals]:
        """Totals for each existing month of `year`, keyed by month number."""
        with self.connect() as conn:
            existing = conn.execute("SELECT month FROM months WHERE year = ?", (year,)).fetchall()
            sums = conn.execute(
                "SELECT month, kind, SUM(amount_pence) AS total FROM items "
                "WHERE year = ? GROUP BY month, kind",
                (year,),
            ).fetchall()
        result = {r["month"]: MonthTotals() for r in existing}
        for r in sums:
            totals = result.setdefault(r["month"], MonthTotals())
            if r["kind"] == INCOME:
                totals.income = r["total"]
            elif r["kind"] == BILL:
                totals.bills = r["total"]
            elif r["kind"] == PLANNED:
                totals.planned = r["total"]
        return result


def totals_for(items: list[Item]) -> MonthTotals:
    totals = MonthTotals()
    for item in items:
        if item.kind == INCOME:
            totals.income += item.amount_pence
        elif item.kind == BILL:
            totals.bills += item.amount_pence
        elif item.kind == PLANNED:
            totals.planned += item.amount_pence
    return totals
