"""Debt payoff projections.

Everything is an estimate: lenders calculate interest daily and post it on
their own statement dates. Here each month adds one month of interest, then
takes the payment. The APR is treated as an annual *effective* rate (as UK
lenders quote it), so the monthly rate is (1 + APR) ** (1/12) - 1.
"""

import dataclasses
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

MAX_MONTHS = 600  # 50 years: stop projecting past this

DEBT_KINDS = {
    "credit_card": "Credit card",
    "loan": "Loan",
    "mortgage": "Mortgage",
    "car_finance": "Car finance",
    "bnpl": "Buy now, pay later",
    "overdraft": "Overdraft",
    "other": "Other",
}

STRATEGIES = {
    "avalanche": "Highest interest rate first (saves the most)",
    "snowball": "Smallest balance first (quickest wins)",
}


@dataclass
class Debt:
    id: int
    name: str
    kind: str
    balance_pence: int
    apr_bp: int  # basis points: 24.9% APR -> 2490
    payment_pence: int
    promo_apr_bp: int | None = None
    promo_until: dt.date | None = None
    notes: str = ""
    updated_on: dt.date | None = None  # when the balance was last entered

    def apr_on(self, day: dt.date) -> int:
        if self.promo_until and self.promo_apr_bp is not None and day < self.promo_until:
            return self.promo_apr_bp
        return self.apr_bp


@dataclass
class DebtResult:
    debt: Debt
    months: int | None = None  # None: not cleared within MAX_MONTHS
    interest_pence: int = 0
    paid_pence: int = 0

    @property
    def cleared(self) -> bool:
        return self.months is not None


@dataclass
class Projection:
    start: dt.date
    results: list[DebtResult]
    balances: list[int] = field(default_factory=list)  # total owed after each month

    @property
    def months(self) -> int | None:
        if any(not r.cleared for r in self.results):
            return None
        return max((r.months for r in self.results), default=0)

    @property
    def interest_pence(self) -> int:
        return sum(r.interest_pence for r in self.results)

    def month_date(self, months: int) -> dt.date:
        return add_months(self.start, months - 1)


def add_months(day: dt.date, months: int) -> dt.date:
    index = day.year * 12 + day.month - 1 + months
    return dt.date(index // 12, index % 12 + 1, 1)


def monthly_rate(apr_bp: int) -> float:
    return (1 + apr_bp / 10_000) ** (1 / 12) - 1


def project(debts: list[Debt], start: dt.date, extra_pence: int = 0,
            strategy: str | None = None) -> Projection:
    """Simulate month by month.

    With strategy=None every debt is paid on its own at its set payment.
    With a strategy, payments from cleared debts roll over (plus any extra)
    and go to the target debt chosen by the strategy.
    """
    start = dt.date(start.year, start.month, 1)
    balance = {d.id: float(d.balance_pence) for d in debts}
    results = {d.id: DebtResult(d) for d in debts}
    for d in debts:
        if d.balance_pence <= 0:
            results[d.id].months = 0
    balances = []
    month = 0
    while month < MAX_MONTHS and any(b > 0.5 for b in balance.values()):
        month += 1
        today = add_months(start, month - 1)
        pool = extra_pence if strategy else 0
        for d in debts:
            if balance[d.id] <= 0.5:
                if strategy:
                    pool += d.payment_pence  # cleared debt frees its payment
                continue
            interest = round(balance[d.id] * monthly_rate(d.apr_on(today)))
            balance[d.id] += interest
            results[d.id].interest_pence += interest
            pay = min(d.payment_pence, balance[d.id])
            balance[d.id] -= pay
            results[d.id].paid_pence += round(pay)
            if strategy and pay < d.payment_pence:
                pool += d.payment_pence - pay
        if strategy:
            for d in _order(debts, balance, strategy, today):
                if pool <= 0:
                    break
                pay = min(pool, balance[d.id])
                balance[d.id] -= pay
                results[d.id].paid_pence += round(pay)
                pool -= pay
        for d in debts:
            if balance[d.id] <= 0.5 and results[d.id].months is None:
                balance[d.id] = 0
                results[d.id].months = month
        balances.append(round(sum(max(b, 0) for b in balance.values())))
    return Projection(start, [results[d.id] for d in debts], balances)


def _order(debts: list[Debt], balance: dict[int, float], strategy: str,
           today: dt.date) -> list[Debt]:
    live = [d for d in debts if balance[d.id] > 0.5]
    if strategy == "snowball":
        return sorted(live, key=lambda d: (balance[d.id], -d.apr_on(today)))
    return sorted(live, key=lambda d: (-d.apr_on(today), balance[d.id]))


def payment_to_clear(debt: Debt, months: int, start: dt.date) -> int:
    """Smallest monthly payment (pence) that clears `debt` within `months`."""
    low, high = 1, max(debt.balance_pence, 1) * 2
    while low < high:
        mid = (low + high) // 2
        trial = dataclasses.replace(debt, payment_pence=mid)
        result = project([trial], start).results[0]
        if result.cleared and result.months <= months:
            high = mid
        else:
            low = mid + 1
    return low


def parse_rate_bp(value: str | None) -> int:
    """'24.9', '24.9%' -> 2490 basis points."""
    text = (value or "").strip().rstrip("%").strip()
    if not text:
        return 0
    try:
        rate = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Not a rate: {value!r}") from exc
    if not 0 <= rate <= 1000:
        raise ValueError("APR must be between 0 and 1000%")
    return int((rate * 100).to_integral_value())


def format_rate(bp: int | None) -> str:
    if bp is None:
        return ""
    return f"{Decimal(bp) / 100:f}".rstrip("0").rstrip(".") or "0"


def describe_months(months: int | None) -> str:
    if months is None:
        return f"over {MAX_MONTHS // 12} years"
    years, rem = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} yr" + ("s" if years != 1 else ""))
    if rem or not years:
        parts.append(f"{rem} mo")
    return " ".join(parts)
