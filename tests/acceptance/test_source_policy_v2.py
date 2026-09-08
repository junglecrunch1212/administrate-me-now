"""Production-composition proof for per-principal exact source policy V2."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.events.schemas.ingest import MessagingReceivedV5
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"p" * 32
A = "member-policy-a"
B = "member-policy-b"
A_HEADERS = {"Tailscale-User-Login": "policy-a.ts.net"}
B_HEADERS = {"Tailscale-User-Login": "policy-b.ts.net"}


def _settings(instance_dir: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"policy-a.ts.net": A, "policy-b.ts.net": B},
        member_roles={A: "principal", B: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "Fixture household",
            "members": [
                {"member_id": A, "label": "Adult A", "role": "principal"},
                {"member_id": B, "label": "Adult B", "role": "principal"},
            ],
            "memberships": [],
            "relationships": [],
            "parties": [],
        },
        adapter_config={
            "adapter_supervisor_port": 0,
            "adapters": [
                {
                    "id": "gmail-policy-a",
                    "kind": "messaging-gmail-api",
                    "enabled": False,
                    "config": {
                        "member_id": A,
                        "provider_account_id": "provider-account-a",
                        "workspace": "personal",
                        "account_label": "Personal mail A",
                    },
                },
                {
                    "id": "gmail-policy-b",
                    "kind": "messaging-gmail-api",
                    "enabled": False,
                    "config": {
                        "member_id": B,
                        "provider_account_id": "provider-account-b",
                        "workspace": "work",
                        "account_label": "Work mail B",
                    },
                },
                {
                    "id": "gmail-policy-a-work",
                    "kind": "messaging-gmail-api",
                    "enabled": False,
                    "config": {
                        "member_id": A,
                        "provider_account_id": "provider-account-a-work",
                        "workspace": "work",
                        "account_label": "Work mail A",
                    },
                },
            ],
        },
        deterministic_openclaw_fixture=True,
        fixture_member_id=A,
    )


def _v2_control(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/member/v1/surfaces/settings", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["control"]["source_policy_v2"]


async def _append_message(
    runtime: Any,
    member_id: str,
    adapter_id: str,
    account_id: str,
    native_id: str,
    provider: str = "gmail",
    channel: str = "gmail",
) -> str:
    payload = MessagingReceivedV5(
        source_channel=channel,
        from_identifier="sender-fixture",
        to_identifier="owner-fixture",
        thread_id=f"thread-{native_id}",
        subject=None,
        body_text=None,
        body_html=None,
        received_at="2026-08-31T12:00:00Z",
        attachments=[],
        native_message_id=native_id,
        member_id=member_id,
        service_principal_id=None,
        provider=provider,
        provider_account_id=account_id,
        workspace="personal" if member_id == A else "work",
        routing_scope="member_private",
        routing_target_id=member_id,
        provider_labels=["IMPORTANT"],
        important=True,
    )
    return await runtime.events.write(
        EventEnvelope(
            event_at_ms=1_788_200_000_000,
            tenant_id=runtime.settings.instance.tenant_id,
            type="messaging.received",
            schema_version=5,
            occurred_at="2026-08-31T12:00:00Z",
            source_adapter=adapter_id,
            source_account_id=account_id,
            owner_scope=f"private:{member_id}",
            visibility_scope=f"private:{member_id}",
            sensitivity="privileged",
            idempotency_key=f"policy-source:{native_id}",
            payload=payload.model_dump(mode="json"),
        )
    )


async def _append_service_message(runtime: Any) -> str:
    payload = MessagingReceivedV5(
        source_channel="gmail",
        from_identifier="external-fixture",
        to_identifier="service-fixture",
        thread_id="thread-service-policy",
        subject=None,
        body_text=None,
        body_html=None,
        received_at="2026-08-31T12:00:00Z",
        attachments=[],
        native_message_id="message-service-policy",
        member_id=None,
        service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        provider="gmail",
        provider_account_id="service-provider-account",
        workspace="service",
        routing_scope="household_shared",
        routing_target_id=None,
    )
    return await runtime.events.write(
        EventEnvelope(
            event_at_ms=1_788_200_000_100,
            tenant_id=runtime.settings.instance.tenant_id,
            type="messaging.received",
            schema_version=5,
            occurred_at="2026-08-31T12:00:00Z",
            source_adapter="service-mail-instance",
            source_account_id="service-provider-account",
            owner_scope="shared:household",
            visibility_scope="shared:household",
            sensitivity="privileged",
            idempotency_key="policy-source:service-message",
            payload=payload.model_dump(mode="json"),
        )
    )


async def _wait_for_shadow(runtime: Any, source_event_id: str) -> dict[str, Any]:
    for _ in range(300):
        rows = await runtime.event_log.get_by_causation(source_event_id)
        for row in rows:
            if row["type"] == "source.policy_v2.shadow_evaluated":
                return row
        await asyncio.sleep(0.01)
    raise AssertionError("source policy shadow receipt did not arrive")


async def _provider_effect_count(runtime: Any) -> int:
    count = 0
    async for event in runtime.event_log.read_since(
        types=["external_sync.write_completed", "external_sync.write_failed"]
    ):
        if (event.get("payload") or {}).get("provider_called") is True:
            count += 1
    return count


def _custom_policy(triage_mode: str, context_mode: str) -> dict[str, Any]:
    return {
        "preset": "custom",
        "acquisition_state": "active",
        "triage_mode": triage_mode,
        "alert_mode": "none",
        "context_mode": context_mode,
        "candidate_kinds": ["task", "commitment", "coordination"],
        "scope_widening": False,
        "credential_creation": False,
        "provider_write_authority": False,
        "admission_active": False,
    }


def test_exact_source_policy_shadow_privacy_preview_replay_and_zero_effects(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    app = create_application(settings)
    a_coordinate: dict[str, Any]
    a_policy_key: str

    with TestClient(app) as client:
        runtime = app.state.runtime
        a_control = _v2_control(client, A_HEADERS)
        b_control = _v2_control(client, B_HEADERS)
        a_provider = next(
            source
            for source in a_control["sources"]
            if source["adapter_instance_id"] == "gmail-policy-a"
        )
        b_provider = next(
            source
            for source in b_control["sources"]
            if source["adapter_instance_id"] == "gmail-policy-b"
        )
        a_work_provider = next(
            source
            for source in a_control["sources"]
            if source["adapter_instance_id"] == "gmail-policy-a-work"
        )
        assert a_provider["coordinate"]["provider_account_id"] == "provider-account-a"
        assert b_provider["coordinate"]["provider_account_id"] == "provider-account-b"
        assert a_work_provider["coordinate"]["provider_account_id"] == (
            "provider-account-a-work"
        )
        assert a_provider["policy_key"] != a_work_provider["policy_key"]
        assert a_provider["policy_key"] != b_provider["policy_key"]
        assert all(
            source["coordinate"] is None
            or source["coordinate"]["owner_principal_id"] == A
            for source in a_control["sources"]
        )
        assert all(
            source["coordinate"] is None
            or source["coordinate"]["owner_principal_id"] == B
            for source in b_control["sources"]
        )

        view_as = _v2_control(
            client,
            {
                **A_HEADERS,
                "X-AdminMe-View-Member": B,
            },
        )
        assert view_as["owner_visible"] is False
        assert view_as["sources"] == []

        a_coordinate = a_provider["coordinate"]
        a_policy_key = a_provider["policy_key"]
        set_a = client.post(
            f"/api/member/v1/settings/source-policies-v2/{a_policy_key}",
            headers=A_HEADERS,
            json={
                "expected_revision": 0,
                "request_id": "policy-a-focused",
                "coordinate": a_coordinate,
                "preset": "focused",
            },
        )
        assert set_a.status_code == 202, set_a.text
        assert set_a.json()["revision"] == 1
        assert set_a.json()["live_admission_changed"] is False

        set_a_work = client.post(
            "/api/member/v1/settings/source-policies-v2/"
            + a_work_provider["policy_key"],
            headers=A_HEADERS,
            json={
                "expected_revision": 1,
                "request_id": "policy-a-work-review-all",
                "coordinate": a_work_provider["coordinate"],
                "preset": "review_everything",
            },
        )
        assert set_a_work.status_code == 202, set_a_work.text
        assert set_a_work.json()["revision"] == 2
        assert set_a_work.json()["policy"]["preset"] == "review_everything"
        set_a_retry = client.post(
            f"/api/member/v1/settings/source-policies-v2/{a_policy_key}",
            headers=A_HEADERS,
            json={
                "expected_revision": 0,
                "request_id": "policy-a-focused",
                "coordinate": a_coordinate,
                "preset": "focused",
            },
        )
        assert set_a_retry.status_code == 202, set_a_retry.text
        assert set_a_retry.json()["idempotent"] is True
        assert set_a_retry.json()["event_id"] == set_a.json()["event_id"]
        assert set_a_retry.json()["revision"] == 2

        set_b = client.post(
            f"/api/member/v1/settings/source-policies-v2/{b_provider['policy_key']}",
            headers=B_HEADERS,
            json={
                "expected_revision": 0,
                "request_id": "policy-b-review-all",
                "coordinate": b_provider["coordinate"],
                "preset": "review_everything",
            },
        )
        assert set_b.status_code == 202, set_b.text
        assert set_b.json()["policy"]["preset"] == "review_everything"

        cross_owner = client.post(
            f"/api/member/v1/settings/source-policies-v2/{b_provider['policy_key']}",
            headers=A_HEADERS,
            json={
                "expected_revision": 1,
                "request_id": "policy-cross-owner",
                "coordinate": b_provider["coordinate"],
                "preset": "focused",
            },
        )
        assert cross_owner.status_code == 403

        before_preview = runtime.settings_control.source_policy_v2_revision(A)
        preview = client.post(
            "/api/member/v1/settings/source-policies-v2/preview",
            headers=A_HEADERS,
            json={"coordinate": a_coordinate, "preset": "focused"},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["mutation_performed"] is False
        assert preview.json()["provider_effects"] is False
        assert runtime.settings_control.source_policy_v2_revision(A) == before_preview

        source_id = client.portal.call(
            _append_message,
            runtime,
            A,
            "gmail-policy-a",
            "provider-account-a",
            "message-policy-focused",
        )
        shadow = client.portal.call(_wait_for_shadow, runtime, source_id)
        assert shadow["payload"]["policy_origin"] == "exact_override"
        assert shadow["payload"]["shadow_route"]["triage"] == "none"
        assert shadow["payload"]["shadow_route"]["context"] == "typed_facts"
        assert shadow["payload"]["signals"]["provider_labels_non_authoritative"] == [
            "IMPORTANT"
        ]
        assert shadow["payload"]["signals"]["security_alert"] is False
        assert shadow["payload"]["provider_effects"] is False
        assert (
            runtime.settings_control.effective_candidate_policy(A, "adapter:gmail")[
                "mode"
            ]
            == "review"
        )
        cross_owner_read = client.get(
            f"/api/member/v1/settings/source-policies-v2/why/{source_id}",
            headers=B_HEADERS,
        )
        assert cross_owner_read.status_code == 404

        why = client.get(
            f"/api/member/v1/settings/source-policies-v2/why/{source_id}",
            headers=A_HEADERS,
        )
        assert why.status_code == 200, why.text
        assert why.json()["provider_labels_authoritative"] is False
        assert why.json()["provider_effects"] is False

        context_only = client.post(
            f"/api/member/v1/settings/source-policies-v2/{a_policy_key}",
            headers=A_HEADERS,
            json={
                "expected_revision": 2,
                "request_id": "policy-a-context-only",
                "coordinate": a_coordinate,
                "preset": "custom",
                "policy": _custom_policy("context_only", "typed_facts"),
            },
        )
        assert context_only.status_code == 202, context_only.text
        context_source = client.portal.call(
            _append_message,
            runtime,
            A,
            "gmail-policy-a",
            "provider-account-a",
            "message-policy-context",
        )
        context_shadow = client.portal.call(_wait_for_shadow, runtime, context_source)[
            "payload"
        ]
        assert context_shadow["shadow_route"]["triage"] == "none"
        assert context_shadow["shadow_route"]["context"] == "typed_facts"

        ignored = client.post(
            f"/api/member/v1/settings/source-policies-v2/{a_policy_key}",
            headers=A_HEADERS,
            json={
                "expected_revision": 3,
                "request_id": "policy-a-ignore",
                "coordinate": a_coordinate,
                "preset": "custom",
                "policy": _custom_policy("ignore", "typed_facts_safe_snippet"),
            },
        )
        assert ignored.status_code == 202, ignored.text
        ignored_source = client.portal.call(
            _append_message,
            runtime,
            A,
            "gmail-policy-a",
            "provider-account-a",
            "message-policy-ignore",
        )
        ignored_shadow = client.portal.call(_wait_for_shadow, runtime, ignored_source)[
            "payload"
        ]
        assert ignored_shadow["shadow_route"]["triage"] == "none"
        assert ignored_shadow["shadow_route"]["context"] == "none"
        assert client.portal.call(runtime.event_log.get, ignored_source) is not None

        direct_source = next(
            source
            for source in _v2_control(client, A_HEADERS)["sources"]
            if source["source_kind"] == "surface:triage"
        )
        suppress_direct = client.post(
            "/api/member/v1/settings/source-policies-v2/" + direct_source["policy_key"],
            headers=A_HEADERS,
            json={
                "expected_revision": 4,
                "request_id": "policy-a-direct-ignore",
                "coordinate": direct_source["coordinate"],
                "preset": "custom",
                "policy": {
                    **_custom_policy("ignore", "none"),
                    "acquisition_state": "paused",
                },
            },
        )
        assert suppress_direct.status_code == 202, suppress_direct.text
        direct = client.post(
            "/api/member/v1/triage/intents",
            headers=A_HEADERS,
            json={
                "text": "Review this explicit fixture request",
                "source_surface": "triage",
                "kind_hint": "task",
                "client_request_id": "policy-direct-intent",
            },
        )
        assert direct.status_code == 202, direct.text
        direct_shadow = client.portal.call(
            _wait_for_shadow, runtime, direct.json()["intent_event_id"]
        )["payload"]
        assert direct_shadow["policy_origin"] == "exact_override"
        assert direct_shadow["shadow_route"]["triage"] == ("direct_work_order_proposal")
        assert (
            "authenticated_member_intent_cannot_be_suppressed"
            in direct_shadow["shadow_route"]["reason_codes"]
        )

        service_source = client.portal.call(_append_service_message, runtime)
        service_shadow = client.portal.call(_wait_for_shadow, runtime, service_source)[
            "payload"
        ]
        assert service_shadow["coordinate"]["owner_kind"] == "service_principal"
        assert service_shadow["coordinate"]["owner_principal_id"] == (
            COS_SERVICE_PRINCIPAL_ID
        )
        assert service_shadow["coordinate"]["context_member_id"] is None
        assert service_shadow["current_route"]["triage"] == "none"
        assert service_shadow["policy_version"] == "source-policy-v2"

        assert client.portal.call(_provider_effect_count, runtime) == 0

    # The journal is a rebuildable cache.  Removing it in this disposable
    # instance proves restart/restore reconstructs V2 from encrypted events.
    (instance / "config" / "settings-control.json").unlink()
    restored_app = create_application(settings)
    with TestClient(restored_app) as client:
        restored = _v2_control(client, A_HEADERS)
        restored_provider = next(
            source
            for source in restored["sources"]
            if source["policy_key"] == a_policy_key
        )
        assert restored["revision"] == 5
        assert restored_provider["policy"]["triage_mode"] == "ignore"
        assert restored_provider["policy"]["context_mode"] == (
            "typed_facts_safe_snippet"
        )
        restored_work = next(
            source
            for source in restored["sources"]
            if source["policy_key"] == a_work_provider["policy_key"]
        )
        assert restored_work["policy"]["preset"] == "review_everything"
        assert (
            client.portal.call(_provider_effect_count, restored_app.state.runtime) == 0
        )


def test_retained_imessage_future_adapter_inventory_and_independent_attention(
    tmp_path: Path,
) -> None:
    """Actual source→proposal→reconciliation→Settings, with no connected provider."""
    instance = tmp_path / "retained-source-instance"
    instance.mkdir()
    settings = replace(
        _settings(instance),
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
    )
    saved_keys: list[str] = []
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime
        initial = _v2_control(client, A_HEADERS)
        assert initial["admission_active"] is True
        future = next(
            row
            for row in initial["sources"]
            if row["source_kind"] == "adapter:microsoft_graph_mail"
        )
        assert future["coordinate"] is None
        assert future["connection_state"] == "not_connected"
        assert future["configurable"] is False

        for index, (provider, channel) in enumerate(
            (("bluebubbles", "imessage"), ("fixture", "future_reviewed_channel"))
        ):
            adapter = f"retained-source-adapter-{index}"
            account = f"retained-source-account-{index}"
            source_event_id = client.portal.call(
                _append_message,
                runtime,
                A,
                adapter,
                account,
                f"retained-source-message-{index}",
                provider,
                channel,
            )
            shadow = client.portal.call(_wait_for_shadow, runtime, source_event_id)
            client.portal.call(
                runtime.wait_for_projection, "context_fabric", shadow["event_id"]
            )
            observed = next(
                row
                for row in _v2_control(client, A_HEADERS)["sources"]
                if (row.get("coordinate") or {}).get("provider_account_id") == account
                and row.get("retained_source_count") == 1
            )
            assert observed["retained_candidate_count"] == 0
            assert observed["connection_state"] == "disconnected"
            proposal_id = f"retained-source-proposal-{index}"
            proposal_event_id = client.portal.call(
                runtime.events.write,
                EventEnvelope(
                    event_at_ms=1_788_200_100_000 + index,
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="triage.item.proposed",
                    schema_version=1,
                    occurred_at="2026-08-31T12:01:00Z",
                    source_adapter="deterministic-candidate-fixture",
                    source_account_id="pipeline",
                    owner_scope=f"private:{A}",
                    visibility_scope=f"private:{A}",
                    sensitivity="normal",
                    idempotency_key=proposal_id,
                    payload={
                        "proposal_id": proposal_id,
                        "target_kind": "task",
                        "owner_member_id": A,
                        "title": "Review fixture action",
                        "candidate": {},
                        "route": {"member_id": A},
                        "source_event_id": source_event_id,
                        "confidence": 0.5,
                        "reasons": ["fixture_action"],
                    },
                ),
            )
            client.portal.call(
                runtime.wait_for_subscriber,
                "triage-reconciliation:admission",
                proposal_event_id,
            )
            children = client.portal.call(
                runtime.event_log.get_by_causation, proposal_event_id
            )
            decision = next(
                row
                for row in children
                if row["type"] == "triage.reconciliation.decided"
            )
            client.portal.call(
                runtime.wait_for_projection, "context_fabric", decision["event_id"]
            )
            client.portal.call(
                runtime.wait_for_projection, "triage", decision["event_id"]
            )
            inventory = _v2_control(client, A_HEADERS)
            retained = next(
                row
                for row in inventory["sources"]
                if row.get("retained_candidate_count") == 1
                and (row.get("coordinate") or {}).get("provider_account_id") == account
            )
            assert retained["connection_state"] == "disconnected"
            assert retained["configurable"] is True
            assert retained["acquisition"]["provider_capability"] == "unavailable"
            if channel == "imessage":
                assert retained["coordinate"]["native_container_kind"] == "conversation"
            saved_keys.append(retained["policy_key"])
            custom = {
                **retained["policy"],
                "preset": "custom",
                "context_mode": "none",
                "alert_mode": "security_only",
                "attention_mode": "possibilities",
                "candidate_kinds": ["commitment"],
                "purpose_routes": [
                    {
                        "purpose": "survey",
                        "route": "reference",
                        "preserve_independent_obligations": True,
                    }
                ],
            }
            kind_preview = client.post(
                "/api/member/v1/settings/source-policies-v2/preview",
                headers=A_HEADERS,
                json={
                    "coordinate": retained["coordinate"],
                    "preset": "custom",
                    "policy": custom,
                },
            )
            assert kind_preview.status_code == 200, kind_preview.text
            [kind_example] = kind_preview.json()["examples"]
            assert kind_example["proposal_id"] == proposal_id
            assert kind_example["previous_route"]["triage"] == "review"
            assert kind_example["proposed_route"]["triage"] == "none"
            assert "candidate_kind_not_enabled" in kind_example["reason_codes"]
            assert kind_preview.json()["mutation_performed"] is False
            save = client.post(
                f"/api/member/v1/settings/source-policies-v2/{retained['policy_key']}",
                headers=A_HEADERS,
                json={
                    "coordinate": retained["coordinate"],
                    "expected_revision": index * 2,
                    "request_id": f"retained-custom-{index}",
                    "preset": "custom",
                    "policy": custom,
                },
            )
            assert save.status_code == 202, save.text
            assert save.json()["live_admission_changed"] is True
            client.portal.call(
                runtime.wait_for_subscriber,
                "triage-reconciliation:admission",
                save.json()["event_id"],
            )
            revised = client.portal.call(
                runtime.event_log.get_by_causation, save.json()["event_id"]
            )
            kind_decision = next(
                row
                for row in revised
                if row["type"] == "triage.reconciliation.decided"
                and row["payload"]["proposal_id"] == proposal_id
            )
            assert kind_decision["payload"]["admission_active"] is True
            assert kind_decision["payload"]["decision"] == "source_policy_suppression"
            assert (
                "candidate_kind_not_enabled" in kind_decision["payload"]["reason_codes"]
            )
            focused = client.post(
                f"/api/member/v1/settings/source-policies-v2/{retained['policy_key']}",
                headers=A_HEADERS,
                json={
                    "coordinate": retained["coordinate"],
                    "expected_revision": index * 2 + 1,
                    "request_id": f"retained-focused-{index}",
                    "preset": "focused",
                },
            )
            assert focused.status_code == 202, focused.text
            assert focused.json()["policy"]["context_mode"] == "none"
            assert focused.json()["policy"]["alert_mode"] == "security_only"
            assert focused.json()["policy"]["candidate_kinds"] == ["commitment"]
            assert (
                focused.json()["policy"]["purpose_routes"] == custom["purpose_routes"]
            )
            preview = client.post(
                "/api/member/v1/settings/source-policies-v2/preview",
                headers=A_HEADERS,
                json={"coordinate": retained["coordinate"], "preset": "balanced"},
            )
            assert preview.status_code == 200, preview.text
            assert preview.json()["mutation_performed"] is False
            assert preview.json()["estimated_recent_effect"]["source_facts"] == 1
            assert preview.json()["examples"][0]["source_event_id"] == source_event_id
            assert preview.json()["examples"][0]["previous_route"]
            assert preview.json()["examples"][0]["proposed_route"]
            assert all(
                row.get("policy_key") != retained["policy_key"]
                for row in _v2_control(client, B_HEADERS)["sources"]
            )
        assert client.portal.call(_provider_effect_count, runtime) == 0

    (instance / "config" / "settings-control.json").unlink()
    with TestClient(create_application(settings)) as client:
        inventory = _v2_control(client, A_HEADERS)
        assert inventory["revision"] == 4
        for key in saved_keys:
            retained = next(
                row for row in inventory["sources"] if row.get("policy_key") == key
            )
            assert retained["connection_state"] == "disconnected"
            assert retained["policy"]["attention_mode"] == "obligations"
            assert retained["policy"]["context_mode"] == "none"
            assert retained["policy"]["candidate_kinds"] == ["commitment"]
            assert retained["retained_candidate_count"] == 1
