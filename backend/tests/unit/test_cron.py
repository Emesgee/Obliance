"""ADR-0010 §1: the cron matcher behind agent cadences."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents.definitions import ALERT_CRON, DEFINITIONS
from app.jobs import cron

CPH = ZoneInfo("Europe/Copenhagen")


def at(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=CPH)


def test_simple_daily_expression() -> None:
    assert cron.matches("0 2 * * *", at(2026, 9, 4, 2, 0))
    assert not cron.matches("0 2 * * *", at(2026, 9, 4, 2, 1))
    assert not cron.matches("0 2 * * *", at(2026, 9, 4, 3, 0))


def test_lists_ranges_and_steps() -> None:
    assert cron.matches("*/15 * * * *", at(2026, 1, 1, 10, 45))
    assert not cron.matches("*/15 * * * *", at(2026, 1, 1, 10, 50))
    assert cron.matches("0 6,18 * * 1-5", at(2026, 9, 4, 18, 0))  # a Friday
    assert not cron.matches("0 6,18 * * 1-5", at(2026, 9, 5, 18, 0))  # Saturday
    assert cron.matches("30 1 1 * *", at(2026, 10, 1, 1, 30))
    assert cron.matches("0 0 * * 0", at(2026, 9, 6, 0, 0))  # Sunday as 0
    assert cron.matches("0 0 * * 7", at(2026, 9, 6, 0, 0))  # Sunday as 7
    assert cron.matches("5-20/5 * * * *", at(2026, 1, 1, 0, 15))
    assert not cron.matches("5-20/5 * * * *", at(2026, 1, 1, 0, 16))


@pytest.mark.parametrize(
    "expr",
    [
        "0 2 * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * * 13 *",
        "* * * * 8",
        "a * * * *",
        "*/0 * * * *",
        "10-5 * * * *",
    ],
)
def test_invalid_expressions_are_refused(expr: str) -> None:
    with pytest.raises(cron.CronError):
        cron.validate(expr)


def test_every_definition_has_a_valid_cadence() -> None:
    for d in DEFINITIONS:
        if d.cadence is not None:
            cron.validate(d.cadence)
        assert d.scheduled == (d.cadence is not None and d.trigger != "event")
    cron.validate(ALERT_CRON)
    # nothing runs every minute (ADR-0010 §1)
    for d in DEFINITIONS:
        if d.cadence:
            assert not d.cadence.startswith("* ")
