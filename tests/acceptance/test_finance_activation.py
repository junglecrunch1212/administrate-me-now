"""Persisted owner/book activation through real APIs using only synthetic Books.

Static fixture Finance flags stay off. This exercises the production control,
approval, import and CoS disclosure paths rather than their legacy test fallback.
"""

import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from adminme.finance.disclosure import FinanceDisclosureError, check_disclosure
from adminme.finance.publication import canonical_bytes, content_hash
from adminme.lib.session import build_internal_session
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_native_facade import _post, _settings
from tests.acceptance.test_openclaw_native_seam import (
    DEMO_HEADERS,
    OTHER,
    PRINCIPAL,
    _grant_cos,
)
from tests.fixtures.finance_planning import projection
from tests.fixtures.finance_provenance import package_fixture, seal_publication

CONTROL = "/api/member/v1/settings/finance"
APPROVALS = "/api/member/v1/finance/import-approvals"
IMPORTS = "/api/member/v1/finance/imports/"
SURFACE = "/api/member/v1/surfaces/finance"
OTHER_HEADERS = {"Tailscale-User-Login": "fixture-other.ts.net"}
BINDING_KEYS = ("book_id", "entity_id", "audience_policy_id", "audience_policy_version")


def configured(root):
    settings = _settings(root)
    assert (
        not settings.finance_publications_enabled and not settings.finance_model_members
    )
    return replace(
        settings,
        member_identities={**settings.member_identities, "fixture-other.ts.net": OTHER},
    )


def pins(raw):
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def fixture(tenant, *, dated=True, witnessed=True, owner=PRINCIPAL, total_events=0):
    publication, package = package_fixture(tenant, owner)
    planned = projection(publication, total_events) if dated else None
    if planned is not None and witnessed:
        witness = deepcopy(planned["evidence"][0])
        witness.update(
            evidence_id="hbsource1:" + "a" * 64, artifact_ref="hbsource1:" + "a" * 64
        )
        planned["evidence"].append(witness)
        planned["evidence_ids"].append(witness["evidence_id"])
        planned["content_sha256"] = content_hash(planned)
    return publication, planned, package


def packet_for(publication, planned, package):
    p_bytes = canonical_bytes(publication)
    d_bytes = canonical_bytes(planned) if planned else None
    manifest = {
        "contract": "household-books.closed-import.v1",
        "binding": {k: publication[k] for k in BINDING_KEYS},
        "source_snapshot_sha256": json.loads(package.index)["source_snapshot_sha256"],
        "publication": pins(p_bytes),
        "projection": pins(d_bytes) if d_bytes else None,
        "index": pins(package.index),
        "blobs": {key: len(value) for key, value in package.blobs.items()},
        "independently_reviewed_evidence": sorted(package.approved_evidence),
    }

    def encode(raw):
        return base64.b64encode(raw).decode("ascii")

    packet = {
        "publication": encode(p_bytes),
        "projection": encode(d_bytes) if d_bytes else None,
        "index": encode(package.index),
        "blobs": {key: encode(value) for key, value in package.blobs.items()},
    }
    return manifest, packet


def control(client, binding, *, books=True, cos=False, headers=DEMO_HEADERS):
    current = client.get(CONTROL, headers=headers)
    assert current.status_code == 200, current.text
    result = client.put(
        CONTROL,
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "expected_revision": current.json()["revision"],
            "binding": binding,
            "books_enabled": books,
            "cos_analysis_enabled": cos,
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def approve(client, manifest):
    revision = client.get(CONTROL, headers=DEMO_HEADERS).json()["revision"]
    response = client.post(
        APPROVALS,
        headers=DEMO_HEADERS,
        json={
            "request_id": str(uuid4()),
            "expected_policy_revision": revision,
            "manifest": manifest,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["approval_event_id"]


def upload(client, approval, packet, *, headers=DEMO_HEADERS):
    return client.post(IMPORTS + approval, headers=headers, json=packet)


async def events(runtime, types):
    return [event async for event in runtime.event_log.read_since(types=types)]



@pytest.mark.parametrize("witness_factory", ["activation", "resource"])
def test_composed_browser_projection_imports_without_shared_evidence_lists(
    tmp_path, witness_factory
):
    from tests.fixtures.finance_activation import witness_projection
    from tests.fixtures.finance_planning import (
        with_boundary_windows,
        with_conservative_minima,
        with_grouped_issues,
        with_unavailable_openings,
    )

    settings = configured(tmp_path)
    p, d, package = fixture(
        settings.instance.tenant_id,
        total_events=24,
        witnessed=witness_factory == "activation",
    )
    if witness_factory == "resource":
        d = witness_projection(d)
    assert d["evidence"][0]["context_ids"] is not d["evidence"][1]["context_ids"]
    d = with_grouped_issues(
        with_conservative_minima(with_unavailable_openings(with_boundary_windows(d)))
    )
    for evidence in d["evidence"]:
        assert len(evidence["context_ids"]) == len(set(evidence["context_ids"]))
    manifest, packet = packet_for(p, d, package)
    with TestClient(create_application(settings)) as client:
        control(client, manifest["binding"])
        result = upload(client, approve(client, manifest), packet)
        assert result.status_code == 200, result.text
        assert result.json()["dated"]["state"] == "verified"


def test_persisted_activation_import_disclosure_restart_and_disable(tmp_path):
    settings = configured(tmp_path)
    p, d, package = fixture(settings.instance.tenant_id)
    manifest, packet = packet_for(p, d, package)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        grant = _grant_cos(client, "activation-existing-grant")
        before = client.get(
            "/api/member/v1/cos/delegations", headers=DEMO_HEADERS
        ).json()["items"]
        assert client.get(CONTROL, headers=DEMO_HEADERS).json()["revision"] == 0
        assert (
            _post(client, "status", "activation-default").json()["state"]
            == "permission_required"
        )
        control(client, manifest["binding"])
        assert (
            _post(client, "status", "activation-no-model").json()["state"]
            == "permission_required"
        )
        approval = approve(client, manifest)
        accepted = upload(client, approval, packet)
        assert accepted.status_code == 200, accepted.text
        accepted = accepted.json()
        assert accepted["accounting"]["publication_id"] == p["publication_id"]
        assert accepted["dated"]["state"] == "verified"
        repeated = upload(client, approval, packet)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["accounting"]["idempotent"]
        assert (
            repeated.json()["accounting"]["event_id"]
            == accepted["accounting"]["event_id"]
        )
        control(client, manifest["binding"], cos=True)
        status = _post(client, "status", "activation-status")
        assert status.status_code == 200, status.text
        assert status.json()["publication"]["publication_id"] == p["publication_id"]
        query = _post(
            client,
            "query",
            "activation-query",
            book_id=p["book_id"],
            publication_id=p["publication_id"],
            report_id="scenarios",
        )
        assert query.status_code == 200, query.text
        assert query.json()["metrics"][0]["value_minor"] == 12500
        assert query.json()["next_offset"] == 1
        assert len(query.json()["metrics"]) == 1
        owner = build_internal_session(PRINCIPAL, "principal", p["tenant_id"])
        _, session_key = runtime.member_chat_binding(PRINCIPAL)

        async def disclosure():
            await check_disclosure(
                runtime, owner, session_key, run_id="activation-query", require_run=True
            )

        client.portal.call(disclosure)
        surface = client.get(SURFACE, headers=DEMO_HEADERS)
        assert surface.status_code == 200, surface.text
        books = surface.json()["cockpit"]["accepted_books"]
        assert books["publication_id"] == p["publication_id"]
        assert "historical" in surface.text.lower()
        assert surface.json()["cockpit"]["scenarios"] == []
        # Selecting this private book does not disable the shared legacy writer.
        assert runtime.xlsx_reverse._finance_reverse_enabled
        assert (
            client.get("/api/member/v1/cos/delegations", headers=DEMO_HEADERS).json()[
                "items"
            ]
            == before
        )
        assert any(item["grant_id"] == grant for item in before)
        retained = client.portal.call(
            events,
            runtime,
            ["finance.publication_accepted", "finance.projection_verified"],
        )
        assert len(retained) == 2
        assert all(e["owner_scope"] == f"private:{PRINCIPAL}" for e in retained)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        state = client.get(CONTROL, headers=DEMO_HEADERS).json()
        assert (
            state["books_enabled"]
            and state["cos_analysis_enabled"]
            and state["revision"] == 2
        )
        assert (
            client.get(SURFACE, headers=DEMO_HEADERS).json()["cockpit"][
                "accepted_books"
            ]["publication_id"]
            == p["publication_id"]
        )
        control(client, manifest["binding"], books=False)
        assert (
            _post(
                client,
                "query",
                "after-disable",
                book_id=p["book_id"],
                publication_id=p["publication_id"],
                report_id="scenarios",
                limit=1,
            ).json()["state"]
            == "permission_required"
        )
        assert upload(client, approval, packet).status_code in {403, 409}
        assert (
            len(
                client.portal.call(
                    events,
                    runtime,
                    ["finance.publication_accepted", "finance.projection_verified"],
                )
            )
            == 2
        )
        assert runtime.xlsx_reverse._finance_reverse_enabled


def test_exact_binding_owner_and_independent_approval_required(tmp_path):
    settings = configured(tmp_path)
    p, d, package = fixture(settings.instance.tenant_id)
    manifest, packet = packet_for(p, d, package)
    with TestClient(create_application(settings)) as client:
        assert upload(client, "missing-approval", packet).status_code == 403
        assert (
            client.get(
                CONTROL, headers={**DEMO_HEADERS, "X-AdminMe-View-Member": OTHER}
            ).status_code
            == 403
        )
        control(client, manifest["binding"], cos=True)
        # Domain consent does not create the independent private read delegation.
        assert (
            client.get("/api/member/v1/cos/delegations", headers=DEMO_HEADERS).json()[
                "items"
            ]
            == []
        )
        no_grant = _post(client, "status", "domain-without-grant")
        assert no_grant.status_code == 403, no_grant.text
        approval = approve(client, manifest)
        assert upload(client, approval, packet, headers=OTHER_HEADERS).status_code in {
            403,
            409,
        }
        assert upload(client, approval, packet).status_code == 200
        _grant_cos(client, "activation-binding-grant")
        denied = _post(
            client,
            "query",
            "wrong-book",
            book_id="unapproved-book",
            publication_id=p["publication_id"],
            report_id="scenarios",
            limit=1,
        )
        assert "12500" not in denied.text
        assert client.get(
            "/api/member/v1/finance/planning",
            headers=DEMO_HEADERS,
            params={"book_id": "unapproved-book"},
        ).status_code in {403, 422}
        other = client.get(SURFACE, headers=OTHER_HEADERS)
        assert other.status_code == 200, other.text
        assert p["publication_id"] not in other.text
        modified = {**manifest["binding"], "book_id": "unapproved-book"}
        result = client.put(
            CONTROL,
            headers=DEMO_HEADERS,
            json={
                "request_id": str(uuid4()),
                "expected_revision": 1,
                "binding": modified,
                "books_enabled": True,
                "cos_analysis_enabled": True,
            },
        )
        assert result.status_code == 409


@pytest.mark.parametrize(
    "mutation",
    ["hash", "extra_blob", "oversize", "duplicate_key", "unreviewed", "wrong_owner"],
)
def test_closed_upload_rejection_advances_no_head(tmp_path, mutation):
    settings = configured(tmp_path)
    p, d, package = fixture(
        settings.instance.tenant_id,
        owner=OTHER if mutation == "wrong_owner" else PRINCIPAL,
    )
    manifest, packet = packet_for(p, d, package)
    with TestClient(create_application(settings)) as client:
        control(client, manifest["binding"])
        if mutation == "unreviewed":
            manifest["independently_reviewed_evidence"].pop()
        approval = approve(client, manifest)
        if mutation == "hash":
            raw = bytearray(canonical_bytes(p))
            raw[-2] ^= 1
            packet["publication"] = base64.b64encode(raw).decode()
        elif mutation == "extra_blob":
            packet["blobs"]["b" * 64] = "eA=="
        elif mutation == "oversize":
            packet["publication"] += "A" * 100000
        if mutation == "duplicate_key":
            raw = json.dumps(packet)[:-1] + ',"projection":null}'
            result = client.post(
                IMPORTS + approval,
                headers={**DEMO_HEADERS, "Content-Type": "application/json"},
                content=raw,
            )
        else:
            result = upload(client, approval, packet)
        assert result.status_code == 422, result.text
        runtime = client.app.state.runtime
        assert not client.portal.call(
            events,
            runtime,
            ["finance.publication_accepted", "finance.projection_verified"],
        )
        audit = client.portal.call(events, runtime, ["finance.import_result"])
        assert audit[-1]["payload"]["outcome"] == "rejected"
        assert "12500" not in json.dumps(audit)


def test_partial_dated_import_and_conflicting_accounting_preserve_last_good_head(
    tmp_path,
):
    settings = configured(tmp_path)
    p, d, package = fixture(settings.instance.tenant_id, witnessed=False)
    manifest, packet = packet_for(p, d, package)
    with TestClient(create_application(settings)) as client:
        control(client, manifest["binding"])
        result = upload(client, approve(client, manifest), packet)
        assert result.status_code == 409, result.text
        receipt = result.json()["detail"]
        assert (
            receipt["state"] == "incomplete"
            and receipt["accounting"]["publication_id"] == p["publication_id"]
        )
        assert receipt["dated"] is None
        conflict = deepcopy(p)
        conflict["reports"][0]["metrics"][0]["value_minor"] += 1
        seal_publication(conflict)
        second_manifest, second_packet = packet_for(conflict, None, package)
        failed = upload(client, approve(client, second_manifest), second_packet)
        assert failed.status_code == 409, failed.text
        surface = client.get(SURFACE, headers=DEMO_HEADERS).json()["cockpit"][
            "accepted_books"
        ]
        assert surface["publication_id"] == p["publication_id"]
        assert (
            len(
                client.portal.call(
                    events, client.app.state.runtime, ["finance.publication_accepted"]
                )
            )
            == 1
        )


def test_revocation_after_retrieval_before_disclosure_blocks_result(
    tmp_path, monkeypatch
):
    from adminme.products.openclaw import main
    from adminme.products.member.main import _write_settings_receipt

    settings = configured(tmp_path)
    p, d, package = fixture(settings.instance.tenant_id)
    manifest, packet = packet_for(p, d, package)
    with TestClient(create_application(settings)) as client:
        control(client, manifest["binding"], cos=True)
        assert upload(client, approve(client, manifest), packet).status_code == 200
        _grant_cos(client, "final-disclosure-grant")
        seeded = _post(
            client,
            "query",
            "before-final-revocation",
            book_id=p["book_id"],
            publication_id=p["publication_id"],
            report_id="scenarios",
            limit=1,
        )
        assert seeded.status_code == 200 and "answer_ref" in seeded.json(), seeded.text
        assert seeded.json()["metrics"][0]["value_minor"] == 12500
        seeded_receipts = client.portal.call(
            events, client.app.state.runtime, ["finance.disclosure_recorded"]
        )
        assert any(
            row["payload"].get("kind") == "run"
            and row["payload"].get("result_sha256") == seeded.json()["answer_ref"]
            for row in seeded_receipts
        )
        from adminme.finance import queries

        read_report = queries.query_publication
        retrieved = []

        def observe_read(*args, **kwargs):
            result = read_report(*args, **kwargs)
            assert result["metrics"][0]["value_minor"] == 12500
            retrieved.append(result)
            return result

        monkeypatch.setattr(queries, "query_publication", observe_read)
        original = main._record_tool_call

        async def revoke_during_audit(request, session, provenance, **kwargs):
            result = await original(request, session, provenance, **kwargs)
            if kwargs["operation"] == "finance.query":
                assert len(retrieved) == 1
                # Simulate an already committed control whose cache notification
                # is delayed. Final durable read must still block disclosure.
                runtime = request.app.state.runtime
                policy = runtime.finance_access.snapshot(session.tenant_id, PRINCIPAL)
                await _write_settings_receipt(
                    runtime,
                    session,
                    event_type="settings.finance_access_changed",
                    payload={
                        "owner_id": PRINCIPAL,
                        "changed_by_member_id": PRINCIPAL,
                        "revision": policy.revision + 1,
                        "prior_event_id": policy.event_id,
                        "binding": manifest["binding"],
                        "books_enabled": True,
                        "cos_analysis_enabled": False,
                        "provider_effects": False,
                    },
                    request_id="revoke-at-delivery",
                    request_fingerprint="synthetic",
                    owner_member_id=PRINCIPAL,
                    sensitivity="privileged",
                )
            return result

        monkeypatch.setattr(main, "_record_tool_call", revoke_during_audit)
        result = _post(
            client,
            "query",
            "revoked-at-delivery",
            book_id=p["book_id"],
            publication_id=p["publication_id"],
            report_id="scenarios",
            limit=1,
        )
        assert result.status_code == 403, result.text
        assert len(retrieved) == 1
        assert "12500" not in result.text
        runtime = client.app.state.runtime
        owner = build_internal_session(PRINCIPAL, "principal", p["tenant_id"])
        _, session_key = runtime.member_chat_binding(PRINCIPAL)

        async def check():
            await check_disclosure(
                runtime,
                owner,
                session_key,
                run_id="revoked-at-delivery",
                require_run=True,
            )

        with pytest.raises(FinanceDisclosureError):
            client.portal.call(check)


@pytest.mark.parametrize("failure", ["unavailable", "malformed"])
def test_one_owner_control_failure_does_not_abort_runtime(
    tmp_path, monkeypatch, failure
):
    from adminme.events.log import EventLog
    from adminme.finance.access_control import FinanceAccessError

    original = EventLog.read_finance_access_control

    async def failing(self, tenant, owner):
        if owner == PRINCIPAL:
            if failure == "unavailable":
                raise RuntimeError("synthetic reader budget exhausted")
            return {"payload": {"malformed": True}}
        return await original(self, tenant, owner)

    monkeypatch.setattr(EventLog, "read_finance_access_control", failing)
    with TestClient(create_application(configured(tmp_path))) as client:
        runtime = client.app.state.runtime
        assert runtime.started
        assert client.get(CONTROL, headers=DEMO_HEADERS).status_code == 503
        assert client.get(CONTROL, headers=OTHER_HEADERS).status_code == 200
        # Failed owner authority cannot silently substitute the legacy Finance
        # source. Another owner's default-off dashboard remains usable.
        finance = client.get(SURFACE, headers=DEMO_HEADERS)
        assert finance.status_code == 503
        assert finance.json() == {"detail": "finance_control_unavailable"}
        assert client.get(SURFACE, headers=OTHER_HEADERS).status_code == 200
        assert (
            client.get("/api/member/v1/finance/choices", headers=DEMO_HEADERS).status_code
            == 403
        )
        _grant_cos(client, "activation-unavailable-control")
        cos = _post(client, "status", "activation-unavailable-status")
        assert cos.status_code == 200, cos.text
        assert cos.json()["state"] == "permission_required"
        assert "publication" not in cos.json()
        assert (
            client.get(
                "/api/member/v1/surfaces/reminders", headers=DEMO_HEADERS
            ).status_code
            == 200
        )
        with pytest.raises(FinanceAccessError):
            runtime.finance_access.snapshot(
                runtime.settings.instance.tenant_id, PRINCIPAL
            )


@pytest.mark.parametrize("interruption", ["cancel", "timeout", "revoke"])
def test_cancelled_decoder_keeps_admission_until_settled_and_never_imports(
    tmp_path, monkeypatch, interruption
):
    from adminme.products.member import finance_import

    settings = configured(tmp_path)
    p, d, package = fixture(settings.instance.tenant_id)
    manifest, packet = packet_for(p, d, package)
    with TestClient(create_application(settings)) as client:
        control(client, manifest["binding"])
        approval = approve(client, manifest)
        release = threading.Event()
        held_buffers = []
        original = finance_import.decode_package
        if interruption == "timeout":
            monkeypatch.setattr(finance_import, "DECODE_SECONDS", 0.01)

        async def scenario():
            started = asyncio.Event()
            loop = asyncio.get_running_loop()

            def blocked(data, approved):
                held_buffers.append(data)
                loop.call_soon_threadsafe(started.set)
                assert release.wait(10), "test decoder was not released"
                return original(data, approved)

            monkeypatch.setattr(finance_import, "decode_package", blocked)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=client.app),
                base_url="http://synthetic",
            ) as http:
                first = asyncio.create_task(
                    http.post(IMPORTS + approval, headers=DEMO_HEADERS, json=packet)
                )
                try:
                    await asyncio.wait_for(started.wait(), 5)
                    if interruption == "cancel":
                        first.cancel()
                        await asyncio.sleep(0)
                        first.cancel()
                        await asyncio.sleep(0)
                    elif interruption == "timeout":
                        await asyncio.sleep(0.05)
                    else:
                        revoked = await asyncio.wait_for(
                            http.put(
                                CONTROL,
                                headers=DEMO_HEADERS,
                                json={
                                    "request_id": str(uuid4()),
                                    "expected_revision": 1,
                                    "binding": manifest["binding"],
                                    "books_enabled": False,
                                    "cos_analysis_enabled": False,
                                },
                            ),
                            2,
                        )
                        assert revoked.status_code == 200, revoked.text
                    assert not first.done()
                    second = await http.post(
                        IMPORTS + approval, headers=DEMO_HEADERS, json=packet
                    )
                    assert (
                        second.status_code == 409
                        and second.json()["detail"] == "finance_import_busy"
                    )
                    assert len(held_buffers) == 1
                    # Other household requests remain serviceable during decode.
                    assert (
                        await http.get(CONTROL, headers=OTHER_HEADERS)
                    ).status_code == 200
                finally:
                    release.set()
                    if interruption == "cancel":
                        with pytest.raises(asyncio.CancelledError):
                            await first
                    else:
                        response = await first
                        assert response.status_code in (
                            {422} if interruption == "timeout" else {403, 409}
                        ), response.text
                assert len(held_buffers[0]) == 0
                assert not client.app.state.runtime.settings_command_locks[
                    "finance-closed-package-import"
                ].locked()

        client.portal.call(scenario)
        assert not client.portal.call(
            events,
            client.app.state.runtime,
            ["finance.publication_accepted", "finance.projection_verified"],
        )


def test_controls_preserve_chosen_baseline_and_shared_legacy_processing(tmp_path):
    from adminme.projections.xlsx_workbooks import FINANCE_WORKBOOK_NAME
    from tests.acceptance.test_finance_choices import goal

    settings = configured(tmp_path)
    p, d, package = fixture(settings.instance.tenant_id)
    manifest, packet = packet_for(p, d, package)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        daemon = runtime.xlsx_reverse
        client.portal.call(daemon.stop)
        other_before = client.get(SURFACE, headers=OTHER_HEADERS).json()["cockpit"]
        control(client, manifest["binding"])
        assert upload(client, approve(client, manifest), packet).status_code == 200
        saved = client.post(
            "/api/member/v1/finance/choices/goals/chosen-reserve",
            headers=DEMO_HEADERS,
            json=goal(p),
        )
        assert saved.status_code == 200, saved.text
        chosen = client.get(
            "/api/member/v1/finance/choices", headers=DEMO_HEADERS
        ).json()["items"]
        control(client, manifest["binding"], cos=True)
        control(client, manifest["binding"], books=False)
        control(client, manifest["binding"])
        assert upload(client, approve(client, manifest), packet).status_code == 200
        assert (
            client.get("/api/member/v1/finance/choices", headers=DEMO_HEADERS).json()[
                "items"
            ]
            == chosen
        )
        # The actual shared workbook cycle remains active after private consent.
        client.portal.call(daemon.run_cycle_now, FINANCE_WORKBOOK_NAME)
        reverse = client.portal.call(events, runtime, ["xlsx.reverse_projected"])
        assert any(
            e["payload"]["workbook_name"] == FINANCE_WORKBOOK_NAME for e in reverse
        )
        other_after = client.get(SURFACE, headers=OTHER_HEADERS).json()["cockpit"]
        assert other_after["scenarios"] == other_before["scenarios"]
        assert other_after["summary"] == other_before["summary"]
        assert p["publication_id"] not in json.dumps(other_after)


@pytest.mark.parametrize("change", ["expiry", "control_revision"])
def test_import_approval_expiry_and_revision_are_not_reusable(
    tmp_path, monkeypatch, change
):
    from datetime import datetime, timedelta, timezone
    from adminme.products.member import finance_import

    settings = configured(tmp_path)
    p, d, package = fixture(settings.instance.tenant_id)
    manifest, packet = packet_for(p, d, package)
    with TestClient(create_application(settings)) as client:
        control(client, manifest["binding"])
        approval = approve(client, manifest)
        if change == "expiry":
            later = datetime.now(timezone.utc) + timedelta(minutes=31)

            class Later(datetime):
                @classmethod
                def now(cls, tz=None):
                    return later

            monkeypatch.setattr(finance_import, "datetime", Later)
        else:
            control(client, manifest["binding"], books=False)
            control(client, manifest["binding"])
        assert upload(client, approval, packet).status_code in {403, 409}
        assert not client.portal.call(
            events, client.app.state.runtime, ["finance.publication_accepted"]
        )
