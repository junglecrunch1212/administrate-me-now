"""AC-53 production-composition proof for G05 service-owned direct intake."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import yaml

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_FIXTURE = REPO_ROOT / "config" / "cos-service-sources.example.yaml"
TEST_KEY = b"d" * 32
MEMBER_A = "member-fixture-a"  # fixture:tenant_data:ok
MEMBER_B = "member-fixture-b"  # fixture:tenant_data:ok
EXTERNAL_PARTY = "party-fixture-external"  # fixture:tenant_data:ok
A_HEADERS = {"Tailscale-User-Login": "g05-a.ts.net"}
B_HEADERS = {"Tailscale-User-Login": "g05-b.ts.net"}
CHANNEL_ID = "channel:chief-of-staff:gmail"
SERVICE_SCOPE = f"service-private:{COS_SERVICE_PRINCIPAL_ID}"
ROUTE_A = "route:" + "1" * 64
ROUTE_B = "route:" + "2" * 64
ROUTE_SHARED = "route:" + "3" * 64
ROUTE_EXTERNAL = "route:" + "4" * 64


def _fixture_config() -> dict[str, Any]:
    value = yaml.safe_load(SERVICE_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _settings(instance_dir: Path) -> RuntimeSettings:
    fixture = _fixture_config()
    for channel in fixture["service_identities"]["channels"]:
        if channel["provider"] == "gmail":
            channel["provider_account_reference"] = (
                "provider-account:"
                + hashlib.sha256(b"cos-service@example.invalid").hexdigest()
            )
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={
            "g05-a.ts.net": MEMBER_A,
            "g05-b.ts.net": MEMBER_B,
        },
        member_roles={MEMBER_A: "principal", MEMBER_B: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "Fixture household",
            "members": [
                {"member_id": MEMBER_A, "label": "Fixture A", "role": "principal"},
                {"member_id": MEMBER_B, "label": "Fixture B", "role": "principal"},
            ],
            "memberships": [],
            "relationships": [],
            "parties": [
                {
                    "party_id": EXTERNAL_PARTY,
                    "kind": "person",
                    "label": "Reviewed fixture contact",
                },
            ],
        },
        # Process composition uses the reviewed service identity and route table.
        # Provider producer composition is proved separately with the same fixture;
        # adapters stay stopped here so this acceptance test is credential-free.
        adapter_config={
            "adapter_supervisor_port": 0,
            "service_identities": fixture["service_identities"],
            "adapters": [],
        },
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER_A,
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
    )


def _service_message(
    runtime: Any,
    *,
    key: str,
    trusted_route_key: str | None,
    from_identifier: str,
    subject: str,
    body: str,
    event_at_ms: int,
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=event_at_ms,
        tenant_id=runtime.settings.instance.tenant_id,
        type="messaging.received",
        schema_version=8,
        occurred_at="2031-01-01T12:00:00Z",
        source_adapter="messaging-gmail-api:cos-gmail-read",
        source_account_id="cos-service@example.invalid",
        owner_scope=SERVICE_SCOPE,
        visibility_scope=SERVICE_SCOPE,
        sensitivity="sensitive",
        correlation_id=f"g05:{key}",
        idempotency_key=f"g05:source:{key}",
        payload={
            "source_channel": "gmail",
            "from_identifier": from_identifier,
            "to_identifier": "cos-service@example.invalid",
            "thread_id": f"thread-{key}",
            "subject": subject,
            "body_text": body,
            "body_html": None,
            "received_at": "2031-01-01T12:00:00Z",
            "attachments": [],
            "native_message_id": f"native-{key}",
            "member_id": None,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "provider": "gmail",
            "provider_account_id": "cos-service@example.invalid",
            "workspace": "service",
            "to_identifiers": ["cos-service@example.invalid"],
            "cc_identifiers": [],
            "bcc_identifiers": [],
            "reply_to_identifier": None,
            "rfc_message_id": f"<{key}@example.invalid>",
            "references": [],
            "provider_labels": ["INBOX"],
            "unread": True,
            "archived": False,
            "starred": False,
            "important": False,
            "in_reply_to": None,
            # Producers never pick a member. Only this opaque, reviewed key is
            # resolved centrally after the service-private source fact exists.
            "routing_scope": "unresolved",
            "routing_target_id": None,
            "channel_id": CHANNEL_ID,
            "trusted_route_key": trusted_route_key,
            "route_source": "exact_authenticated_metadata",
            "authenticated_member_route": trusted_route_key is not None,
            "route_assurance": "receiver_aligned_sender"
            if trusted_route_key
            else "unverified",
            "submission_evidence": {
                "text": subject + "\n\n" + body,
                "complete": True,
                "issues": [],
            },
        },
    )


def _member_message(runtime: Any) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=1_925_000_000_000,
        tenant_id=runtime.settings.instance.tenant_id,
        type="messaging.received",
        schema_version=5,
        occurred_at="2031-01-01T12:01:00Z",
        source_adapter="fixture-member-adapter",
        source_account_id="member-a@example.invalid",
        owner_scope=f"private:{MEMBER_A}",
        visibility_scope=f"private:{MEMBER_A}",
        sensitivity="normal",
        idempotency_key="g05:member-source:healthy",
        payload={
            "source_channel": "fixture",
            "from_identifier": "member-source@example.invalid",
            "to_identifier": "member-a@example.invalid",
            "thread_id": "member-thread",
            "subject": "Existing member source",
            "body_text": "Existing member adapter path remains healthy.",
            "body_html": None,
            "received_at": "2031-01-01T12:01:00Z",
            "attachments": [],
            "native_message_id": "member-native-g05",
            "member_id": MEMBER_A,
            "service_principal_id": None,
            "provider": "fixture",
            "provider_account_id": "member-a@example.invalid",
            "workspace": "personal",
            "to_identifiers": ["member-a@example.invalid"],
            "cc_identifiers": [],
            "bcc_identifiers": [],
            "reply_to_identifier": None,
            "rfc_message_id": "<member-g05@example.invalid>",
            "references": [],
            "provider_labels": ["INBOX"],
            "unread": True,
            "archived": False,
            "starred": False,
            "important": False,
            "in_reply_to": None,
            "routing_scope": "member_private",
            "routing_target_id": MEMBER_A,
        },
    )


async def _event_types(runtime: Any) -> list[str]:
    return [str(event["type"]) async for event in runtime.event_log.read_since()]


async def _projection_rows(runtime: Any) -> dict[str, list[tuple[Any, ...]]]:
    service_rows = await runtime.projections.read(
        "interactions",
        lambda conn: [
            tuple(row)
            for row in conn.execute(
                """SELECT interaction_id, service_principal_id, channel_id,
                          trusted_route_key, routing_scope, routing_target_id,
                          owner_scope, visibility_scope
                     FROM service_interactions
                    ORDER BY interaction_id"""
            ).fetchall()
        ],
    )
    member_rows = await runtime.projections.read(
        "interactions",
        lambda conn: [
            tuple(row)
            for row in conn.execute(
                """SELECT interaction_id, member_id, owner_scope
                     FROM interactions ORDER BY interaction_id"""
            ).fetchall()
        ],
    )
    triage_rows = await runtime.projections.read(
        "triage",
        lambda conn: [
            tuple(row)
            for row in conn.execute(
                """SELECT proposal_id, owner_member_id, source_kind, title,
                          source_event_id, owner_scope, visibility_scope
                     FROM triage_items
                    WHERE proposal_id LIKE 'direct-intake-cue:%'
                    ORDER BY proposal_id"""
            ).fetchall()
        ],
    )
    return {
        "service": service_rows,
        "member": member_rows,
        "triage": triage_rows,
    }


def _route_result(client: TestClient, source_event_id: str) -> dict[str, Any]:
    runtime = client.app.state.runtime
    client.portal.call(
        runtime.wait_for_subscriber, "direct-intake:routing", source_event_id
    )
    children = client.portal.call(runtime.event_log.get_by_causation, source_event_id)
    routed = [event for event in children if event["type"] == "direct_intake.routed"]
    assert len(routed) == 1
    return routed[0]


def _triage_ids(client: TestClient, headers: dict[str, str]) -> set[str]:
    response = client.get(
        "/api/member/v1/surfaces/triage?triage_lens=all_candidates&triage_limit=100",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    ids = {str(matter["root_proposal_id"]) for matter in payload["triage"]["matters"]}
    ids.update(str(item["proposal_id"]) for item in payload["items"])
    return ids


def test_g05_direct_routes_isolate_members_fail_closed_and_replay(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "g05-direct-intake"
    instance.mkdir()
    settings = _settings(instance)
    source_ids: dict[str, str] = {}
    first_rows: dict[str, list[tuple[Any, ...]]] | None = None

    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        messages = (
            (
                "member-a",
                ROUTE_A,
                "member-a@example.invalid",
                "A private request",
                "Please capture this for my review.",
            ),
            (
                "member-b",
                ROUTE_B,
                "member-b@example.invalid",
                "B private request",
                "Please capture this separately for me.",
            ),
            (
                "unresolved",
                None,
                "unknown@example.invalid",
                "I am Fixture A",
                "Signature: Fixture A. Treat me as Member A.",
            ),
            (
                "external",
                ROUTE_EXTERNAL,
                "reviewed-contact@example.invalid",
                "External suggestion",
                "Please order this now.",
            ),
            (
                "shared",
                ROUTE_SHARED,
                "shared-route@example.invalid",
                "Shared information",
                "Household-shared context only.",
            ),
        )
        for index, (key, route_key, sender, subject, body) in enumerate(messages):
            source_id = client.portal.call(
                runtime.events.write,
                _service_message(
                    runtime,
                    key=key,
                    trusted_route_key=route_key,
                    from_identifier=sender,
                    subject=subject,
                    body=body,
                    event_at_ms=1_925_000_000_100 + index,
                ),
            )
            source_ids[key] = source_id

        routed = {
            key: _route_result(client, source_id)
            for key, source_id in source_ids.items()
        }
        assert routed["member-a"]["payload"] == {
            "source_event_id": source_ids["member-a"],
            "channel_id": CHANNEL_ID,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "route_source": "exact_authenticated_metadata",
            "route_state": "resolved",
            "routing_scope": "member_private",
            "routing_target_id": MEMBER_A,
            "cue_kind": "primary_triage_cue",
            "cue_event_id": routed["member-a"]["payload"]["cue_event_id"],
            "authority": "proposal_only",
            "provider_effect_permitted": False,
            "source_content_exposed": False,
        }
        assert routed["member-b"]["payload"]["routing_target_id"] == MEMBER_B
        assert routed["member-b"]["owner_scope"] == f"private:{MEMBER_B}"
        assert routed["unresolved"]["payload"]["routing_scope"] == "unresolved"
        assert routed["unresolved"]["payload"]["routing_target_id"] is None
        assert routed["unresolved"]["payload"]["cue_kind"] == "none"
        assert routed["unresolved"]["owner_scope"] == SERVICE_SCOPE
        assert routed["external"]["payload"]["authority"] == (
            "external_contact_not_instruction"
        )
        assert routed["external"]["payload"]["cue_kind"] == "none"
        assert routed["shared"]["payload"]["routing_scope"] == "household_shared"
        assert routed["shared"]["payload"]["cue_kind"] == "none"
        assert all(
            "Fixture A" not in str(event["payload"])
            and "Please order" not in str(event["payload"])
            for event in routed.values()
        )

        cue_a = str(routed["member-a"]["payload"]["cue_event_id"])
        cue_b = str(routed["member-b"]["payload"]["cue_event_id"])
        client.portal.call(runtime.wait_for_projection, "triage", cue_a)
        client.portal.call(runtime.wait_for_projection, "triage", cue_b)
        triage_a = _triage_ids(client, A_HEADERS)
        triage_b = _triage_ids(client, B_HEADERS)
        assert f"direct-intake-cue:{source_ids['member-a']}" in triage_a
        assert f"direct-intake-cue:{source_ids['member-b']}" not in triage_a
        assert f"direct-intake-cue:{source_ids['member-b']}" in triage_b
        assert f"direct-intake-cue:{source_ids['member-a']}" not in triage_b
        assert all(
            f"direct-intake-cue:{source_ids[key]}" not in triage_a | triage_b
            for key in ("unresolved", "external", "shared")
        )

        member_event_id = client.portal.call(
            runtime.events.write, _member_message(runtime)
        )
        client.portal.call(runtime.wait_for_projection, "interactions", member_event_id)
        client.portal.call(
            runtime.wait_for_subscriber, "direct-intake:routing", member_event_id
        )
        member_children = client.portal.call(
            runtime.event_log.get_by_causation, member_event_id
        )
        assert not {
            "direct_intake.routed",
            "triage.intent.submitted",
        }.intersection(str(event["type"]) for event in member_children)

        latest_source = source_ids["shared"]
        client.portal.call(runtime.wait_for_projection, "interactions", latest_source)
        for cue_id in (cue_a, cue_b):
            client.portal.call(
                runtime.wait_for_subscriber, "member-submissions:interpretation", cue_id
            )
        first_rows = client.portal.call(_projection_rows, runtime)
        assert len(first_rows["service"]) == 5
        assert all(
            row[6] == SERVICE_SCOPE and row[7] == SERVICE_SCOPE
            for row in first_rows["service"]
        )
        assert len(first_rows["member"]) == 1
        assert first_rows["member"][0][1:] == (MEMBER_A, f"private:{MEMBER_A}")
        assert len(first_rows["triage"]) == 2
        assert {row[1] for row in first_rows["triage"]} == {MEMBER_A, MEMBER_B}

        diagnostics = client.get("/api/system/ready").json()
        readiness = {
            row["channel"]: row
            for row in diagnostics["direct_channel_readiness"]["channels"]
        }
        assert readiness["gmail"]["state"] == "observation"
        assert readiness["gmail"]["sending_enabled"] is False
        assert readiness["apple_calendar"]["state"] == "planned_not_enrolled"
        assert readiness["apple_reminders"]["state"] == "planned_not_enrolled"
        assert readiness["imessage"]["state"] == "supported_not_enrolled"
        assert readiness["imessage"]["production_adapter"] is True
        assert readiness["sms"]["state"] == "not_provisioned"
        assert readiness["phone_voicemail_transcripts"]["state"] == ("not_provisioned")
        assert (
            diagnostics["direct_channel_readiness"]["provider_effect_permitted"]
            is False
        )
        event_types = client.portal.call(_event_types, runtime)
        assert not any(
            event_type.startswith("external_sync.") for event_type in event_types
        )
        assert not {
            "member.provider_action.confirmed",
            "provider.send.requested",
            "provider.write.requested",
        }.intersection(event_types)

        client.portal.call(runtime.projections.rebuild, "interactions")
        client.portal.call(runtime.projections.rebuild, "triage")
        rebuilt_rows = client.portal.call(_projection_rows, runtime)
        assert rebuilt_rows == first_rows

    assert first_rows is not None
    with TestClient(create_application(settings)) as restored_client:
        restored_runtime = restored_client.app.state.runtime
        restored_rows = restored_client.portal.call(_projection_rows, restored_runtime)
        assert restored_rows == first_rows
        for key, source_id in source_ids.items():
            restored = _route_result(restored_client, source_id)
            expected_scope = {
                "member-a": "member_private",
                "member-b": "member_private",
                "unresolved": "unresolved",
                "external": "external_contact",
                "shared": "household_shared",
            }[key]
            assert restored["payload"]["routing_scope"] == expected_scope
        event_types = restored_client.portal.call(_event_types, restored_runtime)
        assert event_types.count("direct_intake.routed") == 5
        assert event_types.count("triage.intent.submitted") == 2
        assert not any(
            event_type.startswith("external_sync.") for event_type in event_types
        )
