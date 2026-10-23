"""Credential-free production composition proof of the thin private facade."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from adminme.runtime.application import create_application
from tests.acceptance.test_openclaw_native_seam import (
    DEMO_HEADERS,
    OPENCLAW_HEADERS,
    PRINCIPAL,
    _grant_cos,
    _provenance,
    _settings,
)

BASE = "/api/openclaw/v1/tools/finance-"


def _post(client: TestClient, route: str, message: str, **body: object):
    return client.post(
        BASE + route,
        headers=OPENCLAW_HEADERS,
        json={"provenance": _provenance(message), **body},
    )


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
