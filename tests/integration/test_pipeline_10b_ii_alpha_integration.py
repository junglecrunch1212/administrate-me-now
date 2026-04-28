"""Round-trip integration tests for 10b-ii-α: commitment_extraction.

Mirrors the harness pattern in
``tests/integration/test_pipeline_10b_i_integration.py``: in-memory
EventBus + EventLog + tmp InstanceConfig, register the
`commitment_extraction` pipeline pack via ``register()`` directly, and
monkeypatch ``adminme.pipelines.runner.run_skill`` to return
deterministic skill outputs. Per the Phase A boundary in the universal
preamble, no test contacts a live OpenClaw / BlueBubbles / Plaid.

The parties projection DB is seeded with one party + one identifier so
``find_party_by_identifier`` returns a hit; the runner is constructed
with a `parties_conn_factory` closure that opens a fresh SQLCipher
connection per call as a context manager.

Tests:
1. above-threshold inbound emits commitment.proposed with strength=confident
   and causation_id == triggering event_id.
2. below-threshold inbound emits commitment.suppressed with reason=
   below_confidence_threshold.
3. classify-skill OpenClawTimeout lands a defensive-default
   commitment.suppressed (does NOT propagate per [§7.7]).
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
from adminme.lib.skill_runner import OpenClawTimeout, SkillResult
from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.runner import PipelineRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITMENT_PACK_ROOT = (
    REPO_ROOT / "packs" / "pipelines" / "commitment_extraction"
)
PARTIES_SCHEMA = (
    REPO_ROOT / "adminme" / "projections" / "parties" / "schema.sql"
)
TEST_KEY = b"i" * 32


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
    *, confidence: float, is_candidate: bool = True
) -> dict[str, Any]:
    return {
        "is_candidate": is_candidate,
        "confidence": confidence,
        "reasons": ["promise_keyword"],
    }


def _extract_output() -> dict[str, Any]:
    return {
        "kind": "reply",
        "owed_by_member_id": "member_alpha",
        "owed_to_party_id": "party_seed",
        "text_summary": "Reply with the requested report by Friday",
        "suggested_due": "2026-05-01T17:00:00Z",
        "urgency": "this_week",
        "confidence": 0.9,
    }


def _build_stub_run_skill(
    *,
    classify_confidence: float,
    raise_classify: BaseException | None = None,
) -> Any:
    async def stub(
        skill_id: str, inputs: dict[str, Any], skill_ctx: Any
    ) -> SkillResult:
        if "classify" in skill_id:
            if raise_classify is not None:
                raise raise_classify
            return SkillResult(
                output=_classify_output(confidence=classify_confidence),
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


async def test_commitment_extraction_emits_proposed_for_above_threshold(
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
    pack = load_pipeline_pack(COMMITMENT_PACK_ROOT)
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
                    "body_text": "Can you send me the report by Friday?",
                    "received_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus,
            "pipeline:pipeline:commitment_extraction",
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
        assert e["payload"]["kind"] == "reply"
        assert e["payload"]["owed_to_party_id"] == "party_seed"
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_commitment_extraction_emits_suppressed_for_below_threshold(
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
        _build_stub_run_skill(classify_confidence=0.30),
    )

    runner = PipelineRunner(
        bus,
        log,
        config,
        parties_conn_factory=lambda: _open_parties_conn(parties_db),
    )
    pack = load_pipeline_pack(COMMITMENT_PACK_ROOT)
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
            "pipeline:pipeline:commitment_extraction",
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
        assert e["payload"]["confidence"] == 0.30
        assert e["payload"]["source_event_id"] == triggering_eid
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_commitment_extraction_emits_suppressed_on_skill_timeout(
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
        _build_stub_run_skill(
            classify_confidence=0.0,
            raise_classify=OpenClawTimeout("openclaw stub timeout"),
        ),
    )

    runner = PipelineRunner(
        bus,
        log,
        config,
        parties_conn_factory=lambda: _open_parties_conn(parties_db),
    )
    pack = load_pipeline_pack(COMMITMENT_PACK_ROOT)
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
                    "body_text": "Could you confirm Saturday at 2pm?",
                    "received_at": EventEnvelope.now_utc_iso(),
                },
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(triggering_eid)
        await _wait_for_checkpoint(
            bus,
            "pipeline:pipeline:commitment_extraction",
            triggering_eid,
        )

        all_events = await _read_all(log)
        suppressed = [
            e for e in all_events if e["type"] == "commitment.suppressed"
        ]
        assert len(suppressed) == 1
        e = suppressed[0]
        assert e["causation_id"] == triggering_eid
        assert e["payload"]["reason"] == "skill_failure_defensive_default"
        assert e["payload"]["confidence"] == 0.0
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
