"""Exact maintenance composition, without runtime replay or provider writes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.skill_runner.fixture import build_fixture_runtime
from adminme.runtime import source_contract_recovery as maintenance
from adminme.source_contract_recovery import SourceAssessmentContractRecoveryBatch
from tests.acceptance.test_bounded_triage_execution import OTHER, REPO_ROOT, _settings
from tests.fixtures.source_contract_recovery import seed_lineage, target_payload


def _fixture_seam(monkeypatch):
    calls = []

    def build(**kwargs):
        calls.append("composed")
        kwargs.pop("gateway_token")
        return build_fixture_runtime(**kwargs)

    monkeypatch.setattr(maintenance, "build_skill_runtime", build)
    return calls


async def _history(settings):
    log = EventLog(settings.instance, settings.encryption_key, read_only=True)
    try:
        return [event async for event in log.read_since()]
    finally:
        await log.close()


def _invoke(settings, operation, batch):
    operation.mkdir(mode=0o700, exist_ok=True)
    return asyncio.run(
        maintenance.run_source_contract_recovery(
            instance_dir=settings.instance.instance_dir,
            repository_root=REPO_ROOT,
            operation_dir=operation,
            encryption_key=settings.encryption_key,
            gateway_token=None,
            batch=batch,
            requested_by_actor=settings.fixture_member_id,
        )
    )


def test_maintenance_repairs_only_exact_source_without_consumers_or_active_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    lineage = asyncio.run(seed_lineage(settings))
    other = asyncio.run(seed_lineage(settings, member=OTHER, index=1))
    config = settings.instance.config_dir
    config.mkdir(parents=True, exist_ok=True)
    (config / "runtime.yaml").write_text("observation_mode_override:\n  active: true\n")
    before = asyncio.run(_history(settings))
    protected = {}
    for path in (
        settings.instance.bus_checkpoint_path,
        settings.instance.projection_checkpoint_path,
        settings.instance.adapter_cursor_path,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"Synthetic unopened protected checkpoint")
        protected[path] = path.read_bytes()
    config_before = {p: p.read_bytes() for p in config.rglob("*") if p.is_file()}
    calls = _fixture_seam(monkeypatch)

    async def no_consumer_start(*_args, **_kwargs):
        raise AssertionError("maintenance must not start the durable consumer bus")

    monkeypatch.setattr(EventBus, "start", no_consumer_start)
    batch = SourceAssessmentContractRecoveryBatch(
        request_id="fixture-guarded-exact-maintenance",
        targets=[target_payload(lineage)],
    )
    result = _invoke(settings, tmp_path / "operation", batch)
    assert result["status"] == "completed"
    assert result["publication_complete"] is True
    after = asyncio.run(_history(settings))
    assert after[: len(before)] == before
    appended = after[len(before) :]
    assert appended
    proposals = [event for event in appended if event["type"] == "triage.item.proposed"]
    assert proposals and all(event["schema_version"] == 5 for event in proposals)
    assert all(
        event["payload"]["source_event_id"] == lineage["source_event_id"]
        for event in proposals
    )
    skills = [event for event in appended if event["type"] == "skill.call.recorded"]
    assert len(skills) == 1 and skills[0]["payload"]["skill_version"] == "8.0.1"
    assert all(event["type"] in maintenance._ALLOWED_EVENTS for event in appended)
    assert all(
        event["owner_scope"] == f"private:{settings.fixture_member_id}"
        for event in appended
    )
    assert not any(
        event["payload"].get("source_event_id") == other["source_event_id"]
        for event in appended
    )
    assert all(path.read_bytes() == payload for path, payload in protected.items())
    assert {
        p: p.read_bytes() for p in config.rglob("*") if p.is_file()
    } == config_before
    assert _invoke(settings, tmp_path / "operation", batch) == result
    assert asyncio.run(_history(settings)) == after
    assert calls == ["composed", "composed"]
    assert "body_text" not in json.dumps(result)


def test_maintenance_writer_refuses_same_thread_other_source_atomically(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    lineage = asyncio.run(seed_lineage(settings))
    neighbor = asyncio.run(seed_lineage(settings, index=1))

    async def scenario():
        log = EventLog(settings.instance, settings.encryption_key)
        bus = EventBus(log, tmp_path / "notification.db")
        writer = EventWriter(log, bus)
        try:
            source = await log.get(lineage["source_event_id"])
            scoped = maintenance._RecoveryWriter(writer, source)
            watermark = await log.latest_event_id()
            # Even the same owner and native conversation cannot authorize a
            # different primary evidence source in this exact-source operation.
            draft = EventEnvelope(
                event_at_ms=source["event_at_ms"],
                tenant_id=source["tenant_id"],
                type="triage.item.proposed",
                schema_version=5,
                occurred_at=source["occurred_at"],
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=source["owner_scope"],
                visibility_scope=source["visibility_scope"],
                sensitivity=source["sensitivity"],
                idempotency_key="fixture-foreign-draft",
                payload={"source_event_id": neighbor["source_event_id"]},
            )
            with pytest.raises(
                maintenance.MaintenanceRecoveryError, match="outside_exact_scope"
            ):
                await scoped.write_batch(
                    [draft], causation_id=lineage["source_event_id"]
                )
            assert await log.latest_event_id() == watermark
        finally:
            writer.stop_accepting()
            await bus.stop()
            await log.close()

    asyncio.run(scenario())


def test_interrupted_transport_reservation_cannot_spend_again_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import adminme.lib.skill_runner.wrapper as wrapper

    settings = _settings(tmp_path)
    lineage = asyncio.run(seed_lineage(settings))
    batch = SourceAssessmentContractRecoveryBatch(
        request_id="fixture-guarded-ambiguous-attempt",
        targets=[target_payload(lineage)],
    )
    calls = []

    class Client:
        async def post(self, url, *, json, headers):
            calls.append(json["args"]["timeoutMs"])
            assert calls == [120_000]
            # Cancellation after the durable reservation has no terminal
            # transport response. Reopen must treat this slot as consumed.
            raise asyncio.CancelledError()

    def build(**kwargs):
        kwargs.pop("gateway_token")
        return wrapper._Runtime(
            **kwargs,
            httpx_client_factory=Client,
            gateway_url="http://fixture.invalid/tools/invoke",
            gateway_token="fixture-token",
        )

    monkeypatch.setattr(maintenance, "build_skill_runtime", build)
    with pytest.raises(asyncio.CancelledError):
        _invoke(settings, tmp_path / "operation", batch)
    before = asyncio.run(_history(settings))
    assert (
        len(
            [
                event
                for event in before
                if event["type"]
                == "source.assessment.contract_recovery_attempt_started"
            ]
        )
        == 1
    )
    result = _invoke(settings, tmp_path / "operation", batch)
    assert result["status"] == "failed"
    assert result["completed_assessment_contract"] is None
    assert calls == [120_000]
    after = asyncio.run(_history(settings))
    assert after[: len(before)] == before
    assert not any(
        event["type"] in {"external_sync.write_requested", "skill.call.recorded"}
        for event in after[len(before) :]
    )
    assert _invoke(settings, tmp_path / "operation", batch) == result
    assert asyncio.run(_history(settings)) == after


def test_maintenance_observation_refusal_precedes_request_or_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    lineage = asyncio.run(seed_lineage(settings))
    config = settings.instance.config_dir
    config.mkdir(parents=True, exist_ok=True)
    (config / "runtime.yaml").write_text(
        "observation_mode_override:\n  active: false\n"
    )
    original = asyncio.run(_history(settings))
    calls = _fixture_seam(monkeypatch)
    batch = SourceAssessmentContractRecoveryBatch(
        request_id="fixture-guarded-observation-required",
        targets=[target_payload(lineage)],
    )
    with pytest.raises(
        maintenance.MaintenanceRecoveryError, match="requires_observation"
    ):
        _invoke(settings, tmp_path / "operation", batch)
    assert calls == []
    assert asyncio.run(_history(settings)) == original
