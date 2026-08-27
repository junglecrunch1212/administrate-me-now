"""Process proof for the member-owned Triage adjudication gate."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.lib.governance import GuardedWriteResult
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "member_triage_principal"
CHILD = "member_triage_child"
APPLE_CALENDAR_ACCOUNT = f"provider-account:{'a' * 32}"
APPLE_CALENDAR_PACK = "calendaring-apple-calendar-bridge"
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
            "bridge-triage.ts.net": {
                "member_id": MEMBER,
                "enrollment_id": "enrollment-triage-calendar",
                "status": "active",
                "configured_packs": [APPLE_CALENDAR_PACK],
                "enabled_packs": [APPLE_CALENDAR_PACK],
                "authorized_packs": [APPLE_CALENDAR_PACK],
                "adapters_active": [APPLE_CALENDAR_PACK],
                "pack_config": {
                    APPLE_CALENDAR_PACK: {
                        "member_id": MEMBER,
                        "provider_account_id": APPLE_CALENDAR_ACCOUNT,
                        "deployment_target": "bridge",
                        "workspace": "personal",
                        "permission_scope": [
                            "eventkit.calendar.read",
                            "eventkit.calendar.write",
                        ],
                        "calendar_identifier": "calendar-primary",
                        "allowed_container_ids": ["calendar-primary"],
                        "shared_container_ids": [],
                        "binding_provenance": "member-bridge-enrollment",
                        "ownership_attested": True,
                    }
                },
                "pack_capability_states": {APPLE_CALENDAR_PACK: "attested"},
                "provider_capability_state": "attested",
                "capability_attestations": {
                    APPLE_CALENDAR_PACK: {
                        "event_id": "event-triage-calendar-authenticated"
                    }
                },
            }
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


async def _message_proposals_for_source(
    event_log: Any, source_event_id: str
) -> list[dict[str, Any]]:
    direct = await event_log.get_by_causation(source_event_id)
    proposals: list[dict[str, Any]] = []
    for event in direct:
        if event["type"] != "triage.intent.features_extracted":
            continue
        proposals.extend(
            proposal
            for proposal in await event_log.get_by_causation(event["event_id"])
            if proposal["type"] == "triage.item.proposed"
        )
    return proposals


def test_inbound_recurring_bill_reaches_human_gated_triage_without_env_lookup(
    tmp_path: Path, monkeypatch
) -> None:
    """Production composition proves adapter fact -> NLP -> deterministic gate."""

    monkeypatch.delenv("ADMINME_INSTANCE_DIR", raising=False)
    instance = tmp_path / "instance-inbound-message"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        body = (
            "[fixture:recurring-bill] Your household utility payment is due "
            "August 15 and repeats monthly."
        )
        source_event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_786_100_000_000,
                tenant_id=runtime.settings.instance.tenant_id,
                type="messaging.received",
                schema_version=3,
                occurred_at="2026-08-07T12:00:00Z",
                source_adapter="gmail",
                source_account_id="fixture-account",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="fixture:recurring-bill:source",
                payload={
                    "provider": "gmail",
                    "provider_account_id": "fixture-account",
                    "member_id": MEMBER,
                    "source_channel": "gmail",
                    "native_message_id": "fixture-recurring-bill",
                    "thread_id": "fixture-recurring-bill-thread",
                    "from_identifier": "billing@example.invalid",
                    "to_identifier": "member@example.invalid",
                    "workspace": "personal",
                    "subject": "Monthly utility payment due",
                    "body_text": body,
                    "received_at": "2026-08-07T12:00:00Z",
                    "provider_labels": ["INBOX"],
                    "attachments": [],
                },
            ),
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:noise_filtering",
            source_event_id,
        )
        proposals = client.portal.call(
            _message_proposals_for_source,
            runtime.event_log,
            source_event_id,
        )
        proposals_by_kind = {
            proposal["payload"]["target_kind"]: proposal for proposal in proposals
        }
        task_proposal = proposals_by_kind.get("task")
        recurrence_proposal = proposals_by_kind.get("recurrence")
        assert task_proposal is not None
        assert recurrence_proposal is not None
        client.portal.call(
            runtime.wait_for_projection, "triage", recurrence_proposal["event_id"]
        )

        items = client.get("/api/member/v1/surfaces/triage", headers=AUTH).json()[
            "items"
        ]
        assert {item["target_kind"] for item in items} == {"task", "recurrence"}
        assert all(item["review_state"]["human_gate_required"] for item in items)
        grounded_source = f"Subject: Monthly utility payment due\n\n{body}"
        assert all(item["title"] in grounded_source for item in items)
        assert all(item["source_context"]["state"] == "available" for item in items)
        for item in items:
            context = item["source_context"]
            assert context["source_id"] == "adapter:gmail"
            assert context["channel"] == "gmail"
            assert context["direction"] == "inbound"
            assert context["sender"] == "billing@example.invalid"
            assert context["subject"] == "Monthly utility payment due"
            assert context["evidence"]["exactly_grounded"] is True
            assert context["evidence"]["quote"] in grounded_source
            assert context["policy_rule"] == (
                "candidate_policy_v1:adapter:gmail:review"
            )
        assert (
            runtime.projections.connection("tasks")
            .execute("SELECT COUNT(*) FROM tasks")
            .fetchone()[0]
            == 0
        )
        assert (
            runtime.projections.connection("recurrences")
            .execute("SELECT COUNT(*) FROM recurrences")
            .fetchone()[0]
            == 0
        )
        assert runtime.fixture_adapter.delivered == []


def test_multi_message_legal_context_yields_distinct_explainable_candidates(
    tmp_path: Path,
) -> None:
    """Production composition proves conversation context -> four human gates."""

    instance = tmp_path / "instance-contextual-message"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        common = {
            "provider": "fixture",
            "provider_account_id": "fixture-account",
            "member_id": MEMBER,
            "source_channel": "gmail",
            "thread_id": "fixture-legal-thread",
            "workspace": "personal",
            "subject": "Legal authorization package",
        }

        def source(
            index: int,
            body: str,
            *,
            event_type: str = "messaging.received",
        ) -> EventEnvelope:
            occurred_at = f"2034-02-01T{9 + index:02d}:00:00Z"
            payload = {
                **common,
                "native_message_id": f"fixture-legal-{index}",
                "body_text": body,
                "to_identifier": (
                    "counsel@example.invalid"
                    if event_type == "messaging.sent"
                    else "member@example.invalid"
                ),
            }
            if event_type == "messaging.sent":
                payload.update(
                    {
                        "sent_at": occurred_at,
                        "delivery_status": "sent",
                    }
                )
            else:
                payload.update(
                    {
                        "from_identifier": "counsel@example.invalid",
                        "attachments": [],
                        "received_at": occurred_at,
                        "provider_labels": ["INBOX"],
                        "unread": True,
                        "archived": False,
                        "starred": False,
                        "important": False,
                    }
                )
            return EventEnvelope(
                event_at_ms=2_022_568_400_000 + index,
                tenant_id=runtime.settings.instance.tenant_id,
                type=event_type,
                schema_version=2 if event_type == "messaging.sent" else 3,
                occurred_at=occurred_at,
                source_adapter="fixture-message-adapter",
                source_account_id="fixture-account",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key=f"fixture:contextual-legal:{index}",
                payload=payload,
            )

        prior_sources = [
            source(
                1,
                "Please prepare two authorization forms for the two proceedings.",
            ),
            source(
                2,
                "Please return the compliance form and identification copy.",
            ),
            source(
                3,
                "I will arrange the legal-fee transfer this afternoon.",
                event_type="messaging.sent",
            ),
            source(
                4,
                "Can the signed PDF be used, or is the notarized original required?",
            ),
            source(
                5,
                "The first form is signed; the second form is still outstanding.",
            ),
        ]
        for envelope in prior_sources:
            client.portal.call(runtime.event_log.append, envelope)
        client.portal.call(
            runtime.event_log.append,
            EventEnvelope(
                event_at_ms=2_022_568_400_005,
                tenant_id=runtime.settings.instance.tenant_id,
                type="messaging.received",
                schema_version=3,
                occurred_at="2034-02-01T14:30:00Z",
                source_adapter="fixture-signing-adapter",
                source_account_id="fixture-account",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="fixture:contextual-legal:related",
                payload={
                    **common,
                    "thread_id": "fixture-signing-thread",
                    "native_message_id": "fixture-signing-notice",
                    "from_identifier": "signing-service@example.invalid",
                    "to_identifier": "member@example.invalid",
                    "subject": "Sign legal authorization package",
                    "body_text": "Your authorization package is waiting for signature.",
                    "received_at": "2034-02-01T14:30:00Z",
                    "provider_labels": ["INBOX"],
                    "attachments": [],
                    "unread": True,
                    "archived": False,
                    "starred": False,
                    "important": False,
                },
            ),
        )
        current = source(
            6,
            "[fixture:contextual-legal] Please confirm who will collect the original document.",
        )
        current_source_id = client.portal.call(runtime.events.write, current)
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:noise_filtering",
            current_source_id,
        )
        proposals = client.portal.call(
            _message_proposals_for_source,
            runtime.event_log,
            current_source_id,
        )
        assert len(proposals) == 4, [value["payload"] for value in proposals]
        assert [value["payload"]["target_kind"] for value in proposals] == [
            "task",
            "task",
            "commitment",
            "task",
        ]
        assert all(value["schema_version"] == 4 for value in proposals)
        assert all(value["payload"]["interpretation"] for value in proposals)
        client.portal.call(
            runtime.wait_for_projection,
            "triage",
            proposals[-1]["event_id"],
        )

        surface = client.get("/api/member/v1/surfaces/triage", headers=AUTH).json()
        items = [
            item
            for item in surface["items"]
            if item["source_event_id"] == current_source_id
        ]
        assert len(items) == 4
        assert {item["interpretation"]["compact_summary"] for item in items} == {
            "Prepare two authorization forms",
            "Return compliance form and ID copy",
            "Arrange the legal-fee transfer",
            "Resolve the notarization requirement",
        }
        assert all(
            item["source_context"]["interpretation"]["source_chain"] for item in items
        )
        assert all(
            item["source_context"]["evidence"]["exactly_grounded"] for item in items
        )
        assert all(item["review_state"]["human_gate_required"] for item in items)
        assert runtime.fixture_adapter.delivered == []


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

        language_text = "[fixture:member-intent] The school needs an answer by Friday"
        language = client.post(
            "/api/member/v1/triage/intents",
            headers=AUTH,
            json={
                "text": language_text,
                "source_surface": "today",
                "client_request_id": "intent-language-0001",
            },
        )
        assert language.status_code == 202, language.text
        assert language.json()["state"] == "pending_review"
        assert len(language.json()["proposal_event_ids"]) == 1

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
        [language_row] = [
            item
            for item in surface["items"]
            if item.get("parent_intent_id") == language.json()["intent_id"]
        ]
        assert language_row["target_kind"] == "task"
        assert language_row["title"] == "The school needs an answer by Friday"
        assert language_row["confidence"] == 0.98

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
        assert app.state.runtime.triage_intent_command_locks == {}
        assert app.state.runtime.triage_intent_command_lock_users == {}

        calls = 0

        async def alternating_language_result(*args, **kwargs):
            nonlocal calls
            calls += 1
            target_kind = "task" if calls == 1 else "reminder"
            return SimpleNamespace(
                output={
                    "candidates": [
                        {
                            "target_kind": target_kind,
                            "evidence_quote": "The folder answer is expected Friday",
                            "candidate": {},
                            "confidence": 0.9,
                            "reasons": ["bounded fixture"],
                        }
                    ]
                }
            )

        app.state.runtime.run_skill_fn = alternating_language_result
        recorded_once = client.post(
            "/api/member/v1/triage/intents",
            headers=AUTH,
            json={
                "text": "The folder answer is expected Friday",
                "source_surface": "today",
                "client_request_id": "intent-recorded-feature-0001",
            },
        )
        replayed = client.post(
            "/api/member/v1/triage/intents",
            headers=AUTH,
            json={
                "text": "The folder answer is expected Friday",
                "source_surface": "today",
                "client_request_id": "intent-recorded-feature-0001",
            },
        )
        assert recorded_once.status_code == replayed.status_code == 202
        assert calls == 1
        assert replayed.json()["duplicate"] is True
        assert (
            replayed.json()["feature_event_id"]
            == recorded_once.json()["feature_event_id"]
        )
        assert replayed.json()["proposal_ids"] == recorded_once.json()["proposal_ids"]
        rows = [
            item
            for item in client.get(
                "/api/member/v1/surfaces/triage", headers=AUTH
            ).json()["items"]
            if item.get("parent_intent_id") == recorded_once.json()["intent_id"]
        ]
        assert len(rows) == 1
        assert rows[0]["target_kind"] == "task"

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


def test_member_can_create_canonical_task_directly_in_reminders(tmp_path: Path) -> None:
    instance = tmp_path / "direct-task-instance"
    instance.mkdir()
    app = create_application(_settings(instance))
    direct_body = {
        "request_id": "member-task-create-0001",
        "title": "Call the school office",
        "due_date": "2026-08-12T10:30",
        "available_from": "2026-08-11",
        "energy": "low",
        "effort": "tiny",
        "domain": "school",
        "item_type": "task",
        "micro_script": "Open the contact card",
        "notes": "Explicitly entered by the member.",
    }
    with TestClient(app) as client:
        direct = client.post("/api/member/v1/tasks", headers=AUTH, json=direct_body)
        assert direct.status_code == 201, direct.text
        assert direct.json()["task"]["title"] == "Call the school office"
        assert datetime.fromisoformat(direct.json()["task"]["due_date"]).tzinfo
        assert direct.json()["destination"]["href"].startswith(
            "/member/reminders?item=task%3A"
        )
        reminders = client.get("/api/member/v1/surfaces/reminders", headers=AUTH)
        assert reminders.status_code == 200, reminders.text
        assert (
            reminders.json()["cockpit"]["tasks"][0]["task_id"]
            == (direct.json()["task"]["task_id"])
        )
        direct_retry = client.post(
            "/api/member/v1/tasks", headers=AUTH, json=direct_body
        )
        assert direct_retry.status_code == 201, direct_retry.text
        assert direct_retry.json()["duplicate"] is True
        assert direct_retry.json()["event_id"] == direct.json()["event_id"]
        child_denied = client.post(
            "/api/member/v1/tasks", headers=CHILD_AUTH, json=direct_body
        )
        assert child_denied.status_code == 403
        view_as_denied = client.post(
            "/api/member/v1/tasks",
            headers={**AUTH, "X-AdminMe-View-Member": CHILD},
            json={**direct_body, "request_id": "member-task-view-as-0001"},
        )
        assert view_as_denied.status_code == 403
        blank_denied = client.post(
            "/api/member/v1/tasks",
            headers=AUTH,
            json={
                **direct_body,
                "request_id": "member-task-blank-title-0001",
                "title": "   ",
            },
        )
        assert blank_denied.status_code == 422


def test_existing_object_evidence_cannot_create_duplicate_truth(tmp_path: Path) -> None:
    instance = tmp_path / "existing-object-evidence"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        direct = client.post(
            "/api/member/v1/tasks",
            headers=AUTH,
            json={
                "request_id": "existing-task-create-0001",
                "title": "Call the dentist",
                "domain": "health",
                "item_type": "task",
            },
        )
        assert direct.status_code == 201, direct.text
        intent = client.post(
            "/api/member/v1/triage/intents",
            headers=AUTH,
            json={
                "text": "Call the dentist",
                "source_surface": "today",
                "kind_hint": "task",
                "client_request_id": "existing-task-intent-0001",
            },
        )
        assert intent.status_code == 202, intent.text
        [proposal_id] = intent.json()["proposal_ids"]
        surface = client.get(
            f"/api/member/v1/surfaces/triage?item={proposal_id}", headers=AUTH
        ).json()
        candidate = next(
            item
            for item in [*(surface["items"]), *(surface["recent_items"])]
            if item["proposal_id"] == proposal_id
        )
        assert candidate["decision_disposition"] == "attach_existing"
        assert candidate["existing_object_id"] == direct.json()["task"]["task_id"]
        assert "approve" not in candidate["actions"]
        rejected = client.post(
            f"/api/member/v1/triage/{proposal_id}/approve",
            headers=AUTH,
            json={
                "request_id": "existing-task-approve-0001",
                "expected_last_event_id": candidate["last_event_id"],
            },
        )
        assert rejected.status_code == 409, rejected.text
        tasks = client.get("/api/member/v1/surfaces/reminders", headers=AUTH).json()[
            "cockpit"
        ]["tasks"]
        assert sum(task["title"] == "Call the dentist" for task in tasks) == 1


def test_canonical_truth_wins_race_before_triage_confirmation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    instance = tmp_path / "canonical-race"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        proposed = client.post(
            "/api/openclaw/v1/tools/action",
            headers=OPENCLAW_AUTH,
            json={
                "provenance": {
                    **_provenance(),
                    "correlation_id": "triage:canonical-race",
                },
                "request_id": "canonical-race-proposal-0001",
                "mode": "propose",
                "kind": "task",
                "title": "Call the school registrar",
            },
        )
        assert proposed.status_code == 200, proposed.text
        client.portal.call(
            runtime.wait_for_projection, "triage", proposed.json()["event_id"]
        )
        [item] = client.get("/api/core/v1/triage", headers=AUTH).json()["items"]

        original_write = runtime.events.write
        injected = False

        async def inject_canonical_before_confirmation(envelope):
            nonlocal injected
            if envelope.type == "triage.item.confirmed" and not injected:
                injected = True
                canonical_id = await original_write(
                    EventEnvelope(
                        event_at_ms=1_786_400_100_000,
                        tenant_id=runtime.settings.instance.tenant_id,
                        type="task.created",
                        schema_version=1,
                        occurred_at="2026-08-11T12:01:00Z",
                        source_adapter="fixture",
                        source_account_id=MEMBER,
                        owner_scope=f"private:{MEMBER}",
                        visibility_scope=f"private:{MEMBER}",
                        sensitivity="normal",
                        idempotency_key="fixture:canonical-race:task",
                        payload={
                            "task_id": "task-canonical-race",
                            "title": "Call the school registrar",
                            "owner_member_id": MEMBER,
                        },
                    )
                )
                await runtime.wait_for_projection("tasks", canonical_id)
                await runtime.wait_for_projection("triage", canonical_id)
            return await original_write(envelope)

        monkeypatch.setattr(
            runtime.events, "write", inject_canonical_before_confirmation
        )
        rejected = client.post(
            f"/api/member/v1/triage/{item['proposal_id']}/approve",
            headers=AUTH,
            json={
                "request_id": "canonical-race-approve-0001",
                "expected_last_event_id": item["last_event_id"],
            },
        )
        assert rejected.status_code == 409, rejected.text
        tasks = client.get("/api/member/v1/surfaces/reminders", headers=AUTH).json()[
            "cockpit"
        ]["tasks"]
        assert [task["task_id"] for task in tasks] == ["task-canonical-race"]
        surface = client.get(
            f"/api/member/v1/surfaces/triage?item={item['proposal_id']}",
            headers=AUTH,
        ).json()
        raced = next(
            candidate
            for candidate in [*surface["items"], *surface["recent_items"]]
            if candidate["proposal_id"] == item["proposal_id"]
        )
        assert raced["decision_disposition"] == "attach_existing"
        assert raced["confirmation_event_id"] is None


def test_commitment_evidence_cannot_bypass_triage_duplicate_guard(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "commitment-evidence"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        proposed_ids: list[str] = []
        for index in (1, 2):
            commitment_id = f"commitment-evidence-{index}"
            proposed_id = client.portal.call(
                runtime.events.write,
                EventEnvelope(
                    event_at_ms=1_786_400_000_000 + index,
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="commitment.proposed",
                    schema_version=1,
                    occurred_at=f"2026-08-11T12:00:0{index}Z",
                    source_adapter="fixture",
                    source_account_id="fixture-account",
                    owner_scope=f"private:{MEMBER}",
                    visibility_scope=f"private:{MEMBER}",
                    sensitivity="normal",
                    causation_id="shared-commitment-source",
                    idempotency_key=f"fixture:{commitment_id}:proposed",
                    payload={
                        "commitment_id": commitment_id,
                        "kind": "reply",
                        "owed_by_member_id": MEMBER,
                        "owed_to_party_id": "party-recipient",
                        "text_summary": "Reply with the signed school form",
                        "urgency": "this_week",
                        "confidence": 0.9,
                        "strength": "confident",
                        "source_interaction_id": "shared-commitment-source",
                    },
                ),
            )
            proposed_ids.append(proposed_id)
            client.portal.call(runtime.wait_for_projection, "commitments", proposed_id)
            client.portal.call(runtime.wait_for_projection, "triage", proposed_id)

        surface = client.get(
            "/api/member/v1/surfaces/triage?item=commitment-evidence-2",
            headers=AUTH,
        ).json()
        evidence = next(
            item
            for item in [*surface["items"], *surface["recent_items"]]
            if item["proposal_id"] == "commitment-evidence-2"
        )
        assert evidence["source_kind"] == "commitment_proposal"
        assert evidence["decision_disposition"] == "evidence_only"
        assert "approve" not in evidence["actions"]

        rejected = client.post(
            "/api/member/v1/triage/commitment-evidence-2/approve",
            headers=AUTH,
            json={
                "request_id": "commitment-evidence-approve-0001",
                "expected_last_event_id": evidence["last_event_id"],
            },
        )
        assert rejected.status_code == 409, rejected.text
        direct_rejected = client.post(
            "/api/comms/v1/commitments/commitment-evidence-2/approve",
            headers=AUTH,
            json={
                "request_id": "commitment-evidence-direct-approve-0001",
                "expected_last_event_id": evidence["last_event_id"],
                "send_acknowledgement": False,
            },
        )
        assert direct_rejected.status_code == 409, direct_rejected.text
        confirmations = client.portal.call(_events, runtime, "commitment.confirmed")
        assert all(
            event["payload"]["commitment_id"] != "commitment-evidence-2"
            for event in confirmations
        )


def test_post_capture_governance_conflict_replays_one_recorded_interpretation(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "post-capture-conflict"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        calls = 0

        async def recorded_language_result(*args, **kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                output={
                    "candidates": [
                        {
                            "target_kind": "task",
                            "evidence_quote": "The folder answer is expected Friday",
                            "candidate": {},
                            "confidence": 0.9,
                            "reasons": ["bounded fixture"],
                        },
                        {
                            "target_kind": "reminder",
                            "evidence_quote": "The folder answer is expected Friday",
                            "candidate": {},
                            "confidence": 0.9,
                            "reasons": ["bounded fixture"],
                        },
                    ]
                }
            )

        runtime.run_skill_fn = recorded_language_result
        original_check = runtime.guarded_write.check
        proposal_checks = 0

        async def deny_proposal(session, action, payload):
            nonlocal proposal_checks
            if action == "triage.item.proposed":
                proposal_checks += 1
                if proposal_checks in {2, 4}:
                    return GuardedWriteResult(
                        pass_=False,
                        layer_failed="governance",
                        reason="fixture_post_capture_denial",
                        correlation_id=session.correlation_id,
                    )
            return await original_check(session, action, payload)

        runtime.guarded_write.check = deny_proposal
        body = {
            "text": "The folder answer is expected Friday",
            "source_surface": "today",
            "client_request_id": "post-capture-conflict-0001",
        }
        first = client.post("/api/member/v1/triage/intents", headers=AUTH, json=body)
        second = client.post("/api/member/v1/triage/intents", headers=AUTH, json=body)
        assert first.status_code == second.status_code == 409
        first_detail = first.json()["detail"]
        second_detail = second.json()["detail"]
        assert first_detail["capture_state"] == "intent_and_features_recorded"
        assert second_detail["recovery"] == first_detail["recovery"]
        assert first_detail["recovery"]["href"] == (
            "/member/triage?view=needs_interpretation"
            f"&item={first_detail['recovery']['intent_id']}"
        )
        assert calls == 1
        events = client.portal.call(
            _events,
            runtime,
            "triage.intent.submitted",
            "triage.intent.features_extracted",
            "triage.item.proposed",
        )
        assert [event["type"] for event in events] == [
            "triage.intent.submitted",
            "triage.intent.features_extracted",
        ]
        pending = client.get("/api/member/v1/surfaces/triage", headers=AUTH).json()[
            "items"
        ]
        assert any(
            item["source_kind"] == "member_intent"
            and item["state"] == "pending_interpretation"
            and item["candidate"]["exact_text"] == body["text"]
            for item in pending
        )

        runtime.guarded_write.check = original_check
        recovered = client.post(
            "/api/member/v1/triage/intents", headers=AUTH, json=body
        )
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["duplicate"] is True
        assert calls == 1
        proposal_ids = recovered.json()["proposal_ids"]
        assert len(proposal_ids) == 2
        surface = client.get("/api/member/v1/surfaces/triage", headers=AUTH).json()
        recovered_items = [
            item for item in surface["items"] if item["proposal_id"] in proposal_ids
        ]
        assert {item["target_kind"] for item in recovered_items} == {
            "task",
            "reminder",
        }
        assert not any(
            item["source_kind"] == "member_intent"
            and item["candidate"].get("exact_text") == body["text"]
            for item in surface["items"]
        )


def test_proposal_requires_member_triage_before_official_task(
    tmp_path: Path, monkeypatch: Any
) -> None:
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
        waited_destinations: list[tuple[str, str]] = []
        original_wait_for_projection = runtime.wait_for_projection

        async def tracked_wait_for_projection(
            projection_name: str, event_id: str
        ) -> None:
            waited_destinations.append((projection_name, event_id))
            await original_wait_for_projection(projection_name, event_id)

        monkeypatch.setattr(runtime, "wait_for_projection", tracked_wait_for_projection)
        approved = client.post(
            f"/api/member/v1/triage/{item['proposal_id']}/approve",
            headers=AUTH,
            json=approval_body,
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == "confirmed"
        assert len(approved.json()["materialized_event_ids"]) == 1
        assert (
            "tasks",
            approved.json()["materialized_event_ids"][0],
        ) in waited_destinations
        assert approved.json()["destination"] == {
            "surface": "reminders",
            "href": (
                f"/member/reminders?item=task%3Atriage_task_{item['proposal_id']}"
            ),
            "record_kind": "task",
            "record_id": f"triage_task_{item['proposal_id']}",
            "label": "Reminders",
            "state": "materialized",
        }
        triage_history = client.get(
            "/api/member/v1/surfaces/triage", headers=AUTH
        ).json()["recent_items"]
        [recent_decision] = [
            recent
            for recent in triage_history
            if recent["proposal_id"] == item["proposal_id"]
        ]
        assert recent_decision["destination"] == approved.json()["destination"]
        reminders = client.get("/api/member/v1/surfaces/reminders", headers=AUTH)
        assert reminders.status_code == 200, reminders.text
        [routed_task] = reminders.json()["cockpit"]["tasks"]
        assert routed_task["task_id"] == f"triage_task_{item['proposal_id']}"
        assert routed_task["title"] == "Put the school form on the correct list"
        assert routed_task["record_kind"] == "task"
        assert routed_task["bucket"] == "now"
        edited = client.patch(
            f"/api/member/v1/tasks/{routed_task['task_id']}",
            headers=AUTH,
            json={
                "request_id": "task-edit-from-reminders-0001",
                "expected_last_event_id": routed_task["last_event_id"],
                "energy": "low",
                "effort": "small",
                "item_type": "task",
                "micro_script": "Open the form and identify the required fields",
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["task"]["energy"] == "low"
        assert edited.json()["task"]["effort"] == "small"
        refreshed_task = client.get(
            "/api/member/v1/surfaces/reminders", headers=AUTH
        ).json()["cockpit"]["tasks"][0]
        assert refreshed_task["micro_script"].startswith("Open the form")
        today = client.get("/api/member/v1/surfaces/today", headers=AUTH).json()
        assert any(
            move["id"] == routed_task["task_id"] and move["kind"] == "task"
            for move in today["brief"]["plan"]
        )
        scoreboard = client.get(
            "/api/member/v1/surfaces/scoreboard", headers=AUTH
        ).json()
        assert any(
            move["task_id"] == routed_task["task_id"]
            for move in scoreboard["scoreboard"]["next_steps"]
        )
        calendar = client.get("/api/member/v1/surfaces/calendar", headers=AUTH).json()
        assert all(
            event.get("linked_item_id") != routed_task["task_id"]
            for event in calendar["events"]
        )
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
                source_adapter="calendaring-apple-calendar",
                source_account_id=APPLE_CALENDAR_ACCOUNT,
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                actor_identity=MEMBER,
                payload={
                    "member_id": MEMBER,
                    "provider": "apple_calendar",
                    "provider_account_id": APPLE_CALENDAR_ACCOUNT,
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
                        "provider_account_id": APPLE_CALENDAR_ACCOUNT,
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
                "provider_account_id": APPLE_CALENDAR_ACCOUNT,
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
        assert enriched_item["review_state"]["state"] == "ready"
        retried_enrichment = client.post(
            f"/api/member/v1/triage/{calendar_candidate['proposal_id']}/enrich",
            headers=AUTH,
            json=enrichment_body,
        )
        assert retried_enrichment.status_code == 200, retried_enrichment.text
        assert retried_enrichment.json()["duplicate"] is True
        assert retried_enrichment.json()["event_id"] == enriched.json()["event_id"]
        assert retried_enrichment.json()["triage"]["review_state"]["state"] == "ready"
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
        pending_receipt = next(
            item
            for item in client.get(
                "/api/member/v1/surfaces/triage", headers=AUTH
            ).json()["recent_items"]
            if item["proposal_id"] == calendar_candidate["proposal_id"]
        )
        assert pending_receipt["state"] == "approved"
        assert (
            pending_receipt["domain_approval_event_id"]
            == (calendar_approved.json()["confirmation_event_id"])
        )
        assert pending_receipt["materialization_event_id"] == pending_action["event_id"]
        assert pending_receipt["resolution_reason"] == "approved_awaiting_openclaw"
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
        dismissed_receipt = next(
            item
            for item in client.get(
                "/api/member/v1/surfaces/triage", headers=AUTH
            ).json()["recent_items"]
            if item["proposal_id"] == generic_item["proposal_id"]
        )
        assert dismissed_receipt["dismissal_event_id"] == dismissed.json()["event_id"]
        assert dismissed_receipt["dismissal_request_id"] == dismissal_body["request_id"]
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
        unsupported_google_priority = client.post(
            f"/api/member/v1/triage/{task_list_item['proposal_id']}/enrich",
            headers=AUTH,
            json={
                "request_id": "triage-google-priority-0001",
                "expected_last_event_id": task_list_item["last_event_id"],
                "candidate_updates": {"priority": 1},
                "route": task_list_item["route"],
            },
        )
        assert unsupported_google_priority.status_code == 422
        assert (
            "unsupported list_item enrichment fields"
            in (unsupported_google_priority.json()["detail"])
        )
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
        assert google_task_action["payload"]["command_payload"]["due"] == ("2026-08-07")
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
