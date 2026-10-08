"""Production cutover preserves facts/member work and cannot replay old mining.

Credential-free composition only. These tests run in hosted CI; they never
discover a member provider, installed release, or live household instance.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from adminme.communication_intake_policy import (
    POLICY_EVENT_TYPE,
    POLICY_REASON,
    RETIRED_COMMAND_TYPE,
    SKIPPED_EVENT_TYPE,
    command_retirement,
)
from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.source_assurance import audit_source_assurance


ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-forwarding-policy-member"  # fixture:tenant_data:ok
AT = "2034-02-01T10:00:00Z"


def _settings(path: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        path,
        encryption_key=b"i" * 32,
        repository_root=ROOT,
        member_identities={"forward-policy.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        fixture_member_id=MEMBER,
    )


def _event(
    settings, event_type, payload, key, *, adapter="fixture", version=1, cause=None
):
    return EventEnvelope(
        event_at_ms=2_022_408_000_000,
        tenant_id=settings.instance.tenant_id,
        type=event_type,
        schema_version=version,
        occurred_at=AT,
        source_adapter=adapter,
        source_account_id="fixture-policy-account",
        owner_scope=f"private:{MEMBER}",
        visibility_scope=f"private:{MEMBER}",
        sensitivity="normal",
        causation_id=cause,
        idempotency_key=key,
        payload=payload,
    )


def _message(settings, key="passive-message"):
    return _event(
        settings,
        "messaging.received",
        {
            "source_channel": "gmail",
            "from_identifier": "sender@example.invalid",
            "to_identifier": "member@example.invalid",
            "body_text": "Please return the fixture form tomorrow. Thank you for the gift.",
            "received_at": AT,
        },
        key,
    )


def _proposal(
    settings, source_id, proposal_id, *, adapter="deterministic_candidate_eligibility"
):
    return _event(
        settings,
        "triage.item.proposed",
        {
            "proposal_id": proposal_id,
            "target_kind": "task",
            "owner_member_id": MEMBER,
            "title": f"Fixture task {proposal_id}",
            "candidate": {"title": f"Fixture task {proposal_id}"},
            "route": {"member_id": MEMBER},
            "source_event_id": source_id,
            "reasons": ["fixture historical extraction"],
        },
        f"triage:message-candidate:fixture:{proposal_id}",
        adapter=adapter,
        cause=source_id,
    )


async def _events(runtime, *types):
    return [
        event async for event in runtime.event_log.read_since(types=list(types) or None)
    ]


async def _settle(runtime):
    deadline = asyncio.get_running_loop().time() + 30
    previous = None
    stable = 0
    while asyncio.get_running_loop().time() < deadline:
        states = [
            await runtime.bus.subscriber_status(name)
            for name in runtime.bus._subscribers
        ]
        assert not any(state["degraded"] for state in states), states
        latest = await runtime.event_log.latest_event_id()
        stable = (
            stable + 1
            if latest == previous and all(not s["lag_count"] for s in states)
            else 0
        )
        previous = latest
        if stable >= 2:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("explicit communication intake did not settle")


async def _triage_rows(runtime):
    return await runtime.projections.read(
        "triage",
        lambda conn: {
            row[0]: {"state": row[1], "title": row[2], "reason": row[3]}
            for row in conn.execute(
                "SELECT proposal_id, state, title, resolution_reason FROM triage_items"
            )
        },
    )


def test_default_personal_observation_is_retained_without_obligation_language(tmp_path):
    settings = _settings(tmp_path / "instance")
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime
        source_id = client.portal.call(runtime.events.write, _message(settings))
        client.portal.call(_settle, runtime)
        source = client.portal.call(runtime.event_log.get, source_id)
        assert source["payload"]["body_text"].startswith("Please return")
        assert (
            client.portal.call(
                _events, runtime, "triage.item.proposed", "commitment.proposed"
            )
            == []
        )
        assert (
            client.portal.call(
                _events, runtime, "skill.call.recorded", "skill.call.failed"
            )
            == []
        )
        [receipt] = client.portal.call(_events, runtime, SKIPPED_EVENT_TYPE)
        assert receipt["payload"]["source_event_id"] == source_id
        assert receipt["payload"]["language_called"] is False
        audit = client.portal.call(
            lambda: audit_source_assurance(
                runtime.event_log, repository_root=ROOT, capabilities={}
            )
        )
        assert audit["counts"]["not_selected"] >= 1
        assert audit["counts"]["failed"] == 0
        assert audit["counts"]["missing"] == 0
        # Ordinary source projections remain active after the intake cutover.
        rows = client.portal.call(
            runtime.projections.read,
            "interactions",
            lambda conn: conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[
                0
            ],
        )
        assert rows == 1


async def _preload(settings) -> dict[str, str]:
    log = EventLog(settings.instance, settings.encryption_key)
    bus = EventBus(log, settings.instance.bus_checkpoint_path)
    writer = EventWriter(log, bus)
    try:
        source_id = await writer.write(_message(settings))
        for proposal_id in ("untouched", "member-edited", "approved"):
            await writer.write(_proposal(settings, source_id, proposal_id))
        await writer.write(
            _proposal(settings, source_id, "manual", adapter="member-intent-hybrid-v2")
        )
        await writer.write(
            _event(
                settings,
                "triage.item.enriched",
                {
                    "proposal_id": "member-edited",
                    "enriched_at": AT,
                    "enriched_by_member_id": MEMBER,
                    "title": "Member retained fixture work",
                },
                "member-content-revision",
                adapter="member-triage",
            )
        )
        canonical_id = await writer.write(
            _event(
                settings,
                "task.created",
                {
                    "task_id": "fixture-existing-task",
                    "title": "Member approved task",
                    "owner_member_id": MEMBER,
                },
                "fixture-existing-task",
                adapter="member-triage",
            )
        )
        await writer.write(
            _event(
                settings,
                "triage.item.confirmed",
                {
                    "proposal_id": "approved",
                    "confirmed_at": AT,
                    "confirmed_by_member_id": MEMBER,
                    "materialized_event_ids": [canonical_id],
                },
                "member-approved",
                adapter="member-triage",
            )
        )
        await writer.write(
            _event(
                settings,
                "commitment.proposed",
                {
                    "commitment_id": "legacy-thank-you",
                    "kind": "other",
                    "owed_by_member_id": MEMBER,
                    "owed_to_party_id": "fixture-party",
                    "text_summary": "Review old thank-you",
                    "confidence": 0.8,
                    "strength": "confident",
                },
                "legacy-thankyou",
                adapter="thank_you_detection",
                cause=source_id,
            )
        )
        assessment_key = f"triage:message-features:v23:{source_id}"
        assessment_id = await writer.write(
            _event(
                settings,
                "triage.intent.features_extracted",
                {
                    "intent_id": f"source:{source_id}",
                    "source_event_id": source_id,
                    "extractor_id": "fixture-old-assessor",
                    "candidates": [],
                    "extraction_state": "defensive_default",
                    "extracted_at": AT,
                    "assessment_outcome": "failed",
                    "reason_codes": ["language_seam_OpenClawTimeout"],
                },
                assessment_key,
                adapter="deterministic_candidate_eligibility",
                version=3,
                cause=source_id,
            )
        )
        recovery_id = await writer.write(
            _event(
                settings,
                "source.assessment.recovery_requested",
                {
                    "request_id": "fixture-old-recovery",
                    "batch_fingerprint": "a" * 64,
                    "source_event_id": source_id,
                    "failed_assessment_event_id": assessment_id,
                    "expected_failure_reason": "language_seam_OpenClawTimeout",
                    "owner_member_id": MEMBER,
                    "requested_by_actor": MEMBER,
                    "authority": "member_principal",
                    "policy": "source-assessment-recovery-v1",
                    "attempt_limit": 3,
                    "max_concurrency": 2,
                },
                "fixture-old-recovery",
                adapter="source_assessment_recovery",
            )
        )
        draft = dict(_proposal(settings, source_id, "unfinished-publication").payload)
        draft["eligibility_rule_ids"] = ["fixture-old-rule"]
        plan_id = await writer.write(
            _event(
                settings,
                "triage.source_publication.planned",
                {
                    "contract": "triage-source-publication-v1",
                    "source_event_id": source_id,
                    "assessment_idempotency_key": assessment_key,
                    "assessment_schema_version": 3,
                    "proposals": [
                        {
                            "idempotency_key": "fixture-unfinished-draft",
                            "proposal_schema_version": 4,
                            "payload": draft,
                        }
                    ],
                },
                f"triage:source-publication:v1:{assessment_key}",
                adapter="deterministic_candidate_eligibility",
                cause=source_id,
            )
        )
        return {"source": source_id, "recovery": recovery_id, "plan": plan_id}
    finally:
        await log.close()


def test_cutover_retires_only_untouched_drafts_and_survives_replay(tmp_path):
    settings = _settings(tmp_path / "instance")
    seeded = asyncio.run(_preload(settings))
    for restart in range(2):
        app = create_application(settings)
        with TestClient(app) as client:
            runtime = app.state.runtime
            client.portal.call(_settle, runtime)
            rows = client.portal.call(_triage_rows, runtime)
            assert rows["untouched"]["reason"] == POLICY_REASON
            assert rows["legacy-thank-you"]["reason"] == POLICY_REASON
            assert rows["member-edited"]["title"] == "Member retained fixture work"
            assert rows["member-edited"]["reason"] != POLICY_REASON
            assert rows["approved"]["reason"] != POLICY_REASON
            assert len(client.portal.call(_events, runtime, "task.created")) == 1
            assert rows["manual"]["reason"] != POLICY_REASON
            assert "unfinished-publication" not in rows
            for key in ("recovery", "plan"):
                request = client.portal.call(runtime.event_log.get, seeded[key])
                assert client.portal.call(
                    command_retirement, runtime.event_log, request
                )
            assert len(client.portal.call(_events, runtime, POLICY_EVENT_TYPE)) == 1
            assert len(client.portal.call(_events, runtime, RETIRED_COMMAND_TYPE)) == 2
            assert (
                client.portal.call(
                    _events, runtime, "skill.call.recorded", "skill.call.failed"
                )
                == []
            )
            if restart == 0:
                # A delayed predecessor emission cannot bypass the durable epoch.
                late = _proposal(settings, seeded["source"], "late-old-producer")
                client.portal.call(runtime.events.write, late)
                client.portal.call(_settle, runtime)
                assert "late-old-producer" not in client.portal.call(
                    _triage_rows, runtime
                )
                client.portal.call(runtime.projections.rebuild, "triage")
                assert client.portal.call(_triage_rows, runtime) == rows
