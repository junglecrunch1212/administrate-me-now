"""Process proof for the member-owned Triage adjudication gate."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "member_triage_principal"
CHILD = "member_triage_child"
SECRET = "triage-loopback-secret-at-least-32-bytes"
AUTH = {
    "Tailscale-User-Login": "triage-principal.ts.net",
    "X-Correlation-Id": "triage:acceptance",
}
CHILD_AUTH = {"Tailscale-User-Login": "triage-child.ts.net"}
OPENCLAW_AUTH = {"Authorization": f"Bearer {SECRET}"}


def _settings(instance_dir: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=b"t" * 32,
        repository_root=REPO_ROOT,
        member_identities={
            "triage-principal.ts.net": MEMBER,
            "triage-child.ts.net": CHILD,
        },
        bridge_enrollments={
            "bridge-triage.ts.net": {"member_id": MEMBER, "status": "active"}
        },
        member_roles={MEMBER: "principal", CHILD: "child"},
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            "administrateme-portal|member-portal|actor-triage": MEMBER
        },
        deterministic_openclaw_fixture=True,
    )


def _provenance() -> dict[str, object]:
    return {
        "actor_id": "actor-triage",
        "agent_id": "main",
        "session_key": "session-triage",
        "session_id": "run-triage",
        "channel_id": "administrateme-portal",
        "account_id": "member-portal",
        "message_id": "message-triage",
        "thread_id": None,
        "correlation_id": "triage:proposal",
        "call_kind": "tool",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }


def test_note_recurrence_candidate_remains_human_gated_and_type_distinct(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance-recurrence"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        proposed_event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_786_000_000_000,
                tenant_id=runtime.settings.instance.tenant_id,
                type="triage.item.proposed",
                schema_version=1,
                occurred_at="2026-08-06T10:00:00Z",
                source_adapter="pipeline:member_knowledge_indexing",
                source_account_id="note-semantics",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="triage-note-recurrence-fixture",
                payload={
                    "proposal_id": "triage-note-recurrence",
                    "target_kind": "recurrence",
                    "owner_member_id": MEMBER,
                    "title": "Return the school form every Friday",
                    "candidate": {
                        "rrule": "FREQ=WEEKLY;BYDAY=FR",
                        "next_occurrence": "2026-08-07T17:00:00Z",
                        "kind": "school_form",
                        "trackable": True,
                        "notes": "Exact fixture note evidence",
                    },
                    "route": {"member_id": MEMBER},
                    "source_event_id": "fixture-note-event",
                    "confidence": 0.96,
                    "reasons": ["Exact fixture note evidence"],
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "triage", proposed_event_id)
        [proposal] = client.get("/api/member/v1/surfaces/triage", headers=AUTH).json()[
            "items"
        ]
        assert proposal["target_kind"] == "recurrence"
        assert proposal["review_state"] == {
            "contract": "triage-review-state-v2",
            "state": "ready",
            "missing": [],
            "human_gate_required": True,
            "approval_authorized": False,
        }
        assert (
            runtime.projections.connection("recurrences")
            .execute("SELECT COUNT(*) FROM recurrences")
            .fetchone()[0]
            == 0
        )
        approved = client.post(
            "/api/member/v1/triage/triage-note-recurrence/approve",
            headers=AUTH,
            json={
                "request_id": "triage-recurrence-approve",
                "expected_last_event_id": proposal["last_event_id"],
            },
        )
        assert approved.status_code == 200, approved.text
        recurrence = (
            runtime.projections.connection("recurrences")
            .execute(
                "SELECT linked_kind, linked_id, kind, rrule, trackable FROM recurrences"
            )
            .fetchone()
        )
        assert tuple(recurrence) == (
            "party",
            MEMBER,
            "school_form",
            "FREQ=WEEKLY;BYDAY=FR",
            1,
        )
        assert (
            runtime.projections.connection("tasks")
            .execute("SELECT COUNT(*) FROM tasks")
            .fetchone()[0]
            == 0
        )


def test_add_for_review_holds_exact_text_and_splits_typed_candidates(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance-intent"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        ambiguous_text = "Maybe something about the school form"
        ambiguous = client.post(
            "/api/member/v1/triage/intents",
            headers=AUTH,
            json={
                "text": ambiguous_text,
                "source_surface": "today",
                "client_request_id": "intent-ambiguous-0001",
            },
        )
        assert ambiguous.status_code == 202, ambiguous.text
        assert ambiguous.json()["state"] == "pending_interpretation"
        assert ambiguous.json()["canonical_event_ids"] == []

        multi_text = (
            "Call the school office; appointment on "
            "2026-08-10T18:00:00-04:00 until 2026-08-10T19:00:00-04:00; "
            "every Friday return the school form"
        )
        multi = client.post(
            "/api/member/v1/triage/intents",
            headers=AUTH,
            json={
                "text": multi_text,
                "source_surface": "calendar",
                "client_request_id": "intent-multi-0001",
            },
        )
        assert multi.status_code == 202, multi.text
        assert multi.json()["state"] == "pending_review"
        assert multi.json()["canonical_event_ids"] == []
        assert len(multi.json()["proposal_event_ids"]) == 3

        surface = client.get("/api/member/v1/surfaces/triage", headers=AUTH).json()
        ambiguous_row = next(
            item for item in surface["items"] if item["title"] == ambiguous_text
        )
        assert ambiguous_row["state"] == "pending_interpretation"
        assert ambiguous_row["candidate"]["exact_text"] == ambiguous_text
        typed = [
            item
            for item in surface["items"]
            if item.get("parent_intent_id") == multi.json()["intent_id"]
        ]
        assert {item["target_kind"] for item in typed} == {
            "task",
            "calendar_event",
            "recurrence",
        }
        assert all(item["confidence"] is None for item in typed)

        retry = client.post(
            "/api/member/v1/triage/intents",
            headers=AUTH,
            json={
                "text": multi_text,
                "source_surface": "calendar",
                "client_request_id": "intent-multi-0001",
            },
        )
        assert retry.status_code == 202
        assert retry.json()["duplicate"] is True
        assert retry.json()["proposal_event_ids"] == multi.json()["proposal_event_ids"]
        conflict = client.post(
            "/api/member/v1/triage/intents",
            headers=AUTH,
            json={
                "text": "Different text",
                "source_surface": "calendar",
                "client_request_id": "intent-multi-0001",
            },
        )
        assert conflict.status_code == 409
        assert (
            client.post(
                "/api/member/v1/triage/intents",
                headers=CHILD_AUTH,
                json={
                    "text": "Call the school",
                    "source_surface": "today",
                    "client_request_id": "intent-child-0001",
                },
            ).status_code
            == 403
        )

        canonical = client.portal.call(
            _events,
            app.state.runtime,
            "task.created",
            "recurrence.added",
            "external_sync.write_requested",
        )
        assert canonical == []


async def _events(runtime: object, *types: str) -> list[dict]:
    rows = []
    async for event in runtime.event_log.read_since(types=list(types) or None):
        rows.append(event)
    return rows


def test_proposal_requires_member_triage_before_official_task(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    app = create_application(_settings(instance))

    with TestClient(app) as client:
        runtime = app.state.runtime
        proposed = client.post(
            "/api/openclaw/v1/tools/action",
            headers=OPENCLAW_AUTH,
            json={
                "provenance": _provenance(),
                "request_id": "triage-proposal-0001",
                "mode": "propose",
                "kind": "task",
                "title": "Put the school form on the correct list",
                "due": "2026-08-07T17:00:00Z",
            },
        )
        assert proposed.status_code == 200, proposed.text
        proposal_event_id = proposed.json()["event_id"]
        client.portal.call(runtime.wait_for_projection, "triage", proposal_event_id)

        triage = client.get("/api/core/v1/triage", headers=AUTH)
        assert triage.status_code == 200, triage.text
        [item] = triage.json()["items"]
        assert item["proposal_id"] == "ocp_triage-proposal-0001"
        assert item["state"] == "pending_review"
        assert item["target_kind"] == "task"
        evidence = client.get(
            f"/api/member/v1/evidence/{item['source_event_id']}", headers=AUTH
        )
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["type"] == "household.action.proposed"
        assert (
            runtime.projections.connection("tasks")
            .execute("SELECT COUNT(*) FROM tasks")
            .fetchone()[0]
            == 0
        )
        assert (
            client.get("/api/core/v1/triage", headers=CHILD_AUTH).json()["items"] == []
        )

        legacy = client.get("/api/core/v1/inbox", headers=AUTH)
        assert legacy.json()["deprecated_alias"] == "inbox"
        assert legacy.json()["items"] == triage.json()["items"]
        page_alias = client.get("/member/inbox", headers=AUTH, follow_redirects=False)
        assert page_alias.status_code == 308
        assert page_alias.headers["location"] == "/member/triage"

        approval_body = {
            "request_id": "triage-approve-0001",
            "expected_last_event_id": item["last_event_id"],
        }
        approved = client.post(
            f"/api/member/v1/triage/{item['proposal_id']}/approve",
            headers=AUTH,
            json=approval_body,
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == "confirmed"
        assert len(approved.json()["materialized_event_ids"]) == 1
        retried_approval = client.post(
            f"/api/member/v1/triage/{item['proposal_id']}/approve",
            headers=AUTH,
            json=approval_body,
        )
        assert retried_approval.status_code == 200, retried_approval.text
        assert retried_approval.json() == {**approved.json(), "duplicate": True}
        conflicting_retry = client.post(
            f"/api/member/v1/triage/{item['proposal_id']}/approve",
            headers=AUTH,
            json={**approval_body, "reason": "a different decision body"},
        )
        assert conflicting_retry.status_code == 409
        assert client.get("/api/core/v1/triage", headers=AUTH).json()["items"] == []

        task = (
            runtime.projections.connection("tasks")
            .execute("SELECT title, assignee_party, status FROM tasks")
            .fetchone()
        )
        assert tuple(task) == (
            "Put the school form on the correct list",
            MEMBER,
            "inbox",
        )
        events = client.portal.call(
            _events,
            runtime,
            "household.action.proposed",
            "task.created",
            "triage.item.confirmed",
            "triage.item.materialized",
        )
        assert [event["type"] for event in events] == [
            "household.action.proposed",
            "triage.item.confirmed",
            "task.created",
            "triage.item.materialized",
        ]
        assert events[-1]["payload"]["materialized_event_ids"] == [
            events[-2]["event_id"]
        ]
        assert events[-2]["causation_id"] == events[-3]["event_id"]
        assert (
            events[-1]["payload"]["confirmation_event_id"] == (events[-3]["event_id"])
        )
        client.portal.call(runtime.projections.rebuild, "triage")
        rebuilt = (
            runtime.projections.connection("triage")
            .execute(
                "SELECT state, resolution_reason, materialized_event_ids_json "
                "FROM triage_items WHERE proposal_id=?",
                (item["proposal_id"],),
            )
            .fetchone()
        )
        assert tuple(rebuilt) == (
            "confirmed",
            "confirmed_local",
            f'["{events[-2]["event_id"]}"]',
        )

        calendar_source_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_786_000_000_000,
                tenant_id=runtime.settings.instance.tenant_id,
                type="calendar.source_observed",
                schema_version=1,
                occurred_at="2026-08-06T10:00:00Z",
                source_adapter="triage-calendar-fixture",
                source_account_id=MEMBER,
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                actor_identity=MEMBER,
                payload={
                    "member_id": MEMBER,
                    "provider": "apple_calendar",
                    "provider_account_id": MEMBER,
                    "calendar_external_id": "calendar-primary",
                    "calendar_name": "Fixture personal calendar",
                    "access_role": "owner",
                    "primary": True,
                    "selected": True,
                    "read_details": True,
                    "read_free_busy": True,
                    "write_events": True,
                    "observed_at": "2026-08-06T10:00:00Z",
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "calendars", calendar_source_id)
        calendar_policy = client.post(
            "/api/member/v1/calendar/onboarding",
            headers=AUTH,
            json={
                "request_id": "triage-calendar-policy-0001",
                "member_id": MEMBER,
                "timezone": "Etc/UTC",
                "rules": [
                    {
                        "source_member_id": MEMBER,
                        "provider": "apple_calendar",
                        "provider_account_id": MEMBER,
                        "calendar_external_id": "calendar-primary",
                        "purpose": "personal",
                        "effect": "hard_block",
                        "write_target": True,
                        "destination_kind": "personal",
                    }
                ],
                "windows": [],
                "buffer_minutes": {},
                "accepted_chores_required": True,
                "one_confirm_write": True,
            },
        )
        assert calendar_policy.status_code == 202, calendar_policy.text

        routed = client.post(
            "/api/openclaw/v1/tools/action",
            headers=OPENCLAW_AUTH,
            json={
                "provenance": {**_provenance(), "correlation_id": "triage:calendar"},
                "request_id": "triage-calendar-0001",
                "mode": "propose",
                "kind": "task",
                "title": "School conference",
                "due": "2026-08-10T18:00:00Z",
            },
        )
        assert routed.status_code == 200, routed.text
        client.portal.call(
            runtime.wait_for_projection, "triage", routed.json()["event_id"]
        )
        [calendar_candidate] = client.get("/api/core/v1/triage", headers=AUTH).json()[
            "items"
        ]
        enrichment_body = {
            "request_id": "triage-enrich-calendar-0001",
            "expected_last_event_id": calendar_candidate["last_event_id"],
            "target_kind": "calendar_event",
            "candidate_updates": {
                "start": "2026-08-10T18:00:00-04:00",
                "end": "2026-08-10T19:00:00-04:00",
            },
            "route": {
                "member_id": MEMBER,
                "provider": "apple_calendar",
                "provider_account_id": MEMBER,
                "container_external_id": "calendar-primary",
            },
        }
        enriched = client.post(
            f"/api/member/v1/triage/{calendar_candidate['proposal_id']}/enrich",
            headers=AUTH,
            json=enrichment_body,
        )
        assert enriched.status_code == 200, enriched.text
        enriched_item = enriched.json()["triage"]
        retried_enrichment = client.post(
            f"/api/member/v1/triage/{calendar_candidate['proposal_id']}/enrich",
            headers=AUTH,
            json=enrichment_body,
        )
        assert retried_enrichment.status_code == 200, retried_enrichment.text
        assert retried_enrichment.json()["duplicate"] is True
        assert retried_enrichment.json()["event_id"] == enriched.json()["event_id"]
        triage_surface = client.get(
            "/api/member/v1/surfaces/triage", headers=AUTH
        ).json()
        [reviewed_calendar_candidate] = triage_surface["items"]
        assert reviewed_calendar_candidate["review_state"]["state"] == "ready"
        assert reviewed_calendar_candidate["calendar_preview"]["state"] == "ready"
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        ), "enrichment alone cannot request a provider write"
        calendar_approved = client.post(
            f"/api/member/v1/triage/{calendar_candidate['proposal_id']}/approve",
            headers=AUTH,
            json={
                "request_id": "triage-approve-calendar-0001",
                "expected_last_event_id": enriched_item["last_event_id"],
            },
        )
        assert calendar_approved.status_code == 200, calendar_approved.text
        assert calendar_approved.json()["state"] == "approved_awaiting_openclaw"
        [pending_action] = client.portal.call(
            _events, runtime, "member.provider_action.confirmed"
        )
        assert pending_action["payload"]["member_id"] == MEMBER
        assert pending_action["payload"]["provider"] == "apple_calendar"
        assert pending_action["payload"]["operation"] == "create_event"
        assert pending_action["payload"]["command_payload"]["calendar_id"] == (
            "calendar-primary"
        )
        assert (
            pending_action["payload"]["origin_proposal_id"]
            == (calendar_candidate["proposal_id"])
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        assert (
            runtime.projections.connection("calendars")
            .execute("SELECT COUNT(*) FROM calendar_events")
            .fetchone()[0]
            == 0
        ), "provider command is not a fabricated provider observation"

        generic = EventEnvelope(
            event_at_ms=1_786_050_000_000,
            tenant_id=runtime.settings.instance.tenant_id,
            type="triage.item.proposed",
            schema_version=1,
            occurred_at="2026-08-06T12:00:00Z",
            source_adapter="pipeline:fixture-extraction",
            source_account_id=MEMBER,
            owner_scope=f"private:{MEMBER}",
            visibility_scope=f"private:{MEMBER}",
            sensitivity="normal",
            causation_id=proposal_event_id,
            idempotency_key="triage:generic-list-item:0001",
            payload={
                "proposal_id": "generic-list-item-0001",
                "target_kind": "list_item",
                "owner_member_id": MEMBER,
                "title": "Add poster board to the school list",
                "candidate": {"child_safe": True},
                "route": None,
                "source_event_id": proposal_event_id,
                "confidence": 0.91,
                "reasons": ["fixture extraction candidate"],
            },
        )
        generic_id = client.portal.call(runtime.events.write, generic)
        client.portal.call(runtime.wait_for_projection, "triage", generic_id)
        [generic_item] = client.get("/api/core/v1/triage", headers=AUTH).json()["items"]
        dismissal_body = {
            "request_id": "triage-dismiss-generic-0001",
            "expected_last_event_id": generic_item["last_event_id"],
            "reason": "Not needed",
        }
        dismissed = client.post(
            f"/api/member/v1/triage/{generic_item['proposal_id']}/dismiss",
            headers=AUTH,
            json=dismissal_body,
        )
        assert dismissed.status_code == 200, dismissed.text
        retried_dismissal = client.post(
            f"/api/member/v1/triage/{generic_item['proposal_id']}/dismiss",
            headers=AUTH,
            json=dismissal_body,
        )
        assert retried_dismissal.status_code == 200, retried_dismissal.text
        assert retried_dismissal.json() == {**dismissed.json(), "duplicate": True}
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )

        party_created = EventEnvelope(
            event_at_ms=1_786_050_000_100,
            tenant_id=runtime.settings.instance.tenant_id,
            type="party.created",
            schema_version=1,
            occurred_at="2026-08-06T12:00:01Z",
            source_adapter="fixture-crm",
            source_account_id=MEMBER,
            owner_scope=f"private:{MEMBER}",
            visibility_scope=f"private:{MEMBER}",
            sensitivity="normal",
            idempotency_key="triage:fixture-party:0001",
            payload={
                "party_id": "party-triage-existing",
                "kind": "person",
                "display_name": "Fixture Person",
                "sort_name": "Person, Fixture",
                "nickname": None,
                "pronouns": None,
                "notes": None,
                "attributes": {},
            },
        )
        party_event_id = client.portal.call(runtime.events.write, party_created)
        client.portal.call(runtime.wait_for_projection, "parties", party_event_id)
        identity_suggestion = EventEnvelope(
            event_at_ms=1_786_050_000_200,
            tenant_id=runtime.settings.instance.tenant_id,
            type="identity.merge_suggested",
            schema_version=1,
            occurred_at="2026-08-06T12:00:02Z",
            source_adapter="identity_resolution",
            source_account_id="pipeline",
            owner_scope=f"private:{MEMBER}",
            visibility_scope=f"private:{MEMBER}",
            sensitivity="normal",
            causation_id=proposal_event_id,
            idempotency_key="triage:identity-suggestion:0001",
            payload={
                "surviving_party_id": "party-triage-existing",
                "candidate_value": "fixture@example.invalid",
                "candidate_kind": "email",
                "candidate_value_normalized": "fixture@example.invalid",
                "confidence": 0.93,
                "heuristic": "fixture similarity",
                "source_event_id": proposal_event_id,
            },
        )
        suggestion_id = client.portal.call(runtime.events.write, identity_suggestion)
        client.portal.call(runtime.wait_for_projection, "triage", suggestion_id)
        [identity_item] = client.get("/api/core/v1/triage", headers=AUTH).json()[
            "items"
        ]
        assert identity_item["target_kind"] == "party_identifier"
        identity_approved = client.post(
            f"/api/member/v1/triage/{identity_item['proposal_id']}/approve",
            headers=AUTH,
            json={
                "request_id": "triage-approve-identity-0001",
                "expected_last_event_id": identity_item["last_event_id"],
            },
        )
        assert identity_approved.status_code == 200, identity_approved.text
        [identifier_event_id] = identity_approved.json()["materialized_event_ids"]
        client.portal.call(runtime.wait_for_projection, "parties", identifier_event_id)
        identifier = (
            runtime.projections.connection("parties")
            .execute(
                "SELECT party_id, kind, value_normalized FROM identifiers WHERE identifier_id=?",
                (f"triage_identifier_{identity_item['proposal_id']}",),
            )
            .fetchone()
        )
        assert tuple(identifier) == (
            "party-triage-existing",
            "email",
            "fixture@example.invalid",
        )

        hidden_party = EventEnvelope(
            event_at_ms=1_786_050_000_250,
            tenant_id=runtime.settings.instance.tenant_id,
            type="party.created",
            schema_version=1,
            occurred_at="2026-08-06T12:00:02.500000Z",
            source_adapter="fixture-crm",
            source_account_id=CHILD,
            owner_scope=f"private:{CHILD}",
            visibility_scope=f"private:{CHILD}",
            sensitivity="normal",
            idempotency_key="triage:hidden-party:0001",
            payload={
                "party_id": "party-triage-hidden",
                "kind": "person",
                "display_name": "Private Fixture",
                "sort_name": "Fixture, Private",
                "nickname": None,
                "pronouns": None,
                "notes": None,
                "attributes": {},
            },
        )
        hidden_party_event_id = client.portal.call(runtime.events.write, hidden_party)
        client.portal.call(
            runtime.wait_for_projection, "parties", hidden_party_event_id
        )
        hidden_suggestion = EventEnvelope(
            event_at_ms=1_786_050_000_260,
            tenant_id=runtime.settings.instance.tenant_id,
            type="identity.merge_suggested",
            schema_version=1,
            occurred_at="2026-08-06T12:00:02.600000Z",
            source_adapter="identity_resolution",
            source_account_id="pipeline",
            owner_scope=f"private:{MEMBER}",
            visibility_scope=f"private:{MEMBER}",
            sensitivity="normal",
            causation_id=proposal_event_id,
            idempotency_key="triage:hidden-identity-suggestion:0001",
            payload={
                "surviving_party_id": "party-triage-hidden",
                "candidate_value": "private@example.invalid",
                "candidate_kind": "email",
                "candidate_value_normalized": "private@example.invalid",
                "confidence": 0.93,
                "heuristic": "fixture similarity",
                "source_event_id": proposal_event_id,
            },
        )
        hidden_suggestion_id = client.portal.call(
            runtime.events.write, hidden_suggestion
        )
        client.portal.call(runtime.wait_for_projection, "triage", hidden_suggestion_id)
        [hidden_item] = client.get("/api/core/v1/triage", headers=AUTH).json()["items"]
        hidden_approval = client.post(
            f"/api/member/v1/triage/{hidden_item['proposal_id']}/approve",
            headers=AUTH,
            json={
                "request_id": "triage-approve-hidden-identity-0001",
                "expected_last_event_id": hidden_item["last_event_id"],
            },
        )
        assert hidden_approval.status_code == 422
        assert hidden_approval.json()["detail"] == (
            "identity candidate party is unavailable"
        )
        hidden_dismissal = client.post(
            f"/api/member/v1/triage/{hidden_item['proposal_id']}/dismiss",
            headers=AUTH,
            json={
                "request_id": "triage-dismiss-hidden-identity-0001",
                "expected_last_event_id": hidden_item["last_event_id"],
                "reason": "Fixture cleanup",
            },
        )
        assert hidden_dismissal.status_code == 200, hidden_dismissal.text

        runtime.settings.adapter_config["adapters"] = [
            {
                "id": "google-tasks-primary",
                "kind": "tasks-google-tasks",
                "config": {
                    "member_id": MEMBER,
                    "user_email": "member@example.invalid",
                    "tasklist_id": "@default",
                },
            }
        ]
        task_list_candidate = EventEnvelope(
            event_at_ms=1_786_050_000_300,
            tenant_id=runtime.settings.instance.tenant_id,
            type="triage.item.proposed",
            schema_version=1,
            occurred_at="2026-08-06T12:00:03Z",
            source_adapter="pipeline:fixture-extraction",
            source_account_id=MEMBER,
            owner_scope=f"private:{MEMBER}",
            visibility_scope=f"private:{MEMBER}",
            sensitivity="normal",
            causation_id=proposal_event_id,
            idempotency_key="triage:google-task-candidate:0001",
            payload={
                "proposal_id": "google-task-candidate-0001",
                "target_kind": "list_item",
                "owner_member_id": MEMBER,
                "title": "Buy poster board",
                "candidate": {"due": "2026-08-07T17:00:00Z"},
                "route": {
                    "member_id": MEMBER,
                    "provider": "google_tasks",
                    "provider_account_id": "member@example.invalid",
                    "container_external_id": "@default",
                },
                "source_event_id": proposal_event_id,
                "confidence": 0.89,
                "reasons": ["fixture Google Tasks extraction"],
            },
        )
        task_list_candidate_id = client.portal.call(
            runtime.events.write, task_list_candidate
        )
        client.portal.call(
            runtime.wait_for_projection, "triage", task_list_candidate_id
        )
        [task_list_item] = client.get("/api/core/v1/triage", headers=AUTH).json()[
            "items"
        ]
        task_list_approved = client.post(
            f"/api/member/v1/triage/{task_list_item['proposal_id']}/approve",
            headers=AUTH,
            json={
                "request_id": "triage-approve-google-task-0001",
                "expected_last_event_id": task_list_item["last_event_id"],
            },
        )
        assert task_list_approved.status_code == 200, task_list_approved.text
        pending_actions = client.portal.call(
            _events, runtime, "member.provider_action.confirmed"
        )
        google_task_action = next(
            event
            for event in pending_actions
            if event["payload"].get("origin_proposal_id")
            == task_list_item["proposal_id"]
        )
        assert google_task_action["payload"]["provider"] == "google_tasks"
        assert google_task_action["payload"]["operation"] == "create_task"
        assert google_task_action["payload"]["command_payload"]["tasklist_id"] == (
            "@default"
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )

        recovery_proposal = client.post(
            "/api/openclaw/v1/tools/action",
            headers=OPENCLAW_AUTH,
            json={
                "provenance": {**_provenance(), "correlation_id": "triage:recovery"},
                "request_id": "triage-recovery-proposal-0001",
                "mode": "propose",
                "kind": "task",
                "title": "Crash recovery fixture",
            },
        )
        assert recovery_proposal.status_code == 200, recovery_proposal.text
        client.portal.call(
            runtime.wait_for_projection,
            "triage",
            recovery_proposal.json()["event_id"],
        )
        [recovery_item] = client.get("/api/core/v1/triage", headers=AUTH).json()[
            "items"
        ]
        stale_materialization = EventEnvelope(
            event_at_ms=1_786_050_000_400,
            tenant_id=runtime.settings.instance.tenant_id,
            type="task.created",
            schema_version=1,
            occurred_at="2026-08-06T12:00:04Z",
            source_adapter="member-portal",
            source_account_id=MEMBER,
            owner_scope=f"private:{MEMBER}",
            visibility_scope=f"private:{MEMBER}",
            sensitivity="normal",
            causation_id=recovery_item["last_event_id"],
            actor_identity=MEMBER,
            idempotency_key=(f"triage:materialize:{recovery_item['proposal_id']}:task"),
            raw_ref="triage-state-sha256:stale-fixture",
            payload={
                "task_id": f"triage_task_{recovery_item['proposal_id']}",
                "title": "Stale crash recovery fixture",
                "description": None,
                "owner_member_id": MEMBER,
                "due": None,
                "energy": None,
                "effort_min": None,
                "source_commitment_id": None,
            },
        )
        client.portal.call(runtime.events.write, stale_materialization)
        recovery_approval = client.post(
            f"/api/member/v1/triage/{recovery_item['proposal_id']}/approve",
            headers=AUTH,
            json={
                "request_id": "triage-recovery-approve-0001",
                "expected_last_event_id": recovery_item["last_event_id"],
            },
        )
        assert recovery_approval.status_code == 409
        assert recovery_approval.json()["detail"] == (
            "triage candidate changed after materialization; run Doctor"
        )
