"""Credential-free production composition proof of the thin private facade."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from adminme.runtime.application import create_application
from adminme.openclaw_topology import member_agent_id
from tests.acceptance.test_openclaw_native_seam import (
    DEMO_HEADERS,
    OPENCLAW_HEADERS,
    PRINCIPAL,
    OTHER,
    _grant_cos,
    _provenance as _generic_provenance,
    _settings as _native_settings,
)

BASE = "/api/openclaw/v1/tools/finance-"


def _settings(tmp_path: Path):
    return replace(
        _native_settings(tmp_path), member_chat_enabled_members=frozenset({PRINCIPAL})
    )


def _finance_provenance(client: TestClient, message: str):
    actor, session_key = client.app.state.runtime.member_chat_binding(PRINCIPAL)
    value = _generic_provenance(message)
    value.update(
        actor_id=actor,
        session_key=session_key,
        agent_id=member_agent_id(PRINCIPAL),
        channel_id="administrateme-portal",
        account_id="member-portal",
        message_id=f"finance-run:{message}",
        thread_id=None,
    )
    return value


def _post(client: TestClient, route: str, message: str, **body: object):
    return client.post(
        BASE + route,
        headers=OPENCLAW_HEADERS,
        json={"provenance": _finance_provenance(client, message), **body},
    )


def test_capacity_busy_is_audited_before_any_finance_read(
    tmp_path: Path, monkeypatch
) -> None:
    from adminme.finance import queries
    from adminme.products.openclaw import main

    settings = replace(
        _settings(tmp_path),
        finance_publications_enabled=True,
        finance_model_members=frozenset({PRINCIPAL}),
    )
    app = create_application(settings)
    audit_operations: list[str] = []
    original_audit = main._record_tool_call

    async def audit(request, session, provenance, **kwargs):
        result = await original_audit(request, session, provenance, **kwargs)
        audit_operations.append(kwargs["operation"])
        return result

    def unexpected_read(*args, **kwargs):
        raise AssertionError("finance read happened without capacity admission")

    with TestClient(app) as client:
        _grant_cos(client, "finance-capacity-grant")
        monkeypatch.setattr(main, "_record_tool_call", audit)
        monkeypatch.setattr(queries, "publication_status", unexpected_read)
        assert client.portal is not None
        gate = app.state.runtime.finance_admission
        client.portal.call(gate.foreground_started)
        try:
            response = _post(client, "status", "capacity-busy")
        finally:
            client.portal.call(gate.foreground_finished)
        assert response.status_code == 200, response.text
        assert response.json()["reason"] == "finance_core_capacity_busy"
        assert audit_operations == ["finance.status"]


def test_default_disabled_and_member_permission_are_separate(tmp_path: Path) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        _grant_cos(client, "finance-default-grant")
        response = _post(client, "status", "default", capability_id="F07")
        assert response.status_code == 200, response.text
        assert response.json()["readiness"] == "permission_required"
        assert "cash_minor" not in response.text
        assert "publication_id" not in response.text
        response = _post(
            client,
            "query",
            "default-query",
            book_id="guess-book",
            publication_id="guess-publication",
            report_id="actuals",
        )
        assert response.json()["state"] == "permission_required"
        assert "guess-book" not in response.text


def test_enabled_empty_and_model_boundary_through_real_api(tmp_path: Path) -> None:
    settings = replace(
        _settings(tmp_path),
        finance_publications_enabled=True,
        finance_model_members=frozenset({PRINCIPAL}),
    )
    app = create_application(settings)
    with TestClient(app) as client:
        _grant_cos(client, "finance-enabled-grant")
        response = _post(client, "status", "empty")
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "missing_evidence"
        assert "value_minor" not in response.text
        for capability_id in ("F01", "F09", "F19", "F23", "F27", "F31"):
            response = _post(
                client, "status", f"route-{capability_id}", capability_id=capability_id
            )
            assert response.status_code == 200, response.text
            assert response.json()["readiness"] == "missing_evidence"
            assert (
                response.json()["implementation_state_applies_to"]
                == "routing_and_preparation"
            )
        blocked = _post(
            client,
            "calculate",
            "unsupported",
            calculator_id="tax_estimate",
            version="1",
            inputs={},
            hypothetical=True,
        )
        assert blocked.json()["state"] == "model_unavailable"
        job = _post(client, "job", "heavy", operation="submit")
        assert job.json()["accepted"] is False
        assert job.json()["job_id"] is None
        assert job.json()["implementation_state"] == "planned"
        compound = _post(
            client, "status", "compound", compound_id="retirement_incapacity"
        )
        assert compound.json()["financial_verdict_supported"] is False
        assert {row["capability_id"] for row in compound.json()["dependencies"]} == {
            "F23",
            "F25",
            "F27",
        }
        preparation = _post(
            client, "status", "professional", capability_id="F20", include_bundle=True
        )
        assert preparation.json()["bundle"]["mode"] == "preparation_only"
        assert preparation.json()["bundle"]["disclosure_authorized"] is False


def test_hypothetical_math_never_becomes_accepted_truth(tmp_path: Path) -> None:
    settings = replace(
        _settings(tmp_path),
        finance_publications_enabled=True,
        finance_model_members=frozenset({PRINCIPAL}),
    )
    app = create_application(settings)
    with TestClient(app) as client:
        _grant_cos(client, "finance-calculate-grant")
        response = _post(
            client,
            "calculate",
            "unit-price",
            calculator_id="unit_cost",
            version="1",
            hypothetical=True,
            inputs={
                "currency": "USD",
                "purchase_minor": 1000,
                "allocated_tax_minor": 60,
                "allocated_shipping_minor": 100,
                "allocated_discount_minor": 160,
                "matched_refund_minor": 0,
                "retained_quantity": "5",
                "unit": "count",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["hypothetical"] is True, result
        assert result["accepted_baseline_modified"] is False
        assert result["values"]["retained_net_cost_minor"] == 1000
        assert result["values"]["minor_units_per_selected_unit"] == "200.000000"
        assert result["provider_effect_count"] == 0
        assert (
            _post(client, "status", "still-empty").json()["state"] == "missing_evidence"
        )


def test_revoked_grant_and_invalid_page_never_disclose(tmp_path: Path) -> None:
    settings = replace(
        _settings(tmp_path),
        finance_publications_enabled=True,
        finance_model_members=frozenset({PRINCIPAL}),
    )
    app = create_application(settings)
    with TestClient(app) as client:
        grant = _grant_cos(client, "finance-revoke-grant")
        invalid = _post(
            client,
            "query",
            "bad-page",
            book_id="book",
            publication_id="publication",
            report_id="actuals",
            limit=101,
        )
        assert invalid.status_code == 422
        unknown = _post(client, "status", "bad-route", capability_id="F99")
        assert unknown.status_code == 422
        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant}",
            headers=DEMO_HEADERS,
            json={"request_id": "finance-revoke", "reason": "fixture"},
        )
        assert revoked.status_code == 200, revoked.text
        response = _post(client, "status", "after-revoke", capability_id="F01")
        assert response.status_code == 403


def test_turn_budget_is_shared_across_facade_operations(tmp_path: Path) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        _grant_cos(client, "finance-budget-grant")
        for _ in range(3):
            assert (
                _post(client, "status", "same-turn", capability_id="F01").status_code
                == 200
            )
        fourth = _post(
            client,
            "query",
            "same-turn",
            book_id="book",
            publication_id="publication",
            report_id="actuals",
        )
        assert fourth.json()["reason"] == "finance_turn_call_budget"


def test_processing_authority_is_rechecked_after_audit_before_delivery(
    tmp_path: Path, monkeypatch
) -> None:
    from adminme.products.openclaw import main

    settings = replace(
        _settings(tmp_path),
        finance_publications_enabled=True,
        finance_model_members=frozenset({PRINCIPAL}),
    )
    app = create_application(settings)
    original = main._record_tool_call

    async def revoke_after_audit(request, session, provenance, **kwargs):
        receipt = await original(request, session, provenance, **kwargs)
        if kwargs.get("operation") == "finance.query":
            runtime = request.app.state.runtime
            runtime.settings = replace(
                runtime.settings, finance_model_members=frozenset()
            )
        return receipt

    with TestClient(app) as client:
        _grant_cos(client, "finance-delivery-grant")
        monkeypatch.setattr(main, "_record_tool_call", revoke_after_audit)
        response = _post(
            client,
            "query",
            "revoked-delivery",
            book_id="book",
            publication_id="publication",
            report_id="actuals",
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "finance_authority_changed"


def test_grant_revocation_during_answer_suppresses_delivery(
    tmp_path: Path, monkeypatch
) -> None:
    from datetime import datetime, timezone
    from adminme.events.envelope import EventEnvelope
    from adminme.products.openclaw import main as native

    settings = replace(
        _settings(tmp_path),
        finance_publications_enabled=True,
        finance_model_members=frozenset({PRINCIPAL}),
    )
    app = create_application(settings)
    with TestClient(app) as client:
        grant = _grant_cos(client, "finance-mid-answer-grant")
        original = native._record_tool_call

        async def revoke_after_read(request, session, provenance, **kwargs):
            await original(request, session, provenance, **kwargs)
            if kwargs.get("operation") != "finance.calculate":
                return
            runtime = app.state.runtime
            now = datetime.now(timezone.utc)
            scope = f"private:{PRINCIPAL}"
            event_id = await runtime.events.write(
                EventEnvelope(
                    event_at_ms=int(now.timestamp() * 1000),
                    tenant_id=session.tenant_id,
                    type="service.delegation.revoked",
                    schema_version=1,
                    occurred_at=now.isoformat(),
                    source_adapter="synthetic_acceptance",
                    source_account_id="synthetic",
                    owner_scope=scope,
                    visibility_scope=scope,
                    sensitivity="privileged",
                    payload={
                        "grant_id": grant,
                        "revoked_by_member_id": PRINCIPAL,
                        "revoked_at": now.isoformat(),
                        "reason": "synthetic revocation",
                    },
                )
            )
            await runtime.wait_for_projection("parties", event_id)

        monkeypatch.setattr(native, "_record_tool_call", revoke_after_read)
        response = _post(
            client,
            "calculate",
            "mid-answer",
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
        assert response.status_code == 403
        assert "values" not in response.text
        assert "120000" not in response.text


@pytest.mark.parametrize(
    "context",
    [
        "unknown_dm",
        "group",
        "shared",
        "thread",
        "background",
        "wrong_agent",
        "wrong_account",
    ],
)
def test_private_finance_never_uses_mapped_sender_as_group_authority(
    tmp_path: Path, context: str
) -> None:
    settings = replace(
        _settings(tmp_path),
        finance_publications_enabled=True,
        finance_model_members=frozenset({PRINCIPAL}),
    )
    app = create_application(settings)
    with TestClient(app) as client:
        _grant_cos(client, "finance-audience-grant")
        provenance = _finance_provenance(client, f"unsafe-{context}")
        if context in {"unknown_dm", "group", "shared"}:
            provenance = _generic_provenance(f"unsafe-{context}")
            provenance["message_id"] = f"finance-run:unsafe-{context}"
            provenance["session_key"] = {
                "unknown_dm": "adminme:tenant:actor-a:dm",
                "group": "agent:fixture:discord:account-a:group:room",
                "shared": "agent:fixture:main",
            }[context]
        elif context == "thread":
            provenance["thread_id"] = "shared-thread"
        elif context == "background":
            provenance["call_kind"] = "standing_order"
        elif context == "wrong_agent":
            provenance["agent_id"] = member_agent_id(OTHER)
        elif context == "wrong_account":
            provenance["account_id"] = "another-account"
        response = client.post(
            BASE + "query",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": provenance,
                "book_id": "private-book-canary",
                "publication_id": "private-publication-canary",
                "report_id": "actuals",
            },
        )
        assert response.status_code in {200, 403}, response.text
        if response.status_code == 200:
            assert response.json()["state"] == "permission_required"
        assert "metrics" not in response.text
        assert "private-book-canary" not in response.text
        assert "private-publication-canary" not in response.text


def test_changed_enabled_owner_mapping_suppresses_old_result(
    tmp_path: Path, monkeypatch
) -> None:
    from adminme.products.openclaw import main

    actor = "+12025550101"  # fixture:tenant_data:ok
    account = "fixture-finance"
    coordinate = f"imessage|{account}|{actor}"
    base = _settings(tmp_path)
    settings = replace(
        base,
        finance_publications_enabled=True,
        finance_model_members=frozenset({PRINCIPAL, OTHER}),
        member_identities={**base.member_identities, "fixture-other.ts.net": OTHER},
        openclaw_actor_identities={
            **base.openclaw_actor_identities,
            coordinate: PRINCIPAL,
        },
    )
    app = create_application(settings)
    original = main._record_tool_call

    async def reassign_after_read(request, session, provenance, **kwargs):
        receipt = await original(request, session, provenance, **kwargs)
        if kwargs.get("operation") == "finance.calculate":
            runtime = request.app.state.runtime
            runtime.settings = replace(
                runtime.settings,
                openclaw_actor_identities={
                    **runtime.settings.openclaw_actor_identities,
                    coordinate: OTHER,
                },
            )
        return receipt

    with TestClient(app) as client:
        _grant_cos(client, "finance-owner-a-grant")
        other_grant = client.post(
            "/api/member/v1/cos/delegations",
            headers={"Tailscale-User-Login": "fixture-other.ts.net"},
            json={
                "request_id": "finance-owner-b-grant",
                "context_scope": "member_private",
                "permissions": ["read_context"],
            },
        )
        assert other_grant.status_code in {200, 201}, other_grant.text
        provenance = _generic_provenance("owner-changed")
        provenance.update(
            actor_id=actor,
            account_id=account,
            channel_id="imessage",
            agent_id=member_agent_id(PRINCIPAL),
            session_key=f"agent:{member_agent_id(PRINCIPAL)}:imessage:{account}:direct:{actor}",
            thread_id=None,
            message_id="finance-run:owner-changed",
        )
        monkeypatch.setattr(main, "_record_tool_call", reassign_after_read)
        response = client.post(
            BASE + "calculate",
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": provenance,
                "calculator_id": "reserve_coverage",
                "version": "1",
                "hypothetical": True,
                "inputs": {
                    "currency": "USD",
                    "liquid_cash_minor": 120000,
                    "monthly_reference_spending_minor": 30000,
                    "chosen_reserve_floor_minor": 60000,
                    "liquidity_confirmed": True,
                    "reference_basis": "chosen_essential_spending",
                },
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "finance_authority_changed"
        assert "120000" not in response.text
        assert "values" not in response.text
