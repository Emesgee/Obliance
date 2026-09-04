"""Global RACI templates (ADR-0021 §4) — data, not prompt. The migration seeds
them; the test fixture re-seeds them after truncation (organizations CASCADEs
into raci_templates). Editing per organisation happens in the table."""

from __future__ import annotations

import json

# key, tiers ([] = all), agreement forms ([] = all), name, criticality, cells
GLOBAL_TEMPLATES: list[tuple[str, list[str], list[str], str, str, dict[str, str]]] = [
    (
        "sla_followup",
        ["N1", "N2"],
        [],
        "Følge op på leveringsgrad og SLA",
        "hoej",
        {"CM": "A", "BUS": "R", "IT": "C", "LEV": "I"},
    ),
    (
        "penalty",
        ["N1", "N2"],
        [],
        "Beregne og fremsætte bod eller service credit ved leverancesvigt",
        "hoej",
        {"CO": "A", "FIN": "R", "LEGAL": "C", "CM": "C", "LEV": "I"},
    ),
    (
        "invoice_control",
        [],
        [],
        "Fakturakontrol mod prisbilag",
        "mellem",
        {"FIN": "A", "CM": "R", "BUS": "C"},
    ),
    (
        "renewal",
        ["N1", "N2", "N3"],
        ["rammeaftale", "serviceaftale"],
        "Beslutte forlængelse eller genudbud",
        "hoej",
        {"CO": "A", "PROC": "R", "LEGAL": "C", "CM": "C"},
    ),
    (
        "supplier_meetings",
        [],
        [],
        "Afholde og dokumentere leverandørmøder",
        "mellem",
        {"CM": "A", "BUS": "R", "LEV": "R", "CO": "I"},
    ),
    (
        "ai_review",
        [],
        [],
        "Godkende AI-udtræk af forpligtelser og risici",
        "mellem",
        {"CM": "A", "LEGAL": "R", "CO": "I"},
    ),
    (
        "dpa",
        [],
        ["databehandleraftale", "serviceaftale"],
        "Kontrollere databehandleraftale og underdatabehandlere",
        "hoej",
        {"LEGAL": "A", "IT": "R", "CM": "C", "LEV": "I"},
    ),
]


def insert_statements() -> list[str]:
    """Raw INSERTs for the migration and the test fixture (both run as the owner,
    before/without the RLS policy that refuses NULL organization rows)."""
    out = []
    for key, tiers, forms, name, crit, cells in GLOBAL_TEMPLATES:
        out.append(
            "INSERT INTO raci_templates (organization_id, key, tiers, agreement_forms, name, "
            f"criticality, assignments) VALUES (NULL, '{key}', '{json.dumps(tiers)}'::jsonb, "
            f"'{json.dumps(forms)}'::jsonb, '{name.replace(chr(39), chr(39) * 2)}', '{crit}', "
            f"'{json.dumps(cells)}'::jsonb)"
        )
    return out
