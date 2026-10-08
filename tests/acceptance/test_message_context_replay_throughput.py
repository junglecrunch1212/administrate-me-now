"""Production context replay keeps output stable without scanning old bodies."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import adminme.message_context as message_context
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-context-throughput-member"  # fixture:tenant_data:ok
AUTH = {"Tailscale-User-Login": "context-throughput.ts.net"}  # fixture:tenant_data:ok
REQUEST = "Please complete the required preparation form for activity 2."


def _source(settings: RuntimeSettings, index: int, *, old: bool) -> EventEnvelope:
    occurred_at = datetime(2020 if old else 2034, 2, 1, 10, tzinfo=timezone.utc)
    occurred_at += timedelta(minutes=index)
    return EventEnvelope(
        event_at_ms=int(occurred_at.timestamp() * 1000),
        tenant_id=settings.instance.tenant_id,
        type="messaging.received",
        schema_version=3,
        occurred_at=occurred_at.isoformat(),
        source_adapter="fixture-context-throughput",
        source_account_id="fixture-context-account",
        owner_scope=f"private:{MEMBER}",
        visibility_scope=f"private:{MEMBER}",
        sensitivity="normal",
        actor_identity=MEMBER,
        idempotency_key=f"fixture-context-throughput:{old}:{index}",
        payload={
            "member_id": MEMBER,
            "provider": "fixture",
            "provider_account_id": "fixture-context-account",
            "workspace": "personal",
            "source_channel": "sms",
            "native_message_id": f"fixture-context-message:{old}:{index}",
            "thread_id": "fixture-long-lived-thread",
            "rfc_message_id": f"<fixture-throughput-{old}-{index}@example.invalid>",
            "from_identifier": "fixture-counterparty@example.invalid",
            "to_identifier": "fixture-member@example.invalid",
            "subject": "Fixture preparation request",
            "body_text": (
                "[fixture:no-candidate] " + "fixture-member@example.invalid " * 80
                if old
                else "[fixture:attention-controls:preparation:required:2]\n" + REQUEST
            ),
            "received_at": occurred_at.isoformat(),
            "attachments": [],
        },
    )


async def _preload(settings: RuntimeSettings) -> str:
    log = EventLog(settings.instance, settings.encryption_key)
    try:
        ids = await log.append_batch(
            [
                *(_source(settings, index, old=True) for index in range(102)),
                _source(settings, 0, old=False),
            ]
        )
        return ids[-1]
    finally:
        await log.close()


async def _settle(runtime: Any) -> None:
    deadline = asyncio.get_running_loop().time() + 60.0
    last_event_id = None
    stable_cycles = 0
    while asyncio.get_running_loop().time() < deadline:
        statuses = [
            await runtime.bus.subscriber_status(name)
            for name in sorted(runtime.bus._subscribers)  # noqa: SLF001
        ]
        assert not any(status["degraded"] for status in statuses), statuses
        latest = await runtime.event_log.latest_event_id()
        if (
            all(status["lag_count"] == 0 for status in statuses)
            and latest == last_event_id
        ):
            stable_cycles += 1
        else:
            stable_cycles = 0
            last_event_id = latest
        if stable_cycles == 2:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Synthetic context replay did not settle")


class _SelectedSources:
    """Minimal baseline containing only the retained current episode."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def read_since(self, **_kwargs: Any):  # type: ignore[no-untyped-def]
        for event in self.events:
            yield event


async def _events(runtime: Any, event_type: str) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since(types=[event_type])]


def test_long_thread_replay_and_new_source_preserve_production_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = RuntimeSettings.credential_free(
        tmp_path / "instance",
        # Preserve this isolated historical passive-extraction contract.
        legacy_personal_obligation_compatibility=True,
        encryption_key=b"t" * 32,
        repository_root=REPO_ROOT,
        member_identities={AUTH["Tailscale-User-Login"]: MEMBER},
        member_roles={MEMBER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
    )
    source_id = asyncio.run(_preload(settings))
    with TestClient(create_application(settings)) as client:
        assert client.portal is not None
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime)
        current = client.portal.call(runtime.event_log.get, source_id)
        assert current is not None
        baseline = client.portal.call(
            message_context.build_message_context_pack,
            current,
            _SelectedSources([current]),
        )
        edge_checks = []
        body_scans = []
        original_edges = message_context._related_edge_reasons
        original_references = message_context._referenced_identifiers

        def counted_edges(payload: Mapping[str, Any], **kwargs: Any):
            edge_checks.append(payload["rfc_message_id"])
            return original_edges(payload, **kwargs)

        def counted_references(payload: Mapping[str, Any]):
            body_scans.append(payload["rfc_message_id"])
            return original_references(payload)

        monkeypatch.setattr(message_context, "_related_edge_reasons", counted_edges)
        monkeypatch.setattr(
            message_context, "_referenced_identifiers", counted_references
        )
        replayed = client.portal.call(
            message_context.build_message_context_pack, current, runtime.event_log
        )
        assert replayed == baseline
        new_id = client.portal.call(
            runtime.events.write, _source(settings, 1, old=False)
        )
        client.portal.call(_settle, runtime)
        newest = client.portal.call(runtime.event_log.get, new_id)
        assert newest is not None
        after_append = client.portal.call(
            message_context.build_message_context_pack, newest, runtime.event_log
        )
        expected_append = client.portal.call(
            message_context.build_message_context_pack,
            newest,
            _SelectedSources([current, newest]),
        )
        assert after_append == expected_append
        assert after_append.episode_source_event_ids == (source_id, new_id)
        assert after_append.anchor_event_id == new_id
        assert edge_checks == []
        assert body_scans == []

        response = client.get("/api/member/v1/surfaces/triage", headers=AUTH)
        assert response.status_code == 200
        proposals = client.portal.call(_events, runtime, "triage.item.proposed")
        assert any(
            event["payload"].get("source_event_id") == new_id
            and event["payload"].get("interpretation", {}).get("assessment_state")
            == "verified"
            and event["payload"]["interpretation"]["obligation_assessment"][
                "independent_obligation"
            ]
            for event in proposals
        )
        assert client.portal.call(_events, runtime, "task.created") == []
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
