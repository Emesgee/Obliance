"""KPI status — ADR-0019 §3: never stored, derived from the latest approved
measurement, and grey is the first rule. Pure: periods and Decimals in, a
status out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

Color = Literal["groen", "gul", "roed", "graa"]

_MONTHS = {"maaned": 1, "kvartal": 3, "halvaar": 6, "aar": 12}


def period_start(period: str, d: date) -> date:
    n = _MONTHS[period]
    m0 = ((d.month - 1) // n) * n + 1
    return date(d.year, m0, 1)


def period_end(period: str, start: date) -> date:
    n = _MONTHS[period]
    m = start.month - 1 + n
    y = start.year + m // 12
    return date(y, m % 12 + 1, 1) - timedelta(days=1)


def previous_period_start(period: str, start: date) -> date:
    return period_start(period, start - timedelta(days=1))


def is_period_start(period: str, d: date) -> bool:
    return period_start(period, d) == d


def target_text(operator: str, value: Decimal, high: Decimal | None, unit: str) -> str:
    sym = {"gte": "≥", "lte": "≤", "eq": "=", "between": "mellem"}[operator]
    suffix = {"pct": " %", "antal": " stk.", "timer": " timer", "dkk": " kr.", "score": ""}[unit]

    def f(x: Decimal) -> str:
        return f"{x.normalize():f}".replace(".", ",")

    if operator == "between" and high is not None:
        return f"mellem {f(value)} og {f(high)}{suffix}"
    return f"{sym} {f(value)}{suffix}"


def met_and_distance(
    operator: str, target: Decimal, high: Decimal | None, value: Decimal
) -> tuple[bool, Decimal]:
    """Whether the target is met, and the distance to the nearest boundary (≥ 0 when met)."""
    if operator == "gte":
        return value >= target, value - target
    if operator == "lte":
        return value <= target, target - value
    if operator == "eq":
        return value == target, -abs(value - target)
    hi = high if high is not None else target
    if target <= value <= hi:
        return True, min(value - target, hi - value)
    return False, -(min(abs(value - target), abs(value - hi)))


@dataclass(frozen=True, slots=True)
class Status:
    color: Color
    reason: str
    measured_period_start: date | None = None
    value: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Measurement:
    period_start: date
    value: Decimal


def evaluate(
    *,
    period: str,
    operator: str,
    target: Decimal,
    high: Decimal | None,
    warn_band: Decimal,
    latest: Measurement | None,
    today: date,
) -> Status:
    if latest is None:
        return Status("graa", "data mangler")
    cur = period_start(period, today)
    prev = previous_period_start(period, cur)
    if latest.period_start < prev:
        return Status(
            "graa",
            f"forældet — seneste måling {latest.period_start.isoformat()}",
            latest.period_start,
            latest.value,
        )
    met, dist = met_and_distance(operator, target, high, latest.value)
    if not met:
        return Status("roed", "målet ikke opfyldt", latest.period_start, latest.value)
    if dist < warn_band:
        return Status("gul", "opfyldt, tæt på grænsen", latest.period_start, latest.value)
    return Status("groen", "opfyldt", latest.period_start, latest.value)


def default_warn_band(unit: str, target: Decimal) -> Decimal:
    """ADR-0019 afklaring 1: 1 percentage point for pct, else 5 % relative."""
    if unit == "pct":
        return Decimal("1")
    return (abs(target) * Decimal("0.05")).quantize(Decimal("0.0001"))
