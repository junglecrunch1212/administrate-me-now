"""Explicit unavailable-capability regression, retained from the owner-only release.

This module forces only the release ceiling off. Shared-advisor acceptance and
resource drivers exercise shipping availability; this is kill-switch evidence,
not proof that the shipping advisor is disabled.
"""

import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import httpx
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.finance import release
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_activation import (
    CONTROL,
    SURFACE,
    OTHER_HEADERS,
    configured,
    fixture,
    packet_for,
    upload,
    events,
)
from tests.acceptance.test_finance_native_facade import _post, _finance_provenance
from tests.acceptance.test_openclaw_native_seam import (
    PRINCIPAL,
    DEMO_HEADERS,
    OPENCLAW_HEADERS,
    _grant_cos,
)


@pytest.fixture(autouse=True)
def unavailable_capability(monkeypatch):
    monkeypatch.setattr(release, "ADVISOR_AVAILABLE", False)


def owner_import(client, headers=DEMO_HEADERS, owner=PRINCIPAL):
    runtime = client.app.state.runtime
    p, d, package = fixture(runtime.settings.instance.tenant_id, owner=owner)
    manifest, packet = packet_for(p, d, package)
    state = client.get(CONTROL, headers=headers).json()
    response = client.put(
        CONTROL,
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "expected_revision": state["revision"],
            "binding": manifest["binding"],
            "books_enabled": True,
            "cos_analysis_enabled": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["advisor_available"] is False
    approval = client.post(
        "/api/member/v1/finance/import-approvals",
        headers=headers,
        json={
            "request_id": str(uuid4()),
            "expected_policy_revision": response.json()["revision"],
            "manifest": manifest,
        },
    )
    assert approval.status_code == 200, approval.text
    result = client.post(
        "/api/member/v1/finance/imports/" + approval.json()["approval_event_id"],
        headers=headers,
        json=packet,
    )
    assert result.status_code == 200, result.text
    return p, d, manifest, packet, approval.json()["approval_event_id"]


def test_owner_planning_encodes_complete_json_on_money_worker(tmp_path, monkeypatch):
    from adminme.finance import planning_store
    from adminme.products.member import main

    with TestClient(create_application(configured(tmp_path))) as client:
        p, d, *_ = owner_import(client)
        runtime = client.app.state.runtime
        original_query = planning_store.query_projection
        original_read = runtime.projections.read
        query_threads, render_threads, loop_threads, expected, detached = (
            [],
            [],
            [],
            [],
            [],
        )

        def observed_query(*args, **kwargs):
            query_threads.append(threading.get_ident())
            value = original_query(*args, **kwargs)
            value["encoding_fixture"] = {
                "text": "Café — 東京",
                "nullable": None,
                "numbers": [0, -7, 9007199254740993, 1.25, -0.0],
                "flag": False,
            }
            expected.append(value)
            return value

        class ObservedJSONResponse(JSONResponse):
            def render(self, content):
                render_threads.append(threading.get_ident())
                return super().render(content)

        async def observed_read(name, callback):
            loop_threads.append(threading.get_ident())
            value = await original_read(name, callback)
            if name == "money":
                assert type(value) is bytes
                detached.append(value)
            return value

        monkeypatch.setattr(planning_store, "query_projection", observed_query)
        monkeypatch.setattr(main, "JSONResponse", ObservedJSONResponse)
        monkeypatch.setattr(runtime.projections, "read", observed_read)
        for report, days in (
            ("planning_contexts", 30),
            ("planning_projection", 7),
            ("planning_projection", 365),
            ("planning_events", 30),
        ):
            response = client.get(
                "/api/member/v1/finance/planning",
                headers=DEMO_HEADERS,
                params={
                    "book_id": p["book_id"],
                    "context_id": d["contexts"][0]["context_id"],
                    "report_id": report,
                    "horizon_days": days,
                },
            )
            assert response.status_code == 200, response.text
            assert response.headers["content-type"] == "application/json"
            assert (
                response.content
                == detached[-1]
                == JSONResponse(jsonable_encoder(expected[-1])).body
            )
            assert response.json() == expected[-1]
        assert len(query_threads) == len(render_threads) == len(detached) == 4
        assert query_threads == render_threads
        assert set(query_threads).isdisjoint(loop_threads)


@pytest.mark.parametrize("revocation", ["identity", "role", "books"])
def test_owner_planning_rechecks_authority_after_encoding(
    tmp_path, monkeypatch, revocation
):
    with TestClient(create_application(configured(tmp_path))) as client:
        p, d, manifest, *_ = owner_import(client)
        runtime = client.app.state.runtime
        original_read = runtime.projections.read
        captured = []

        async def revoke_after_read(name, callback):
            value = await original_read(name, callback)
            if name == "money" and not captured:
                assert type(value) is bytes and p["publication_id"].encode() in value
                captured.append(value)
                if revocation == "identity":
                    runtime.settings = replace(runtime.settings, member_identities={})
                elif revocation == "role":
                    runtime.settings = replace(
                        runtime.settings,
                        member_roles={
                            **runtime.settings.member_roles,
                            PRINCIPAL: "child",
                        },
                    )
                else:
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=client.app),
                        base_url="http://fixture",
                    ) as http:
                        current = await http.get(CONTROL, headers=DEMO_HEADERS)
                        revoked = await http.put(
                            CONTROL,
                            headers=DEMO_HEADERS,
                            json={
                                "request_id": str(uuid4()),
                                "expected_revision": current.json()["revision"],
                                "binding": manifest["binding"],
                                "books_enabled": False,
                                "cos_analysis_enabled": False,
                            },
                        )
                    assert revoked.status_code == 200, revoked.text
            return value

        monkeypatch.setattr(runtime.projections, "read", revoke_after_read)
        response = client.get(
            "/api/member/v1/finance/planning",
            headers=DEMO_HEADERS,
            params={
                "book_id": p["book_id"],
                "context_id": d["contexts"][0]["context_id"],
                "report_id": "planning_projection",
            },
        )
        assert len(captured) == 1
        assert response.status_code == (401 if revocation == "identity" else 403)
        assert (
            p["publication_id"] not in response.text
            and '"summary":' not in response.text
        )


@pytest.mark.parametrize("failure", ["selection", "encoding"])
def test_owner_planning_preserves_selection_and_render_errors(
    tmp_path, monkeypatch, failure
):
    from adminme.finance import planning_store

    with TestClient(
        create_application(configured(tmp_path)), raise_server_exceptions=False
    ) as client:
        p, *_ = owner_import(client)

        def invalid(*args, **kwargs):
            if failure == "selection":
                raise ValueError("fixture_invalid_selection")
            return {"value": float("nan")}

        monkeypatch.setattr(planning_store, "query_projection", invalid)
        response = client.get(
            "/api/member/v1/finance/planning",
            headers=DEMO_HEADERS,
            params={"book_id": p["book_id"]},
        )
        if failure == "selection":
            assert response.status_code == 422
            assert response.json() == {"detail": "finance_planning_selection_invalid"}
        else:
            assert response.status_code == 500
            assert response.text == "Internal Server Error"


async def saved_consent(runtime, binding):
    """Simulate an existing installation, without changing the release ceiling."""
    previous = await runtime.event_log.read_finance_access_control(
        runtime.settings.instance.tenant_id, PRINCIPAL
    )
    assert previous is not None
    now = datetime.now(timezone.utc)
    scope = f"private:{PRINCIPAL}"
    for event_type, payload in [
        (
            "settings.finance_access_changed",
            {
                "owner_id": PRINCIPAL,
                "changed_by_member_id": PRINCIPAL,
                "revision": previous["payload"]["revision"] + 1,
                "prior_event_id": previous["event_id"],
                "binding": binding,
                "books_enabled": True,
                "cos_analysis_enabled": True,
                "provider_effects": False,
            },
        ),
        (
            "settings.finance_metadata_changed",
            {
                "owner_id": PRINCIPAL,
                "changed_by_member_id": PRINCIPAL,
                "revision": 1,
                "prior_event_id": None,
                "owner_read_enabled": True,
                "cos_read_enabled": True,
                "provider_effects": False,
            },
        ),
    ]:
        await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(now.timestamp() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type=event_type,
                schema_version=1,
                occurred_at=now.isoformat(),
                source_adapter="member-settings-control-plane",
                source_account_id=PRINCIPAL,
                actor_identity=PRINCIPAL,
                owner_scope=scope,
                visibility_scope=scope,
                sensitivity="privileged",
                payload=payload,
            )
        )


def test_owner_import_replay_restart_and_enable_refusal(tmp_path):
    assert release.ADVISOR_AVAILABLE is False
    settings = configured(tmp_path)
    with TestClient(create_application(settings)) as client:
        p, d, manifest, packet, approval_id = owner_import(client)
        value = client.get(SURFACE, headers=DEMO_HEADERS)
        assert value.status_code == 200
        assert (
            value.json()["cockpit"]["accepted_books"]["publication_id"]
            == p["publication_id"]
        )
        assert (
            client.get(SURFACE, headers=OTHER_HEADERS)
            .json()["cockpit"]["accepted_books"]
            .get("publication_id")
            is None
        )
        query = client.get(
            "/api/member/v1/finance/planning",
            headers=DEMO_HEADERS,
            params={"book_id": p["book_id"]},
        )
        assert query.status_code == 200, query.text
        assert upload(client, approval_id, packet).json()["accounting"]["idempotent"]
        assert upload(
            client, approval_id, packet, headers=OTHER_HEADERS
        ).status_code in {403, 404, 409}
        client.portal.call(client.app.state.runtime.projections.rebuild, "money")
        assert (
            client.get(SURFACE, headers=DEMO_HEADERS).json()["cockpit"][
                "accepted_books"
            ]["publication_id"]
            == p["publication_id"]
        )
        rejected = client.put(
            CONTROL,
            headers=DEMO_HEADERS,
            json={
                "request_id": str(uuid4()),
                "expected_revision": 1,
                "binding": manifest["binding"],
                "books_enabled": True,
                "cos_analysis_enabled": True,
            },
        )
        assert (
            rejected.status_code == 409
            and rejected.json()["detail"] == "finance_advisor_not_released"
        )
        assert (
            client.get(CONTROL, headers=DEMO_HEADERS).json()["cos_analysis_enabled"]
            is False
        )
        metadata = client.put(
            "/api/member/v1/settings/finance-metadata",
            headers=DEMO_HEADERS,
            json={
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "owner_read_enabled": True,
                "cos_read_enabled": False,
            },
        )
        assert metadata.status_code == 200
        assert (
            client.post(
                "/api/member/v1/finance/bindings", headers=DEMO_HEADERS, json={}
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/member/v1/settings/finance-metadata",
                headers=DEMO_HEADERS,
                json={
                    "request_id": str(uuid4()),
                    "expected_revision": 1,
                    "owner_read_enabled": True,
                    "cos_read_enabled": True,
                },
            ).status_code
            == 409
        )
        assert not client.portal.call(
            events, client.app.state.runtime, ["finance.disclosure_recorded"]
        )
    with TestClient(create_application(settings)) as client:
        assert release.ADVISOR_AVAILABLE is False
        assert (
            client.get(CONTROL, headers=DEMO_HEADERS).json()["advisor_available"]
            is False
        )
        assert (
            client.get(SURFACE, headers=DEMO_HEADERS).json()["cockpit"][
                "accepted_books"
            ]["publication_id"]
            == p["publication_id"]
        )


def test_saved_true_consent_cannot_release_any_advisor_surface(tmp_path, monkeypatch):
    assert release.ADVISOR_AVAILABLE is False
    from adminme.finance import (
        capabilities,
        calculators,
        disclosure,
        metadata,
        queries,
        planning_store,
    )

    with TestClient(create_application(configured(tmp_path))) as client:
        p, d, manifest, _, _ = owner_import(client)
        runtime = client.app.state.runtime
        from adminme.capability_catalog import load_catalog

        catalog = load_catalog(runtime.settings.repository_root)
        advisor = next(
            row
            for row in catalog["items"]
            if row["capability_id"] == "domain.household_finance"
        )
        assert advisor["openclaw_tools"] == []
        assert advisor["implementation_present"] is False
        assert set(advisor["verbs"].values()) == {"unsupported"}
        assert "unavailable" in advisor["unavailable_reason"]
        assert len(catalog["openclaw_plugin"]["tools"]) == 46
        _grant_cos(client, "owner-release-existing-private-grant")
        client.portal.call(saved_consent, runtime, manifest["binding"])
        stored = client.get(CONTROL, headers=DEMO_HEADERS).json()
        assert (
            stored["cos_analysis_enabled"] is True
            and stored["advisor_available"] is False
        )
        assert (
            client.get(
                "/api/member/v1/settings/finance-metadata", headers=DEMO_HEADERS
            ).json()["cos_read_enabled"]
            is True
        )

        def forbidden(*args, **kwargs):
            raise AssertionError(
                "Owner release entered an unreleased Finance operation"
            )

        for module, name in [
            (metadata, "inspect_metadata"),
            (queries, "publication_status"),
            (queries, "query_publication"),
            (planning_store, "query_projection"),
            (calculators, "calculate"),
            (capabilities, "skill_bundle"),
            (disclosure, "record_disclosure"),
        ]:
            monkeypatch.setattr(module, name, forbidden)
        cases = [
            ("bindings", {}),
            ("status", {}),
            ("status", {"include_bindings": True}),
            ("status", {"capability_id": "F07", "include_bundle": True}),
            ("status", {"calculator_id": "reserve_coverage"}),
            ("status", {"compound_id": "household_readiness"}),
            (
                "calculate",
                {
                    "calculator_id": "reserve_coverage",
                    "version": "1",
                    "hypothetical": True,
                    "inputs": {},
                },
            ),
        ]
        for report in [
            "actuals",
            "planning_contexts",
            "planning_projection",
            "owner_choices",
        ]:
            cases.append(
                (
                    "query",
                    {
                        "book_id": p["book_id"],
                        "publication_id": p["publication_id"],
                        "report_id": report,
                    },
                )
            )
        for operation in ["submit", "status", "cancel"]:
            cases.append(("job", {"operation": operation}))
        for index, (route, body) in enumerate(cases):
            result = _post(client, route, f"owner-release-{index}", **body)
            assert result.status_code == 200, result.text
            assert result.json() == release.advisor_unavailable()
        assert release.ADVISOR_AVAILABLE is False
        assert not client.portal.call(events, runtime, ["finance.disclosure_recorded"])
        provenance = _finance_provenance(client, "generic-context")
        for path in [
            "/dashboard/finance",
            "/dashboard/today",
            "/tools/settings-context",
            "/tools/state",
        ]:
            result = client.post(
                "/api/openclaw/v1" + path,
                headers=OPENCLAW_HEADERS,
                json={"provenance": provenance},
            )
            assert result.status_code == 200, result.text
            assert (
                p["publication_id"] not in result.text
                and p["book_id"] not in result.text
            )
            assert (
                '"type":"finance.' not in result.text
                and '"type":"settings.finance_' not in result.text
            )
            assert "administrateme_finance_status" not in result.text
        assert (
            client.post(
                "/api/openclaw/v1/tools/finance-status", json={"provenance": provenance}
            ).status_code
            == 401
        )
        # Saved permissions and owner history were not rewritten by the ceiling.
        assert (
            client.get(CONTROL, headers=DEMO_HEADERS).json()["cos_analysis_enabled"]
            is True
        )
        assert (
            len(client.portal.call(events, runtime, ["finance.publication_accepted"]))
            == 1
        )


def test_ordinary_portal_continues_after_owner_import(tmp_path):
    assert release.ADVISOR_AVAILABLE is False
    settings = replace(
        configured(tmp_path),
        deterministic_member_chat_fixture=True,
        openclaw_model_configured=True,
    )
    with TestClient(create_application(settings)) as client:
        _grant_cos(client, "owner-release-chat-grant")
        for imported in [False, True]:
            if imported:
                owner_import(client)
            result = client.post(
                "/api/member/v1/chat/stream",
                headers=DEMO_HEADERS,
                json={
                    "message": "Help plan the ordinary day.",
                    "request_id": str(uuid4()),
                },
            )
            assert result.status_code == 200, result.text
            chunks = [
                json.loads(line[6:])
                for line in result.text.splitlines()
                if line.startswith("data: ")
            ]
            assert not any(chunk["kind"] == "error" for chunk in chunks), result.text
            assert any(chunk["kind"] == "final" for chunk in chunks), result.text
        assert not client.portal.call(
            events, client.app.state.runtime, ["finance.disclosure_recorded"]
        )


def test_owner_import_preserves_native_reply_and_prepared_reminder(
    tmp_path, monkeypatch
):
    """Only synthetic transport claims/receipts; no provider is invoked."""
    from tests.acceptance.test_cos_response_execution import A, HEADERS, AUTH, _settings
    from tests.acceptance.test_cos_self_replies import (
        _activate,
        _inbound,
        _send,
        ENDPOINT,
    )
    from tests.acceptance.test_cos_self_reminders import (
        enable,
        prepare,
        advance,
        receipt,
        BASE,
    )

    assert release.ADVISOR_AVAILABLE is False
    with TestClient(create_application(_settings(tmp_path))) as client:
        _activate(client, monkeypatch)
        enable(client)
        request, callback, due = prepare(client)
        owner_import(client, HEADERS, A)
        _send(
            client,
            _inbound(
                client,
                native_id="owner-release-reply",
                text="Help with the ordinary day.",
            ),
        )
        advance(monkeypatch, due + timedelta(seconds=1))
        fired = client.post(BASE + "/fire", headers=AUTH, json=callback)
        assert fired.status_code == 200, fired.text
        dispatch = fired.json()["transport_dispatch"]
        assert dispatch["body"] == request["body"]
        claim = client.post(
            ENDPOINT + "/claim",
            headers=AUTH,
            json={k: dispatch[k] for k in ("account_id", "recipient", "body")},
        )
        assert claim.status_code == 200, claim.text
        terminal = client.post(
            ENDPOINT + "/terminal",
            headers=AUTH,
            json={"claim_id": claim.json()["claim_id"], "status": "sent"},
        )
        assert terminal.status_code == 200, terminal.text
        assert (
            receipt(client, request, callback["reminder_event_id"])["status"] == "sent"
        )
        repeated = client.post(BASE + "/fire", headers=AUTH, json=callback)
        assert (
            repeated.status_code == 200 and "transport_dispatch" not in repeated.json()
        )
        assert not client.portal.call(
            events, client.app.state.runtime, ["finance.disclosure_recorded"]
        )


def test_kill_switch_preserves_existing_discussion_but_denies_new_reference(tmp_path):
    from adminme.finance.disclosure import (
        _append,
        _marker_key,
        _session_digest,
        check_disclosure,
        session_has_finance,
        FinanceDisclosureError,
    )
    from adminme.lib.session import build_internal_session

    assert release.ADVISOR_AVAILABLE is False
    settings = replace(
        configured(tmp_path),
        deterministic_member_chat_fixture=True,
        openclaw_model_configured=True,
    )
    with TestClient(create_application(settings)) as client:
        _grant_cos(client, "owner-release-marked-history")
        runtime = client.app.state.runtime
        session = build_internal_session(
            PRINCIPAL, "principal", runtime.settings.instance.tenant_id
        )
        _, key = runtime.member_chat_binding(PRINCIPAL)
        digest = _session_digest(session, key)
        # Historical encrypted marker, not a release override or a fresh disclosure.
        client.portal.call(
            _append,
            runtime,
            session,
            _marker_key(digest),
            {
                "kind": "session",
                "member_id": PRINCIPAL,
                "session_sha256": digest,
                "authority_sha256": "a" * 64,
                "issued_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        owner_import(client)
        assert client.portal.call(session_has_finance, runtime, session, key)
        runtime._finance_disclosure_sessions.clear()
        assert client.portal.call(check_disclosure, runtime, session, key) is None

        async def new_reference():
            return await check_disclosure(
                runtime,
                session,
                key,
                answer_text=json.dumps({"finance_answer": {"result_refs": ["a" * 64]}}),
            )

        with pytest.raises(
            FinanceDisclosureError, match="finance_disclosure_authority_changed"
        ):
            client.portal.call(new_reference)
        history = client.get("/api/member/v1/chat/history", headers=DEMO_HEADERS)
        assert history.status_code == 200
        assert (
            len(client.portal.call(events, runtime, ["finance.disclosure_recorded"]))
            == 1
        )

    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        session = build_internal_session(
            PRINCIPAL, "principal", runtime.settings.instance.tenant_id
        )
        _, key = runtime.member_chat_binding(PRINCIPAL)
        assert client.portal.call(session_has_finance, runtime, session, key)
        assert client.portal.call(check_disclosure, runtime, session, key) is None

        async def new_reference():
            return await check_disclosure(
                runtime,
                session,
                key,
                answer_text=json.dumps({"finance_answer": {"result_refs": ["a" * 64]}}),
            )

        with pytest.raises(
            FinanceDisclosureError, match="finance_disclosure_authority_changed"
        ):
            client.portal.call(new_reference)
        assert (
            client.get("/api/member/v1/chat/history", headers=DEMO_HEADERS).status_code
            == 200
        )
        assert (
            len(client.portal.call(events, runtime, ["finance.disclosure_recorded"]))
            == 1
        )


def test_service_context_rejects_exact_owner_books_event_ids(tmp_path):
    from tests.acceptance.test_cos_identity_delegation_kernel import (
        A,
        A_HEADERS,
        _settings,
        _grant_private,
    )
    from tests.acceptance.test_cos_service_working_context import _context, _source

    assert release.ADVISOR_AVAILABLE is False
    with TestClient(create_application(_settings(tmp_path))) as client:
        _grant_private(client, A, "owner-release-service-context")
        ordinary = _source(client, A, "Ordinary operational fact")
        owner_import(client, A_HEADERS, A)
        runtime = client.app.state.runtime
        books = client.portal.call(
            events,
            runtime,
            [
                "finance.publication_accepted",
                "finance.projection_verified",
                "settings.finance_access_changed",
            ],
        )
        assert len(books) == 3
        assert (
            _context(
                client, "ordinary-exact-source", source_event_ids=[ordinary]
            ).status_code
            == 200
        )
        for event in books:
            result = _context(
                client, "held-books-source", source_event_ids=[event["event_id"]]
            )
            assert result.status_code == 404, result.text
        scan = _context(client, "ordinary-source-scan")
        assert scan.status_code == 200
        assert {fact["event_id"] for fact in scan.json()["facts"]} == {ordinary}
        assert not client.portal.call(events, runtime, ["finance.disclosure_recorded"])
