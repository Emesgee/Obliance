"""Penalty and credit calculation — ADR-0013 §2. One pure function per term_type.

Rules: Decimal, never float; round to whole øre with ROUND_HALF_UP only on the
final amount; cap applied after calculation with both amounts kept; a missing
input raises DataMissing — it is never zero. The registry grows by adding a
function and its tests, not a prompt. FORMULA_VERSION is stored on every claim
so an old claim recomputes with the logic it was made with.
"""

# ruff: noqa: E501  — basis-text templates read better unwrapped
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

FORMULA_VERSION = "2026.09.1"

_OERE = Decimal("0.01")


class DataMissing(ValueError):
    """An input the formula needs is not available (ADR-0013 §2: not zero, a task)."""

    def __init__(self, field: str, message: str | None = None) -> None:
        super().__init__(message or f"Input mangler: {field}")
        self.field = field


class Unstructurable(ValueError):
    """The term's parameters do not describe a computable formula."""


@dataclass(frozen=True, slots=True)
class Term:
    """The approved parameters a formula reads — decoupled from the ORM row."""

    term_type: str
    rate: Decimal | None = None
    tiers: tuple[tuple[Decimal, Decimal], ...] = ()  # (below, rate), any order
    basis: str = "fast_beloeb"
    basis_amount: Decimal | None = None
    cap_rate: Decimal | None = None
    cap_amount: Decimal | None = None
    citation_label: str = ""


@dataclass(frozen=True, slots=True)
class Result:
    amount_uncapped: Decimal
    amount: Decimal
    cap_applied: bool
    basis_text: str
    inputs: dict[str, Any] = field(default_factory=dict)
    formula_version: str = FORMULA_VERSION


def _dkk(x: Decimal) -> str:
    q = x.quantize(_OERE, rounding=ROUND_HALF_UP)
    whole, frac = f"{q:,.2f}".split(".")
    return f"{whole.replace(',', '.')},{frac} kr."


def _pct(x: Decimal) -> str:
    s = f"{(x * 100).normalize():f}".replace(".", ",")
    return f"{s} %"


def _round(x: Decimal) -> Decimal:
    return x.quantize(_OERE, rounding=ROUND_HALF_UP)


def _need(inputs: Mapping[str, Any], key: str) -> Decimal:
    v = inputs.get(key)
    if v is None or v == "":
        raise DataMissing(key)
    return Decimal(str(v))


def _basis_amount(term: Term, inputs: Mapping[str, Any]) -> Decimal:
    if term.basis_amount is not None:
        return term.basis_amount
    if inputs.get("basis_amount") not in (None, ""):
        return Decimal(str(inputs["basis_amount"]))
    raise DataMissing(term.basis)


_BASIS_DA = {
    "maanedligt_driftsvederlag": "månedligt driftsvederlag",
    "aarligt_vederlag": "årligt vederlag",
    "vaerdi_ikke_leverede_ordrelinjer": "værdien af ikke-leverede ordrelinjer",
    "maanedens_omsaetning": "månedens omsætning under aftalen",
    "fast_beloeb": "fast beløb",
}


def _cite(term: Term) -> str:
    return f" — jf. {term.citation_label}" if term.citation_label else ""


def _cap(term: Term, uncapped: Decimal, inputs: Mapping[str, Any], text: str) -> Result:
    cap: Decimal | None = None
    cap_text = ""
    if term.cap_amount is not None:
        cap = term.cap_amount
        cap_text = f", dog maksimalt {_dkk(cap)}"
    elif term.cap_rate is not None:
        turnover = _need(inputs, "cap_basis_amount")
        cap = term.cap_rate * turnover
        cap_text = f", dog maksimalt {_pct(term.cap_rate)} af {_BASIS_DA['maanedens_omsaetning']} ({_dkk(turnover)}) = {_dkk(cap)}"
    amount = _round(uncapped)
    applied = False
    if cap is not None and amount > _round(cap):
        amount = _round(cap)
        applied = True
    return Result(
        amount_uncapped=_round(uncapped),
        amount=amount,
        cap_applied=applied,
        basis_text=f"{text}{cap_text} = {_dkk(amount)}{_cite(term)}",
        inputs=dict(inputs),
    )


# ---- the registry -----------------------------------------------------------------------------


def service_credit_pct_of_fee(term: Term, inputs: Mapping[str, Any]) -> Result:
    """Mockup SLA-B1: 5 % af månedligt driftsvederlag (612.500 kr.) = 30.625 kr."""
    if term.rate is None:
        raise Unstructurable("rate mangler")
    basis = _basis_amount(term, inputs)
    uncapped = term.rate * basis
    text = f"{_pct(term.rate)} af {_BASIS_DA.get(term.basis, term.basis)} ({_dkk(basis)})"
    return _cap(
        term, uncapped, {**inputs, "basis_amount": str(basis), "rate": str(term.rate)}, text
    )


def service_credit_tiered(term: Term, inputs: Mapping[str, Any]) -> Result:
    """Rate by tier: the lowest `below` threshold the actual value falls under wins."""
    if not term.tiers:
        raise Unstructurable("tiers mangler")
    actual = _need(inputs, "actual")
    hit = [(below, rate) for below, rate in term.tiers if actual < below]
    if not hit:
        raise DataMissing("actual", "Målingen ligger ikke under nogen trappetærskel")
    below, rate = min(hit, key=lambda t: t[0])
    basis = _basis_amount(term, inputs)
    uncapped = rate * basis
    text = (
        f"{_pct(rate)} af {_BASIS_DA.get(term.basis, term.basis)} ({_dkk(basis)}) "
        f"for måling under {str(below).replace('.', ',')}"
    )
    return _cap(
        term,
        uncapped,
        {**inputs, "basis_amount": str(basis), "rate": str(rate), "tier_below": str(below)},
        text,
    )


def delivery_penalty_per_week(term: Term, inputs: Mapping[str, Any]) -> Result:
    """Mockup: 2 % af værdien af ikke-leverede ordrelinjer pr. påbegyndt uge, dog
    maksimalt 15 % af månedens omsætning under aftalen."""
    if term.rate is None:
        raise Unstructurable("rate mangler")
    value = _need(inputs, "value_not_delivered")
    weeks = _need(inputs, "weeks_started")
    if weeks < 1 or weeks != weeks.to_integral_value():
        raise DataMissing("weeks_started", "Antal påbegyndte uger skal være et helt tal ≥ 1")
    uncapped = term.rate * value * weeks
    text = (
        f"{_pct(term.rate)} af {_BASIS_DA['vaerdi_ikke_leverede_ordrelinjer']} ({_dkk(value)}) "
        f"× {int(weeks)} påbegyndt{'e' if weeks > 1 else ''} uge{'r' if weeks > 1 else ''}"
    )
    return _cap(term, uncapped, {**inputs, "rate": str(term.rate)}, text)


def fixed_penalty_per_breach(term: Term, inputs: Mapping[str, Any]) -> Result:
    basis = _basis_amount(term, inputs)
    return _cap(
        term, basis, {**inputs, "basis_amount": str(basis)}, f"fast bod pr. brud ({_dkk(basis)})"
    )


def price_deviation(
    agreed_unit_price: Decimal, invoiced_unit_price: Decimal, quantity: Decimal
) -> Result:
    """ADR-0013 §2's third case (invoice lines, ADR-0018): difference per unit × quantity.
    Mockup: 1.211,60 − 1.184,00 = 27,60 kr./pakning × 3.496 pakninger = 96.489,60 kr.
    (the mockup prints 96.512 kr., which no quantity reproduces exactly — the code
    is the truth, the mockup's figure is a typo)."""
    diff = invoiced_unit_price - agreed_unit_price
    uncapped = diff * quantity
    amount = _round(uncapped)
    qty = f"{quantity.normalize():f}".replace(".", ",")
    text = f"({_dkk(invoiced_unit_price)} − {_dkk(agreed_unit_price)}) pr. enhed × {qty} enheder = {_dkk(amount)}"
    return Result(
        amount_uncapped=amount,
        amount=amount,
        cap_applied=False,
        basis_text=text,
        inputs={
            "agreed_unit_price": str(agreed_unit_price),
            "invoiced_unit_price": str(invoiced_unit_price),
            "quantity": str(quantity),
        },
    )


REGISTRY: dict[str, Callable[[Term, Mapping[str, Any]], Result]] = {
    "service_credit_pct_of_fee": service_credit_pct_of_fee,
    "service_credit_tiered": service_credit_tiered,
    "delivery_penalty_per_week": delivery_penalty_per_week,
    "fixed_penalty_per_breach": fixed_penalty_per_breach,
}


def calculate(term: Term, inputs: Mapping[str, Any]) -> Result:
    fn = REGISTRY.get(term.term_type)
    if fn is None:
        raise Unstructurable(f"ukendt term_type {term.term_type}")
    return fn(term, inputs)
