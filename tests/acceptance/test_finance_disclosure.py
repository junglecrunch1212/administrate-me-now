"""Final finance delivery uses current grants and durable private provenance."""

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


def enabled_settings(tmp_path):
    return replace(
        _settings(tmp_path),
        finance_publications_enabled=True,
        finance_model_members=frozenset({PRINCIPAL}),
        deterministic_member_chat_fixture=True,
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
    ["revoked", "disabled", "changed_run", "missing_final", "oversized", "none"],
)
def test_stream_buffers_finance_until_final_authorization(tmp_path, fault):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "stream-grant")
        assert calculate(client, "stream-run").status_code == 200
        runtime = client.app.state.runtime
        aborted = []

        async def stream(**kwargs):
            yield MemberChatChunk(
                kind="status", phase="starting_model", run_id="stream-run"
            )
            yield MemberChatChunk(kind="delta", text="Protected finance answer")
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
                    run_id="other-run" if fault == "changed_run" else "stream-run",
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
            assert "Protected finance answer" in response.text
            assert (
                '"kind": "final"' in response.text or '"kind":"final"' in response.text
            )
            assert not aborted
        else:
            assert "Protected finance answer" not in response.text
            assert "finance_disclosure_" in response.text
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
