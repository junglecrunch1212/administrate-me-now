"""Final finance delivery uses current grants and durable private provenance."""

import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.finance.disclosure import check_disclosure, FinanceDisclosureError
from adminme.lib.session import build_internal_session
from adminme.member_chat import MemberChatChunk
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_native_facade import _settings, _post
from tests.acceptance.test_openclaw_native_seam import (
    PRINCIPAL,
    DEMO_HEADERS,
    _grant_cos,
)

# Held advisor contracts only; the owner-release suite uses the unmodified ceiling.
pytestmark = pytest.mark.usefixtures("unreleased_finance_advisor")


def enabled_settings(tmp_path):
    return replace(
        _settings(tmp_path),
        finance_publications_enabled=True,
        finance_model_members=frozenset({PRINCIPAL}),
        deterministic_member_chat_fixture=True,
        openclaw_model_configured=True,
    )


def calculate(client, message="disclosure-run"):
    return _post(
        client,
        "calculate",
        message,
        calculator_id="reserve_coverage",
        version="1",
        hypothetical=True,
        inputs={
            "currency": "USD",
            "liquid_cash_minor": 120000,
            "monthly_reference_spending_minor": 30000,
            "chosen_reserve_floor_minor": 60000,
            "liquidity_confirmed": True,
            "reference_basis": "chosen_essential_spending",
        },
    )


async def revoke(runtime, grant):
    now = datetime.now(timezone.utc)
    scope = f"private:{PRINCIPAL}"
    event_id = await runtime.events.write(
        EventEnvelope(
            event_at_ms=int(now.timestamp() * 1000),
            tenant_id=runtime.settings.instance.tenant_id,
            type="service.delegation.revoked",
            schema_version=1,
            occurred_at=now.isoformat(),
            source_adapter="synthetic_acceptance",
            source_account_id="synthetic",
            actor_identity=PRINCIPAL,
            owner_scope=scope,
            visibility_scope=scope,
            sensitivity="privileged",
            payload={
                "grant_id": grant,
                "revoked_by_member_id": PRINCIPAL,
                "revoked_at": now.isoformat(),
                "reason": "fixture",
            },
        )
    )
    await runtime.wait_for_projection("parties", event_id)


def test_tool_delivery_records_private_receipts_and_revoked_history_fails(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "disclosure-grant")
        assert calculate(client).status_code == 200
        runtime = client.app.state.runtime
        session = build_internal_session(
            PRINCIPAL, "principal", runtime.settings.instance.tenant_id
        )
        _, key = runtime.member_chat_binding(PRINCIPAL)

        async def check():
            await check_disclosure(
                runtime, session, key, run_id="disclosure-run", require_run=True
            )

        client.portal.call(check)
        # Drop volatile hints: the encrypted event history remains authoritative.
        runtime._finance_disclosure_sessions.clear()
        client.portal.call(revoke, runtime, grant)
        with pytest.raises(FinanceDisclosureError, match="authority_changed"):
            client.portal.call(check)
        response = client.get("/api/member/v1/chat/history", headers=DEMO_HEADERS)
        assert response.status_code == 403
        assert "120000" not in response.text


@pytest.mark.parametrize(
    "fault",
    [
        "revoked",
        "disabled",
        "changed_run",
        "unrecognized_run",
        "invented_claim",
        "missing_final",
        "oversized",
        "none",
    ],
)
def test_stream_buffers_finance_until_final_authorization(tmp_path, fault):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "stream-grant")
        result = calculate(client, "stream-run")
        assert result.status_code == 200, result.text
        answer = json.dumps(
            {"finance_answer": {"result_refs": [result.json()["answer_ref"]]}}
        )
        if fault == "invented_claim":
            answer = answer[:-1] + ', "cash_available": 999999}'
        runtime = client.app.state.runtime
        aborted = []

        async def stream(**kwargs):
            run_id = "unrecognized" if fault == "unrecognized_run" else "stream-run"
            yield MemberChatChunk(kind="status", phase="starting_model", run_id=run_id)
            yield MemberChatChunk(kind="delta", text=answer)
            if fault == "revoked":
                await revoke(runtime, grant)
            if fault == "disabled":
                runtime.settings = replace(
                    runtime.settings, finance_publications_enabled=False
                )
            if fault == "oversized":
                yield MemberChatChunk(kind="delta", text="x" * 24001)
            if fault != "missing_final":
                yield MemberChatChunk(
                    kind="final",
                    run_id="other-run" if fault == "changed_run" else run_id,
                )

        async def abort(**kwargs):
            aborted.append(True)

        runtime.member_chat = SimpleNamespace(stream=stream, abort=abort)
        response = client.post(
            "/api/member/v1/chat/stream",
            headers=DEMO_HEADERS,
            json={"message": "Explain the calculation.", "request_id": "stream-check"},
        )
        assert response.status_code == 200, response.text
        if fault == "none":
            assert "4.000000 months" in response.text
            assert "Caller-supplied inputs" in response.text
            assert (
                '"kind": "final"' in response.text or '"kind":"final"' in response.text
            )
            assert not aborted
        else:
            assert "4.000000 months" not in response.text
            assert "finance_" in response.text
            assert aborted
        assert "stream-run" not in response.text


def test_disclosure_rechecks_after_durable_append(tmp_path, monkeypatch):
    from adminme.finance import disclosure

    original = disclosure._append
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "append-grant")

        async def append_then_revoke(runtime, session, key, payload):
            await original(runtime, session, key, payload)
            if payload["kind"] == "run":
                await revoke(runtime, grant)

        monkeypatch.setattr(disclosure, "_append", append_then_revoke)
        response = calculate(client)
        assert response.status_code == 403
        assert "values" not in response.text


def import_fixture(client):
    from tests.acceptance.test_finance_publications import publication
    from adminme.finance.publication import content_hash
    from adminme.finance.importer import import_publication

    runtime = client.app.state.runtime
    value = publication(runtime.settings.instance.tenant_id)
    value["owner_id"] = PRINCIPAL
    for report in value["reports"]:
        for metric in report["metrics"]:
            metric["owner_id"] = PRINCIPAL
    value["content_sha256"] = content_hash(value)
    owner = build_internal_session(PRINCIPAL, "principal", value["tenant_id"])
    client.portal.call(import_publication, runtime, owner, value)
    return value, owner


def test_readiness_exposes_qualified_report_without_inventing_dependencies(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "readiness-grant")
        value, _ = import_fixture(client)
        response = _post(
            client, "status", "readiness", capability_id="F01", book_id=value["book_id"]
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["readiness"] == "missing_evidence", data
        assert "category_totals" in data["missing_evidence"]
        report = data["report_availability"][0]
        assert report["readiness"] == "qualified"
        assert report["numeric_conclusion_supported"] is False
        assert report["next_query"]["publication_id"] == value["publication_id"]
        assert "value_minor" not in response.text


def test_new_publication_invalidates_inflight_result_but_allows_pinned_history(
    tmp_path,
):
    from adminme.finance.publication import content_hash
    from adminme.finance.importer import import_publication
    from copy import deepcopy

    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "publication-grant")
        value, owner = import_fixture(client)
        runtime = client.app.state.runtime
        _, key = runtime.member_chat_binding(PRINCIPAL)
        response = _post(
            client,
            "query",
            "before-change",
            book_id=value["book_id"],
            publication_id=value["publication_id"],
            report_id="actuals",
            limit=1,
        )
        assert response.status_code == 200 and response.json().get("metrics"), (
            response.text
        )
        successor = deepcopy(value)
        successor["prior_publication_id"] = value["publication_id"]
        successor["publication_id"] = "publication-next"
        successor["content_sha256"] = content_hash(successor)
        client.portal.call(import_publication, runtime, owner, successor)

        async def check(run):
            await check_disclosure(runtime, owner, key, run_id=run, require_run=True)

        with pytest.raises(FinanceDisclosureError, match="publication_changed"):
            client.portal.call(check, "before-change")
        response = _post(
            client,
            "query",
            "historical",
            book_id=value["book_id"],
            publication_id=value["publication_id"],
            report_id="actuals",
            limit=1,
        )
        assert response.status_code == 200 and response.json().get("metrics"), (
            response.text
        )
        assert response.json()["state"] == "qualified"
        client.portal.call(check, "historical")


def test_receipt_write_cannot_extend_query_deadline(tmp_path, monkeypatch):
    from adminme.finance import disclosure, budgets

    original = disclosure._append
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "deadline-grant")
        clock = [100.0]
        monkeypatch.setattr(
            budgets.TurnBudgetStore, "_time", staticmethod(lambda now: clock[0])
        )

        async def slow_append(runtime, session, key, payload):
            await original(runtime, session, key, payload)
            if payload["kind"] == "run":
                clock[0] += 2.1

        monkeypatch.setattr(disclosure, "_append", slow_append)
        response = calculate(client)
        assert response.status_code == 200
        assert response.json()["reason"] == "finance_query_deadline"
        assert "120000" not in response.text
        assert "values" not in response.json()


@pytest.mark.parametrize("revoke_before_claim", [False, True])
def test_native_reply_renders_current_result_and_rechecks_at_claim(
    tmp_path, monkeypatch, revoke_before_claim
):
    from tests.acceptance import test_cos_self_replies as native
    from tests.unit.test_finance_calculators import inputs

    settings = replace(
        native._settings(tmp_path),
        finance_publications_enabled=True,
        finance_model_members=frozenset({native.A}),
    )
    with TestClient(create_application(settings)) as client:
        native._activate(client, monkeypatch)
        body = native._inbound(client)
        provenance = {
            **body["provenance"],
            "call_kind": "tool",
            "message_id": "finance-run:native-finance",
        }
        result = client.post(
            "/api/openclaw/v1/tools/finance-calculate",
            headers=native.AUTH,
            json={
                "provenance": provenance,
                "calculator_id": "reserve_coverage",
                "version": "1",
                "hypothetical": True,
                "inputs": inputs()["reserve_coverage"],
            },
        )
        assert result.status_code == 200, result.text
        body.update(
            finance_run_id="native-finance",
            body=json.dumps(
                {"finance_answer": {"result_refs": [result.json()["answer_ref"]]}}
            ),
        )
        response = client.post(
            native.ENDPOINT + "/self-reply", headers=native.AUTH, json=body
        )
        assert response.status_code == 200, response.text
        dispatch = response.json()["transport_dispatch"]
        assert "4.000000 months" in dispatch["body"]
        assert "Caller-supplied inputs" in dispatch["body"]
        assert "finance_verified_input_sha256" in response.json()
        if revoke_before_claim:
            runtime = client.app.state.runtime
            runtime.settings = replace(
                runtime.settings, finance_publications_enabled=False
            )
        claim = client.post(
            native.ENDPOINT + "/claim",
            headers=native.AUTH,
            json={key: dispatch[key] for key in ("account_id", "recipient", "body")},
        )
        assert claim.status_code == (403 if revoke_before_claim else 200), claim.text


def test_new_grant_requires_fresh_result_without_permanently_blocking_conversation(
    tmp_path,
):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "original-finance-authority")
        result = calculate(client, "original-run")
        assert result.status_code == 200
        runtime = client.app.state.runtime
        session = build_internal_session(
            PRINCIPAL, "principal", runtime.settings.instance.tenant_id
        )
        _, key = runtime.member_chat_binding(PRINCIPAL)
        client.portal.call(revoke, runtime, grant)
        _grant_cos(client, "replacement-finance-authority")

        async def check(run):
            await check_disclosure(runtime, session, key, run_id=run, require_run=True)

        with pytest.raises(FinanceDisclosureError):
            client.portal.call(check, "original-run")
        fresh = calculate(client, "fresh-run")
        assert fresh.status_code == 200, fresh.text
        client.portal.call(check, "fresh-run")


def test_publication_change_during_later_receipt_reads_blocks_earlier_result(
    tmp_path, monkeypatch
):
    from copy import deepcopy
    from adminme.finance import disclosure
    from adminme.finance.publication import content_hash
    from adminme.finance.importer import import_publication

    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "publication-race-grant")
        value, owner = import_fixture(client)
        result = _post(
            client,
            "query",
            "publication-race",
            book_id=value["book_id"],
            publication_id=value["publication_id"],
            report_id="actuals",
            limit=1,
        )
        assert result.status_code == 200 and result.json().get("metrics")
        runtime = client.app.state.runtime
        _, key = runtime.member_chat_binding(PRINCIPAL)
        successor = deepcopy(value)
        successor["prior_publication_id"] = value["publication_id"]
        successor["publication_id"] = "publication-during-final-read"
        successor["content_sha256"] = content_hash(successor)
        original = disclosure._read

        async def read_then_change(runtime, session, key):
            found = await original(runtime, session, key)
            if key.startswith("finance-disclosure-run:") and key.endswith(":6"):
                await import_publication(runtime, owner, successor)
            return found

        monkeypatch.setattr(disclosure, "_read", read_then_change)

        async def verify():
            await check_disclosure(
                runtime, owner, key, run_id="publication-race", require_run=True
            )

        with pytest.raises(FinanceDisclosureError, match="publication_changed"):
            client.portal.call(verify)


@pytest.mark.parametrize("report_id", ["actuals", "owner_choices"])
def test_authority_revoked_during_async_finance_read_cannot_receive_result(
    tmp_path, monkeypatch, report_id
):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "async-finance-read")
        publication, _ = import_fixture(client)
        runtime = client.app.state.runtime
        original = runtime.projections.read
        changed = False

        async def read_then_revoke(name, query):
            nonlocal changed
            value = await original(name, query)
            if name == "money" and not changed:
                changed = True
                await revoke(runtime, grant)
            return value

        monkeypatch.setattr(runtime.projections, "read", read_then_revoke)
        response = _post(
            client,
            "query",
            "async-read-revocation",
            book_id=publication["book_id"],
            publication_id=publication["publication_id"],
            report_id=report_id,
            limit=1,
        )
        assert changed
        assert response.status_code == 403, response.text
        assert "publication_id" not in response.text
        assert "choices" not in response.text and "metrics" not in response.text


@pytest.mark.parametrize("change", ["portal_binding", "transport_key"])
def test_binding_or_transport_revoked_during_receipt_append_blocks_delivery(
    tmp_path, monkeypatch, change
):
    from adminme.finance import disclosure

    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "receipt-binding-race")
        original = disclosure._append

        async def append_then_change(runtime, session, key, payload):
            await original(runtime, session, key, payload)
            if payload["kind"] == "run":
                if change == "portal_binding":
                    runtime.settings = replace(
                        runtime.settings, member_chat_enabled_members=frozenset()
                    )
                else:
                    runtime.settings = replace(
                        runtime.settings,
                        openclaw_shared_secret="rotated-synthetic-transport-key",
                    )

        monkeypatch.setattr(disclosure, "_append", append_then_change)
        response = calculate(client, "receipt-binding-race")
        assert response.status_code in {401, 403}, response.text
        assert "values" not in response.text and "answer_ref" not in response.text


@pytest.mark.parametrize("change", ["revoke_grant", "remove_owner_role"])
def test_authority_change_after_receipt_snapshot_blocks_release(
    tmp_path, monkeypatch, change
):
    """The receipt's already-resolved authority never replaces its final check."""
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "receipt-authority-snapshot")
        runtime = client.app.state.runtime
        original = runtime.event_log.get_by_idempotency_keys
        changed = False

        async def read_then_change(tenant, keys):
            nonlocal changed
            found = await original(tenant, keys)
            if not changed and any(
                k.startswith("finance-disclosure-run:") for k in keys
            ):
                changed = True
                if change == "revoke_grant":
                    await revoke(runtime, grant)
                else:
                    runtime.settings = replace(
                        runtime.settings,
                        member_roles={
                            **runtime.settings.member_roles,
                            PRINCIPAL: "child",
                        },
                    )
            return found

        monkeypatch.setattr(
            runtime.event_log, "get_by_idempotency_keys", read_then_change
        )
        response = calculate(client, "receipt-authority-snapshot")
        assert changed
        assert response.status_code == 403, response.text
        assert "values" not in response.text and "answer_ref" not in response.text


def test_finance_receipt_yields_to_new_foreground_then_rechecks_authority(
    tmp_path, monkeypatch
):
    import asyncio
    from adminme.finance import disclosure

    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "foreground-revocation")
        original = disclosure._append
        foreground = []

        async def append_with_new_foreground(runtime, session, key, payload):
            await original(runtime, session, key, payload)
            if payload["kind"] == "run":
                runtime.finance_admission.foreground_started()

                async def finish_foreground():
                    try:
                        await asyncio.sleep(0.01)
                        await revoke(runtime, grant)
                    finally:
                        runtime.finance_admission.foreground_finished()

                foreground.append(asyncio.create_task(finish_foreground()))

        monkeypatch.setattr(disclosure, "_append", append_with_new_foreground)
        response = calculate(client, "foreground-revoked")
        assert response.status_code == 403, response.text
        assert "120000" not in response.text and foreground[0].done()
