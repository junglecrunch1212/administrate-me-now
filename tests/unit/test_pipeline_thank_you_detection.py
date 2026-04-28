"""Handler-direct unit tests for thank_you_detection.

Construct a fake PipelineContext with stubbed `run_skill_fn` and a tmp
parties DB seeded with one party + one identifier. Each test patches
`pipeline._config_override` to control thresholds and per-member
overrides; no test loads the on-disk `config.example.yaml`.

Mirrors `tests/unit/test_pipeline_commitment_extraction.py` literally
per the prompt's clone-the-shape directive.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import sqlcipher3

from adminme.events.envelope import EventEnvelope
from adminme.lib.skill_runner import (
    OpenClawTimeout,
    SkillContext,
    SkillResult,
)
from adminme.pipelines.base import PipelineContext

PACK_ROOT = (
    Path(__file__).resolve().parents[2]
    / "packs"
    / "pipelines"
    / "thank_you_detection"
)
PARTIES_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "adminme"
    / "projections"
    / "parties"
    / "schema.sql"
)
TEST_KEY = b"t" * 32
TENANT_ID = "tenant-test"


def _import_handler_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "test_thank_you_detection_handler",
        PACK_ROOT / "handler.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_handler = _import_handler_module()
ThankYouDetectionPipeline = _handler.ThankYouDetectionPipeline


@dataclass
class _RecordedAppend:
    envelope: EventEnvelope
    correlation_id: str | None
    causation_id: str | None


@dataclass
class _FakeEventLog:
    calls: list[_RecordedAppend] = field(default_factory=list)
    _next_id: int = 0

    async def append(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        self.calls.append(
            _RecordedAppend(
                envelope=envelope,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        )
        self._next_id += 1
        return f"fake-event-id-{self._next_id}"


def _key_pragma() -> str:
    return f"x'{bytes(TEST_KEY).hex()}'"


def _build_parties_db(
    db_path: Path,
    *,
    party_id: str,
    identifier_kind: str,
    value_normalized: str,
) -> None:
    """Seed a fresh SQLCipher parties DB with one party + one identifier."""
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
        (party_id, TENANT_ID),
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
            TENANT_ID,
            party_id,
            identifier_kind,
            value_normalized,
            value_normalized,
        ),
    )
    conn.close()


def _open_parties_conn(db_path: Path) -> sqlcipher3.Connection:
    conn = sqlcipher3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlcipher3.Row
    conn.execute(f"PRAGMA key = \"{_key_pragma()}\"")
    return conn


@contextmanager
def _conn_context(db_path: Path) -> Iterator[sqlcipher3.Connection]:
    conn = _open_parties_conn(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _make_factory(db_path: Path) -> Any:
    def factory() -> Any:
        return _conn_context(db_path)

    return factory


def _make_ctx(
    event_log: Any,
    run_skill_fn: Any,
    *,
    parties_factory: Any | None = None,
) -> PipelineContext:
    from adminme.lib.session import build_internal_session

    session = build_internal_session("pipeline_runner", "device", TENANT_ID)
    return PipelineContext(
        session=session,
        event_log=event_log,
        run_skill_fn=run_skill_fn,
        outbound_fn=lambda *a, **kw: None,  # type: ignore[arg-type, return-value]
        guarded_write=None,
        observation_manager=None,
        triggering_event_id="trigger-eid-001",
        correlation_id="corr-001",
        parties_conn_factory=parties_factory,
    )


def _messaging_received_event(
    *,
    body_text: str = "Thanks so much for hosting us last weekend!",
    from_id: str = "ada@example.com",
    to_id: str = "member_alpha",
    party_tags: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source_channel": "gmail",
        "from_identifier": from_id,
        "to_identifier": to_id,
        "body_text": body_text,
        "received_at": EventEnvelope.now_utc_iso(),
    }
    if party_tags is not None:
        payload["party_tags"] = party_tags
    return {
        "event_id": "trigger-eid-001",
        "type": "messaging.received",
        "event_at_ms": 1700000000000,
        "tenant_id": TENANT_ID,
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "payload": payload,
    }


def _messaging_sent_event() -> dict[str, Any]:
    """F-5 carry-forward: outbound event the pipeline must early-return on."""
    return {
        "event_id": "trigger-eid-002",
        "type": "messaging.sent",
        "event_at_ms": 1700000000000,
        "tenant_id": TENANT_ID,
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "payload": {
            "source_channel": "gmail",
            "to_identifier": "ada@example.com",
            "body_text": "Reply",
        },
    }


def _classify_result(
    *,
    confidence: float = 0.85,
    is_candidate: bool = True,
    include_urgency_fields: bool = True,
) -> SkillResult:
    output: dict[str, Any] = {
        "is_candidate": is_candidate,
        "confidence": confidence,
        "reasons": ["hosted hospitality"],
    }
    # Per the upstream skill's JSON Schema if/then, urgency/suggested_medium
    # are required only when is_candidate=true. Tests vary this to confirm
    # the pipeline doesn't crash when the fields are absent on the False
    # path.
    if is_candidate and include_urgency_fields:
        output["urgency"] = "this_week"
        output["suggested_medium"] = "handwritten_card"
    return SkillResult(
        output=output,
        openclaw_invocation_id="stub-classify",
        provider="stub",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        duration_ms=10,
    )


def _extract_result(
    *,
    urgency: str = "this_week",
    suggested_text: str = "Thanks so much for hosting us — the kids are still talking about it.",
) -> SkillResult:
    return SkillResult(
        output={
            "recipient_party_id": "party_seed",
            "suggested_text": suggested_text,
            "urgency": urgency,
            "confidence": 0.9,
        },
        openclaw_invocation_id="stub-extract",
        provider="stub",
        input_tokens=20,
        output_tokens=15,
        cost_usd=0.0,
        duration_ms=20,
    )


def _seed_party(tmp_path: Path) -> Path:
    db_path = tmp_path / "parties.db"
    _build_parties_db(
        db_path,
        party_id="party_seed",
        identifier_kind="email",
        value_normalized="ada@example.com",
    )
    return db_path


_DEFAULT_CONFIG: dict[str, Any] = {
    "min_confidence": 0.55,
    "review_threshold": 0.75,
    "dedupe_window_hours": 72,
    "per_member_overrides": {},
    "skip_party_tags": ["privileged", "opposing_counsel"],
}


# ---------------------------------------------------------------------------
# threshold paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_above_review_threshold_emits_proposed_confident(
    tmp_path: Path,
) -> None:
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(
        skill_id: str, inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        if skill_id == _handler.CLASSIFY_SKILL_ID:
            return _classify_result(confidence=0.85)
        return _extract_result()

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    await pipeline.handle(_messaging_received_event(), ctx)

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.proposed"
    assert envelope.payload["strength"] == "confident"
    assert envelope.payload["confidence"] == 0.85
    # kind="other" v1 disposition — Literal NOT extended.
    assert envelope.payload["kind"] == "other"
    assert envelope.payload["owed_to_party_id"] == "party_seed"
    assert envelope.payload["owed_by_member_id"] == "member_alpha"
    assert envelope.payload["urgency"] == "this_week"
    assert envelope.payload["classify_reasons"] == ["hosted hospitality"]
    assert log.calls[0].causation_id == "trigger-eid-001"


@pytest.mark.asyncio
async def test_between_min_and_review_emits_proposed_weak(
    tmp_path: Path,
) -> None:
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(
        skill_id: str, inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        if skill_id == _handler.CLASSIFY_SKILL_ID:
            return _classify_result(confidence=0.62)
        return _extract_result()

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    await pipeline.handle(_messaging_received_event(), ctx)

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.proposed"
    assert envelope.payload["strength"] == "weak"
    assert envelope.payload["confidence"] == 0.62
    assert envelope.payload["kind"] == "other"


@pytest.mark.asyncio
async def test_is_candidate_false_emits_suppressed(tmp_path: Path) -> None:
    """High classify confidence in the negative — `is_candidate=False`
    with confidence 0.95 — must still emit suppressed (not proposed)."""
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        return _classify_result(confidence=0.95, is_candidate=False)

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    await pipeline.handle(_messaging_received_event(), ctx)

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.suppressed"
    assert envelope.payload["reason"] == "below_confidence_threshold"
    assert envelope.payload["confidence"] == 0.95
    assert envelope.payload["source_event_id"] == "trigger-eid-001"


@pytest.mark.asyncio
async def test_below_min_threshold_emits_suppressed(tmp_path: Path) -> None:
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        return _classify_result(confidence=0.30, is_candidate=True)

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    await pipeline.handle(_messaging_received_event(), ctx)

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.suppressed"
    assert envelope.payload["reason"] == "below_confidence_threshold"
    assert envelope.payload["confidence"] == 0.30
    assert envelope.payload["threshold"] == 0.55


# ---------------------------------------------------------------------------
# F-5 carry-forward: messaging.sent must early-return silently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messaging_sent_returns_silently(tmp_path: Path) -> None:
    """F-5 carry-forward: the pipeline does not subscribe to outbound,
    but defense-in-depth tests the handler's early-return guard so that
    a misconfigured runner subscription cannot generate audit-trail
    noise."""
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        raise AssertionError("skill must not be called for outbound events")

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    await pipeline.handle(_messaging_sent_event(), ctx)

    assert log.calls == []


# ---------------------------------------------------------------------------
# defense-in-depth paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unresolvable_sender_emits_suppressed(tmp_path: Path) -> None:
    """parties DB exists but has no record for the sender — defense-in-depth."""
    db_path = tmp_path / "parties.db"
    _build_parties_db(
        db_path,
        party_id="party_other",
        identifier_kind="email",
        value_normalized="other@example.com",
    )
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        raise AssertionError("skill must not be called when sender unknown")

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    await pipeline.handle(
        _messaging_received_event(from_id="unknown@example.com"), ctx
    )

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.suppressed"
    assert envelope.payload["reason"] == "skill_failure_defensive_default"


@pytest.mark.asyncio
async def test_no_parties_factory_emits_suppressed() -> None:
    """parties_conn_factory is None → defense-in-depth path."""
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        raise AssertionError("skill must not be called when factory is None")

    ctx = _make_ctx(log, stub_run_skill, parties_factory=None)
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    await pipeline.handle(_messaging_received_event(), ctx)

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.suppressed"
    assert envelope.payload["reason"] == "skill_failure_defensive_default"
    assert envelope.payload["confidence"] == 0.0


@pytest.mark.asyncio
async def test_skip_party_tag_returns_silently(tmp_path: Path) -> None:
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        raise AssertionError("skill must not be called for skipped tags")

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    event = _messaging_received_event(
        party_tags=["privileged", "opposing_counsel"]
    )
    await pipeline.handle(event, ctx)
    assert log.calls == []


# ---------------------------------------------------------------------------
# skill failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_timeout_emits_suppressed(tmp_path: Path) -> None:
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        raise OpenClawTimeout("openclaw stub timeout")

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    await pipeline.handle(_messaging_received_event(), ctx)

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.suppressed"
    assert envelope.payload["reason"] == "skill_failure_defensive_default"
    assert envelope.payload["confidence"] == 0.0


@pytest.mark.asyncio
async def test_extract_failure_emits_suppressed(tmp_path: Path) -> None:
    """If classify succeeds but extract raises, we suppress (defensive default)."""
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(
        skill_id: str, inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        if skill_id == _handler.CLASSIFY_SKILL_ID:
            return _classify_result(confidence=0.85)
        raise OpenClawTimeout("extract timed out")

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    await pipeline.handle(_messaging_received_event(), ctx)

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.suppressed"
    assert envelope.payload["reason"] == "skill_failure_defensive_default"


# ---------------------------------------------------------------------------
# per-member overrides (REFERENCE_EXAMPLES.md §2 lines 651-666)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_member_override_lowers_threshold(tmp_path: Path) -> None:
    """Override `min_confidence` to 0.40 for `member_alpha`; an event with
    confidence 0.45 emits commitment.proposed (would be suppressed under
    the global 0.55 threshold)."""
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(
        skill_id: str, inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        if skill_id == _handler.CLASSIFY_SKILL_ID:
            return _classify_result(confidence=0.45)
        return _extract_result()

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = {
        **_DEFAULT_CONFIG,
        "per_member_overrides": {
            "member_alpha": {"min_confidence": 0.40, "review_threshold": 0.70}
        },
    }
    await pipeline.handle(
        _messaging_received_event(to_id="member_alpha"), ctx
    )

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.proposed"
    assert envelope.payload["confidence"] == 0.45
    # 0.45 is below 0.70 review_threshold → weak.
    assert envelope.payload["strength"] == "weak"
    assert envelope.payload["kind"] == "other"


@pytest.mark.asyncio
async def test_per_member_override_disables_via_high_threshold(
    tmp_path: Path,
) -> None:
    """Override `min_confidence` to 1.1 for `member_omega` — impossibly
    high disables the pipeline for that member per
    `REFERENCE_EXAMPLES.md §2 line 666`."""
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        # Confidence 0.99 — extremely high but still below the 1.1
        # impossibly-high override.
        return _classify_result(confidence=0.99)

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = {
        **_DEFAULT_CONFIG,
        "per_member_overrides": {"member_omega": {"min_confidence": 1.1}},
    }
    await pipeline.handle(
        _messaging_received_event(to_id="member_omega"), ctx
    )

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.suppressed"
    assert envelope.payload["reason"] == "below_confidence_threshold"
    assert envelope.payload["threshold"] == 1.1


# ---------------------------------------------------------------------------
# classify-output-shape edge case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_output_without_urgency_when_is_candidate_false(
    tmp_path: Path,
) -> None:
    """Per `classify_thank_you_candidate@1.3.0`'s JSON Schema if/then,
    `urgency` and `suggested_medium` are present only when
    `is_candidate=true`. The pipeline must not crash when those fields
    are absent on the False path — the suppressed branch never reads
    them. Asserts robustness against the conditional output shape."""
    db_path = _seed_party(tmp_path)
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        return _classify_result(
            confidence=0.95,
            is_candidate=False,
            include_urgency_fields=False,
        )

    ctx = _make_ctx(
        log, stub_run_skill, parties_factory=_make_factory(db_path)
    )
    pipeline = ThankYouDetectionPipeline()
    pipeline._config_override = dict(_DEFAULT_CONFIG)
    await pipeline.handle(_messaging_received_event(), ctx)

    assert len(log.calls) == 1
    envelope = log.calls[0].envelope
    assert envelope.type == "commitment.suppressed"
    assert envelope.payload["reason"] == "below_confidence_threshold"
