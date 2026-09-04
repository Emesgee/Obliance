"""A five-field cron matcher — `minute hour day month weekday` — small enough to
own rather than pull in a scheduler library for seven agents (ADR-0010 alternatives).

Supports `*`, lists `1,15`, ranges `1-5`, steps `*/10` and `1-30/5`. Weekday
0–7 with both 0 and 7 meaning Sunday (cron convention). No names, no `L`/`W`.
"""

from __future__ import annotations

from datetime import datetime

_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NAMES = ("minut", "time", "dag", "måned", "ugedag")


class CronError(ValueError):
    pass


def _field(spec: str, lo: int, hi: int, name: str) -> frozenset[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronError(f"Ugyldigt trin i {name}: {step_s!r}")
            step = int(step_s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a_s, b_s = part.split("-", 1)
            if not (a_s.isdigit() and b_s.isdigit()):
                raise CronError(f"Ugyldigt interval i {name}: {part!r}")
            a, b = int(a_s), int(b_s)
        elif part.isdigit():
            a = b = int(part)
            if step != 1:  # "5/10" means "5-hi/10" in Vixie cron
                b = hi
        else:
            raise CronError(f"Ugyldigt felt i {name}: {part!r}")
        if a < lo or b > hi or a > b:
            raise CronError(f"{name} uden for {lo}–{hi}: {part!r}")
        out.update(range(a, b + 1, step))
    return frozenset(out)


def parse(expr: str) -> tuple[frozenset[int], ...]:
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"Cron-udtryk skal have 5 felter, ikke {len(fields)}: {expr!r}")
    parsed = tuple(
        _field(f, lo, hi, name) for f, (lo, hi), name in zip(fields, _BOUNDS, _NAMES, strict=True)
    )
    dow = {0 if d == 7 else d for d in parsed[4]}
    return (*parsed[:4], frozenset(dow))


def validate(expr: str) -> None:
    parse(expr)


def matches(expr: str, at: datetime) -> bool:
    """True when `at` (already in the schedule's timezone) is a firing minute."""
    minute, hour, day, month, dow = parse(expr)
    return (
        at.minute in minute
        and at.hour in hour
        and at.day in day
        and at.month in month
        and (at.isoweekday() % 7) in dow  # Monday=1 … Sunday=0
    )
