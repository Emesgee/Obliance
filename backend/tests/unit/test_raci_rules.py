"""ADR-0021 §1 — the matrix rules, pure."""

from __future__ import annotations

from app.raci.service import validate


def test_exactly_one_a_and_at_least_one_r():
    assert validate({"CM": "A", "BUS": "R"}) == []
    assert "intet A" in validate({"CM": "R", "BUS": "R"})[0]
    assert "der er 2" in validate({"CM": "A", "CO": "A", "BUS": "R"})[0]
    assert "Mindst ét R" in validate({"CM": "A", "BUS": "C"})


def test_mockup_ra6_is_invalid():
    # RA-6 "restordrer i patientkritiske uger": two R, no A
    errors = validate({"CM": "R", "BUS": "R", "PROC": "C"})
    assert any("intet A" in e for e in errors)


def test_lev_is_never_accountable():
    errors = validate({"LEV": "A", "CM": "R"})
    assert any("LEV kan ikke være A" in e for e in errors)
    assert validate({"CM": "A", "LEV": "R"}) == []


def test_unknown_function_or_letter():
    assert any("Ukendt funktion" in e for e in validate({"CM": "A", "HR": "R"}))
    assert any("Ukendt bogstav" in e for e in validate({"CM": "A", "BUS": "X"}))
