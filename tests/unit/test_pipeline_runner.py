"""Unit tests for adminme.pipelines.runner.PipelineRunner.

Five tests:
  1. register() after start() raises RuntimeError.
  2. register() with duplicate pack_id raises ValueError.
  3. happy-path dispatch: counter increments once for one event.
  4. handler raises -> bus checkpoint not advanced past the bad event.
  5. status() returns one entry per registered pack with non-None
     subscriber_id.

Per the universal preamble's failure-mode-handler-direct discipline,
test 4 uses a synthetic pack constructed in-process whose handle() is
intentionally raising; we route through the bus only to inspect the
bus checkpoint behavior, not to assert about anything else.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.pipelines import (
    LoadedPipelinePack,
    PipelineContext,
    invalidate_cache,
    load_pipeline_pack,
)
from adminme.pipelines.runner import PipelineRunner
from adminme.source_assessment_recovery import recovery_refusal_idempotency_key
from adminme.source_assessment_revision import (
    REVISION_PLAN_TYPE,
    REVISION_COMPLETION_TYPE,
)
from adminme.source_publication import (
    COMPLETION_EVENT_TYPE,
    PLAN_EVENT_TYPE,
    publication_completion_key,
    publication_plan_key,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ECHO_LOGGER_ROOT = REPO_ROOT / "tests" / "fixtures" / "pipelines" / "echo_logger"
NOISE_FILTERING_ROOT = REPO_ROOT / "packs" / "pipelines" / "noise_filtering"
TEST_KEY = b"p" * 32


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


def _messaging_received_payload() -> dict[str, Any]:
    return {
        "source_channel": "test",
        "from_identifier": "alice@example.com",
        "to_identifier": "bob@example.com",
        "received_at": EventEnvelope.now_utc_iso(),
    }


def _recovery_request(
    *, tenant_id: str, request_id: str, source_event_id: str, event_at_ms: int
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=event_at_ms,
        tenant_id=tenant_id,
        type="source.assessment.recovery_requested",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="source_assessment_recovery",
        source_account_id="operator",
        owner_scope="private:member-recovery",
        visibility_scope="private:member-recovery",
        sensitivity="normal",
        idempotency_key=f"runner-recovery:{request_id}:{source_event_id}",
        payload={
            "request_id": request_id,
            "batch_fingerprint": "a" * 64,
            "source_event_id": source_event_id,
            "failed_assessment_event_id": f"failed:{source_event_id}",
            "expected_failure_reason": "language_seam_OpenClawUnreachable",
            "owner_member_id": "member-recovery",
            "requested_by_actor": "member-recovery",
            "authority": "member_principal",
            "policy": "source-assessment-recovery-v1",
            "attempt_limit": 3,
            "max_concurrency": 2,
        },
    )


def _recovery_refusal(
    *,
    tenant_id: str,
    request_event_id: str,
    source_event_id: str,
    event_at_ms: int,
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=event_at_ms,
        tenant_id=tenant_id,
        type="source.assessment.recovery_refused",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="noise_filtering",
        source_account_id="pipeline",
        owner_scope="private:member-recovery",
        visibility_scope="private:member-recovery",
        sensitivity="normal",
        idempotency_key=recovery_refusal_idempotency_key(request_event_id),
        payload={
            "request_event_id": request_event_id,
            "source_event_id": source_event_id,
            "reason": "source_not_found",
            "policy": "source-assessment-recovery-v1",
        },
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


@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, EventWriter(log, bus), config)
    invalidate_cache()
    # Reset class-level counter on the fixture pack between tests.
    pack = load_pipeline_pack(ECHO_LOGGER_ROOT)
    pack.instance.__class__.reset()  # type: ignore[attr-defined]
    try:
        yield {
            "config": config,
            "log": log,
            "bus": bus,
            "runner": runner,
            "pack": pack,
        }
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
        invalidate_cache()


async def test_register_after_start_raises(rig: dict[str, Any]) -> None:
    runner: PipelineRunner = rig["runner"]
    pack: LoadedPipelinePack = rig["pack"]
    runner.register(pack)
    await runner.start()
    with pytest.raises(RuntimeError, match="must be called before start"):
        runner.register(pack)


async def test_register_duplicate_pack_id_raises(rig: dict[str, Any]) -> None:
    runner: PipelineRunner = rig["runner"]
    pack: LoadedPipelinePack = rig["pack"]
    runner.register(pack)
    with pytest.raises(ValueError, match="already registered"):
        runner.register(pack)


async def test_dispatch_happy_path(rig: dict[str, Any]) -> None:
    log: EventLog = rig["log"]
    bus: EventBus = rig["bus"]
    runner: PipelineRunner = rig["runner"]
    pack: LoadedPipelinePack = rig["pack"]

    runner.register(pack)
    await runner.start()

    eid = await log.append(
        _envelope(
            "messaging.received",
            _messaging_received_payload(),
            tenant_id=rig["config"].tenant_id,
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "pipeline:pipeline:echo_logger", eid)

    counter = pack.instance.__class__._count_for_test()  # type: ignore[attr-defined]
    assert counter == 1
    assert pack.instance.__class__.last_event_id == eid  # type: ignore[attr-defined]


async def test_handler_raise_does_not_advance_checkpoint(
    tmp_path: Path,
) -> None:
    """When the pack's handle() raises, the bus must NOT advance the
    checkpoint past the failing event ([§7.7])."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, EventWriter(log, bus), config)

    class _RaisingPipeline:
        pack_id = "pipeline:raiser"
        version = "1.0.0"
        triggers = {"events": ["messaging.received"]}

        async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
            raise RuntimeError("boom")

    bad_pack = LoadedPipelinePack(
        pack_id="pipeline:raiser",
        version="1.0.0",
        manifest={},
        triggers={"events": ["messaging.received"]},
        events_emitted=[],
        instance=_RaisingPipeline(),
        pack_root=tmp_path / "raiser",
    )
    runner.register(bad_pack)
    await runner.start()

    try:
        eid = await log.append(
            _envelope(
                "messaging.received",
                _messaging_received_payload(),
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(eid)
        # Give the bus a moment to attempt + fail + retry-pending; the
        # checkpoint should remain pre-event (None or whatever came
        # before).
        for _ in range(30):
            status = await bus.subscriber_status("pipeline:pipeline:raiser")
            if status["consecutive_failures"] >= 1:
                break
            await asyncio.sleep(0.01)
        status = await bus.subscriber_status("pipeline:pipeline:raiser")
        assert status["checkpoint_event_id"] != eid
        assert status["consecutive_failures"] >= 1
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_status_returns_per_pack_entry(rig: dict[str, Any]) -> None:
    runner: PipelineRunner = rig["runner"]
    pack: LoadedPipelinePack = rig["pack"]
    runner.register(pack)
    await runner.start()
    snap = await runner.status()
    assert pack.pack_id in snap
    entry = snap[pack.pack_id]
    assert entry["version"] == pack.version
    assert entry["subscriber_id"] == f"pipeline:{pack.pack_id}"
    assert entry["subscriber_status"] is not None
    assert entry["subscriber_status"]["delivery_concurrency"] == 1


async def test_runner_passes_reviewed_delivery_concurrency_to_bus(
    rig: dict[str, Any],
) -> None:
    runner: PipelineRunner = rig["runner"]
    pack: LoadedPipelinePack = rig["pack"]
    pack = LoadedPipelinePack(
        pack_id=pack.pack_id,
        version=pack.version,
        manifest=pack.manifest,
        triggers=pack.triggers,
        events_emitted=pack.events_emitted,
        instance=pack.instance,
        pack_root=pack.pack_root,
        replay_on_version_change=pack.replay_on_version_change,
        delivery_concurrency=3,
    )
    runner.register(pack)
    await runner.start()

    status = await rig["bus"].subscriber_status(f"pipeline:{pack.pack_id}")
    assert status["delivery_concurrency"] == 3


async def test_startup_reconciliation_is_bounded_terminal_and_checkpoint_free(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, EventWriter(log, bus), config)
    invalidate_cache()
    runner.register(load_pipeline_pack(NOISE_FILTERING_ROOT))
    await runner.start(start_bus=False)

    try:
        first_id = await log.append(
            _recovery_request(
                tenant_id=config.tenant_id,
                request_id="runner-request-1",
                source_event_id="missing-source-1",
                event_at_ms=1_786_500_000_000,
            )
        )
        second_id = await log.append(
            _recovery_request(
                tenant_id=config.tenant_id,
                request_id="runner-request-2",
                source_event_id="missing-source-2",
                event_at_ms=1_786_500_000_001,
            )
        )
        terminal_id = await log.append(
            _recovery_request(
                tenant_id=config.tenant_id,
                request_id="runner-request-3",
                source_event_id="missing-source-3",
                event_at_ms=1_786_500_000_002,
            )
        )
        await log.append(
            _recovery_refusal(
                tenant_id=config.tenant_id,
                request_event_id=terminal_id,
                source_event_id="missing-source-3",
                event_at_ms=1_786_500_000_003,
            ),
            causation_id=terminal_id,
        )

        with pytest.raises(RuntimeError, match="ceiling exceeded"):
            await runner.reconcile_pending_source_assessment_recoveries(max_pending=1)
        refusals_before = [
            event
            async for event in log.read_since(
                types=["source.assessment.recovery_refused"]
            )
        ]
        assert [event["payload"]["request_event_id"] for event in refusals_before] == [
            terminal_id
        ]

        assert await runner.reconcile_pending_source_assessment_recoveries() == 2
        assert await runner.reconcile_pending_source_assessment_recoveries() == 0
        refusals_after = [
            event
            async for event in log.read_since(
                types=["source.assessment.recovery_refused"]
            )
        ]
        assert refusals_after == refusals_before
        retired = [
            event
            async for event in log.read_since(
                types=["communication_intake.command_retired"]
            )
        ]
        assert {event["payload"]["command_event_id"] for event in retired} == {
            first_id,
            second_id,
        }
        assert all(
            event["payload"]["reason"] == "explicit_member_submission_required"
            and event["payload"]["language_called"] is False
            and event["payload"]["candidate_count"] == 0
            for event in retired
        )
        status = await bus.subscriber_status("pipeline:pipeline:noise_filtering")
        assert status["checkpoint_event_id"] is None
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
        invalidate_cache()


async def _source_publication_plan(
    log: EventLog, *, tenant_id: str, index: int
) -> dict[str, Any]:
    owner = "private:member-publication"
    source_id = await log.append(
        _envelope(
            "messaging.received",
            _messaging_received_payload(),
            tenant_id=tenant_id,
        ).model_copy(update={"owner_scope": owner, "visibility_scope": owner})
    )
    assessment_key = f"triage:message-features:v23:{source_id}"
    candidate_key = f"publication-fixture-{index}"
    title = f"Review fixture request {index}"
    proposal_key = f"triage:message-candidate:v23:{'a' * 64}:{candidate_key}:task"
    proposal_id = (
        "triage:message:"
        + hashlib.sha256(f"{proposal_key}:{source_id}:{title}".encode()).hexdigest()[
            :24
        ]
    )
    draft = {
        "idempotency_key": proposal_key,
        "proposal_schema_version": 4,
        "payload": {
            "proposal_id": proposal_id,
            "target_kind": "task",
            "owner_member_id": "member-publication",
            "title": title,
            "candidate": {},
            "route": {"member_id": "member-publication"},
            "source_event_id": source_id,
            "candidate_key": candidate_key,
            "eligibility_rule_ids": ["fixture-reviewed-rule"],
            "extraction_event_id": None,
        },
    }
    assessment = _envelope(
        "triage.intent.features_extracted",
        {
            "intent_id": f"source:{source_id}",
            "source_event_id": source_id,
            "skill_call_event_id": None,
            "extractor_id": "fixture-publication-assessor",
            "language": None,
            "candidates": [
                {"candidate_key": candidate_key, "target_kind": "task", "title": title}
            ],
            "extraction_state": "complete",
            "extracted_at": EventEnvelope.now_utc_iso(),
            "assessment_outcome": "eligible_candidates",
            "reason_codes": [
                "language_assessment_completed",
                "eligible_candidate_found",
            ],
        },
        tenant_id=tenant_id,
    ).model_copy(
        update={
            "schema_version": 3,
            "owner_scope": owner,
            "visibility_scope": owner,
            "source_adapter": "deterministic_candidate_eligibility",
            "source_account_id": "pipeline",
            "idempotency_key": assessment_key,
        }
    )
    plan = assessment.model_copy(
        update={
            "type": PLAN_EVENT_TYPE,
            "schema_version": 1,
            "idempotency_key": publication_plan_key(assessment_key),
            "payload": {
                "contract": "triage-source-publication-v1",
                "source_event_id": source_id,
                "assessment_idempotency_key": assessment_key,
                "assessment_schema_version": 3,
                "proposals": [draft],
            },
        }
    )
    _, plan_id = await log.append_batch([assessment, plan], causation_id=source_id)
    event = await log.get(plan_id)
    assert event is not None
    return event


async def test_publication_startup_uses_headers_for_completed_plans_and_bounds_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)

    async def forbidden_skill(*_args, **_kwargs):
        raise AssertionError("publication recovery must never call a language skill")

    runner = PipelineRunner(
        bus,
        log,
        EventWriter(log, bus),
        config,
        run_skill_fn=forbidden_skill,
        # This test replays historical passive publication plans.
        legacy_personal_obligation_compatibility=True,
    )
    invalidate_cache()
    pack = load_pipeline_pack(NOISE_FILTERING_ROOT)
    runner.register(pack)
    await runner.start(start_bus=False)
    try:
        completed_ids: set[str] = set()
        for index in range(35):
            plan = await _source_publication_plan(
                log, tenant_id=config.tenant_id, index=index
            )
            await runner._make_callback(pack)(plan)  # noqa: SLF001
            completed_ids.add(plan["event_id"])
        pending = [
            await _source_publication_plan(log, tenant_id=config.tenant_id, index=index)
            for index in (35, 36)
        ]
        foreign = await _source_publication_plan(
            log, tenant_id="other-tenant", index=37
        )
        # Reproduce the offset left by a stopped predecessor that did not
        # understand plan events. Startup reconciliation must not rewind it.
        predecessor_checkpoint = foreign["event_id"]
        await bus.set_checkpoint(
            "pipeline:pipeline:noise_filtering", predecessor_checkpoint
        )
        get = log.get
        get_headers = log.get_event_headers
        hydrated: list[str] = []
        header_batches: list[int] = []

        async def observed_get(event_id: str):
            assert event_id not in completed_ids, "completed private draft was hydrated"
            assert event_id != foreign["event_id"], "foreign private draft was hydrated"
            hydrated.append(event_id)
            return await get(event_id)

        async def observed_headers(tenant_id: str, event_ids: list[str]):
            assert tenant_id == config.tenant_id
            header_batches.append(len(event_ids))
            return await get_headers(tenant_id, event_ids)

        with monkeypatch.context() as patch:
            patch.setattr(log, "get", observed_get)
            patch.setattr(log, "get_event_headers", observed_headers)
            before = await log.latest_event_id()
            with pytest.raises(RuntimeError, match="ceiling exceeded"):
                await runner.reconcile_pending_source_publications(max_pending=1)
            assert hydrated == []  # Preflight ceiling precedes bodies and writes.
            assert await log.latest_event_id() == before
            assert await runner.reconcile_pending_source_publications() == 2
            assert {plan["event_id"] for plan in pending}.issubset(hydrated)
            hydrated.clear()
            assert await runner.reconcile_pending_source_publications() == 0
            assert hydrated == []
        assert max(header_batches) == 32
        assert all(0 < count <= 32 for count in header_batches)
        for plan in pending:
            completion = await log.get_by_idempotency(
                config.tenant_id, publication_completion_key(plan["event_id"])
            )
            assert completion is not None
            assert completion["payload"]["published_count"] == 1
        status = await bus.subscriber_status("pipeline:pipeline:noise_filtering")
        assert status["checkpoint_event_id"] == predecessor_checkpoint
        for invalid_limit in (0, -1, 65):
            with pytest.raises(ValueError, match="between 1 and 64"):
                await runner.reconcile_pending_source_publications(
                    max_pending=invalid_limit
                )
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
        invalidate_cache()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_scope", "private:other-member"),
        ("tenant_id", "other-tenant"),
        ("idempotency_key", "wrong-completion-key"),
        ("source_adapter", "other-producer"),
        ("source_account_id", "other-account"),
        ("assessment_event_id", "wrong-assessment"),
        ("source_event_id", "wrong-source"),
        ("plan_event_id", "wrong-plan"),
        ("visibility_scope", "shared:household"),
        ("sensitivity", "sensitive"),
    ],
)
async def test_mismatched_completion_cannot_hide_pending_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, EventWriter(log, bus), config)
    invalidate_cache()
    runner.register(load_pipeline_pack(NOISE_FILTERING_ROOT))
    await runner.start(start_bus=False)
    try:
        plan = await _source_publication_plan(log, tenant_id=config.tenant_id, index=0)
        assessment = await log.get_by_idempotency(
            config.tenant_id, plan["payload"]["assessment_idempotency_key"]
        )
        assert assessment is not None
        completion = _envelope(
            COMPLETION_EVENT_TYPE,
            {
                "contract": "triage-source-publication-completed-v1",
                "plan_event_id": plan["event_id"],
                "source_event_id": plan["payload"]["source_event_id"],
                "assessment_event_id": assessment["event_id"],
                "planned_count": 1,
                "published_count": 1,
                "already_present_count": 0,
                "member_decided_count": 0,
                "superseded_count": 0,
            },
            tenant_id=config.tenant_id,
        ).model_copy(
            update={
                "source_adapter": "deterministic_candidate_eligibility",
                "source_account_id": "pipeline",
                "owner_scope": plan["owner_scope"],
                "visibility_scope": plan["visibility_scope"],
                "idempotency_key": publication_completion_key(plan["event_id"]),
            }
        )
        if field in {"assessment_event_id", "source_event_id", "plan_event_id"}:
            completion.payload[field] = value
        else:
            setattr(completion, field, value)
        await log.append(completion, causation_id=plan["event_id"])
        dispatched: list[str] = []

        async def stop_at_pending(event):
            dispatched.append(event["event_id"])
            raise RuntimeError("fixture reached pending publication handler")

        monkeypatch.setattr(runner, "_make_callback", lambda _pack: stop_at_pending)
        with pytest.raises(RuntimeError, match="reached pending publication handler"):
            await runner.reconcile_pending_source_publications()
        assert dispatched == [plan["event_id"]]
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
        invalidate_cache()


async def test_publication_startup_snapshot_does_not_expand_during_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, EventWriter(log, bus), config)
    invalidate_cache()
    runner.register(load_pipeline_pack(NOISE_FILTERING_ROOT))
    await runner.start(start_bus=False)
    try:
        metadata = log.read_metadata_since
        plan: dict[str, Any] | None = None

        async def append_after_snapshot(*args, **kwargs):
            nonlocal plan
            assert kwargs["through"] == ""
            assert kwargs["types"] == [
                PLAN_EVENT_TYPE,
                COMPLETION_EVENT_TYPE,
                REVISION_PLAN_TYPE,
                REVISION_COMPLETION_TYPE,
                "triage.currentness_publication.planned",
                "triage.currentness_publication.completed",
            ]
            assert kwargs["include_payload_for"] == [
                COMPLETION_EVENT_TYPE,
                REVISION_COMPLETION_TYPE,
                "triage.currentness_publication.completed",
            ]
            plan = await _source_publication_plan(
                log, tenant_id=config.tenant_id, index=0
            )
            async for item in metadata(*args, **kwargs):
                yield item

        with monkeypatch.context() as patch:
            patch.setattr(log, "read_metadata_since", append_after_snapshot)
            assert await runner.reconcile_pending_source_publications() == 0
        assert plan is not None
        assert (
            await log.get_by_idempotency(
                config.tenant_id, publication_completion_key(plan["event_id"])
            )
            is None
        )

        async def nonterminal_handler(_event):
            return None

        with monkeypatch.context() as patch:
            patch.setattr(runner, "_make_callback", lambda _pack: nonterminal_handler)
            with pytest.raises(RuntimeError, match="remained non-terminal"):
                await runner.reconcile_pending_source_publications()
        assert await runner.reconcile_pending_source_publications() == 1
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()
        invalidate_cache()


# ---------------------------------------------------------------------------
# parties_conn_factory seam (10b-ii-α)
# ---------------------------------------------------------------------------


async def test_runner_accepts_parties_conn_factory_none_default(
    rig: dict[str, Any],
) -> None:
    """The constructed PipelineContext carries no parties factory by default."""
    runner: PipelineRunner = rig["runner"]
    log: EventLog = rig["log"]
    bus: EventBus = rig["bus"]

    captured: dict[str, Any] = {}

    class _CapturingPipeline:
        pack_id = "pipeline:capturing"
        version = "1.0.0"
        triggers = {"events": ["messaging.received"]}

        async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
            captured["factory"] = ctx.parties_conn_factory

    pack = LoadedPipelinePack(
        pack_id="pipeline:capturing",
        version="1.0.0",
        manifest={},
        triggers={"events": ["messaging.received"]},
        events_emitted=[],
        instance=_CapturingPipeline(),
        pack_root=ECHO_LOGGER_ROOT,
    )
    runner.register(pack)
    await runner.start()

    eid = await log.append(
        _envelope(
            "messaging.received",
            _messaging_received_payload(),
            tenant_id=rig["config"].tenant_id,
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "pipeline:pipeline:capturing", eid)

    assert captured["factory"] is None


async def test_runner_threads_parties_conn_factory_into_context(
    tmp_path: Path,
) -> None:
    """A `parties_conn_factory` passed at construction time is threaded
    into the PipelineContext handed to handle()."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)

    sentinel = object()

    def _factory() -> Any:
        return sentinel

    runner = PipelineRunner(
        bus, log, EventWriter(log, bus), config, parties_conn_factory=_factory
    )

    captured: dict[str, Any] = {}

    class _CapturingPipeline:
        pack_id = "pipeline:capturing_threaded"
        version = "1.0.0"
        triggers = {"events": ["messaging.received"]}

        async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
            captured["factory"] = ctx.parties_conn_factory

    pack = LoadedPipelinePack(
        pack_id="pipeline:capturing_threaded",
        version="1.0.0",
        manifest={},
        triggers={"events": ["messaging.received"]},
        events_emitted=[],
        instance=_CapturingPipeline(),
        pack_root=tmp_path / "capturing_threaded",
    )
    runner.register(pack)
    await runner.start()

    try:
        eid = await log.append(
            _envelope(
                "messaging.received",
                _messaging_received_payload(),
                tenant_id=config.tenant_id,
            )
        )
        await bus.notify(eid)
        await _wait_for_checkpoint(bus, "pipeline:pipeline:capturing_threaded", eid)

        assert captured["factory"] is _factory
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_runner_parties_conn_factory_is_same_object_per_dispatch(
    tmp_path: Path,
) -> None:
    """The same factory object is threaded into every dispatched
    PipelineContext (it is NOT re-constructed per event); the per-event
    fresh-connection behavior comes from pipelines invoking the factory
    inside handle() via `with ctx.parties_conn_factory() as conn:`."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)

    def _factory() -> Any:
        return None

    runner = PipelineRunner(
        bus, log, EventWriter(log, bus), config, parties_conn_factory=_factory
    )

    captured: list[Any] = []

    class _CapturingPipeline:
        pack_id = "pipeline:capturing_repeated"
        version = "1.0.0"
        triggers = {"events": ["messaging.received"]}

        async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
            captured.append(ctx.parties_conn_factory)

    pack = LoadedPipelinePack(
        pack_id="pipeline:capturing_repeated",
        version="1.0.0",
        manifest={},
        triggers={"events": ["messaging.received"]},
        events_emitted=[],
        instance=_CapturingPipeline(),
        pack_root=tmp_path / "capturing_repeated",
    )
    runner.register(pack)
    await runner.start()

    try:
        last_eid = ""
        for _ in range(3):
            last_eid = await log.append(
                _envelope(
                    "messaging.received",
                    _messaging_received_payload(),
                    tenant_id=config.tenant_id,
                )
            )
            await bus.notify(last_eid)
        await _wait_for_checkpoint(
            bus, "pipeline:pipeline:capturing_repeated", last_eid
        )

        assert len(captured) == 3
        assert all(f is _factory for f in captured)
    finally:
        await runner.stop()
        await bus.stop()
        await log.close()


async def test_status_before_start_preserves_missing_subscriber_entry(rig):
    runner = rig["runner"]
    pack = rig["pack"]
    runner.register(pack)
    entry = (await runner.status())[pack.pack_id]
    assert entry["subscriber_status"] == {}
    assert entry["subscriber_id"] == f"pipeline:{pack.pack_id}"
