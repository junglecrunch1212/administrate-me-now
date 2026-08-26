"""Deterministic, credential-free stress proof for retained source facts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.skill_runner import OpenClawUnreachable
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.source_assessment_recovery import latest_source_assessment
from adminme.source_assurance import audit_source_assurance


MEMBER_ID = "member-source-stress"
OWNER_SCOPE = f"private:{MEMBER_ID}"
FIXED_SEED = 20_260_825
FIXED_START = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
ENCRYPTION_KEY = b"s" * 32


@dataclass(frozen=True)
class StressProfile:
    name: str
    source_facts: int
    maximum_seconds: float
    minimum_throughput_per_second: float
    maximum_append_p95_ms: float
    maximum_peak_mib: float
    maximum_storage_bytes_per_source: float


PROFILES = {
    "fast": StressProfile("fast", 48, 60.0, 2.0, 250.0, 384.0, 512 * 1024),
    "extended": StressProfile(
        "extended", 8_760, 1_800.0, 4.0, 250.0, 1_024.0, 256 * 1024
    ),
}


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _p95(values: list[float]) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)]


def _settings(instance_dir: Path, repository_root: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=ENCRYPTION_KEY,
        repository_root=repository_root,
        member_identities={"source-stress.ts.net": MEMBER_ID},
        member_roles={MEMBER_ID: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "Source stress fixture household",
            "members": [
                {"member_id": MEMBER_ID, "label": "Fixture member", "role": "principal"}
            ],
            "parties": [],
            "memberships": [
                {
                    "membership_id": "membership-source-stress",
                    "party_id": MEMBER_ID,
                    "role": "principal",
                }
            ],
            "relationships": [],
        },
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER_ID,
    )


def _message(index: int, occurred_at: str) -> EventEnvelope:
    sent = index % 5 == 0
    common = {
        "member_id": MEMBER_ID,
        "provider": "gmail",
        "provider_account_id": "credential-free-account",
        "workspace": "personal",
        "source_channel": "gmail",
        "native_message_id": f"stress-message-{index:05d}",
        "thread_id": f"stress-thread-{index // 3:05d}",
        "subject": "Credential-free source stress fixture",
        "body_text": f"Routine fixture update {index}.",
    }
    payload: dict[str, Any]
    if sent:
        payload = {
            **common,
            "to_identifier": "recipient@example.invalid",
            "sent_at": occurred_at,
            "delivery_status": "sent",
            "rfc_message_id": f"<stress-{index}@example.invalid>",
            "references": [],
            "in_reply_to": None,
            "body_html": f"<p>Routine fixture update {index}.</p>",
        }
        event_type = "messaging.sent"
        version = 4
    else:
        payload = {
            **common,
            "from_identifier": "sender@example.invalid",
            "to_identifier": "member@example.invalid",
            "to_identifiers": ["member@example.invalid"],
            "received_at": occurred_at,
            "rfc_message_id": f"<stress-{index}@example.invalid>",
            "references": [],
            "in_reply_to": None,
            "provider_labels": ["INBOX"],
            "attachments": [],
        }
        event_type = "messaging.received"
        version = 4
    return EventEnvelope(
        event_at_ms=1_775_214_400_000 + index,
        tenant_id="tenant-placeholder",
        type=event_type,
        schema_version=version,
        occurred_at=occurred_at,
        source_adapter="gmail",
        source_account_id="credential-free-account",
        owner_scope=OWNER_SCOPE,
        visibility_scope=OWNER_SCOPE,
        sensitivity="normal",
        idempotency_key=f"source-stress:message:{index}",
        payload=payload,
    )


def _calendar(index: int, occurred_at: str) -> EventEnvelope:
    start = FIXED_START + timedelta(days=index % 365, hours=index % 12)
    return EventEnvelope(
        event_at_ms=1_775_214_400_000 + index,
        tenant_id="tenant-placeholder",
        type="calendar.event_added",
        schema_version=2,
        occurred_at=occurred_at,
        source_adapter="calendaring-google-calendar",
        source_account_id="credential-free-account",
        owner_scope=OWNER_SCOPE,
        visibility_scope=OWNER_SCOPE,
        sensitivity="normal",
        idempotency_key=f"source-stress:calendar:{index}",
        payload={
            "member_id": MEMBER_ID,
            "provider": "google_calendar",
            "provider_account_id": "credential-free-account",
            "calendar_external_id": "stress-calendar",
            "calendar_name": "Fixture calendar",
            "external_event_id": f"stress-event-{index:05d}",
            "status": "confirmed",
            "summary": f"Fixture calendar event {index}",
            "start": _iso(start),
            "end": _iso(start + timedelta(minutes=30)),
            "availability": "busy",
            "updated_at": occurred_at,
        },
    )


def _list_item(index: int, occurred_at: str) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=1_775_214_400_000 + index,
        tenant_id="tenant-placeholder",
        type="list_item.added",
        schema_version=3,
        occurred_at=occurred_at,
        source_adapter="tasks-google-tasks",
        source_account_id="credential-free-account",
        owner_scope=OWNER_SCOPE,
        visibility_scope=OWNER_SCOPE,
        sensitivity="normal",
        idempotency_key=f"source-stress:list-item:{index}",
        payload={
            "member_id": MEMBER_ID,
            "provider": "google_tasks",
            "provider_account_id": "credential-free-account",
            "list_external_id": "stress-task-list",
            "item_external_id": f"stress-task-{index:05d}",
            "list_name": "Fixture tasks",
            "title": f"Fixture provider task {index}",
            "priority": 0,
            "completed": False,
            "alarms": [],
            "recurrence_rules": [],
            "completion_policy": "manual",
            "last_modified_at": occurred_at,
            "sync_cycle_id": "source-stress-cycle",
            "provider_position": f"{index:010d}",
            "source_links": [],
            "provider_data": {},
        },
    )


def build_source_workload(profile: str) -> list[EventEnvelope]:
    """Build exactly the profile's unique source facts with source-time disorder."""

    selected = PROFILES[profile]
    rng = random.Random(FIXED_SEED)
    offsets = list(range(selected.source_facts - 1))
    rng.shuffle(offsets)
    workload: list[EventEnvelope] = []
    for index, offset in enumerate(offsets, start=1):
        occurred_at = _iso(FIXED_START + timedelta(minutes=offset))
        selector = index % 10
        envelope = (
            _message(index, occurred_at)
            if selector < 6
            else _calendar(index, occurred_at)
            if selector < 8
            else _list_item(index, occurred_at)
        )
        workload.append(envelope)
    return workload


async def _seed_poison(settings: RuntimeSettings) -> tuple[str, str, dict[str, Any]]:
    log = EventLog(settings.instance, settings.encryption_key)
    poison = _message(0, _iso(FIXED_START))
    poison = poison.model_copy(update={"tenant_id": settings.instance.tenant_id})
    source_id = await log.append(poison)
    failed_id = await log.append(
        EventEnvelope(
            event_at_ms=poison.event_at_ms + 1,
            tenant_id=settings.instance.tenant_id,
            type="triage.intent.features_extracted",
            schema_version=3,
            occurred_at=_iso(FIXED_START + timedelta(seconds=1)),
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            owner_scope=OWNER_SCOPE,
            visibility_scope=OWNER_SCOPE,
            sensitivity="normal",
            causation_id=source_id,
            idempotency_key=f"triage:message-features:v20:{source_id}",
            payload={
                "intent_id": f"source:{source_id}",
                "source_event_id": source_id,
                "skill_call_event_id": None,
                "extractor_id": "source-stress-v20",
                "language": None,
                "candidates": [],
                "extraction_state": "unavailable",
                "extracted_at": _iso(FIXED_START + timedelta(seconds=1)),
                "assessment_outcome": "failed",
                "reason_codes": ["language_seam_OpenClawUnreachable"],
            },
        )
    )
    bus = EventBus(log, settings.instance.bus_checkpoint_path)

    async def fail(_event: dict[str, Any]) -> None:
        raise RuntimeError("source stress poison fixture")

    bus.subscribe(
        "pipeline:pipeline:noise_filtering",
        ["messaging.received", "messaging.sent", "telephony.sms_received"],
        fail,
        consumer_version="20.0.0",
    )
    await bus.start()
    await bus.notify(source_id)
    deadline = time.monotonic() + 5
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = await bus.subscriber_status("pipeline:pipeline:noise_filtering")
        if status["degraded"]:
            break
        await asyncio.sleep(0.02)
    await bus.stop()
    await log.close()
    if not status.get("degraded") or status.get("poison_event_id") != source_id:
        raise AssertionError("failed to create the deterministic v20 poison state")
    return source_id, failed_id, status


def _projection_digest(connection: Any) -> str:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY name"
        )
    ]
    rows = {
        table: sorted(
            [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')],
            key=repr,
        )
        for table in tables
    }
    return hashlib.sha256(
        json.dumps(rows, default=str, sort_keys=True).encode("utf-8")
    ).hexdigest()


async def _wait_for_assessment(runtime: Any, source_event_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        latest = await latest_source_assessment(runtime.event_log, source_event_id)
        if latest is not None and latest.get("schema_version") == 4:
            return latest
        await asyncio.sleep(0.02)
    raise TimeoutError(f"source assessment recovery did not settle for {source_event_id}")


async def _wait_for_subscriber(
    runtime: Any, subscriber_id: str, event_id: str, timeout_s: float
) -> None:
    await runtime.wait_for_subscriber(subscriber_id, event_id, timeout_s=timeout_s)


async def _wait_for_composition_quiescence(
    runtime: Any,
    timeout_s: float,
    *,
    stable_observations: int = 5,
) -> dict[str, dict[str, Any]]:
    """Wait for every bus consumer and derived append to reach a fixed point."""

    if runtime.bus is None or runtime.event_log is None:
        raise RuntimeError("production composition is unavailable")
    subscriber_ids = tuple(sorted(runtime.bus._subscribers))
    if not subscriber_ids:
        raise AssertionError("production composition registered no bus subscribers")
    deadline = time.monotonic() + timeout_s
    prior_watermark: str | None = None
    stable_count = 0
    latest_statuses: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        latest_statuses = {
            subscriber_id: await runtime.bus.subscriber_status(subscriber_id)
            for subscriber_id in subscriber_ids
        }
        degraded = [
            subscriber_id
            for subscriber_id, status in latest_statuses.items()
            if status["degraded"]
        ]
        if degraded:
            raise RuntimeError(
                "composition subscriber degraded while settling: "
                + ", ".join(degraded)
            )
        watermark = await runtime.event_log.latest_event_id()
        if (
            all(status["lag_count"] == 0 for status in latest_statuses.values())
            and watermark == prior_watermark
        ):
            stable_count += 1
            if stable_count >= stable_observations:
                return latest_statuses
        else:
            stable_count = 0
        prior_watermark = watermark
        await asyncio.sleep(0.05)
    lagged = {
        subscriber_id: status["lag_count"]
        for subscriber_id, status in latest_statuses.items()
        if status["lag_count"]
    }
    raise TimeoutError(
        "production composition did not quiesce before timeout; "
        f"lagged_subscribers={lagged}"
    )


async def _audit_assurance(
    runtime: Any, repository_root: Path, capabilities: dict[str, Any]
) -> dict[str, Any]:
    return await audit_source_assurance(
        runtime.event_log,
        repository_root=repository_root,
        capabilities=capabilities,
    )


def run_source_pipeline_stress(
    profile: str,
    *,
    instance_dir: Path,
    repository_root: Path,
) -> dict[str, Any]:
    """Run one bounded profile through the real encrypted composition root."""

    selected = PROFILES[profile]
    instance_dir.mkdir(parents=True, exist_ok=True)
    settings = _settings(instance_dir, repository_root)
    poison_source_id, failed_assessment_id, poisoned = asyncio.run(
        _seed_poison(settings)
    )
    workload = build_source_workload(profile)
    midpoint = len(workload) // 2
    append_ms: list[float] = []
    source_ids = [poison_source_id]
    transient_attempts = 0
    correction_event_id: str | None = None
    started = time.perf_counter()
    tracemalloc.start()

    for phase, phase_workload in enumerate((workload[:midpoint], workload[midpoint:])):
        app = create_application(settings)
        with TestClient(app) as client:
            portal = client.portal
            assert portal is not None
            runtime = app.state.runtime
            portal.call(_wait_for_composition_quiescence, runtime, 600)
            if phase == 0:
                current = portal.call(
                    runtime.bus.subscriber_status,
                    "pipeline:pipeline:noise_filtering",
                )
                if current["degraded"] or current["poison_event_id"] is not None:
                    raise AssertionError("v21 startup did not clear the v20 poison state")
                fingerprint = hashlib.sha256(
                    f"{poison_source_id}:{failed_assessment_id}".encode("utf-8")
                ).hexdigest()
                recovery_id = portal.call(
                    runtime.events.write,
                    EventEnvelope(
                        event_at_ms=1_775_214_400_100,
                        tenant_id=settings.instance.tenant_id,
                        type="source.assessment.recovery_requested",
                        schema_version=1,
                        occurred_at=_iso(FIXED_START + timedelta(seconds=2)),
                        source_adapter="source_assessment_recovery",
                        source_account_id=MEMBER_ID,
                        owner_scope=OWNER_SCOPE,
                        visibility_scope=OWNER_SCOPE,
                        sensitivity="normal",
                        idempotency_key="source-stress:recovery:poison",
                        payload={
                            "request_id": "source-stress-recovery-0001",
                            "batch_fingerprint": fingerprint,
                            "source_event_id": poison_source_id,
                            "failed_assessment_event_id": failed_assessment_id,
                            "expected_failure_reason": (
                                "language_seam_OpenClawUnreachable"
                            ),
                            "owner_member_id": MEMBER_ID,
                            "requested_by_actor": "source-stress-harness",
                            "authority": "guarded_release_exception",
                            "policy": "source-assessment-recovery-v1",
                            "attempt_limit": 3,
                            "max_concurrency": 2,
                        },
                    ),
                )
                portal.call(
                    _wait_for_subscriber,
                    runtime,
                    "pipeline:pipeline:noise_filtering",
                    recovery_id,
                    15,
                )
                recovered = portal.call(
                    _wait_for_assessment, runtime, poison_source_id
                )
                if recovered["payload"]["assessment_outcome"] == "failed":
                    raise AssertionError("exact poison-source recovery remained failed")

                healthy_skill = runtime.pipelines._run_skill_fn

                async def transient_skill(*args: Any, **kwargs: Any) -> Any:
                    nonlocal transient_attempts
                    transient_attempts += 1
                    if transient_attempts <= 2:
                        raise OpenClawUnreachable("credential-free transient fixture")
                    return await healthy_skill(*args, **kwargs)

                runtime.pipelines._run_skill_fn = transient_skill

            prepared = [
                envelope.model_copy(update={"tenant_id": settings.instance.tenant_id})
                for envelope in phase_workload
            ]
            if phase == 1 and prepared:
                prior_thread = "stress-thread-correction"
                for index, envelope in enumerate(prepared):
                    if envelope.type in {"messaging.received", "messaging.sent"}:
                        prepared[index] = envelope.model_copy(
                            update={
                                "payload": {**envelope.payload, "thread_id": prior_thread}
                            }
                        )
                        break

            duplicate_envelope: EventEnvelope | None = None
            duplicate_source_id: str | None = None
            if phase == 0:
                probe_index = next(
                    index
                    for index, envelope in enumerate(prepared)
                    if envelope.type in {"messaging.received", "messaging.sent"}
                )
                probe = prepared.pop(probe_index)
                probe = probe.model_copy(
                    update={"payload": {**probe.payload, "thread_id": "stress-thread-correction"}}
                )
                append_started = time.perf_counter()
                probe_id = portal.call(runtime.events.write, probe)
                append_ms.append((time.perf_counter() - append_started) * 1000)
                source_ids.append(probe_id)
                portal.call(
                    _wait_for_subscriber,
                    runtime,
                    "pipeline:pipeline:noise_filtering",
                    probe_id,
                    15,
                )
                runtime.pipelines._run_skill_fn = runtime.run_skill_fn
                if transient_attempts != 3:
                    raise AssertionError(
                        f"transient probe used {transient_attempts} attempts, expected 3"
                    )
                duplicate_envelope = probe
                duplicate_source_id = probe_id
                body = str(probe.payload.get("body_text") or "")
                correction_event_id = portal.call(
                    runtime.events.write,
                    EventEnvelope(
                        event_at_ms=probe.event_at_ms + 1,
                        tenant_id=settings.instance.tenant_id,
                        type="context.source_text.corrected",
                        schema_version=1,
                        occurred_at=_iso(FIXED_START + timedelta(seconds=3)),
                        source_adapter="member-triage",
                        source_account_id=MEMBER_ID,
                        owner_scope=OWNER_SCOPE,
                        visibility_scope=OWNER_SCOPE,
                        sensitivity="normal",
                        idempotency_key="source-stress:context-correction:1",
                        payload={
                            "correction_id": "source-stress-correction-1",
                            "source_event_id": probe_id,
                            "proposal_id": "source-stress-proposal-fixture",
                            "candidate_key": "source-stress-candidate-fixture",
                            "member_id": MEMBER_ID,
                            "algorithm": "authored-message-text-v2",
                            "authored_text_digest": hashlib.sha256(
                                body.encode("utf-8")
                            ).hexdigest(),
                            "evidence_start": 0,
                            "evidence_end": len(body),
                            "corrected_by_member_id": MEMBER_ID,
                            "corrected_at": _iso(FIXED_START + timedelta(seconds=3)),
                            "reason": "quoted_or_forwarded_history",
                            "evidence_quote_digest": hashlib.sha256(
                                body.encode("utf-8")
                            ).hexdigest(),
                        },
                    ),
                )
                portal.call(
                    _wait_for_subscriber,
                    runtime,
                    "projection:context_fabric",
                    correction_event_id,
                    15,
                )

            for offset in range(0, len(prepared), 64):
                batch = prepared[offset : offset + 64]
                append_started = time.perf_counter()
                ids = portal.call(runtime.events.write_batch, batch)
                duration = (time.perf_counter() - append_started) * 1000
                append_ms.extend([duration / len(batch)] * len(batch))
                source_ids.extend(ids)
            if duplicate_envelope is not None and duplicate_source_id is not None:
                duplicate_id = portal.call(runtime.events.write, duplicate_envelope)
                if duplicate_id != duplicate_source_id:
                    raise AssertionError("exact duplicate did not reuse the source id")
            portal.call(_wait_for_composition_quiescence, runtime, 600)

    app = create_application(settings)
    with TestClient(app) as client:
        portal = client.portal
        assert portal is not None
        runtime = app.state.runtime
        lag = portal.call(_wait_for_composition_quiescence, runtime, 600)
        capabilities = runtime._source_assurance_capability_snapshot()
        assurance = portal.call(
            _audit_assurance, runtime, repository_root, capabilities
        )
        rebuild_ms: dict[str, float] = {}
        rebuild_equal: dict[str, bool] = {}
        for projection_name in ("interactions", "calendars", "lists", "triage"):
            before = _projection_digest(runtime.projections.connection(projection_name))
            rebuild_started = time.perf_counter()
            portal.call(runtime.projections.rebuild, projection_name)
            rebuild_ms[projection_name] = round(
                (time.perf_counter() - rebuild_started) * 1000, 2
            )
            after = _projection_digest(runtime.projections.connection(projection_name))
            rebuild_equal[projection_name] = before == after
        event_count = portal.call(runtime.event_log.count)
        storage_bytes = sum(
            path.stat().st_size
            for path in instance_dir.rglob("*")
            if path.is_file()
        )
        _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    elapsed = time.perf_counter() - started
    counts = assurance["counts"]
    metrics = {
        "elapsed_seconds": round(elapsed, 3),
        "source_facts": len(source_ids),
        "event_count": event_count,
        "throughput_source_facts_per_second": round(len(source_ids) / elapsed, 2),
        "append_p95_ms_per_source": round(_p95(append_ms), 3),
        "tracemalloc_peak_mib": round(peak_bytes / 1024 / 1024, 2),
        "encrypted_instance_storage_bytes": storage_bytes,
        "storage_bytes_per_source": round(storage_bytes / len(source_ids), 2),
        "out_of_order_source_times": sum(
            first.occurred_at > second.occurred_at
            for first, second in zip(workload, workload[1:])
        ),
        "transient_attempts": transient_attempts,
        "poison_failures_before_upgrade": poisoned["consecutive_failures"],
        "rebuild_ms": rebuild_ms,
    }
    budgets = {
        "maximum_seconds": selected.maximum_seconds,
        "minimum_throughput_per_second": selected.minimum_throughput_per_second,
        "maximum_append_p95_ms": selected.maximum_append_p95_ms,
        "maximum_peak_mib": selected.maximum_peak_mib,
        "maximum_storage_bytes_per_source": selected.maximum_storage_bytes_per_source,
        "maximum_terminal_lag": 0,
        "maximum_missing_failed_pending": 0,
        "maximum_rebuild_mismatches": 0,
    }
    failures: list[str] = []
    if elapsed > selected.maximum_seconds:
        failures.append("maximum_seconds")
    if metrics["throughput_source_facts_per_second"] < selected.minimum_throughput_per_second:
        failures.append("minimum_throughput_per_second")
    if metrics["append_p95_ms_per_source"] > selected.maximum_append_p95_ms:
        failures.append("maximum_append_p95_ms")
    if metrics["tracemalloc_peak_mib"] > selected.maximum_peak_mib:
        failures.append("maximum_peak_mib")
    if metrics["storage_bytes_per_source"] > selected.maximum_storage_bytes_per_source:
        failures.append("maximum_storage_bytes_per_source")
    if any(value["lag_count"] for value in lag.values()):
        failures.append("maximum_terminal_lag")
    if any(counts[name] for name in ("missing", "failed", "pending")):
        failures.append("maximum_missing_failed_pending")
    if not all(rebuild_equal.values()):
        failures.append("maximum_rebuild_mismatches")
    return {
        "schema_version": 1,
        "profile": profile,
        "seed": FIXED_SEED,
        "fixed_start": _iso(FIXED_START),
        "boundary": "credential-free encrypted production composition; no live providers",
        "coverage": {
            "gmail": True,
            "calendar": True,
            "tasks": True,
            "authored_text_correction": correction_event_id is not None,
            "exact_duplicate": True,
            "out_of_order_source_time": metrics["out_of_order_source_times"] > 0,
            "restart": True,
            "transient_openclaw_failure": transient_attempts == 3,
            "persisted_poison_upgrade": poisoned["degraded"] is True,
            "exact_failed_assessment_recovery": True,
            "projection_rebuild": rebuild_equal,
        },
        "source_assurance_counts": counts,
        "subscriber_lag": {name: value["lag_count"] for name, value in lag.items()},
        "metrics": metrics,
        "budgets": budgets,
        "failures": failures,
        "status": "PASS" if not failures else "FAIL",
    }


__all__ = ["PROFILES", "build_source_workload", "run_source_pipeline_stress"]
