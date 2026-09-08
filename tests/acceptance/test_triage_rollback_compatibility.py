"""A real retained predecessor opens current V4 history in a disposable process."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.runtime.application import RuntimeSettings, create_application


ROOT = Path(__file__).resolve().parents[2]
PREDECESSOR = "890a0b5b5a86650806fcda54ac319f00a0d636cb"
MEMBER = "fixture-rollback-adult"  # fixture:tenant_data:ok
PROPOSAL = "fixture-rollback-legacy-proposal"
DISMISSED = "fixture-rollback-member-dismissal"
KEY = b"r" * 32
HEADERS = {"Tailscale-User-Login": "rollback.ts.net"}


async def _preload(settings: RuntimeSettings) -> str:
    """Retain a pre-v4 Sent interpretation and a separate member dismissal."""
    log = EventLog(settings.instance, settings.encryption_key)
    bus = EventBus(log, settings.instance.bus_checkpoint_path)
    writer = EventWriter(log, bus)
    common = {
        "event_at_ms": 1_706_705_900_000,
        "tenant_id": settings.instance.tenant_id,
        "occurred_at": "2024-01-31T15:58:20Z",
        "owner_scope": f"private:{MEMBER}",
        "visibility_scope": f"private:{MEMBER}",
        "sensitivity": "normal",
    }
    try:
        source_id = await writer.write(
            EventEnvelope(
                **common,
                type="messaging.sent",
                schema_version=3,
                source_adapter="messaging-gmail-api",
                source_account_id="fixture-rollback-account",
                idempotency_key="fixture-rollback-legacy-source",
                payload={
                    "source_channel": "gmail",
                    "to_identifier": "fixture-recipient@example.invalid",
                    "thread_id": "fixture-rollback-thread",
                    "subject": "Historical fixture reply",
                    "body_text": "I will authorize the old fixture transfer.",
                    "sent_at": common["occurred_at"],
                    "delivery_status": "sent",
                    "member_id": MEMBER,
                    "provider": "gmail",
                    "provider_account_id": "fixture-rollback-account",
                    "workspace": "personal",
                    "native_message_id": "fixture-rollback-message",
                    "rfc_message_id": "<fixture-rollback@example.invalid>",
                    "references": [],
                    "in_reply_to": None,
                    "in_reply_to_interaction_id": None,
                },
            )
        )
        proposal_event_id = await writer.write(
            EventEnvelope(
                **common,
                type="triage.item.proposed",
                schema_version=3,
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                idempotency_key=PROPOSAL,
                payload={
                    "proposal_id": PROPOSAL,
                    "target_kind": "commitment",
                    "owner_member_id": MEMBER,
                    "title": "Authorize the old fixture transfer",
                    "candidate": {},
                    "route": {"member_id": MEMBER},
                    "source_event_id": source_id,
                    "candidate_key": "fixture-legacy-promise",
                    "eligibility_rule_ids": ["grounded_language_exact_quote_v1"],
                },
            ),
            causation_id=source_id,
        )
        intent_id = await writer.write(
            EventEnvelope(
                **common,
                type="triage.intent.submitted",
                schema_version=1,
                source_adapter="direct-intake",
                source_account_id=MEMBER,
                actor_identity=MEMBER,
                idempotency_key="fixture-rollback-member-capture",
                payload={
                    "intent_id": DISMISSED,
                    "member_id": MEMBER,
                    "text": "Review the separate fixture checklist",
                    "source_surface": "triage",
                    "kind_hint": "task",
                    "source_event_id": None,
                    "submitted_at": common["occurred_at"],
                    "client_request_id": "fixture-rollback-capture",
                },
            )
        )
        await writer.write(
            EventEnvelope(
                **common,
                type="triage.item.dismissed",
                schema_version=1,
                source_adapter="member-triage",
                source_account_id=MEMBER,
                actor_identity=MEMBER,
                idempotency_key="fixture-rollback-dismissed",
                payload={
                    "proposal_id": DISMISSED,
                    "dismissed_at": common["occurred_at"],
                    "dismissed_by_member_id": MEMBER,
                    "reason": "Member dismissed fixture checklist",
                },
            ),
            causation_id=intent_id,
        )
        return str(proposal_event_id)
    finally:
        await bus.stop()
        await log.close()


async def _settle(runtime: Any) -> None:
    deadline = asyncio.get_running_loop().time() + 60.0
    prior: str | None = None
    stable = 0
    statuses: dict[str, dict[str, Any]] = {}
    while asyncio.get_running_loop().time() < deadline:
        statuses = {
            name: await runtime.bus.subscriber_status(name)
            for name in sorted(runtime.bus._subscribers)  # noqa: SLF001
        }
        assert not any(value["degraded"] for value in statuses.values()), statuses
        latest = await runtime.event_log.latest_event_id()
        stable = (
            stable + 1
            if latest == prior
            and all(value["lag_count"] == 0 for value in statuses.values())
            else 0
        )
        prior = latest
        if stable >= 2:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"rollback fixture did not settle: {statuses}")


async def _supersessions(runtime: Any) -> list[dict[str, Any]]:
    return [
        event
        async for event in runtime.event_log.read_since(
            types=["triage.item.superseded"]
        )
    ]


async def _external_writes(runtime: Any) -> int:
    return await runtime.event_log.count_since(
        None, types=["external_sync.write_requested"]
    )


def test_protected_predecessor_starts_and_serves_member_after_current_v4_supersession(
    tmp_path: Path,
) -> None:
    # Release CI checks out full history. A missing predecessor is a failed
    # rollback prerequisite, never a skipped proof or a network fetch here.
    resolved = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(ROOT),
            "rev-parse",
            f"{PREDECESSOR}^{{commit}}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    assert resolved == PREDECESSOR
    archive_bytes = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(ROOT), "archive", PREDECESSOR],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    predecessor_root = tmp_path / "protected-predecessor"
    predecessor_root.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        archive.extractall(predecessor_root, filter="data")
    old_handler = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(ROOT),
            "show",
            f"{PREDECESSOR}:adminme/projections/triage/handlers.py",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    assert (
        predecessor_root / "adminme/projections/triage/handlers.py"
    ).read_bytes() == old_handler

    instance = tmp_path / "synthetic-encrypted-state"
    instance.mkdir(mode=0o700)
    settings = RuntimeSettings.credential_free(
        instance,
        encryption_key=KEY,
        repository_root=ROOT,
        member_identities={"rollback.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
    )
    proposal_event_id = asyncio.run(_preload(settings))
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime)
        supersessions = client.portal.call(_supersessions, runtime)
        assert len(supersessions) == 1
        retirement = supersessions[0]
        assert retirement["schema_version"] == 4
        assert (
            retirement["payload"]["expected_content_revision_event_id"]
            == proposal_event_id
        )
        assert retirement["payload"]["expected_last_event_id"] == proposal_event_id
        conn = runtime.projections.connection("triage")
        rows = {
            row["proposal_id"]: dict(row)
            for row in conn.execute("SELECT * FROM triage_items").fetchall()
        }
        assert rows[PROPOSAL]["state"] == "dismissed"
        assert rows[DISMISSED]["state"] == "dismissed"
        assert rows[DISMISSED]["resolved_by"] == MEMBER
        response = client.get(
            "/api/member/v1/surfaces/triage",
            headers=HEADERS,
            params={"member_id": MEMBER},
        )
        assert response.status_code == 200, response.text
        assert client.portal.call(_external_writes, runtime) == 0

    # The current process has closed every encrypted connection before the
    # actual predecessor imports and opens these same files in a fresh process.
    result_path = tmp_path / "predecessor-result.json"
    worker = (ROOT / "tests/fixtures/triage_rollback_worker.py").read_text("utf-8")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ADMINME_", "OPENCLAW_")) and key != "PYTHONPATH"
    }
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(predecessor_root), str(predecessor_root / "bridge"))
    )
    environment["PYTHONNOUSERSITE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            str(predecessor_root),
            str(instance),
            str(result_path),
            retirement["event_id"],
        ],
        cwd=predecessor_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(result_path.read_text("utf-8"))
    assert result == {
        "contract": "protected-predecessor-triage-rollback-v1",
        "imports_from_archive": True,
        "same_retained_v4_event": True,
        "all_consumers_settled": True,
        "member_api_status": 200,
        "member_dismissal_preserved": True,
        "external_write_requests": 0,
    }
