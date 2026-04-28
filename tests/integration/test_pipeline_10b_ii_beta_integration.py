"""Round-trip integration tests for 10b-ii-β: thank_you_detection.

Mirrors the harness pattern in
``tests/integration/test_pipeline_10b_ii_alpha_integration.py``:
in-memory EventBus + EventLog + tmp InstanceConfig, register the
`thank_you_detection` pipeline pack via ``register()`` directly, and
monkeypatch ``adminme.pipelines.runner.run_skill`` to return
deterministic skill outputs. Per the Phase A boundary in the universal
preamble, no test contacts a live OpenClaw / BlueBubbles / Plaid.

The parties projection DB is seeded with one party + one identifier so
``find_party_by_identifier`` returns a hit; the runner is constructed
with a `parties_conn_factory` closure that opens a fresh SQLCipher
connection per call as a context manager.

Tests:
1. above-threshold inbound emits commitment.proposed with kind="other",
   strength=confident, and causation_id == triggering event_id.
2. is_candidate=False inbound emits commitment.suppressed with reason=
   below_confidence_threshold (and the upstream classifier omits
   urgency/suggested_medium per its JSON Schema if/then — pipeline must
   not crash).
3. F-5 carry-forward absence assertion: a `messaging.sent` event
   appended ahead of a follow-up `messaging.received` results in NO
   commitment derived from the outbound (the pipeline does not
   subscribe to `messaging.sent`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import sqlcipher3

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.lib.skill_runner import SkillResult
from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.runner import PipelineRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
THANK_YOU_PACK_ROOT = (
    REPO_ROOT / "packs" / "pipelines" / "thank_you_detection"
)
PARTIES_SCHEMA = (
    REPO_ROOT / "adminme" / "projections" / "parties" / "schema.sql"
)
TEST_KEY = b"j" * 32


def _key_pragma() -> str:
    return f"x'{bytes(TEST_KEY).hex()}'"


def _seed_parties_db(
    db_path: Path,
    *,
    tenant_id: str,
    party_id: str,
    identifier_kind: str,
    value_normalized: str,
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlcipher3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"{_key_pragma()}\"")
    conn.execute("PRAGMA journal_mode=WAL")
    schema = PARTIES_SCHEMA.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.execute(
        """
        INSERT INTO parties (
          party_id, tenant_id, kind, display_name, sort_name,
          attributes_json, owner_scope, visibility_scope, sensitivity,
          created_at_ms, last_event_id
        ) VALUES (?, ?, 'person', 'Seed Party', 'seed party', '{}',
                  'shared:household', 'shared:household', 'normal',
                  1700000000000, 'seed-evt')
        """,
        (party_id, tenant_id),
    )
    conn.execute(
        """
        INSERT INTO identifiers (
          identifier_id, tenant_id, party_id, kind, value, value_normalized,
          verified, primary_for_kind, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, 'seed-evt')
        """,
        (
            f"ident-{party_id}",
            tenant_id,
            party_id,
            identifier_kind,
            value_normalized,
            value_normalized,
        ),
    )
    conn.close()


@contextmanager
def _open_parties_conn(db_path: Path) -> Iterator[sqlcipher3.Connection]:
    conn = sqlcipher3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"{_key_pragma()}\"")
    try:
        yield conn
    finally:
        conn.close()


def _envelope(
    event_type: str,
    payload: dict[str, Any],
    *,
    tenant_id: str,
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(asyncio.get_event_loop().time() * 1000),
        tenant_id=tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test",
        source_account_id="test-acct",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        payload=payload,
    )


async def _wait_for_checkpoint(
    bus: EventBus, subscriber_id: str, target: str, timeout_iters: int = 200
) -> None:
    for _ in range(timeout_iters):
        status = await bus.subscriber_status(subscriber_id)
        if status["checkpoint_event_id"] == target and status["lag_count"] == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"subscriber {subscriber_id} did not reach {target}: "
        f"{await bus.subscriber_status(subscriber_id)}"
    )


async def _read_all(log: EventLog) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for row in log.read_since():
        out.append(row)
    return out


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    invalidate_cache()
    yield
    invalidate_cache()


def _classify_output(
    *,
    confidence: float,
    is_candidate: bool = True,
    include_urgency_fields: bool = True,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "is_candidate": is_candidate,
        "confidence": confidence,
        "reasons": ["hosted hospitality"],
    }
    if is_candidate and include_urgency_fields:
        output["urgency"] = "this_week"
        output["suggested_medium"] = "handwritten_card"
    return output


def _extract_output() -> dict[str, Any]:
    return {
        "recipient_party_id": "party_seed",
        "suggested_text": "Thanks so much for hosting us — the kids are still talking about it.",
        "urgency": "this_week",
        "confidence": 0.9,
    }


def _build_stub_run_skill(
    *,
    classify_confidence: float,
    is_candidate: bool = True,
    include_urgency_fields: bool = True,
) -> Any:
    async def stub(
        skill_id: str, inputs: dict[str, Any], skill_ctx: Any
    ) -> SkillResult:
        if "classify" in skill_id:
            return SkillResult(
                output=_classify_output(
                    confidence=classify_confidence,
                    is_candidate=is_candidate,
                    include_urgency_fields=include_urgency_fields,
                ),
                openclaw_invocation_id="stub-classify",
                provider="stub",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.0,
                duration_ms=10,
            )
        return SkillResult(
            output=_extract_output(),
            openclaw_invocation_id="stub-extract",
            provider="stub",
            input_tokens=20,
            output_tokens=15,
            cost_usd=0.0,
            duration_ms=20,
        )

    return stub


async def test_thank_you_detection_emits_proposed_for_above_threshold_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)

    parties_db = config.projection_db_path("parties")
    _seed_parties_db(
        parties_db,
        tenant_id=config.tenant_id,
        party_id="party_seed",
        identifier_kind="email",
        value_normalized="ada@example.com",
    )

    monkeypatch.setattr(
        "adminme.pipelines.runner.run_skill",
        _build_stub_run_skill(classify_confidence=0.85),
    )

    runner = PipelineRunner(
        bus,
        log,
        config,
        parties_conn_factory=lambda: _open_parties_conn(parties_db),
    )
    pack = load_pipeline_pack(THANK_YOU_PACK_ROOT)
    runner.register(pack)
    await runner.start()

    try:
        triggering_eid = await log.append(
            _envelope(
                "messaging.received",
                {
                    "source_channel": "gmail",
                    "from_identifier": "ada@example.com",
                    "to_identifier": "member_alpha",
                    "body_text": "Thanks so much for having us over Friday — the kids loved it.",
                    "received_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus,
            "pipeline:pipeline:thank_you_detection",
            triggering_eid,
        )

        all_events = await _read_all(log)
        proposed = [
            e for e in all_events if e["type"] == "commitment.proposed"
        ]
        assert len(proposed) == 1
        e = proposed[0]
        assert e["causation_id"] == triggering_eid
        assert e["payload"]["strength"] == "confident"
        assert e["payload"]["confidence"] == 0.85
        # kind="other" v1 disposition — Literal NOT extended.
        assert e["payload"]["kind"] == "other"
        assert e["payload"]["owed_to_party_id"] == "party_seed"
        assert e["payload"]["owed_by_member_id"] == "member_alpha"
        assert e["payload"]["urgency"] == "this_week"
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_thank_you_detection_emits_suppressed_for_is_candidate_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Classify returns is_candidate=False with high confidence and NO
    urgency / suggested_medium fields (per the upstream skill's JSON
    Schema if/then). The pipeline must emit suppressed without crashing
    on the missing fields."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)

    parties_db = config.projection_db_path("parties")
    _seed_parties_db(
        parties_db,
        tenant_id=config.tenant_id,
        party_id="party_seed",
        identifier_kind="email",
        value_normalized="ada@example.com",
    )

    monkeypatch.setattr(
        "adminme.pipelines.runner.run_skill",
        _build_stub_run_skill(
            classify_confidence=0.95,
            is_candidate=False,
            include_urgency_fields=False,
        ),
    )

    runner = PipelineRunner(
        bus,
        log,
        config,
        parties_conn_factory=lambda: _open_parties_conn(parties_db),
    )
    pack = load_pipeline_pack(THANK_YOU_PACK_ROOT)
    runner.register(pack)
    await runner.start()

    try:
        triggering_eid = await log.append(
            _envelope(
                "messaging.received",
                {
                    "source_channel": "gmail",
                    "from_identifier": "ada@example.com",
                    "to_identifier": "member_alpha",
                    "body_text": "Practice moved to upper field, 5:30 sharp.",
                    "received_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus,
            "pipeline:pipeline:thank_you_detection",
            triggering_eid,
        )

        all_events = await _read_all(log)
        suppressed = [
            e for e in all_events if e["type"] == "commitment.suppressed"
        ]
        assert len(suppressed) == 1
        e = suppressed[0]
        assert e["causation_id"] == triggering_eid
        assert e["payload"]["reason"] == "below_confidence_threshold"
        assert e["payload"]["confidence"] == 0.95
        assert e["payload"]["source_event_id"] == triggering_eid
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_thank_you_detection_skips_messaging_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-5 carry-forward (integration-level absence assertion).

    Append a `messaging.sent`, then a follow-up `messaging.received` so
    the subscriber checkpoint can advance, then wait-for-checkpoint on
    the follow-up and assert NO commitment event was derived from the
    original `messaging.sent` (its event_id does not appear as a
    causation_id in the log). Per the universal preamble's "absence
    assertion" pattern."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)

    parties_db = config.projection_db_path("parties")
    _seed_parties_db(
        parties_db,
        tenant_id=config.tenant_id,
        party_id="party_seed",
        identifier_kind="email",
        value_normalized="ada@example.com",
    )

    # Even if the stub were called, we want to detect via post-hoc
    # inspection rather than the stub raising — outbound should never
    # reach skill calls because the pipeline doesn't subscribe.
    monkeypatch.setattr(
        "adminme.pipelines.runner.run_skill",
        _build_stub_run_skill(
            classify_confidence=0.30, is_candidate=True
        ),
    )

    runner = PipelineRunner(
        bus,
        log,
        config,
        parties_conn_factory=lambda: _open_parties_conn(parties_db),
    )
    pack = load_pipeline_pack(THANK_YOU_PACK_ROOT)
    runner.register(pack)
    await runner.start()

    try:
        outbound_eid = await log.append(
            _envelope(
                "messaging.sent",
                {
                    "source_channel": "gmail",
                    "to_identifier": "ada@example.com",
                    "body_text": "Thanks for the kind note!",
                    "sent_at": EventEnvelope.now_utc_iso(),
                    "delivery_status": "sent",
                },
                tenant_id=config.tenant_id,
            )
        )
        followup_eid = await log.append(
            _envelope(
                "messaging.received",
                {
                    "source_channel": "gmail",
                    "from_identifier": "unknown@example.com",
                    "to_identifier": "member_alpha",
                    "body_text": "Practice update.",
                    "received_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(outbound_eid)
        await bus.notify(followup_eid)
        await _wait_for_checkpoint(
            bus,
            "pipeline:pipeline:thank_you_detection",
            followup_eid,
        )

        all_events = await _read_all(log)
        # No event in the log should have causation_id == outbound_eid.
        derived_from_outbound = [
            e
            for e in all_events
            if e.get("causation_id") == outbound_eid
            and e["type"]
            in ("commitment.proposed", "commitment.suppressed")
        ]
        assert derived_from_outbound == []
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
