"""ADR-0019 §3 — status is derived, grey is the first rule. Pure."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.finance.kpi_status import (
    Measurement,
    default_warn_band,
    evaluate,
    period_end,
    period_start,
    target_text,
)

D = Decimal
TODAY = date(2026, 9, 4)


def _ev(latest, *, op="gte", target="99.8", band="1", period="maaned", high=None):
    return evaluate(
        period=period,
        operator=op,
        target=D(target),
        high=D(high) if high else None,
        warn_band=D(band),
        latest=latest,
        today=TODAY,
    )


def test_periods():
    assert period_start("kvartal", date(2026, 8, 15)) == date(2026, 7, 1)
    assert period_end("kvartal", date(2026, 7, 1)) == date(2026, 9, 30)
    assert period_start("halvaar", date(2026, 9, 4)) == date(2026, 7, 1)
    assert period_end("aar", date(2026, 1, 1)) == date(2026, 12, 31)
    assert period_end("maaned", date(2026, 2, 1)) == date(2026, 2, 28)


def test_grey_first_no_data_and_stale():
    assert _ev(None).color == "graa" and "mangler" in _ev(None).reason
    stale = _ev(Measurement(date(2026, 6, 1), D("99.9")))  # two periods before August
    assert stale.color == "graa" and "forældet" in stale.reason and "2026-06-01" in stale.reason
    assert _ev(Measurement(date(2026, 8, 1), D("99.9"))).color != "graa"  # previous period counts


def test_traffic_light_with_warn_band():
    assert _ev(Measurement(date(2026, 8, 1), D("99.62"))).color == "roed"  # mockup SLA-B1
    assert _ev(Measurement(date(2026, 8, 1), D("99.8"))).color == "gul"  # met, on the line
    assert _ev(Measurement(date(2026, 8, 1), D("100.7"))).color == "gul"  # met, inside 1 pp
    assert _ev(Measurement(date(2026, 9, 1), D("100.8"))).color == "groen"


def test_lte_and_between():
    assert (
        _ev(Measurement(date(2026, 8, 1), D("3.9")), op="lte", target="4", band="0.2").color
        == "gul"
    )
    assert (
        _ev(Measurement(date(2026, 8, 1), D("4.5")), op="lte", target="4", band="0.2").color
        == "roed"
    )
    assert (
        _ev(Measurement(date(2026, 8, 1), D("3")), op="lte", target="4", band="0.2").color
        == "groen"
    )
    s = _ev(Measurement(date(2026, 8, 1), D("5")), op="between", target="2", high="8", band="1")
    assert s.color == "groen"
    assert (
        _ev(
            Measurement(date(2026, 8, 1), D("9")), op="between", target="2", high="8", band="1"
        ).color
        == "roed"
    )


def test_target_text_and_default_band():
    assert target_text("gte", D("99.8"), None, "pct") == "≥ 99,8 %"
    assert target_text("lte", D("4"), None, "timer") == "≤ 4 timer"
    assert target_text("between", D("2"), D("8"), "score") == "mellem 2 og 8"
    assert default_warn_band("pct", D("99.8")) == D("1")
    assert default_warn_band("timer", D("4")) == D("0.2000")
