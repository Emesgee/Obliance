"""ADR-0006 over HTTP: upload → version 1 gaeldende, duplicates refused, later
versions are kladde until a human makes them current — and ADR-0002's child
policies: a document on a contract you cannot see does not exist for you."""

from __future__ import annotations

import pymupdf
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PW = "korrekt-adgangskode-123"


def _login(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _pdf(*pages: str) -> bytes:
    doc = pymupdf.open()
    for i, body in enumerate(pages, start=1):
        page = doc.new_page()
        page.insert_text((72, 72), body, fontsize=11)
        page.insert_text((250, 800), f"Side {i} af {len(pages)}", fontsize=9)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _upload(client: TestClient, headers: dict[str, str], contract: str, data: bytes, **form: str):
    return client.post(
        f"/api/contracts/{contract}/documents",
        headers=headers,
        files={"file": ("hovedkontrakt.pdf", data, "application/pdf")},
        data={"doc_type": "hovedkontrakt", "title": "Hovedkontrakt", **form},
    )


@pytest.fixture
def cm(client, make_org, make_user, make_contract):
    org = make_org("A")
    make_user(org, "cm@test.dk", "contract_manager", password=PW)
    contract = make_contract(org, "K-2026-001")
    return org, str(contract), _login(client, "cm@test.dk")


# ---- upload + ingest -------------------------------------------------------------------


def test_upload_creates_document_with_current_version_pages_and_clauses(client, cm):
    _, contract, h = cm
    r = _upload(client, h, contract, _pdf("8.1 Oppetid\nMinimum 99,8 %.", "8.2 Service credits"))
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["doc_type"] == "hovedkontrakt" and doc["title"] == "Hovedkontrakt"
    (v1,) = doc["versions"]
    assert v1["version_no"] == 1
    assert v1["status"] == "gaeldende"  # first good ingest is made current automatically
    assert v1["ingest_status"] == "ok" and v1["page_count"] == 2
    assert doc["current_version_id"] == v1["id"]

    pages = client.get(f"/api/documents/versions/{v1['id']}/pages", headers=h).json()
    assert [p["page_pdf"] for p in pages] == [1, 2]
    assert [p["page_printed"] for p in pages] == ["1", "2"]
    assert "Oppetid" in pages[0]["text"]

    clauses = client.get(f"/api/documents/versions/{v1['id']}/clauses", headers=h).json()
    assert [(c["clause_ref"], c["page_pdf"]) for c in clauses] == [("8.1", 1), ("8.2", 2)]

    listed = client.get(f"/api/contracts/{contract}/documents", headers=h).json()
    assert [d["id"] for d in listed] == [doc["id"]]


def test_file_round_trips_byte_for_byte(client, cm):
    _, contract, h = cm
    data = _pdf("Indhold")
    v = _upload(client, h, contract, data).json()["versions"][0]
    r = client.get(f"/api/documents/versions/{v['id']}/file", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content == data


def test_same_bytes_twice_is_409_duplicate_version(client, cm):
    _, contract, h = cm
    data = _pdf("Samme fil")
    doc = _upload(client, h, contract, data).json()
    r = client.post(
        f"/api/documents/{doc['id']}/versions",
        headers=h,
        files={"file": ("igen.pdf", data, "application/pdf")},
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_version"


def test_second_version_is_kladde_until_made_current(client, cm):
    _, contract, h = cm
    doc = _upload(client, h, contract, _pdf("Version 1")).json()
    v1 = doc["versions"][0]
    r = client.post(
        f"/api/documents/{doc['id']}/versions",
        headers=h,
        files={"file": ("v2.pdf", _pdf("Version 2"), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version_no"] == 2 and v2["status"] == "kladde" and v2["ingest_status"] == "ok"

    listed = client.get(f"/api/contracts/{contract}/documents", headers=h).json()[0]
    assert listed["current_version_id"] == v1["id"]  # untouched by the upload

    r = client.post(f"/api/documents/versions/{v2['id']}/make-current", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "gaeldende" and r.json()["made_current_at"] is not None

    listed = client.get(f"/api/contracts/{contract}/documents", headers=h).json()[0]
    assert listed["current_version_id"] == v2["id"]
    by_no = {v["version_no"]: v["status"] for v in listed["versions"]}
    assert by_no == {1: "historisk", 2: "gaeldende"}


def test_unsupported_and_empty_files_are_refused(client, cm):
    _, contract, h = cm
    r = client.post(
        f"/api/contracts/{contract}/documents",
        headers=h,
        files={"file": ("noter.txt", b"hej", "text/plain")},
    )
    assert r.status_code == 415 and r.json()["detail"]["code"] == "unsupported_type"
    r = client.post(
        f"/api/contracts/{contract}/documents",
        headers=h,
        files={"file": ("tom.pdf", b"", "application/pdf")},
    )
    assert r.status_code == 400 and r.json()["detail"]["code"] == "empty_file"


# ---- who may do what ---------------------------------------------------------------------


def test_business_user_can_read_but_not_upload(client, cm, make_user):
    org, contract, h_cm = cm
    make_user(org, "bu@test.dk", "business_user", password=PW)
    h_bu = _login(client, "bu@test.dk")
    v = _upload(client, h_cm, contract, _pdf("Læsbar")).json()["versions"][0]
    assert _upload(client, h_bu, contract, _pdf("Afvist")).status_code == 403
    assert client.get(f"/api/documents/versions/{v['id']}/pages", headers=h_bu).status_code == 200


def test_other_tenant_and_fortrolig_without_access_see_nothing(
    client, cm, make_org, make_user, make_contract
):
    org, contract, h_cm = cm
    v = _upload(client, h_cm, contract, _pdf("Intern")).json()["versions"][0]

    # another tenant: contract, documents and version are all 404
    org_b = make_org("B")
    make_user(org_b, "b@test.dk", "systemadministrator", password=PW)
    h_b = _login(client, "b@test.dk")
    assert client.get(f"/api/contracts/{contract}", headers=h_b).status_code == 404
    assert client.get(f"/api/contracts/{contract}/documents", headers=h_b).status_code == 404
    assert client.get(f"/api/documents/versions/{v['id']}/pages", headers=h_b).status_code == 404
    assert client.get(f"/api/documents/versions/{v['id']}/file", headers=h_b).status_code == 404

    # same tenant, fortrolig contract, no contract_access: child rows are invisible too
    pm = make_user(org, "pm@test.dk", "procurement_manager", password=PW)
    secret = make_contract(org, "R-2026-001", confidentiality="fortrolig", manager_id=pm)
    h_pm = _login(client, "pm@test.dk")
    sv = _upload(client, h_pm, str(secret), _pdf("Hemmeligt")).json()["versions"][0]
    make_user(org, "bu@test.dk", "business_user", password=PW)
    h_bu = _login(client, "bu@test.dk")
    assert client.get(f"/api/contracts/{secret}/documents", headers=h_bu).status_code == 404
    assert client.get(f"/api/documents/versions/{sv['id']}/pages", headers=h_bu).status_code == 404
    assert (
        client.get(f"/api/documents/versions/{sv['id']}/clauses", headers=h_bu).status_code == 404
    )
