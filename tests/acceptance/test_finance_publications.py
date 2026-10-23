"""Synthetic Books boundary through the production runtime/event composition root."""

from __future__ import annotations
import copy
import json
from dataclasses import replace
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from adminme.finance.importer import import_publication
from adminme.finance.publication import (
    PublicationError,
    content_hash,
    schema_hash,
    validate_publication,
)
from adminme.finance.queries import publication_status, query_publication
from adminme.lib.session import build_internal_session
from adminme.runtime.application import RuntimeSettings, create_application

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/finance/accepted-publication.synthetic.json"


def publication(tenant: str = "tenant-synthetic") -> dict:
    p = json.loads(FIXTURE.read_text())
    p["tenant_id"] = tenant
    p["content_sha256"] = content_hash(p)
    return p


def settings(tmp_path: Path, enabled: bool = True) -> RuntimeSettings:
    return replace(
        RuntimeSettings.credential_free(
            tmp_path,
            encryption_key=b"f" * 32,
            repository_root=ROOT,
            member_roles={"member-synthetic": "principal"},
        ),
        finance_publications_enabled=enabled,
    )


def test_contract_hash_and_unchanged_scenarios():
    p = publication()
    assert (
        schema_hash()
        == (
            ROOT
            / "adminme/finance/contracts/household-books.accepted-publication.v1.sha256"
        )
        .read_text()
        .strip()
    )
    assert validate_publication(p) == p
    scenarios = next(r for r in p["reports"] if r["report_id"] == "scenarios")
    assert [m["value_minor"] for m in scenarios["metrics"]] == [
        12500,
        13500,
        14500,
        15500,
    ]


@pytest.mark.parametrize(
    "mutation", ["pending", "conflict", "release", "owner", "missing", "url", "period"]
)
def test_reject_invalid_publication(mutation):
    p = publication()
    metric = p["reports"][0]["metrics"][0]
    if mutation == "pending":
        metric["revision_ids"] = ["revision-pending"]
    elif mutation == "conflict":
        p["accepted_journal_revision_ids"].append("revision-pending")
    elif mutation == "release":
        p["reports"][0]["release_state"] = "ready"
    elif mutation == "owner":
        metric["owner_id"] = "member-other"
    elif mutation == "missing":
        metric["value_minor"] = None
    elif mutation == "url":
        p["source_references"][0]["source_url"] = "https://example.invalid/private"
    elif mutation == "period":
        metric["period_end"] = "2026-02-28"
    p["content_sha256"] = content_hash(p)
    with pytest.raises(PublicationError):
        validate_publication(p)


def test_atomic_import_replay_and_owner_isolation(tmp_path):
    configured = settings(tmp_path)
    p = publication(configured.instance.tenant_id)
    owner = build_internal_session("member-synthetic", "principal", p["tenant_id"])
    other = build_internal_session("member-other", "principal", p["tenant_id"])
    app = create_application(configured)
    with TestClient(app) as client:
        runtime = app.state.runtime
        assert client.portal
        first = client.portal.call(import_publication, runtime, owner, p)
        repeat = client.portal.call(import_publication, runtime, owner, p)
        assert repeat["idempotent"] and first["event_id"] == repeat["event_id"]

        async def read(session, report):
            return query_publication(
                runtime.projections.connection("money"),
                session,
                p["book_id"],
                p["publication_id"],
                report,
            )

        result = client.portal.call(read, owner, "scenarios")
        assert [m["value_minor"] for m in result["metrics"]] == [
            12500,
            13500,
            14500,
            15500,
        ]
        assert (
            client.portal.call(read, other, "scenarios")["state"] == "missing_evidence"
        )
        assert client.portal.call(read, owner, "cash")["metrics"] == []
        conflict = copy.deepcopy(p)
        conflict["reports"][0]["metrics"][0]["value_minor"] = 999
        conflict["content_sha256"] = content_hash(conflict)
        with pytest.raises(PublicationError, match="conflict"):
            client.portal.call(import_publication, runtime, owner, conflict)
        partial = copy.deepcopy(p)
        partial["publication_id"] = "publication-2"
        partial["prior_publication_id"] = p["publication_id"]
        partial["reports"][0]["metrics"][0]["revision_ids"] = ["revision-pending"]
        partial["content_sha256"] = content_hash(partial)
        with pytest.raises(PublicationError):
            client.portal.call(import_publication, runtime, owner, partial)
        assert (
            client.portal.call(read, owner, "actuals")["metrics"][0]["value_minor"]
            == 12500
        )
    restarted = create_application(configured)
    with TestClient(restarted) as client:
        runtime = restarted.state.runtime
        assert client.portal

        async def inspect():
            return publication_status(
                runtime.projections.connection("money"), owner, p["book_id"]
            )

        assert client.portal.call(inspect)["publication_id"] == "publication-1"
        assert client.portal.call(import_publication, runtime, owner, p)["idempotent"]


def test_disabled_ingress_and_pending_tail_never_advance(tmp_path):
    configured = settings(tmp_path, False)
    app = create_application(configured)
    with TestClient(app) as client:
        assert client.portal
        p = publication(configured.instance.tenant_id)
        s = build_internal_session("member-synthetic", "principal", p["tenant_id"])
        with pytest.raises(PublicationError, match="disabled"):
            client.portal.call(import_publication, app.state.runtime, s, p)


def test_paused_recovery_cannot_be_cleared_or_ingested(tmp_path):
    configured = settings(tmp_path)
    p = publication(configured.instance.tenant_id)
    owner = build_internal_session("member-synthetic", "principal", p["tenant_id"])
    app = create_application(configured)
    with TestClient(app) as client:
        assert client.portal
        client.portal.call(import_publication, app.state.runtime, owner, p)
        for clear in (True, False):
            changed = copy.deepcopy(p)
            changed["publication_id"] = "publication-2"
            changed["prior_publication_id"] = p["publication_id"]
            if clear:
                changed["recovery_state"] = "clear"
            else:
                changed["accepted_journal_revision_ids"].append("revision-pending")
                changed["excluded_pending_revision_ids"] = []
            changed["content_sha256"] = content_hash(changed)
            with pytest.raises(PublicationError, match="recovery_hold"):
                client.portal.call(
                    import_publication, app.state.runtime, owner, changed
                )
