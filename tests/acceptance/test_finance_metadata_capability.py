"""Metadata-only native capability uses existing authority, never finance enablement."""

from dataclasses import replace
from datetime import datetime, timezone
import asyncio

from fastapi.testclient import TestClient
import pytest

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_disclosure import (
    enabled_settings,
    import_fixture,
)
from tests.acceptance.test_finance_native_facade import _post, _finance_provenance
from tests.acceptance.test_openclaw_native_seam import (
    DEMO_HEADERS,
    OPENCLAW_HEADERS,
    OTHER,
    PRINCIPAL,
)

# Exercises shipping availability; kill-switch tests explicitly disable it.
pytestmark = pytest.mark.usefixtures("unreleased_finance_advisor")


def settings(tmp_path, *, finance=False):
    config = enabled_settings(tmp_path)
    return replace(
        config,
        finance_publications_enabled=finance,
        finance_model_members=frozenset({PRINCIPAL}) if finance else frozenset(),
    )


def control(
    client,
    *,
    owner=False,
    cos=False,
    request_id="metadata-control",
    revision=0,
    headers=None,
):
    return client.put(
        "/api/member/v1/settings/finance-metadata",
        headers=headers or DEMO_HEADERS,
        json={
            "request_id": request_id,
            "expected_revision": revision,
            "owner_read_enabled": owner,
            "cos_read_enabled": cos,
        },
    )


def grant_read_only(client, *, metadata=True):
    response = client.post(
        "/api/member/v1/cos/delegations",
        headers=DEMO_HEADERS,
        json={
            "request_id": "metadata-only-grant",
            "context_scope": "member_private",
            "permissions": ["read_context"],
        },
    )
    assert response.status_code in {200, 201}, response.text
    if metadata:
        response_control = control(client, cos=True)
        assert response_control.status_code == 200, response_control.text
    return response.json()["grant_id"]


async def revoke_metadata_grant(runtime, grant):
    """This separate metadata capability retains general private-read authority."""
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


async def disable_during_audit(runtime):
    # Model a concurrently committed authority revocation at the final boundary.
    current = await runtime.event_log.read_finance_metadata_control(
        runtime.settings.instance.tenant_id, PRINCIPAL
    )
    await runtime.events.write(
        EventEnvelope(
            event_at_ms=1,
            tenant_id=runtime.settings.instance.tenant_id,
            type="settings.finance_metadata_changed",
            schema_version=1,
            occurred_at="2026-10-01T00:00:00Z",
            source_adapter="synthetic_acceptance",
            source_account_id=PRINCIPAL,
            actor_identity=PRINCIPAL,
            owner_scope=f"private:{PRINCIPAL}",
            visibility_scope=f"private:{PRINCIPAL}",
            sensitivity="privileged",
            payload={
                "owner_id": PRINCIPAL,
                "changed_by_member_id": PRINCIPAL,
                "revision": current["payload"]["revision"] + 1,
                "prior_event_id": current["event_id"],
                "owner_read_enabled": False,
                "cos_read_enabled": False,
                "provider_effects": False,
            },
        )
    )


def read(client, message="metadata-only", **kwargs):
    return _post(client, "bindings", message, **kwargs)


def test_metadata_alone_empty_audited_without_financial_query_or_model(tmp_path):
    with TestClient(create_application(settings(tmp_path))) as client:
        grant_read_only(client)
        result = read(client)
        assert result.status_code == 200, result.text
        value = result.json()
        assert value["state"] == "verified_empty"
        assert value["completeness"]["complete"] is True
        assert value["identity"]["owner_id"] == PRINCIPAL
        assert value["publisher_policy"]["state"] == "external_authority_required"
        assert value["book_registration"]["local_registry_supported"] is False
        assert value["household_entity"]["state"] == "exact_existing_id_required"
        assert value["provider_effect_count"] == 0
        assert len(result.content) <= 8300
        query = _post(
            client,
            "query",
            "disabled-query",
            book_id="unknown-book",
            publication_id="unknown-publication",
            report_id="actuals",
        )
        assert query.json()["state"] == "permission_required"
        runtime = client.app.state.runtime
        assert not getattr(runtime, "_finance_disclosure_sessions", {})

        async def audits():
            return [
                e
                async for e in runtime.event_log.read_since(
                    types=["openclaw.tool_call.recorded"]
                )
            ]

        records = client.portal.call(audits)
        audit = next(
            e
            for e in records
            if e["payload"]["operation"] == "finance.bindings.inspect"
        )
        assert audit["payload"]["permissions"] == ["read_context"]
        assert audit["visibility_scope"] == f"private:{PRINCIPAL}"


def test_separate_gate_defaults_closed_even_when_finance_enabled(tmp_path, monkeypatch):
    config = settings(tmp_path, finance=True)
    with TestClient(create_application(config)) as client:
        grant_read_only(client, metadata=False)

        async def forbidden(*args):
            raise AssertionError("disabled metadata read")

        monkeypatch.setattr(
            client.app.state.runtime.event_log,
            "read_finance_binding_metadata",
            forbidden,
        )
        response = read(client)
        assert response.json()["state"] == "access_unavailable"
        assert "identity" not in response.json()


def test_missing_existing_auth_or_private_read_grant_denied(tmp_path):
    with TestClient(create_application(settings(tmp_path))) as client:
        assert (
            client.post(
                "/api/openclaw/v1/tools/finance-bindings",
                json={
                    "provenance": _finance_provenance(client, "missing-auth"),
                },
            ).status_code
            == 401
        )
        assert read(client).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"thread_id": "other-thread"},
        {"message_id": "unbound-message"},
        {"agent_id": "other-agent"},
        {"session_key": "shared-session"},
        {"call_kind": "standing_order"},
    ],
)
def test_nonprivate_or_conflicting_context_never_reads(tmp_path, monkeypatch, change):
    with TestClient(create_application(settings(tmp_path))) as client:
        grant_read_only(client)

        async def forbidden(*args):
            raise AssertionError("audience mismatch reached history")

        monkeypatch.setattr(
            client.app.state.runtime.event_log,
            "read_finance_binding_metadata",
            forbidden,
        )
        provenance = {**_finance_provenance(client, "audience-denied"), **change}
        response = client.post(
            "/api/openclaw/v1/tools/finance-bindings",
            headers=OPENCLAW_HEADERS,
            json={"provenance": provenance},
        )
        assert (
            response.status_code == 403
            or response.json()["state"] == "access_unavailable"
        )
        assert "owner_id" not in response.text


@pytest.mark.parametrize("change", ["grant", "gate", "role"])
def test_revocation_after_audit_prevents_disclosure(tmp_path, monkeypatch, change):
    from adminme.products.openclaw import main

    with TestClient(create_application(settings(tmp_path, finance=True))) as client:
        grant = grant_read_only(client)
        pub, _ = import_fixture(client)
        original = main._record_tool_call
        runtime = client.app.state.runtime

        async def revoke_after_audit(request, session, provenance, **kwargs):
            result = await original(request, session, provenance, **kwargs)
            if change == "grant":
                await revoke_metadata_grant(runtime, grant)
            elif change == "gate":
                await disable_during_audit(runtime)
            else:
                runtime.settings = replace(
                    runtime.settings,
                    member_roles={**runtime.settings.member_roles, PRINCIPAL: "child"},
                )
            return result

        monkeypatch.setattr(main, "_record_tool_call", revoke_after_audit)
        response = read(client)
        assert response.status_code == 403, response.text
        assert pub["book_id"] not in response.text
        assert "bindings" not in response.text


def test_retained_history_survives_restart_with_full_finance_disabled(tmp_path):
    config = settings(tmp_path, finance=True)
    with TestClient(create_application(config)) as client:
        grant_read_only(client)
        pub, _ = import_fixture(client)
        first = read(client).json()
    config = replace(
        config, finance_publications_enabled=False, finance_model_members=frozenset()
    )
    with TestClient(create_application(config)) as client:
        response = read(client, "restart")
        assert response.status_code == 200, response.text
        second = response.json()
        assert second["bindings"] == first["bindings"]
        assert second["completeness"] == first["completeness"]
        assert (
            second["bindings"][0]["accounting"]["publication_id"]
            == pub["publication_id"]
        )
        for field in ("metrics", "value_minor", "source_url", "description"):
            assert field not in response.text


def test_single_inflight_limit_returns_audited_busy_without_history_read(
    tmp_path, monkeypatch
):
    with TestClient(create_application(settings(tmp_path))) as client:
        grant_read_only(client)
        runtime = client.app.state.runtime

        async def hold():
            lock = runtime.settings_command_locks.setdefault(
                "finance-metadata:read", asyncio.Lock()
            )
            await lock.acquire()
            return lock

        async def forbidden(*args):
            raise AssertionError("busy metadata read")

        lock = client.portal.call(hold)
        monkeypatch.setattr(
            runtime.event_log, "read_finance_binding_metadata", forbidden
        )
        try:
            response = read(client)
        finally:
            client.portal.call(lock.release)
        assert response.json()["reason"] == "metadata_read_busy"
        assert response.json()["bindings"] == []


def test_exact_existing_household_party_lookup_is_scoped_and_nonselecting(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_application(config)) as client:
        grant_read_only(client)
        runtime = client.app.state.runtime

        async def party(party_id, owner, kind="household"):
            event_id = await runtime.events.write(
                EventEnvelope(
                    event_at_ms=1,
                    tenant_id=config.instance.tenant_id,
                    type="party.created",
                    schema_version=1,
                    occurred_at="2026-10-01T00:00:00Z",
                    source_adapter="synthetic_acceptance",
                    source_account_id="synthetic",
                    owner_scope=f"private:{owner}",
                    visibility_scope=f"private:{owner}",
                    sensitivity="normal",
                    actor_identity=owner,
                    payload={
                        "party_id": party_id,
                        "kind": kind,
                        "display_name": "Synthetic private label",
                        "sort_name": "Synthetic",
                    },
                )
            )
            await runtime.wait_for_projection("parties", event_id)

        client.portal.call(party, "household-visible", PRINCIPAL)
        client.portal.call(party, "household-other", OTHER)
        client.portal.call(party, "person-visible", PRINCIPAL, "person")
        for party_id in ("household-other", "household-missing"):
            response = read(client, party_id, household_party_id=party_id)
            assert (
                response.json()["household_entity"]["state"] == "not_visible_or_missing"
            )
            assert party_id not in response.text
        response = read(client, "visible", household_party_id="household-visible")
        assert (
            response.json()["household_entity"]["state"] == "visible_household_record"
        )
        assert response.json()["household_entity"]["binding_selected"] is False
        assert "Synthetic private label" not in response.text
        assert (
            read(client, "wrong-kind", household_party_id="person-visible").json()[
                "household_entity"
            ]["state"]
            == "not_eligible_household"
        )


def owner_read(client, **selection):
    return client.post(
        "/api/member/v1/finance/bindings", headers=DEMO_HEADERS, json=selection
    )


def test_direct_owner_enable_read_restart_disable_and_audit_without_cos_grant(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_application(config)) as client:
        assert owner_read(client).json()["state"] == "access_unavailable"
        assert (
            client.get(
                "/api/member/v1/settings/finance-metadata", headers=DEMO_HEADERS
            ).json()["revision"]
            == 0
        )
        enabled = control(client, owner=True)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["revision"] == 1
        response = owner_read(client)
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["state"] == "verified_empty"
        assert value["completeness"]["complete"] is True
        assert read(client).status_code == 403  # Owner enablement never delegates.
        assert control(client, owner=True).json()["idempotent"] is True
        assert (
            control(
                client, owner=True, revision=0, request_id="stale-command"
            ).status_code
            == 409
        )
    with TestClient(create_application(config)) as client:
        assert owner_read(client).json()["completeness"] == value["completeness"]
        disabled = control(client, revision=1, request_id="disable-metadata")
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["revision"] == 2
        assert owner_read(client).json()["state"] == "access_unavailable"
        # Retrying an old successful enable does not resurrect revoked access.
        old = control(client, owner=True)
        assert old.json()["idempotent"] is True
        assert old.json()["owner_read_enabled"] is False

        async def records():
            return [
                e
                async for e in client.app.state.runtime.event_log.read_since(
                    types=[
                        "settings.finance_metadata_changed",
                        "finance.metadata_inspected",
                    ]
                )
            ]

        events = client.portal.call(records)
        assert (
            len([e for e in events if e["type"] == "settings.finance_metadata_changed"])
            == 2
        )
        assert all(e["actor_identity"] == PRINCIPAL for e in events)
        assert all(e["visibility_scope"] == f"private:{PRINCIPAL}" for e in events)
        assert any(e["payload"].get("complete") is True for e in events)
    with TestClient(create_application(config)) as client:
        assert owner_read(client).json()["state"] == "access_unavailable"


@pytest.mark.parametrize("change", ["role", "mapping", "policy"])
def test_direct_owner_final_revocation_after_durable_audit(
    tmp_path, monkeypatch, change
):
    from adminme.products.member import finance_metadata as endpoint

    with TestClient(create_application(settings(tmp_path, finance=True))) as client:
        pub, _ = import_fixture(client)
        assert control(client, owner=True).status_code == 200
        original = endpoint.audit_metadata_read

        async def change_after_audit(runtime, session, policy, result):
            event_id = await original(runtime, session, policy, result)
            if change == "role":
                runtime.settings = replace(
                    runtime.settings,
                    member_roles={**runtime.settings.member_roles, PRINCIPAL: "child"},
                )
            elif change == "mapping":
                runtime.settings = replace(runtime.settings, member_identities={})
            else:
                await disable_during_audit(runtime)
            return event_id

        monkeypatch.setattr(endpoint, "audit_metadata_read", change_after_audit)
        response = owner_read(client)
        assert response.status_code in {401, 403}, response.text
        assert pub["book_id"] not in response.text
        assert "bindings" not in response.text


def test_direct_owner_authentication_scope_and_body_do_not_select_another_owner(
    tmp_path,
):
    config = settings(tmp_path)
    config = replace(
        config,
        member_identities={**config.member_identities, "fixture-other.ts.net": OTHER},
    )
    with TestClient(create_application(config)) as client:
        assert control(client, owner=True).status_code == 200
        assert (
            client.post("/api/member/v1/finance/bindings", json={}).status_code == 401
        )
        viewed = {**DEMO_HEADERS, "X-AdminMe-View-Member": OTHER}
        assert (
            client.post(
                "/api/member/v1/finance/bindings", headers=viewed, json={}
            ).status_code
            == 403
        )
        assert control(client, owner=True, headers=viewed).status_code == 403
        assert owner_read(client, owner_id=OTHER).status_code == 422
        other = client.post(
            "/api/member/v1/finance/bindings",
            headers={"Tailscale-User-Login": "fixture-other.ts.net"},
            json={},
        )
        assert other.json()["state"] == "access_unavailable"
        assert PRINCIPAL not in other.text


def test_control_failed_persistence_does_not_enable_metadata(tmp_path, monkeypatch):
    from adminme.products.member import finance_metadata as endpoint

    with TestClient(create_application(settings(tmp_path))) as client:

        async def fail(*args, **kwargs):
            raise RuntimeError("synthetic durable write failure")

        with monkeypatch.context() as patch:
            patch.setattr(endpoint, "_write_settings_receipt", fail)
            with pytest.raises(RuntimeError, match="synthetic durable write failure"):
                control(client, owner=True)
        state = client.get(
            "/api/member/v1/settings/finance-metadata", headers=DEMO_HEADERS
        ).json()
        assert state["revision"] == 0
        assert state["owner_read_enabled"] is False
        assert owner_read(client).json()["state"] == "access_unavailable"


@pytest.mark.parametrize("value", ["true", 1, None])
def test_control_rejects_implicit_boolean_enablement(tmp_path, value):
    with TestClient(create_application(settings(tmp_path))) as client:
        assert control(client, owner=value).status_code == 422


def test_control_unavailable_is_never_verified_empty(tmp_path, monkeypatch):
    with TestClient(create_application(settings(tmp_path))) as client:
        grant_read_only(client)

        async def fail(*args):
            raise RuntimeError("private control diagnostic must not escape")

        monkeypatch.setattr(
            client.app.state.runtime.event_log, "read_finance_metadata_control", fail
        )
        for response in (owner_read(client), read(client)):
            assert response.status_code == 503
            assert "private control diagnostic" not in response.text
            assert "verified_empty" not in response.text
