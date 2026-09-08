"""Production-composition proof for reconciliation and Triage admission."""

from __future__ import annotations

import json
from itertools import count
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.reconciliation import issuer_reference, reference_digest
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"r" * 32
A = "member-reconciliation-a"
B = "member-reconciliation-b"
_CLOCK = count(1_788_278_400_000, 1_000)


def _settings(instance_dir: Path, *, admission: bool) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_roles={A: "principal", B: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "Fixture household",
            "members": [
                {"member_id": A, "label": "Fixture adult A", "role": "principal"},
                {"member_id": B, "label": "Fixture adult B", "role": "principal"},
            ],
            "memberships": [],
            "relationships": [],
            "parties": [],
        },
        adapter_config={"adapter_supervisor_port": 0, "adapters": []},
        reconciliation_admission_enabled=admission,
    )


async def _children(
    runtime: Any, causation_id: str, event_type: str
) -> list[dict[str, Any]]:
    return [
        event
        for event in await runtime.event_log.get_by_causation(causation_id)
        if event["type"] == event_type
    ]


async def _events(runtime: Any, event_type: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    async for event in runtime.event_log.read_since(types=[event_type]):
        values.append(event)
    return values


def _write_message(
    client: TestClient,
    runtime: Any,
    *,
    member: str,
    key: str,
    subject: str,
    body: str,
    direction: str = "received",
    sender: str = "correspondent@example.invalid",
    recipient: str = "member@example.invalid",
    provider: str = "gmail",
    source_adapter: str = "fixture-mail",
    source_channel: str = "email",
    provider_account_id: str = "fixture-account",
    thread_id: str | None = None,
    labels: list[str] | None = None,
    important: bool = False,
) -> str:
    now_ms = next(_CLOCK)
    occurred_at = "2026-09-01T12:00:00Z"
    common: dict[str, Any] = {
        "source_channel": source_channel,
        "to_identifier": recipient,
        "thread_id": thread_id,
        "subject": subject,
        "body_text": body,
        "member_id": member,
        "provider": provider,
        "provider_account_id": provider_account_id,
        "workspace": "personal",
        "native_message_id": f"native-{key}",
    }
    if direction == "received":
        payload = {
            **common,
            "from_identifier": sender,
            "received_at": occurred_at,
            "attachments": [],
            "provider_labels": labels or [],
            "important": important,
        }
        event_type = "messaging.received"
    else:
        payload = {
            key: value for key, value in common.items() if key != "native_message_id"
        } | {
            "sent_at": occurred_at,
            "delivery_status": "sent",
            "native_message_id": f"native-{key}",
        }
        event_type = "messaging.sent"
    event_id = client.portal.call(
        runtime.events.write,
        EventEnvelope(
            event_at_ms=now_ms,
            tenant_id=runtime.settings.instance.tenant_id,
            type=event_type,
            schema_version=3,
            occurred_at=occurred_at,
            source_adapter=source_adapter,
            source_account_id=provider_account_id,
            owner_scope=f"private:{member}",
            visibility_scope=f"private:{member}",
            sensitivity="sensitive",
            idempotency_key=f"reconciliation-source:{key}",
            payload=payload,
        ),
    )
    client.portal.call(
        runtime.wait_for_subscriber,
        "triage-reconciliation:admission",
        event_id,
    )
    return str(event_id)


def _write_intent(client: TestClient, runtime: Any, *, key: str, text: str) -> str:
    event_id = client.portal.call(
        runtime.events.write,
        EventEnvelope(
            event_at_ms=next(_CLOCK),
            tenant_id=runtime.settings.instance.tenant_id,
            type="triage.intent.submitted",
            schema_version=1,
            occurred_at="2026-09-01T12:00:00Z",
            source_adapter="member-surface",
            source_account_id=A,
            owner_scope=f"private:{A}",
            visibility_scope=f"private:{A}",
            sensitivity="normal",
            actor_identity=A,
            idempotency_key=f"reconciliation-intent:{key}",
            payload={
                "intent_id": f"intent-{key}",
                "member_id": A,
                "text": text,
                "source_surface": "triage",
                "kind_hint": "task",
                "source_event_id": None,
                "submitted_at": "2026-09-01T12:00:00Z",
                "client_request_id": f"request-{key}",
            },
        ),
    )
    client.portal.call(
        runtime.wait_for_subscriber,
        "triage-reconciliation:admission",
        event_id,
    )
    return str(event_id)


def _write_proposal(
    client: TestClient,
    runtime: Any,
    *,
    proposal_id: str,
    source_event_id: str,
    title: str,
    target_kind: str = "task",
    candidate: dict[str, Any] | None = None,
    confidence: float = 0.5,
    member: str = A,
) -> dict[str, Any]:
    proposal_event_id = client.portal.call(
        runtime.events.write,
        EventEnvelope(
            event_at_ms=next(_CLOCK),
            tenant_id=runtime.settings.instance.tenant_id,
            type="triage.item.proposed",
            schema_version=1,
            occurred_at="2026-09-01T12:00:00Z",
            source_adapter="deterministic-candidate-fixture",
            source_account_id="pipeline",
            owner_scope=f"private:{member}",
            visibility_scope=f"private:{member}",
            sensitivity="sensitive",
            idempotency_key=f"reconciliation-proposal:{proposal_id}",
            payload={
                "proposal_id": proposal_id,
                "target_kind": target_kind,
                "owner_member_id": member,
                "title": title,
                "candidate": candidate or {},
                "route": {"member_id": member},
                "source_event_id": source_event_id,
                "confidence": confidence,
                "reasons": ["deterministic_acceptance_fixture"],
            },
        ),
    )
    client.portal.call(
        runtime.wait_for_subscriber,
        "triage-reconciliation:admission",
        proposal_event_id,
    )
    [decision] = client.portal.call(
        _children,
        runtime,
        proposal_event_id,
        "triage.reconciliation.decided",
    )
    client.portal.call(runtime.wait_for_projection, "triage", decision["event_id"])
    client.portal.call(
        runtime.wait_for_projection, "context_fabric", decision["event_id"]
    )
    return decision


def _write_shared_state(
    client: TestClient,
    runtime: Any,
    *,
    key: str,
    reference_kind: str,
    reference_digest_value: str,
    current_state: str,
    target_member: str = A,
) -> list[dict[str, Any]]:
    event_id = client.portal.call(
        runtime.events.write,
        EventEnvelope(
            event_at_ms=next(_CLOCK),
            tenant_id=runtime.settings.instance.tenant_id,
            type="context.reconciliation.typed_fact_shared",
            schema_version=1,
            occurred_at="2026-09-01T12:00:00Z",
            source_adapter="member-authorized-typed-share",
            source_account_id=B,
            owner_scope="shared:household",
            visibility_scope="shared:household",
            sensitivity="sensitive",
            actor_identity=B,
            idempotency_key=f"reconciliation-shared:{key}",
            payload={
                "fact_id": f"fact-{key}",
                "source_member_id": B,
                "shared_with_member_ids": [target_member],
                "shared_by_member_id": B,
                "share_grant_id": f"grant-{key}",
                "source_reference_digest": "d" * 64,
                "fact_kind": "account_state",
                "references": [
                    {"kind": reference_kind, "digest": reference_digest_value}
                ],
                "current_state": current_state,
                "occurred_at": "2026-09-01T12:00:00Z",
                "expires_at": None,
                "raw_body_included": False,
                "owner_transfer": False,
                "action_authority": False,
            },
        ),
    )
    client.portal.call(
        runtime.wait_for_subscriber,
        "triage-reconciliation:admission",
        event_id,
    )
    decisions = client.portal.call(
        _children,
        runtime,
        event_id,
        "triage.reconciliation.decided",
    )
    for decision in decisions:
        client.portal.call(runtime.wait_for_projection, "triage", decision["event_id"])
        client.portal.call(
            runtime.wait_for_projection, "context_fabric", decision["event_id"]
        )
    client.portal.call(runtime.wait_for_projection, "context_fabric", event_id)
    return decisions


def _triage_row(runtime: Any, proposal_id: str) -> dict[str, Any]:
    row = (
        runtime.projections.connection("triage")
        .execute("SELECT * FROM triage_items WHERE proposal_id=?", (proposal_id,))
        .fetchone()
    )
    assert row is not None
    return dict(row)


def _latest_decision(runtime: Any, proposal_id: str) -> dict[str, Any]:
    rows = [
        event
        for event in runtime._acceptance_decisions
        if event["payload"]["proposal_id"] == proposal_id
    ]
    assert rows
    return rows[-1]


def _refresh_decisions(client: TestClient, runtime: Any) -> None:
    runtime._acceptance_decisions = client.portal.call(
        _events, runtime, "triage.reconciliation.decided"
    )


def test_reconciliation_remains_shadow_only_by_default(tmp_path: Path) -> None:
    instance = tmp_path / "instance-shadow"
    instance.mkdir()
    with TestClient(create_application(_settings(instance, admission=False))) as client:
        runtime = client.app.state.runtime
        source_id = _write_message(
            client,
            runtime,
            member=A,
            key="shadow",
            subject="Review fixture request",
            body="Please review the fixture request.",
        )
        decision = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-shadow",
            source_event_id=source_id,
            title="Review fixture request",
        )

        assert decision["payload"]["admission_active"] is False
        row = _triage_row(runtime, "proposal-shadow")
        assert row["state"] == "pending_review"
        assert row["reconciliation_active"] == 0
        assert row["admission_decision"] == "admit"
        assert row["reconciliation_id"] is None
        context = runtime.projections.connection("context_fabric")
        assert tuple(
            context.execute(
                "SELECT decision, admission_active FROM reconciliation_decisions "
                "WHERE proposal_id='proposal-shadow'"
            ).fetchone()
        ) == ("admit", 0)


def test_cross_source_scenarios_priority_privacy_corrections_and_replay(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance-active-lab"
    instance.mkdir()
    with TestClient(create_application(_settings(instance, admission=True))) as client:
        runtime = client.app.state.runtime
        tenant_id = runtime.settings.instance.tenant_id

        mortgage_due = _write_message(
            client,
            runtime,
            member=A,
            key="mortgage-due",
            subject="Mortgage invoice MTG-2048 payment due",
            body="Mortgage invoice MTG-2048 is due this week.",
            sender="billing@example.invalid",
        )
        mortgage_primary = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-mortgage-primary",
            source_event_id=mortgage_due,
            title="Pay mortgage invoice MTG-2048",
            candidate={"due_at": "2026-09-08T21:00:00Z"},
        )
        mortgage_budget = _write_message(
            client,
            runtime,
            member=A,
            key="mortgage-budget",
            subject="Budget view for mortgage invoice MTG-2048",
            body="Budget notification for mortgage invoice MTG-2048.",
            sender="updates@example.invalid",
        )
        mortgage_attachment = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-mortgage-budget",
            source_event_id=mortgage_budget,
            title="Review mortgage budget invoice MTG-2048",
        )
        assert mortgage_primary["payload"]["decision"] == "admit"
        # A different issuer's notification does not establish invoice identity.
        assert mortgage_attachment["payload"]["decision"] == "context_only"
        assert (
            mortgage_attachment["payload"]["matter_id"]
            != mortgage_primary["payload"]["matter_id"]
        )

        _write_message(
            client,
            runtime,
            member=B,
            key="mortgage-private-payment",
            subject="Mortgage invoice MTG-2048 payment received",
            body="Payment received for mortgage invoice MTG-2048.",
            sender="billing@example.invalid",
            provider_account_id="fixture-account-b",
        )
        assert _triage_row(runtime, "proposal-mortgage-primary")["state"] == (
            "pending_review"
        )

        mortgage_digest = reference_digest(
            tenant_id,
            "exact_payment_reference",
            issuer_reference(
                client.portal.call(runtime.event_log.get, mortgage_due), "MTG-2048"
            ),
        )
        shared_satisfaction = _write_shared_state(
            client,
            runtime,
            key="mortgage-satisfied",
            reference_kind="payment_reference",
            reference_digest_value=mortgage_digest,
            current_state="satisfied",
        )
        _refresh_decisions(client, runtime)
        mortgage_latest = _latest_decision(runtime, "proposal-mortgage-primary")
        assert mortgage_latest["payload"]["decision"] == "already_satisfied"
        assert mortgage_latest["payload"]["decision_revision"] == 2
        assert (
            mortgage_latest["payload"]["supersedes_reconciliation_event_id"]
            == (mortgage_primary["event_id"])
        )
        assert any(
            decision["payload"]["proposal_id"] == "proposal-mortgage-primary"
            for decision in shared_satisfaction
        )
        assert _triage_row(runtime, "proposal-mortgage-primary")["state"] == "dismissed"

        healthcare_decisions: list[dict[str, Any]] = []
        for key, provider, adapter, channel, account in (
            (
                "health-app",
                "openclaw_channel",
                "fixture-health-app",
                "app",
                "health-app",
            ),
            ("health-sms", "twilio_sms", "fixture-health-sms", "sms", "health-sms"),
            ("health-email", "gmail", "fixture-health-email", "email", "health-email"),
        ):
            source_id = _write_message(
                client,
                runtime,
                member=A,
                key=key,
                subject="Appointment APT-8842 follow-up",
                body="Appointment APT-8842 has a follow-up form.",
                sender="care-team@example.invalid",
                provider=provider,
                source_adapter=adapter,
                source_channel=channel,
                provider_account_id=account,
            )
            healthcare_decisions.append(
                _write_proposal(
                    client,
                    runtime,
                    proposal_id=f"proposal-{key}",
                    source_event_id=source_id,
                    title="Complete appointment APT-8842 follow-up",
                )
            )
        assert [item["payload"]["decision"] for item in healthcare_decisions] == [
            "admit",
            "attach_evidence",
            "attach_evidence",
        ]
        assert len({item["payload"]["matter_id"] for item in healthcare_decisions}) == 1
        assert [
            _triage_row(runtime, f"proposal-{key}")["state"]
            for key in ("health-app", "health-sms", "health-email")
        ] == ["pending_review", "dismissed", "dismissed"]

        prior_human = _write_message(
            client,
            runtime,
            member=A,
            key="known-human-prior",
            subject="Prior fixture correspondence",
            body="Thanks for the prior fixture update.",
            direction="sent",
            recipient="known-human@example.invalid",
        )
        assert prior_human
        known_source = _write_message(
            client,
            runtime,
            member=A,
            key="known-human-request",
            subject="Fixture form request",
            body="Could you please send the fixture form?",
            sender="known-human@example.invalid",
        )
        known_decision = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-known-human",
            source_event_id=known_source,
            title="Send the fixture form",
            confidence=0.1,
        )
        deadline_source = _write_message(
            client,
            runtime,
            member=A,
            key="provider-deadline",
            subject="Provider deadline notice",
            body="Provider deadline notice.",
            sender="billing@example.invalid",
            important=True,
        )
        deadline_decision = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-provider-deadline",
            source_event_id=deadline_source,
            title="Review provider deadline notice",
            candidate={"deadline_at": "2026-09-02T12:00:00Z"},
            confidence=1.0,
        )
        promise_source = _write_message(
            client,
            runtime,
            member=A,
            key="member-promise",
            subject="Fixture form",
            body="I will send the fixture form tomorrow.",
            direction="sent",
            recipient="known-human@example.invalid",
        )
        promise_decision = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-member-promise",
            source_event_id=promise_source,
            title="Send the promised fixture form",
            target_kind="commitment",
        )
        intent_source = _write_intent(
            client, runtime, key="direct", text="Review the fixture checklist"
        )
        direct_decision = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-direct-instruction",
            source_event_id=intent_source,
            title="Review the fixture checklist",
        )
        assert [
            direct_decision["payload"]["priority_rank"],
            promise_decision["payload"]["priority_rank"],
            known_decision["payload"]["priority_rank"],
            deadline_decision["payload"]["priority_rank"],
        ] == [
            0,
            10,
            20,
            60,
        ]  # An unsupported candidate date is not a verified deadline.
        assert known_decision["payload"]["priority_basis"] == [
            "known_human_actionable_request"
        ]
        assert prior_human in known_decision["payload"]["evidence_source_event_ids"]
        assert deadline_decision["payload"]["priority_basis"] == [
            "standard_actionable_candidate"
        ]
        assert promise_decision["payload"]["member_authored_text_digest"]
        assert len(promise_decision["payload"]["member_authored_text_digest"]) == 64
        assert (
            _triage_row(runtime, "proposal-member-promise")[
                "reconciliation_authored_text_digest"
            ]
            == promise_decision["payload"]["member_authored_text_digest"]
        )
        assert all(
            "provider_urgency_and_model_confidence_excluded_from_priority"
            in item["payload"]["reason_codes"]
            for item in (known_decision, deadline_decision)
        )

        security_source = _write_message(
            client,
            runtime,
            member=A,
            key="security-alert",
            subject="New sign-in detected",
            body="Security alert for a new device sign-in.",
            sender="security-alerts@example.invalid",
        )
        security_decision = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-security-alert",
            source_event_id=security_source,
            title="Review security alert",
        )
        assert security_decision["payload"]["decision"] == "alert_only"
        assert _triage_row(runtime, "proposal-security-alert")["state"] == "dismissed"

        low_signal: list[dict[str, Any]] = []
        for key, subject, labels, target_kind in (
            ("promotion", "Fixture promotion", ["CATEGORY_PROMOTIONS"], "task"),
            ("receipt", "Fixture receipt", ["CATEGORY_UPDATES"], "task"),
            ("spam", "Fixture bulk message", ["SPAM"], "task"),
            ("update", "Fixture provider update", ["CATEGORY_UPDATES"], "task"),
            (
                "incidental-party",
                "Incidental person mention",
                ["CATEGORY_UPDATES"],
                "party_candidate",
            ),
        ):
            source_id = _write_message(
                client,
                runtime,
                member=A,
                key=key,
                subject=subject,
                body=subject,
                sender="updates@example.invalid",
                labels=labels,
            )
            low_signal.append(
                _write_proposal(
                    client,
                    runtime,
                    proposal_id=f"proposal-{key}",
                    source_event_id=source_id,
                    title=subject,
                    target_kind=target_kind,
                    confidence=1.0,
                )
            )
        assert {item["payload"]["decision"] for item in low_signal} == {"context_only"}
        assert all(
            _triage_row(runtime, str(item["payload"]["proposal_id"]))["state"]
            == "dismissed"
            for item in low_signal
        )

        valued_party_source = _write_message(
            client,
            runtime,
            member=A,
            key="valued-party",
            subject="Fixture follow-up",
            body="Thank you for the fixture follow-up.",
            sender="known-human@example.invalid",
        )
        valued_party = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-valued-party",
            source_event_id=valued_party_source,
            title="Review known correspondent candidate",
            target_kind="party_candidate",
        )
        # People context remains available without inventing personal work.
        assert valued_party["payload"]["decision"] == "context_only"

        weak_first_source = _write_message(
            client,
            runtime,
            member=A,
            key="weak-first",
            subject="School enrollment packet submit",
            body="School enrollment packet submit.",
        )
        weak_first = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-weak-first",
            source_event_id=weak_first_source,
            title="School enrollment packet submit",
        )
        weak_second_source = _write_message(
            client,
            runtime,
            member=A,
            key="weak-second",
            subject="Submit packet school enrollment",
            body="Submit packet school enrollment.",
        )
        weak_second = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-weak-second",
            source_event_id=weak_second_source,
            title="Submit packet school enrollment",
        )
        assert weak_first["payload"]["decision"] == "admit"
        assert weak_second["payload"]["decision"] == "admit"
        weak_relationships = [
            item
            for item in weak_second["payload"]["relationships"]
            if item["relationship_type"] == "weak_review_suggestion"
        ]
        assert weak_relationships
        assert all(not item["automatic_effect"] for item in weak_relationships)
        assert weak_first["payload"]["matter_id"] != weak_second["payload"]["matter_id"]

        conflict_decisions: list[dict[str, Any]] = []
        for key, due in (
            ("conflict-first", "2026-09-12T12:00:00Z"),
            ("conflict-second", "2026-09-13T12:00:00Z"),
        ):
            source_id = _write_message(
                client,
                runtime,
                member=A,
                key=key,
                subject="Account CNF-3300 action",
                body="Account CNF-3300 action.",
            )
            conflict_decisions.append(
                _write_proposal(
                    client,
                    runtime,
                    proposal_id=f"proposal-{key}",
                    source_event_id=source_id,
                    title="Complete invoice CNF-3300 action",
                    candidate={"due_at": due},
                )
            )
        assert [item["payload"]["decision"] for item in conflict_decisions] == [
            "admit",
            "conflict_review",
        ]
        assert any(
            item["relationship_type"] == "contradiction"
            for item in conflict_decisions[1]["payload"]["relationships"]
        )

        correction_source = _write_message(
            client,
            runtime,
            member=A,
            key="correction-source",
            subject="Invoice COR-9900 action due",
            body="Invoice COR-9900 action due.",
        )
        correction_initial = _write_proposal(
            client,
            runtime,
            proposal_id="proposal-correction",
            source_event_id=correction_source,
            title="Complete invoice COR-9900 action",
        )
        correction_digest = reference_digest(
            tenant_id,
            "exact_payment_reference",
            issuer_reference(
                client.portal.call(runtime.event_log.get, correction_source), "COR-9900"
            ),
        )
        _write_shared_state(
            client,
            runtime,
            key="correction-satisfied",
            reference_kind="payment_reference",
            reference_digest_value=correction_digest,
            current_state="satisfied",
        )
        _write_shared_state(
            client,
            runtime,
            key="correction-superseded",
            reference_kind="payment_reference",
            reference_digest_value=correction_digest,
            current_state="superseded",
        )
        _refresh_decisions(client, runtime)
        correction_latest = _latest_decision(runtime, "proposal-correction")
        assert correction_initial["payload"]["decision_revision"] == 1
        assert correction_latest["payload"]["decision"] == "superseded"
        assert correction_latest["payload"]["decision_revision"] == 3
        assert correction_latest["payload"]["supersedes_reconciliation_event_id"]

        triage_before = [
            tuple(row)
            for row in runtime.projections.connection("triage").execute(
                """SELECT proposal_id, state, admission_decision,
                          reconciliation_revision, context_id, priority_rank,
                          priority_basis_json, reconciliation_evidence_json
                     FROM triage_items
                    ORDER BY proposal_id"""
            )
        ]
        context_before = {
            "decisions": [
                tuple(row)
                for row in runtime.projections.connection("context_fabric").execute(
                    """SELECT reconciliation_id, proposal_id, decision,
                              decision_revision, matter_id, policy_json,
                              source_coordinate_json, freshness
                         FROM reconciliation_decisions
                        ORDER BY reconciliation_id"""
                )
            ],
            "relationships": [
                tuple(row)
                for row in runtime.projections.connection("context_fabric").execute(
                    """SELECT relationship_id, reconciliation_id,
                              relationship_type, authority, automatic_effect,
                              reason_codes_json
                         FROM reconciliation_relationships
                        ORDER BY relationship_id, reconciliation_id"""
                )
            ],
            "shared": [
                tuple(row)
                for row in runtime.projections.connection("context_fabric").execute(
                    """SELECT fact_id, source_member_id,
                              shared_with_member_ids_json, references_json,
                              current_state
                         FROM shared_reconciliation_facts
                        ORDER BY fact_id"""
                )
            ],
        }
        shared_serialized = json.dumps(context_before["shared"])
        assert "MTG-2048" not in shared_serialized
        assert "payment received" not in shared_serialized.casefold()

        client.portal.call(runtime.projections.rebuild, "triage")
        client.portal.call(runtime.projections.rebuild, "context_fabric")
        triage_after = [
            tuple(row)
            for row in runtime.projections.connection("triage").execute(
                """SELECT proposal_id, state, admission_decision,
                          reconciliation_revision, context_id, priority_rank,
                          priority_basis_json, reconciliation_evidence_json
                     FROM triage_items
                    ORDER BY proposal_id"""
            )
        ]
        context_after = {
            "decisions": [
                tuple(row)
                for row in runtime.projections.connection("context_fabric").execute(
                    """SELECT reconciliation_id, proposal_id, decision,
                              decision_revision, matter_id, policy_json,
                              source_coordinate_json, freshness
                         FROM reconciliation_decisions
                        ORDER BY reconciliation_id"""
                )
            ],
            "relationships": [
                tuple(row)
                for row in runtime.projections.connection("context_fabric").execute(
                    """SELECT relationship_id, reconciliation_id,
                              relationship_type, authority, automatic_effect,
                              reason_codes_json
                         FROM reconciliation_relationships
                        ORDER BY relationship_id, reconciliation_id"""
                )
            ],
            "shared": [
                tuple(row)
                for row in runtime.projections.connection("context_fabric").execute(
                    """SELECT fact_id, source_member_id,
                              shared_with_member_ids_json, references_json,
                              current_state
                         FROM shared_reconciliation_facts
                        ORDER BY fact_id"""
                )
            ],
        }
        assert triage_after == triage_before
        assert context_after == context_before

        _refresh_decisions(client, runtime)
        assert all(
            decision["payload"]["provider_effects"] is False
            and decision["payload"]["llm_authority"] is False
            and decision["payload"]["raw_cross_member_join"] is False
            for decision in runtime._acceptance_decisions
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        assert (
            client.portal.call(_events, runtime, "provider.operation.requested") == []
        )
