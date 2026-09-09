"""Large encrypted source history retains truthful readiness through replay."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.skill_runner import OpenClawUnreachable
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.simulations.source_pipeline_stress import (
    _wait_for_composition_quiescence,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-assurance-owner"  # fixture:tenant_data:ok
OTHER = "fixture-assurance-other"  # fixture:tenant_data:ok
AUTH = {"Tailscale-User-Login": "assurance-owner.ts.net"}  # fixture:tenant_data:ok
OTHER_AUTH = {
    "Tailscale-User-Login": "assurance-other.ts.net",  # fixture:tenant_data:ok
}
HISTORY_SIZE = 512
PRIVATE_MARKER = "private-assurance-large-history-fixture"
LARGE_BODY = PRIVATE_MARKER + " synthetic retained calendar description" * 1024


def _envelope(
    settings: RuntimeSettings,
    event_type: str,
    key: str,
    payload: dict[str, Any],
    *,
    version: int,
    owner: str = MEMBER,
    causation_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=settings.instance.tenant_id,
        type=event_type,
        schema_version=version,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="fixture-source-assurance",
        source_account_id=f"fixture-account:{owner}",
        owner_scope=f"private:{owner}",
        visibility_scope=f"private:{owner}",
        sensitivity="normal",
        causation_id=causation_id,
        idempotency_key=f"fixture-source-assurance:{key}",
        payload=payload,
    )


async def _preload(settings: RuntimeSettings) -> tuple[str, str]:
    """Use canonical sources so bulk replay requires no synthetic model work."""
    log = EventLog(settings.instance, settings.encryption_key)
    try:
        first_id: str | None = None
        last_id = ""
        batch = []
        for index in range(HISTORY_SIZE):
            owner = OTHER if index == HISTORY_SIZE - 1 else MEMBER
            event = _envelope(
                settings,
                "calendar.event_added",
                f"calendar:{index}",
                {
                    "member_id": owner,
                    "provider": "google_calendar",
                    "provider_account_id": f"fixture-account:{owner}",
                    "calendar_external_id": "fixture-calendar",
                    "external_event_id": f"fixture-calendar-event:{index}",
                    "summary": f"Retained fixture calendar entry {index}",
                    "description": LARGE_BODY,
                    "start": "2034-02-01T10:00:00Z",
                    "end": "2034-02-01T10:30:00Z",
                    "updated_at": "2034-01-01T10:00:00Z",
                },
                version=2,
                owner=owner,
                causation_id=first_id,
            )
            if first_id is None:
                first_id = await log.append(event)
            else:
                batch.append(event)
        last_id = (await log.append_batch(batch))[-1]
        assert first_id is not None
        return first_id, last_id
    finally:
        await log.close()


def _current_assurance(client: TestClient, **expected: int) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        capability = client.get("/api/system/ready").json()["capabilities"][
            "policy:source-assurance"
        ]
        details = capability["details"]
        if (
            details["freshness"]["revision_current"]
            and not details["freshness"]["refresh_in_progress"]
            and all(details["counts"][key] == value for key, value in expected.items())
        ):
            return capability
        time.sleep(0.01)
    raise AssertionError(f"Source assurance did not reach {expected}: {capability}")


async def _assessments(runtime: Any, source_id: str) -> list[dict[str, Any]]:
    return [
        event
        for event in await runtime.event_log.get_by_causation(source_id)
        if event["type"] == "triage.intent.features_extracted"
    ]


def test_large_source_metadata_replay_preserves_readiness_and_private_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=b"m" * 32,
        repository_root=REPO_ROOT,
        member_identities={
            AUTH["Tailscale-User-Login"]: MEMBER,
            OTHER_AUTH["Tailscale-User-Login"]: OTHER,
        },
        member_roles={MEMBER: "principal", OTHER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
    )
    first_id, other_id = asyncio.run(_preload(settings))
    metadata_sources: dict[str, str | None] = {}
    original_metadata = EventLog.read_metadata_since

    async def observe_metadata(log: EventLog, *args: Any, **kwargs: Any):
        async for event in original_metadata(log, *args, **kwargs):
            if event["type"] == "calendar.event_added":
                # Observe the real auditor's selected rows, without replacing SQL.
                assert event.get("payload") is None
                metadata_sources[event["event_id"]] = event.get("causation_id")
            yield event

    monkeypatch.setattr(EventLog, "read_metadata_since", observe_metadata)
    stable_counts = None
    for boot in range(2):
        with TestClient(create_application(settings)) as client:
            assert client.portal is not None
            runtime = client.app.state.runtime
            client.portal.call(_wait_for_composition_quiescence, runtime, 60)
            initial = _current_assurance(client, pending=0, missing=0, failed=0)
            assert initial["state"] == "healthy"
            details = initial["details"]
            assert details["by_event_type"]["calendar.event_added"] == {
                "source_facts": HISTORY_SIZE,
                "accounted": HISTORY_SIZE,
                "pending": 0,
                "missing": 0,
            }
            assert len(metadata_sources) == HISTORY_SIZE
            assert metadata_sources[first_id] is None
            assert metadata_sources[other_id] == first_id
            assert PRIVATE_MARKER not in json.dumps(details)
            retained = client.portal.call(runtime.event_log.get, other_id)
            assert retained is not None
            assert retained["payload"]["description"] == LARGE_BODY
            assert retained["causation_id"] == first_id
            if boot:
                assert details["counts"] == stable_counts
                assert details["provider_effects"]["zero_effect_state"] == "healthy"
                continue

            own_activity = client.get("/api/core/v1/activity?limit=200", headers=AUTH)
            other_activity = client.get(
                "/api/core/v1/activity?limit=200", headers=OTHER_AUTH
            )
            assert own_activity.status_code == other_activity.status_code == 200
            assert other_id not in {
                row["event_id"] for row in own_activity.json()["items"]
            }
            assert other_id in {
                row["event_id"] for row in other_activity.json()["items"]
            }
            assert PRIVATE_MARKER not in own_activity.text + other_activity.text

            healthy_skill = runtime.pipelines._run_skill_fn
            entered, release = asyncio.Event(), asyncio.Event()

            async def blocked_failure(*_args: Any, **_kwargs: Any) -> Any:
                entered.set()
                await release.wait()
                raise OpenClawUnreachable("content-free source assurance fixture")

            runtime.pipelines._run_skill_fn = blocked_failure
            now = EventEnvelope.now_utc_iso()
            source_id = client.portal.call(
                runtime.events.write,
                _envelope(
                    settings,
                    "messaging.received",
                    "pending-message",
                    {
                        "member_id": MEMBER,
                        "provider": "gmail",
                        "provider_account_id": f"fixture-account:{MEMBER}",
                        "source_channel": "gmail",
                        "native_message_id": "fixture-pending-message",
                        "thread_id": "fixture-pending-thread",
                        "from_identifier": "sender@example.invalid",
                        "to_identifier": "member@example.invalid",
                        "workspace": "personal",
                        "subject": "Fixture update",
                        "body_text": "[fixture:no-candidate] No action required.",
                        "received_at": now,
                        "attachments": [],
                    },
                    version=3,
                    causation_id=first_id,
                ),
            )

            async def wait_for_entry() -> None:
                await asyncio.wait_for(entered.wait(), timeout=15)

            async def release_failure() -> None:
                release.set()

            try:
                client.portal.call(wait_for_entry)
                pending = _current_assurance(client, pending=1, failed=0, missing=0)
                assert pending["details"]["counts"]["source_facts"] == HISTORY_SIZE + 1
                assert pending["details"]["counts"]["accounted"] == HISTORY_SIZE
                assert (
                    pending["details"]["freshness"]["pending_recheck_required"] is True
                )
            finally:
                client.portal.call(release_failure)
            client.portal.call(_wait_for_composition_quiescence, runtime, 60)
            failed = _current_assurance(client, pending=0, failed=1, missing=0)
            assert failed["state"] == "degraded"
            [assessment] = client.portal.call(_assessments, runtime, source_id)
            assert assessment["causation_id"] == source_id
            assert assessment["payload"]["assessment_outcome"] == "failed"
            runtime.pipelines._run_skill_fn = healthy_skill
            recovery = client.post(
                "/api/system/source-assessment-recoveries",
                headers=AUTH,
                json={
                    "request_id": "fixture-source-assurance-recovery",
                    "targets": [
                        {
                            "source_event_id": source_id,
                            "failed_assessment_event_id": assessment["event_id"],
                            "expected_failure_reason": (
                                "language_seam_OpenClawUnreachable"
                            ),
                        }
                    ],
                },
            )
            assert recovery.status_code == 202, recovery.text
            assert recovery.json()["accepted"] == 1
            assert recovery.json()["external_write_requests"] == 0
            client.portal.call(_wait_for_composition_quiescence, runtime, 60)
            recovered = _current_assurance(client, pending=0, failed=0, missing=0)
            assert recovered["state"] == "healthy"
            final = recovered["details"]
            stable_counts = final["counts"]
            assert stable_counts["accounted"] == stable_counts["obligations"]
            assert stable_counts["no_candidate"] == 1
            assert final["recovery"]["counts"]["succeeded"] == 1
            assert final["provider_effects"]["zero_effect_state"] == "healthy"
            assert final["provider_effects"]["counts"]["write_requests"] == 0
            assert final["provider_effects"]["counts"]["provider_called_true"] == 0
            triage = client.get("/api/member/v1/surfaces/triage", headers=AUTH)
            assert triage.status_code == 200
            assert triage.json()["processing_health"]["state"] == "healthy"
            assert PRIVATE_MARKER not in triage.text
