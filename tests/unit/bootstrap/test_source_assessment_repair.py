"""Exact degraded-baseline enforcement for the guarded updater."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store
from bootstrap.source_assessment_repair import (
    BaselineCounts,
    BaselineTarget,
    SourceAssessmentRepairError,
    SourceAssessmentRepairPolicy,
    load_repair_policy,
    offline_validate_repair_baseline,
    validate_declared_baseline,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
ACTIVE_COMMIT = "2fede3f6a72f3ee6df99c0cefb7fca71471ff7fe"
CANDIDATE_COMMIT = "a" * 40
TENANT = "tenant-release-repair"


def _policy(source_id: str) -> SourceAssessmentRepairPolicy:
    return SourceAssessmentRepairPolicy(
        contract="source-assessment-degraded-baseline-v2",
        required_active_commit=ACTIVE_COMMIT,
        observation_mode_required=True,
        expected_counts=BaselineCounts(
            minimum_obligations=1, pending=0, missing=0, failed=1
        ),
        targets=[
            BaselineTarget(
                source_event_id=source_id,
                expected_failure_reason="language_seam_OpenClawUnreachable",
            )
        ],
        expires_after_activation=True,
    )


def _doctor() -> dict[str, object]:
    return {
        "status": "failed",
        "checks": [{"name": "source_assurance_terminal_dispositions", "ok": False}],
    }


def _diagnostics(
    source_id: str, *, watermark_event_id: str | None = None
) -> dict[str, object]:
    return {
        "observation_active": True,
        "ready": True,
        "capabilities": {
            "pipeline:pipeline:noise_filtering": {
                "state": "healthy",
                "details": {"checkpoint_event_id": source_id},
            },
            "projection:triage": {
                "state": "healthy",
                "details": {"checkpoint_event_id": source_id},
            },
            "policy:source-assurance": {
                "state": "degraded",
                "details": {
                    "state": "degraded",
                    "counts": {
                        "obligations": 1,
                        "accounted": 1,
                        "pending": 0,
                        "missing": 0,
                        "failed": 1,
                    },
                    "failed": [
                        {
                            "event_id": source_id,
                            "reason": "language_seam_OpenClawUnreachable",
                        }
                    ],
                    "content_exposed": False,
                    "watermark_event_id": watermark_event_id or source_id,
                    "freshness": {
                        "state": "fresh",
                        "revision_current": True,
                        "pending_recheck_required": False,
                        "settled_revision": True,
                        "refresh_error_type": None,
                    },
                },
            },
            "product:member": {"state": "degraded"},
            "surface:triage-processing": {"state": "degraded"},
        },
    }


def _seed_healthy_consumer_health(paths: DeploymentPaths) -> None:
    checkpoint_db = paths.instance_dir / "events" / "bus_checkpoints.db"
    connection = sqlite3.connect(checkpoint_db)
    try:
        connection.execute(
            "CREATE TABLE bus_consumer_offsets ("
            "subscriber_id TEXT PRIMARY KEY, checkpoint_event_id TEXT, "
            "last_success_at_ms INTEGER, last_failure_at_ms INTEGER, "
            "consecutive_failures INTEGER NOT NULL DEFAULT 0, "
            "degraded INTEGER NOT NULL DEFAULT 0, poison_event_id TEXT, "
            "consumer_version TEXT)"
        )
        connection.executemany(
            "INSERT INTO bus_consumer_offsets "
            "(subscriber_id, checkpoint_event_id) VALUES (?, ?)",
            (
                ("pipeline:pipeline:noise_filtering", None),
                ("projection:triage", None),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _latest_event_id(paths: DeploymentPaths, event_key: bytes) -> str:
    async def read() -> str:
        log = EventLog(paths.instance_dir / "events" / "events.db", event_key)
        try:
            event_id = await log.latest_event_id()
            assert event_id is not None
            return event_id
        finally:
            await log.close()

    return asyncio.run(read())


def _source() -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=1_786_420_000_000,
        tenant_id=TENANT,
        type="messaging.received",
        schema_version=3,
        occurred_at="2026-08-11T16:00:00Z",
        source_adapter="gmail",
        source_account_id="fixture-account",
        owner_scope="private:member-release",
        visibility_scope="private:member-release",
        sensitivity="normal",
        idempotency_key="release-repair-source",
        payload={
            "provider": "gmail",
            "provider_account_id": "fixture-account",
            "member_id": "member-release",
            "source_channel": "gmail",
            "native_message_id": "release-repair-source",
            "thread_id": "release-repair-thread",
            "from_identifier": "sender@example.invalid",
            "to_identifier": "member@example.invalid",
            "workspace": "personal",
            "subject": "Fixture release repair",
            "body_text": "Content must never enter updater evidence.",
            "received_at": "2026-08-11T16:00:00Z",
            "provider_labels": ["INBOX"],
            "attachments": [],
        },
    )


def test_checked_in_policy_uses_original_floor_and_exact_failure_set() -> None:
    policy = load_repair_policy(REPO_ROOT)

    assert policy.contract == "source-assessment-degraded-baseline-v2"
    assert policy.expected_counts.minimum_obligations == 62_623
    assert policy.expected_counts.pending == 0
    assert policy.expected_counts.missing == 0
    assert policy.expected_counts.failed == 4
    assert len(policy.targets) == 4


async def _seed(log: EventLog) -> tuple[str, str]:
    source_id = await log.append(_source())
    failed_id = await log.append(
        EventEnvelope(
            event_at_ms=1_786_420_000_001,
            tenant_id=TENANT,
            type="triage.intent.features_extracted",
            schema_version=3,
            occurred_at="2026-08-11T16:00:01Z",
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            owner_scope="private:member-release",
            visibility_scope="private:member-release",
            sensitivity="normal",
            idempotency_key=f"triage:message-features:v19:{source_id}",
            payload={
                "intent_id": f"source:{source_id}",
                "source_event_id": source_id,
                "skill_call_event_id": None,
                "extractor_id": "fixture-source-assessor",
                "language": None,
                "candidates": [],
                "extraction_state": "unavailable",
                "extracted_at": "2026-08-11T16:00:01Z",
                "assessment_outcome": "failed",
                "reason_codes": ["language_seam_OpenClawUnreachable"],
            },
        ),
        causation_id=source_id,
    )
    return source_id, failed_id


def test_declared_baseline_rejects_any_extra_degradation_or_failure_change() -> None:
    source_id = "ev_fixture_source"
    policy = _policy(source_id)
    diagnostics = _diagnostics(source_id)
    validate_declared_baseline(
        policy,
        active_commit=ACTIVE_COMMIT,
        doctor=_doctor(),
        diagnostics=diagnostics,
    )

    changed = json.loads(json.dumps(diagnostics))
    changed["capabilities"]["pipeline:another"] = {"state": "degraded"}
    with pytest.raises(SourceAssessmentRepairError, match="unexpected capability"):
        validate_declared_baseline(
            policy,
            active_commit=ACTIVE_COMMIT,
            doctor=_doctor(),
            diagnostics=changed,
        )

    changed = json.loads(json.dumps(diagnostics))
    changed["ready"] = False
    with pytest.raises(SourceAssessmentRepairError, match="ready live runtime"):
        validate_declared_baseline(
            policy,
            active_commit=ACTIVE_COMMIT,
            doctor=_doctor(),
            diagnostics=changed,
        )

    changed = json.loads(json.dumps(diagnostics))
    changed["capabilities"]["policy:source-assurance"]["details"]["failed"][0][
        "reason"
    ] = "language_seam_OpenClawTimeout"
    with pytest.raises(SourceAssessmentRepairError, match="ids or reasons"):
        validate_declared_baseline(
            policy,
            active_commit=ACTIVE_COMMIT,
            doctor=_doctor(),
            diagnostics=changed,
        )


def test_declared_baseline_accepts_only_new_fully_settled_obligations() -> None:
    source_id = "ev_fixture_source"
    policy = _policy(source_id)
    diagnostics = _diagnostics(source_id)
    counts = diagnostics["capabilities"]["policy:source-assurance"]["details"]["counts"]
    counts["obligations"] = 10
    counts["accounted"] = 10

    snapshot = validate_declared_baseline(
        policy,
        active_commit=ACTIVE_COMMIT,
        doctor=_doctor(),
        diagnostics=diagnostics,
    )

    assert snapshot == {
        "minimum_obligations": 1,
        "counts": {
            "obligations": 10,
            "accounted": 10,
            "pending": 0,
            "missing": 0,
            "failed": 1,
        },
        "failed_target_count": 1,
        "watermark_event_id": source_id,
        "content_exposed": False,
    }

    counts["accounted"] = 9
    with pytest.raises(SourceAssessmentRepairError, match="counts differ"):
        validate_declared_baseline(
            policy,
            active_commit=ACTIVE_COMMIT,
            doctor=_doctor(),
            diagnostics=diagnostics,
        )

    counts["accounted"] = 10
    diagnostics["capabilities"]["policy:source-assurance"]["details"]["freshness"][
        "revision_current"
    ] = False
    with pytest.raises(SourceAssessmentRepairError, match="fresh settled"):
        validate_declared_baseline(
            policy,
            active_commit=ACTIVE_COMMIT,
            doctor=_doctor(),
            diagnostics=diagnostics,
        )

    diagnostics = _diagnostics(source_id)
    del diagnostics["capabilities"]["policy:source-assurance"]["details"][
        "watermark_event_id"
    ]
    with pytest.raises(SourceAssessmentRepairError, match="exact event watermark"):
        validate_declared_baseline(
            policy,
            active_commit=ACTIVE_COMMIT,
            doctor=_doctor(),
            diagnostics=diagnostics,
        )


def test_stopped_activation_rejects_post_validation_event_or_consumer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment", production=False)
    config = paths.instance_dir / "config"
    events = paths.instance_dir / "events"
    config.mkdir(parents=True)
    events.mkdir(parents=True)
    event_key = b"e" * 32
    reference = secret_store(paths, test_mode=True).put("event-log", event_key)
    (config / "secret-references.json").write_text(
        json.dumps({"references": {"event_log": reference}}), encoding="utf-8"
    )

    async def seed_and_append_error() -> tuple[str, str]:
        log = EventLog(events / "events.db", event_key)
        try:
            source_id, failed_id = await _seed(log)
            await log.append(
                EventEnvelope(
                    event_at_ms=1_786_420_000_002,
                    tenant_id=TENANT,
                    type="adapter.error",
                    schema_version=1,
                    occurred_at="2026-08-11T16:00:02Z",
                    source_adapter="adapter_supervisor",
                    source_account_id="google-tasks",
                    owner_scope="shared:household",
                    visibility_scope="shared:household",
                    sensitivity="normal",
                    idempotency_key="fixture:post-validation-adapter-error",
                    payload={
                        "adapter_name": "Google Tasks",
                        "adapter_family": "tasks",
                        "severity": "transient",
                        "error_kind": "network",
                        "error_message": "fixture post-validation failure",
                        "retry_after_s": 1,
                        "occurred_at": "2026-08-11T16:00:02Z",
                    },
                )
            )
            return source_id, failed_id
        finally:
            await log.close()

    source_id, live_watermark = asyncio.run(seed_and_append_error())
    _seed_healthy_consumer_health(paths)
    with pytest.raises(SourceAssessmentRepairError, match="Gateway was not live"):
        offline_validate_repair_baseline(
            paths,
            repository_root=REPO_ROOT,
            candidate_commit=CANDIDATE_COMMIT,
            policy=_policy(source_id),
            diagnostics=_diagnostics(source_id, watermark_event_id=live_watermark),
            gateway_live_at_quiescence=False,
            append_requests=True,
            test_mode=True,
        )
    with pytest.raises(SourceAssessmentRepairError, match="event log advanced"):
        offline_validate_repair_baseline(
            paths,
            repository_root=REPO_ROOT,
            candidate_commit=CANDIDATE_COMMIT,
            policy=_policy(source_id),
            diagnostics=_diagnostics(source_id, watermark_event_id=live_watermark),
            gateway_live_at_quiescence=True,
            append_requests=True,
            test_mode=True,
        )

    current_watermark = _latest_event_id(paths, event_key)
    checkpoint_db = events / "bus_checkpoints.db"
    connection = sqlite3.connect(checkpoint_db)
    try:
        connection.execute(
            "UPDATE bus_consumer_offsets SET degraded=1, "
            "consecutive_failures=1 WHERE subscriber_id LIKE 'pipeline:%'"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(SourceAssessmentRepairError, match="degraded or poisoned"):
        offline_validate_repair_baseline(
            paths,
            repository_root=REPO_ROOT,
            candidate_commit=CANDIDATE_COMMIT,
            policy=_policy(source_id),
            diagnostics=_diagnostics(source_id, watermark_event_id=current_watermark),
            gateway_live_at_quiescence=True,
            append_requests=True,
            test_mode=True,
        )

    connection = sqlite3.connect(checkpoint_db)
    try:
        connection.execute(
            "UPDATE bus_consumer_offsets SET degraded=0, consecutive_failures=0 "
            "WHERE subscriber_id LIKE 'pipeline:%'"
        )
        connection.execute(
            "DELETE FROM bus_consumer_offsets WHERE subscriber_id LIKE 'projection:%'"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(SourceAssessmentRepairError, match="final live readiness"):
        offline_validate_repair_baseline(
            paths,
            repository_root=REPO_ROOT,
            candidate_commit=CANDIDATE_COMMIT,
            policy=_policy(source_id),
            diagnostics=_diagnostics(source_id, watermark_event_id=current_watermark),
            gateway_live_at_quiescence=True,
            append_requests=True,
            test_mode=True,
        )


def test_offline_activation_appends_only_exact_content_free_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment", production=False)
    config = paths.instance_dir / "config"
    events = paths.instance_dir / "events"
    config.mkdir(parents=True)
    events.mkdir(parents=True)
    store = secret_store(paths, test_mode=True)
    event_key = b"e" * 32
    reference = store.put("event-log", event_key)
    (config / "secret-references.json").write_text(
        json.dumps({"references": {"event_log": reference}}), encoding="utf-8"
    )

    async def seed() -> tuple[str, str]:
        log = EventLog(events / "events.db", event_key)
        try:
            return await _seed(log)
        finally:
            await log.close()

    source_id, failed_id = asyncio.run(seed())
    _seed_healthy_consumer_health(paths)
    evidence = offline_validate_repair_baseline(
        paths,
        repository_root=REPO_ROOT,
        candidate_commit=CANDIDATE_COMMIT,
        policy=_policy(source_id),
        diagnostics=_diagnostics(source_id, watermark_event_id=failed_id),
        gateway_live_at_quiescence=True,
        append_requests=True,
        test_mode=True,
    )
    assert evidence["requested"] == 1
    assert evidence["content_exposed"] is False
    assert evidence["stopped_log_snapshot"]["minimum_obligations"] == 1
    assert evidence["stopped_log_snapshot"]["counts"] == {
        "obligations": 1,
        "accounted": 1,
        "pending": 0,
        "missing": 0,
        "failed": 1,
    }
    assert evidence["stopped_log_snapshot"]["watermark_event_id"] == failed_id
    assert evidence["stopped_log_snapshot"]["consumer_health"] == {
        "pipeline_count": 1,
        "projection_count": 1,
        "unhealthy_count": 0,
        "content_exposed": False,
    }
    assert evidence["stopped_log_snapshot"]["gateway_live_at_quiescence"] is True
    assert "Content must never" not in str(evidence)

    async def inspect() -> None:
        log = EventLog(events / "events.db", event_key)
        try:
            request = await log.get(evidence["request_event_ids"][0])
            assert request is not None
            assert request["type"] == "source.assessment.recovery_requested"
            assert request["payload"]["source_event_id"] == source_id
            assert request["payload"]["failed_assessment_event_id"] == failed_id
            assert request["payload"]["authority"] == "guarded_release_exception"
            assert request["payload"]["attempt_limit"] == 3
            assert request["payload"]["max_concurrency"] == 2
            assert "Content must never" not in str(request)
        finally:
            await log.close()

    asyncio.run(inspect())

    changed_policy = _policy(source_id).model_copy(
        update={
            "expected_counts": BaselineCounts(
                minimum_obligations=2, pending=0, missing=0, failed=1
            )
        }
    )
    with pytest.raises(SourceAssessmentRepairError, match="counts differ"):
        offline_validate_repair_baseline(
            paths,
            repository_root=REPO_ROOT,
            candidate_commit=CANDIDATE_COMMIT,
            policy=changed_policy,
            diagnostics=_diagnostics(
                source_id,
                watermark_event_id=_latest_event_id(paths, event_key),
            ),
            gateway_live_at_quiescence=True,
            append_requests=False,
            test_mode=True,
        )


def test_stopped_audit_uses_persisted_checkpoint_not_stale_live_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment", production=False)
    config = paths.instance_dir / "config"
    events = paths.instance_dir / "events"
    config.mkdir(parents=True)
    events.mkdir(parents=True)
    event_key = b"e" * 32
    reference = secret_store(paths, test_mode=True).put("event-log", event_key)
    (config / "secret-references.json").write_text(
        json.dumps({"references": {"event_log": reference}}), encoding="utf-8"
    )

    async def seed() -> tuple[str, str]:
        log = EventLog(events / "events.db", event_key)
        try:
            source_id, _failed_id = await _seed(log)
            list_event_id = await log.append(
                EventEnvelope(
                    event_at_ms=1_786_420_000_002,
                    tenant_id=TENANT,
                    type="list.created",
                    schema_version=1,
                    occurred_at="2026-08-11T16:00:02Z",
                    source_adapter="google_tasks",
                    source_account_id="fixture-account",
                    owner_scope="private:member-release",
                    visibility_scope="private:member-release",
                    sensitivity="normal",
                    idempotency_key="fixture:stale-live-list-checkpoint",
                    payload={
                        "member_id": "member-release",
                        "provider": "google_tasks",
                        "provider_account_id": "fixture-account",
                        "list_external_id": "fixture-list",
                        "list_name": "Fixture list",
                        "is_default": False,
                        "changed_at": "2026-08-11T16:00:02Z",
                    },
                )
            )
            return source_id, list_event_id
        finally:
            await log.close()

    source_id, list_event_id = asyncio.run(seed())
    _seed_healthy_consumer_health(paths)
    checkpoint_db = events / "bus_checkpoints.db"
    connection = sqlite3.connect(checkpoint_db)
    try:
        connection.execute(
            "INSERT INTO bus_consumer_offsets "
            "(subscriber_id, checkpoint_event_id) VALUES (?, ?)",
            ("projection:lists", None),
        )
        connection.commit()
    finally:
        connection.close()

    diagnostics = _diagnostics(source_id, watermark_event_id=list_event_id)
    diagnostics["capabilities"]["projection:lists"] = {
        "state": "healthy",
        # The last live document claimed this projection had passed the event,
        # but its stopped durable row above proves otherwise.
        "details": {"checkpoint_event_id": list_event_id},
    }
    with pytest.raises(SourceAssessmentRepairError, match="counts differ"):
        offline_validate_repair_baseline(
            paths,
            repository_root=REPO_ROOT,
            candidate_commit=CANDIDATE_COMMIT,
            policy=_policy(source_id),
            diagnostics=diagnostics,
            gateway_live_at_quiescence=True,
            append_requests=True,
            test_mode=True,
        )


def test_offline_activation_moves_request_beyond_an_advanced_rollback_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A v19 rollback cannot strand the only request behind its checkpoint."""

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment", production=False)
    config = paths.instance_dir / "config"
    events = paths.instance_dir / "events"
    config.mkdir(parents=True)
    events.mkdir(parents=True)
    store = secret_store(paths, test_mode=True)
    event_key = b"e" * 32
    reference = store.put("event-log", event_key)
    (config / "secret-references.json").write_text(
        json.dumps({"references": {"event_log": reference}}), encoding="utf-8"
    )

    async def seed() -> tuple[str, str]:
        log = EventLog(events / "events.db", event_key)
        try:
            return await _seed(log)
        finally:
            await log.close()

    source_id, failed_id = asyncio.run(seed())
    _seed_healthy_consumer_health(paths)
    policy = _policy(source_id)
    first = offline_validate_repair_baseline(
        paths,
        repository_root=REPO_ROOT,
        candidate_commit=CANDIDATE_COMMIT,
        policy=policy,
        diagnostics=_diagnostics(source_id, watermark_event_id=failed_id),
        gateway_live_at_quiescence=True,
        append_requests=True,
        test_mode=True,
    )
    duplicate = offline_validate_repair_baseline(
        paths,
        repository_root=REPO_ROOT,
        candidate_commit=CANDIDATE_COMMIT,
        policy=policy,
        diagnostics=_diagnostics(
            source_id, watermark_event_id=_latest_event_id(paths, event_key)
        ),
        gateway_live_at_quiescence=True,
        append_requests=True,
        test_mode=True,
    )
    assert duplicate["request_event_ids"] == first["request_event_ids"]

    checkpoint_db = events / "bus_checkpoints.db"
    connection = sqlite3.connect(checkpoint_db)
    try:
        connection.execute(
            "UPDATE bus_consumer_offsets SET checkpoint_event_id=? "
            "WHERE subscriber_id=?",
            ("ev_after_skipped_request", "pipeline:pipeline:noise_filtering"),
        )
        connection.commit()
    finally:
        connection.close()

    after_rollback_advance = offline_validate_repair_baseline(
        paths,
        repository_root=REPO_ROOT,
        candidate_commit=CANDIDATE_COMMIT,
        policy=policy,
        diagnostics=_diagnostics(
            source_id, watermark_event_id=_latest_event_id(paths, event_key)
        ),
        gateway_live_at_quiescence=True,
        append_requests=True,
        test_mode=True,
    )
    assert after_rollback_advance["request_event_ids"] != first["request_event_ids"]

    async def refuse_request(request_event_id: str) -> None:
        log = EventLog(events / "events.db", event_key)
        try:
            request = await log.get(request_event_id)
            assert request is not None
            await log.append(
                EventEnvelope(
                    event_at_ms=int(request["event_at_ms"]) + 1,
                    tenant_id=TENANT,
                    type="source.assessment.recovery_refused",
                    schema_version=1,
                    occurred_at="2026-08-11T16:00:02Z",
                    source_adapter="noise_filtering",
                    source_account_id="pipeline",
                    owner_scope="private:member-release",
                    visibility_scope="private:member-release",
                    sensitivity="normal",
                    idempotency_key=(
                        "source-assessment-recovery-refused:" + request_event_id
                    ),
                    payload={
                        "request_event_id": request_event_id,
                        "source_event_id": source_id,
                        "reason": "observation_inactive",
                        "policy": "source-assessment-recovery-v1",
                    },
                ),
                causation_id=request_event_id,
            )
        finally:
            await log.close()

    asyncio.run(refuse_request(after_rollback_advance["request_event_ids"][0]))
    after_terminal_refusal = offline_validate_repair_baseline(
        paths,
        repository_root=REPO_ROOT,
        candidate_commit=CANDIDATE_COMMIT,
        policy=policy,
        diagnostics=_diagnostics(
            source_id, watermark_event_id=_latest_event_id(paths, event_key)
        ),
        gateway_live_at_quiescence=True,
        append_requests=True,
        test_mode=True,
    )
    assert (
        after_terminal_refusal["request_event_ids"]
        != after_rollback_advance["request_event_ids"]
    )

    async def count_requests() -> int:
        log = EventLog(events / "events.db", event_key)
        try:
            return len(
                [
                    event
                    async for event in log.read_since(
                        None, types=["source.assessment.recovery_requested"]
                    )
                ]
            )
        finally:
            await log.close()

    assert asyncio.run(count_requests()) == 3
