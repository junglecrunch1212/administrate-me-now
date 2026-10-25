"""Direct-owner metadata extraction; no finance importer or CoS grant fixture."""

from dataclasses import replace
import json
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from adminme.events.envelope import EventEnvelope
from adminme.finance.publication import content_hash
from adminme.runtime.application import create_application
from tests.acceptance.test_openclaw_native_seam import (
    DEMO_HEADERS,
    PRINCIPAL,
    OTHER,
    _settings,
)

settings = _settings


def publication_fixture(tenant, owner_id=PRINCIPAL):
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures/finance/accepted-publication.synthetic.json"
    )
    pub = json.loads(fixture.read_text())
    original_owner = pub["owner_id"]

    def owner(value):
        if isinstance(value, dict):
            return {k: owner(v) for k, v in value.items()}
        if isinstance(value, list):
            return [owner(v) for v in value]
        return owner_id if value == original_owner else value

    pub = owner(pub)
    pub["tenant_id"] = tenant
    pub["content_sha256"] = content_hash(pub)
    return pub


def retained_envelope(pub):
    owner = pub["owner_id"]
    return EventEnvelope(
        event_at_ms=1,
        tenant_id=pub["tenant_id"],
        type="finance.publication_accepted",
        schema_version=1,
        occurred_at="2026-10-01T00:00:00Z",
        source_adapter="synthetic_acceptance",
        source_account_id=owner,
        actor_identity=owner,
        owner_scope=f"private:{owner}",
        visibility_scope=f"private:{owner}",
        sensitivity="privileged",
        payload={"publication": pub},
    )


def seed_retained_publication(client):
    runtime = client.app.state.runtime
    pub = publication_fixture(runtime.settings.instance.tenant_id)
    client.portal.call(runtime.events.write, retained_envelope(pub))
    return pub


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
        assert enabled.json()["cos_read_enabled"] is False
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

    with TestClient(create_application(settings(tmp_path))) as client:
        pub = seed_retained_publication(client)
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
        assert control(client, owner=True).status_code == 200

        async def fail(*args):
            raise RuntimeError("private control diagnostic must not escape")

        monkeypatch.setattr(
            client.app.state.runtime.event_log, "read_finance_metadata_control", fail
        )
        for response in (owner_read(client),):
            assert response.status_code == 503
            assert "private control diagnostic" not in response.text
            assert "verified_empty" not in response.text


@pytest.mark.parametrize("boundary", ["history_wait", "entity_wait", "audit"])
@pytest.mark.parametrize("change", ["role", "mapping", "policy"])
def test_revocation_after_each_disclosure_wait(tmp_path, monkeypatch, boundary, change):
    from adminme.finance import metadata
    from adminme.products.member import finance_metadata as endpoint

    with TestClient(create_application(settings(tmp_path))) as client:
        runtime = client.app.state.runtime
        pub = seed_retained_publication(client)
        assert control(client, owner=True).status_code == 200
        target, name = {
            "history_wait": (runtime.event_log, "read_finance_binding_metadata"),
            "entity_wait": (metadata, "household_entity_metadata"),
            "audit": (endpoint, "audit_metadata_read"),
        }[boundary]
        original = getattr(target, name)

        async def revoke_after_wait(*args, **kwargs):
            result = await original(*args, **kwargs)
            if change == "role":
                runtime.settings = replace(
                    runtime.settings,
                    member_roles={**runtime.settings.member_roles, PRINCIPAL: "child"},
                )
            elif change == "mapping":
                runtime.settings = replace(runtime.settings, member_identities={})
            else:
                await disable_during_audit(runtime)
            return result

        monkeypatch.setattr(target, name, revoke_after_wait)
        response = owner_read(client)
        assert response.status_code in {401, 403}, response.text
        assert pub["book_id"] not in response.text
        assert pub["content_sha256"] not in response.text
        assert "bindings" not in response.text


def test_disclosure_then_disable_preserves_history_and_blocks_future_reads(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_application(config)) as client:
        pub = seed_retained_publication(client)
        assert control(client, owner=True).status_code == 200
        prior = owner_read(client).json()
        assert prior["state"] == "populated"
        assert (
            control(client, revision=1, request_id="disable-after-read").status_code
            == 200
        )
        assert owner_read(client).json()["state"] == "access_unavailable"
    with TestClient(create_application(config)) as client:
        assert owner_read(client).json()["state"] == "access_unavailable"
        enabled = control(
            client, owner=True, revision=2, request_id="reenable-owner-only"
        )
        assert enabled.status_code == 200
        result = owner_read(client).json()
        assert (
            result["bindings"][0]["accounting"]["publication_id"]
            == pub["publication_id"]
        )
        assert result["completeness"] == prior["completeness"]
        assert enabled.json()["cos_read_enabled"] is False
