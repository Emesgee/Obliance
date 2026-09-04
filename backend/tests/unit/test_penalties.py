"""ADR-0013 §2 — pure calculation, mockup numbers first. No database."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.finance import penalties
from app.finance.penalties import DataMissing, Term, Unstructurable, calculate

D = Decimal


def test_mockup_service_credit_5_pct_of_monthly_fee():
    term = Term(
        "service_credit_pct_of_fee",
        rate=D("0.05"),
        basis="maanedligt_driftsvederlag",
        basis_amount=D("612500.00"),
        citation_label="Bilag 5 · s. 2 · tabel 1",
    )
    r = calculate(term, {"actual": "99.62", "target": "99.8"})
    assert r.amount == D("30625.00") and r.amount_uncapped == D("30625.00")
    assert r.cap_applied is False
    assert r.basis_text == (
        "5 % af månedligt driftsvederlag (612.500,00 kr.) = 30.625,00 kr. — jf. Bilag 5 · s. 2 · tabel 1"
    )
    assert (
        r.inputs["basis_amount"] == "612500.00" and r.formula_version == penalties.FORMULA_VERSION
    )


def test_tiered_rate_picks_the_lowest_threshold_the_value_falls_under():
    term = Term(
        "service_credit_tiered",
        tiers=((D("99.8"), D("0.05")), (D("99.5"), D("0.10"))),
        basis="maanedligt_driftsvederlag",
        basis_amount=D("612500"),
    )
    assert calculate(term, {"actual": "99.62"}).amount == D("30625.00")
    assert calculate(term, {"actual": "99.30"}).amount == D("61250.00")
    with pytest.raises(DataMissing):
        calculate(term, {"actual": "99.9"})  # above every tier: nothing to compute


def test_mockup_delivery_penalty_with_cap_keeps_both_amounts():
    term = Term("delivery_penalty_per_week", rate=D("0.02"), cap_rate=D("0.15"))
    r = calculate(
        term, {"value_not_delivered": "1000000", "weeks_started": "3", "cap_basis_amount": "300000"}
    )
    assert r.amount_uncapped == D("60000.00")
    assert r.amount == D("45000.00") and r.cap_applied is True
    assert (
        "dog maksimalt 15 % af månedens omsætning under aftalen (300.000,00 kr.) = 45.000,00 kr."
        in r.basis_text
    )
    # without the turnover the cap cannot be applied → the whole calculation is a task
    with pytest.raises(DataMissing) as e:
        calculate(term, {"value_not_delivered": "1000000", "weeks_started": "3"})
    assert e.value.field == "cap_basis_amount"


def test_missing_basis_is_not_zero():
    term = Term("service_credit_pct_of_fee", rate=D("0.05"), basis="maanedligt_driftsvederlag")
    with pytest.raises(DataMissing) as e:
        calculate(term, {"actual": "99"})
    assert e.value.field == "maanedligt_driftsvederlag"
    # the basis may also arrive as an input (resolved from the contract by the caller)
    assert calculate(term, {"basis_amount": "1000"}).amount == D("50.00")


def test_rounding_half_up_only_on_the_final_amount():
    term = Term("service_credit_pct_of_fee", rate=D("0.05"), basis_amount=D("100.10"))
    assert calculate(term, {}).amount == D("5.01")  # 5.005 → 5.01, not banker's 5.00


def test_fixed_penalty_and_unknown_type():
    assert calculate(Term("fixed_penalty_per_breach", basis_amount=D("25000")), {}).amount == D(
        "25000.00"
    )
    with pytest.raises(Unstructurable):
        calculate(Term("renter_ved_forsinkelse"), {})
    with pytest.raises(Unstructurable):
        calculate(Term("service_credit_pct_of_fee", basis_amount=D("1")), {})  # no rate


def test_price_deviation_per_unit_times_quantity():
    r = penalties.price_deviation(D("1184.00"), D("1211.60"), D("3496"))
    assert r.amount == D("96489.60")
    assert r.basis_text.startswith(
        "(1.211,60 kr. − 1.184,00 kr.) pr. enhed × 3496 enheder = 96.489,60 kr."
    )
