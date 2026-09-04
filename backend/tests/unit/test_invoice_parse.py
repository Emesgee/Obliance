"""ADR-0018 §2/§4/§7 — parsing Danish CSV/Excel, grouping, fingerprint. Pure."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import openpyxl
import pytest

from app.finance import invoices as feed

CSV = (
    "fakturanr;fakturadato;forfaldsdato;leverandoer_cvr;kontraktreference;linje;beskrivelse;antal;enhed;enhedspris;linjetotal\n"
    "10493;05-08-2026;04-09-2026;12345678;K-2026-001;1;Farmaceuttimer, dagtimer;37;time;590,00;21.830,00\n"
    "10493;05-08-2026;04-09-2026;12345678;K-2026-001;2;Kørsel;1;stk;1.250,50;1.250,50\n"
    "10494;06-08-2026;;12345678;;1;Pakning A;3.496;pakning;1.211,60;\n"
    ";06-08-2026;;12345678;;1;Uden nummer;1;stk;10;10\n"
)


def test_csv_danish_formats_are_parsed_and_grouped():
    rows = feed.parse_file("fakturaer.csv", CSV.encode("utf-8"))
    assert len(rows) == 4 and rows[0]["enhedspris"] == "590,00"
    report = feed.ImportReport(received=len(rows))
    headers = feed._group(rows, report)
    assert [h.number for h in headers] == ["10493", "10494"]
    h = headers[0]
    assert h.invoice_date == date(2026, 8, 5) and h.due_date == date(2026, 9, 4)
    assert h.reference == "K-2026-001" and h.total == Decimal("23080.50")
    assert h.lines[0].quantity == Decimal("37") and h.lines[0].unit_price == Decimal("590.00")
    # missing linjetotal → antal × enhedspris
    assert headers[1].lines[0].line_total == Decimal("4235753.60")  # 3.496 × 1.211,60
    # the row without an invoice number is an error, not a silent skip
    assert len(report.errors) == 1 and "fakturanr mangler" in report.errors[0].reason
    assert report.errors[0].row_no == 5  # header is row 1


def test_missing_columns_and_size_limits_are_refused():
    with pytest.raises(feed.ImportRejected) as e:
        feed.parse_file("x.csv", b"fakturanr;antal\n1;2\n")
    assert e.value.code == "bad_schema" and "enhedspris" in str(e.value)
    with pytest.raises(feed.ImportRejected) as e:
        feed.parse_file("x.csv", b"x" * (feed.MAX_BYTES + 1))
    assert e.value.code == "too_large"


def test_xlsx_is_read_as_cached_values_not_formulas():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(
        [
            "fakturanr",
            "fakturadato",
            "leverandoer_cvr",
            "linje",
            "beskrivelse",
            "antal",
            "enhedspris",
            "linjetotal",
        ]
    )
    ws.append(
        ["A1", date(2026, 8, 5), "12345678", 1, "Timer", 2, 100, "=B2*1000"]
    )  # a formula, never evaluated
    buf = io.BytesIO()
    wb.save(buf)
    rows = feed.parse_file("f.xlsx", buf.getvalue())
    assert rows[0]["fakturanr"] == "A1" and feed._date(rows[0]["fakturadato"]) == date(2026, 8, 5)
    report = feed.ImportReport()
    (h,) = feed._group(rows, report)
    # the formula cell has no cached value → falls back to antal × enhedspris, no evaluation
    assert h.lines[0].line_total == Decimal("200.00")


def test_fingerprint_changes_with_amount_and_date_only():
    a = feed.fingerprint("12345678", "10493", Decimal("100.00"), date(2026, 8, 5))
    assert a == feed.fingerprint("12345678 ", " 10493", Decimal("100"), date(2026, 8, 5))
    assert a != feed.fingerprint("12345678", "10493", Decimal("100.01"), date(2026, 8, 5))
    assert a != feed.fingerprint("12345678", "10493", Decimal("100.00"), date(2026, 8, 6))
