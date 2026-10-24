"""Server-rendered SVG bar chart: income vs outgoings for each month of a year."""

from dataclasses import dataclass

from .db import MonthTotals
from .money import format_gbp
from .structure import MONTH_NAMES

WIDTH, HEIGHT = 720, 260
LEFT, RIGHT, TOP, BOTTOM = 56, 8, 12, 28
BAR_W, GAP = 14, 2  # thin bars, 2px surface gap between the pair


@dataclass
class Bar:
    path: str
    css: str


@dataclass
class Group:
    label: str
    x: float
    width: float
    tip: str
    bars: list[Bar]


@dataclass
class Tick:
    y: float
    label: str


@dataclass
class Chart:
    width: int
    height: int
    left: int
    right_edge: int
    baseline: float
    ticks: list[Tick]
    groups: list[Group]
    empty: bool


def _nice_max(value: int) -> int:
    """Round the axis top up to 1, 2, 2.5 or 5 x a power of ten (in pence)."""
    if value <= 0:
        return 100_000
    magnitude = 10 ** (len(str(value)) - 1)
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return int(step * magnitude)
    return 10 * magnitude


def _bar_path(x: float, y: float, w: float, base: float, r: float = 4) -> str:
    """Rounded data-end, square at the baseline."""
    h = base - y
    if h <= 0:
        return ""
    r = min(r, h, w / 2)
    return (f"M{x:.1f},{base:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
            f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{base:.1f}Z")


def build(year_totals: dict[int, MonthTotals]) -> Chart:
    peak = max([max(t.income, t.outgoings) for t in year_totals.values()] or [0])
    top = _nice_max(peak)
    plot_h = HEIGHT - TOP - BOTTOM
    base = TOP + plot_h
    slot = (WIDTH - LEFT - RIGHT) / 12

    def y_of(pence: int) -> float:
        return base - max(pence, 0) / top * plot_h

    ticks = [Tick(y=y_of(top * i // 4), label=format_gbp(top * i // 4).split(".")[0])
             for i in range(5)]
    groups = []
    for m in range(1, 13):
        t = year_totals.get(m)
        x0 = LEFT + (m - 1) * slot
        cx = x0 + slot / 2
        bars = []
        tip = f"{MONTH_NAMES[m - 1]}: no data"
        if t is not None:
            bars = [
                Bar(_bar_path(cx - BAR_W - GAP / 2, y_of(t.income), BAR_W, base), "series-1"),
                Bar(_bar_path(cx + GAP / 2, y_of(t.outgoings), BAR_W, base), "series-2"),
            ]
            tip = (f"{MONTH_NAMES[m - 1]}|Income {format_gbp(t.income)}"
                   f"|Outgoings {format_gbp(t.outgoings)}"
                   f"|Left over {format_gbp(t.after_spending)}")
        groups.append(Group(MONTH_NAMES[m - 1][:3], x0, slot, tip, bars))
    return Chart(WIDTH, HEIGHT, LEFT, WIDTH - RIGHT, base, ticks, groups,
                 empty=not year_totals)


# --- debt balance over time --------------------------------------------------

@dataclass
class Line:
    path: str
    css: str
    label: str
    end_x: float
    end_y: float


@dataclass
class LineChart:
    width: int
    height: int
    left: int
    right_edge: int
    baseline: float
    ticks: list[Tick]
    x_labels: list[tuple[float, str]]
    lines: list[Line]
    hits: list[Group]


def build_balances(start_total: int, series: list[tuple[str, str, list[int]]],
                   start, add_months) -> LineChart:
    """`series` is [(label, css class, balances after each month)]."""
    width, height, left, right = 720, 260, 64, 120  # room on the right for direct labels
    months = max(len(s[2]) for s in series) or 1
    top = _nice_max(start_total)
    plot_h = height - TOP - BOTTOM
    base = TOP + plot_h
    plot_w = width - left - right

    def x_of(m: int) -> float:
        return left + m / months * plot_w

    def y_of(pence: int) -> float:
        return base - max(pence, 0) / top * plot_h

    lines = []
    for label, css, balances in series:
        points = [start_total, *balances]
        d = "M" + " L".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, v in enumerate(points))
        lines.append(Line(d, css, label, x_of(len(points) - 1), y_of(points[-1])))
    # Keep the two end labels from overlapping.
    if len(lines) == 2 and abs(lines[0].end_y - lines[1].end_y) < 14:
        lines[1].end_y = lines[0].end_y - 14

    ticks = [Tick(y=y_of(top * i // 4), label=format_gbp(top * i // 4).split(".")[0])
             for i in range(5)]
    year_step = max(1, round(months / 12 / 8))
    x_labels = []
    for m in range(0, months + 1):
        day = add_months(start, m)
        if day.month == 1 and (day.year % year_step == 0):
            x_labels.append((x_of(m), str(day.year)))

    step = max(1, -(-months // 60))  # at most ~60 hover columns
    hits = []
    for m in range(1, months + 1, step):
        day = add_months(start, m - 1)
        tip = day.strftime("%b %Y")
        for label, _, balances in series:
            value = balances[m - 1] if m - 1 < len(balances) else 0
            tip += f"|{label} {format_gbp(value)}"
        x0 = x_of(m - 1)
        hits.append(Group("", x0, x_of(min(m - 1 + step, months)) - x0, tip, []))
    return LineChart(width, height, left, width - right, base, ticks, x_labels, lines, hits)
