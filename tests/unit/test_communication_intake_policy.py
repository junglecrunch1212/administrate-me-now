"""All legacy entry paths stop before reading context or invoking language."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from adminme.communication_intake_policy import (
    PASSIVE_CONTROL_TYPES,
    POLICY_EVENT_KEY,
    POLICY_EVENT_TYPE,
    POLICY_ID,
    POLICY_REASON,
    RETIRED_COMMAND_TYPE,
    SKIPPED_EVENT_TYPE,
    passive_obligations_disabled,
    source_skip_valid,
)
from adminme.pipelines.pack_loader import load_pipeline_pack
from adminme.runtime.application import RuntimeSettings
from tests.unit.test_triage_inventory import _event as proposal_event, _projection


ROOT = Path(__file__).resolve().parents[2]
TENANT = "fixture-triage-tenant"  # fixture:tenant_data:ok
MEMBER = "fixture-triage-member"  # fixture:tenant_data:ok


class _Untouchable:
    def __getattr__(self, key):
        raise AssertionError(f"passive extraction touched {key}")


class _Sink:
    def __init__(self):
        self.records = []

    async def write(self, envelope, **kwargs):
        self.records.append({**envelope.model_dump(), **kwargs})
        return f"receipt-{len(self.records)}"


def _source(event_type="messaging.received"):
    return {
        "event_id": "fixture-source-1",
        "type": event_type,
        "event_at_ms": 1_786_100_000_000,
        "occurred_at": "2034-02-01T10:00:00Z",
        "tenant_id": TENANT,
        "owner_scope": f"private:{MEMBER}",
        "visibility_scope": f"private:{MEMBER}",
        "sensitivity": "normal",
        "source_adapter": "fixture-provider",
        "source_account_id": "fixture-account",
        "payload": {"body_text": "Please handle the fixture form tomorrow."},
    }


def _context():
    sink = _Sink()
    return SimpleNamespace(
        event_sink=sink,
        event_log=_Untouchable(),
        run_skill_fn=_Untouchable(),
        legacy_personal_obligation_compatibility=False,
        session=SimpleNamespace(tenant_id=TENANT),
    )


@pytest.mark.parametrize(
    "pack", ["noise_filtering", "commitment_extraction", "thank_you_detection"]
)
async def test_passive_producers_audit_without_context_or_language(pack):
    pipeline = load_pipeline_pack(ROOT / "packs" / "pipelines" / pack).instance
    ctx = _context()
    source = _source()
    await pipeline.handle(source, ctx)
    [receipt] = ctx.event_sink.records
    assert receipt["type"] == SKIPPED_EVENT_TYPE
    assert source_skip_valid(source, receipt)
    assert receipt["payload"]["candidate_count"] == 0


@pytest.mark.parametrize("event_type", sorted(PASSIVE_CONTROL_TYPES))
async def test_every_recovery_and_saved_publication_command_is_terminal(event_type):
    pipeline = load_pipeline_pack(ROOT / "packs/pipelines/noise_filtering").instance
    ctx = _context()
    command = {**_source(event_type), "payload": {}}
    await pipeline.handle(command, ctx)
    [receipt] = ctx.event_sink.records
    assert receipt["type"] == RETIRED_COMMAND_TYPE
    assert receipt["payload"]["command_event_id"] == command["event_id"]
    assert receipt["payload"]["reason"] == POLICY_REASON
    assert receipt["payload"]["language_called"] is False


async def test_internal_resume_and_publish_helpers_cannot_bypass_policy():
    pipeline = load_pipeline_pack(ROOT / "packs/pipelines/noise_filtering").instance
    ctx = _context()
    source = _source()
    await pipeline._handle_source(source, ctx)
    assert await pipeline._resume_source_publication(source, ctx, {}) is True
    counts = await pipeline._publish_source_drafts(source, ctx, {}, [{}, {}])
    assert counts["planned_count"] == counts["superseded_count"] == 2
    assert counts["published_count"] == 0
    assert all(
        record["type"] == SKIPPED_EVENT_TYPE for record in ctx.event_sink.records
    )


def test_manual_intent_is_distinct_and_compatibility_requires_fixture(tmp_path):
    ctx = _context()
    assert passive_obligations_disabled(_source(), ctx)
    assert not passive_obligations_disabled(_source("triage.intent.submitted"), ctx)
    settings = RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=b"p" * 32,
        repository_root=ROOT,
    )
    assert settings.legacy_personal_obligation_compatibility is False
    with pytest.raises(ValueError, match="credential-free"):
        replace(
            settings,
            _credential_free_fixture=False,
            legacy_personal_obligation_compatibility=True,
        )
    legacy = replace(settings, legacy_personal_obligation_compatibility=True)
    assert legacy.legacy_personal_obligation_compatibility is True


def test_epoch_rejects_late_passive_proposals_and_preserves_manual_and_other_sources():
    projection, conn = _projection()
    epoch = {
        **_source(POLICY_EVENT_TYPE),
        "event_id": "event-0001",
        "source_adapter": "communication-intake-policy",
        "source_account_id": "system",
        "idempotency_key": POLICY_EVENT_KEY,
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "payload": {
            "policy_id": POLICY_ID,
            "automatic_personal_obligations_enabled": False,
        },
    }
    projection.apply(epoch, conn)
    for index, adapter in enumerate(
        [
            "deterministic_candidate_eligibility",
            "member-intent-hybrid-v2",
            "deterministic_source_candidates",
        ],
        start=2,
    ):
        projection.apply({**proposal_event(index), "source_adapter": adapter}, conn)
    ids = {row[0] for row in conn.execute("SELECT proposal_id FROM triage_items")}
    assert ids == {"proposal-0003", "proposal-0004"}
    conn.close()


def test_retirement_compare_and_apply_preserves_a_concurrent_member_revision():
    projection, conn = _projection()
    original = proposal_event(1)
    projection.apply(original, conn)
    projection.apply(
        {
            **original,
            "event_id": "event-0002",
            "type": "triage.item.enriched",
            "payload": {
                "proposal_id": "proposal-0001",
                "enriched_at": original["occurred_at"],
                "enriched_by_member_id": MEMBER,
                "title": "Member corrected work",
                "candidate_updates": {},
            },
        },
        conn,
    )
    projection.apply(
        {
            **original,
            "event_id": "event-0003",
            "type": "triage.item.superseded",
            "schema_version": 5,
            "payload": {
                "proposal_id": "proposal-0001",
                "superseded_at": original["occurred_at"],
                "replacement_source_event_id": "epoch",
                "reason": POLICY_REASON,
                "expected_content_revision_event_id": original["event_id"],
                "expected_last_event_id": original["event_id"],
            },
        },
        conn,
    )
    row = conn.execute("SELECT state,title FROM triage_items").fetchone()
    assert tuple(row) == ("pending_review", "Member corrected work")
    conn.close()
