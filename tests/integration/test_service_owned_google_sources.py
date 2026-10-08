from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from adminme.adapters.base import AdapterContext
from adminme.adapters.loader import load_builtin_adapters
from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.provider_bindings import validate_provider_bindings
from adminme.service_identities import (
    COS_SERVICE_PRINCIPAL_ID,
    validate_service_identity_config,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "config" / "cos-service-sources.example.yaml"
TEST_KEY = b"g" * 32


def _config() -> dict[str, Any]:
    value = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


async def test_real_google_producers_emit_service_owned_current_facts(
    tmp_path: Path,
) -> None:
    config = _config()
    principals, channels = validate_service_identity_config(
        config["service_identities"],
        member_ids={"member-fixture-a", "member-fixture-b"},
        household_party_ids={"party-fixture-external"},
    )
    assert len(principals) == 1
    assert len(channels) == 1
    bindings = validate_provider_bindings(
        config,
        member_roles={
            "member-fixture-a": "principal",
            "member-fixture-b": "principal",
        },
        household_mapping={},
        service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
    )
    assert {binding.kind for binding in bindings} == {
        "messaging-gmail-api",
        "calendaring-google-calendar",
        "tasks-google-tasks",
    }
    assert all(binding.owner_kind == "service_principal" for binding in bindings)
    assert all(binding.workspace == "service" for binding in bindings)

    adapters = load_builtin_adapters(
        config,
        repository_root=REPO_ROOT,
        member_roles={
            "member-fixture-a": "principal",
            "member-fixture-b": "principal",
        },
        household_mapping={},
        service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
        validate_bindings=True,
    )
    assert {getattr(adapter, "instance_name") for adapter in adapters} == {
        "cos-gmail-read",
        "cos-google-calendar-read",
        "cos-google-tasks-read",
    }

    instance = tmp_path / "instance"
    instance.mkdir()
    instance_config = load_instance_config(instance)
    log = EventLog(instance_config, TEST_KEY)
    bus = EventBus(log, instance_config.bus_checkpoint_path)
    writer = EventWriter(log, bus)
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    ctx = AdapterContext(
        event_sink=writer,
        config={},
        correlation_id_factory=lambda: "service-source-correlation",
        now_factory=lambda: now,
    )
    by_name = {adapter.name: adapter for adapter in adapters}
    gmail = by_name["messaging-gmail-api"]
    calendar = by_name["calendaring-google-calendar"]
    tasks = by_name["tasks-google-tasks"]
    body = base64.urlsafe_b64encode(b"Please capture this for review.").decode("ascii")
    try:
        await gmail._emit_messaging_received(
            ctx=ctx,
            message={
                "id": "gmail-service-1",
                "threadId": "thread-service-1",
                "internalDate": "1788264000000",
                "labelIds": ["INBOX", "UNREAD"],
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {
                            "name": "Received",
                            "value": "from sender.invalid by mx.google.com with ESMTPS id fixture",
                        },
                        {
                            "name": "Authentication-Results",
                            "value": "mx.google.com; dkim=pass header.i=@example.invalid; dmarc=pass header.from=example.invalid",
                        },
                        {
                            "name": "From",
                            "value": "Fixture A <member-a@example.invalid>",
                        },
                        {"name": "To", "value": "cos-service@example.invalid"},
                        {"name": "Subject", "value": "Direct fixture"},
                    ],
                    "body": {"data": body},
                },
            },
        )
        await calendar._emit_calendar_snapshot(
            ctx=ctx,
            event_type="calendar.event_added",
            calendar_id="primary",
            calendar_name="CoS fixture calendar",
            event={
                "id": "calendar-service-1",
                "summary": "Fixture observation",
                "status": "confirmed",
                "start": {"dateTime": "2026-09-02T12:00:00Z"},
                "end": {"dateTime": "2026-09-02T13:00:00Z"},
                "updated": "2026-09-01T12:00:00Z",
                "etag": "calendar-service-revision-1",
            },
        )
        await tasks._emit_item(
            ctx=ctx,
            event_type="list_item.added",
            tasklist={"id": "@default", "title": "CoS fixture tasks"},
            task={
                "id": "task-service-1",
                "title": "Fixture task observation",
                "status": "needsAction",
                "updated": "2026-09-01T12:00:00Z",
                "etag": "task-service-revision-1",
            },
        )
        events = [event async for event in log.read_since()]
        assert [(event["type"], event["schema_version"]) for event in events] == [
            ("messaging.received", 8),
            ("calendar.event_added", 3),
            ("list_item.added", 4),
        ]
        for event in events:
            assert event["owner_scope"] == (
                "service-private:service-principal:chief-of-staff"
            )
            assert event["visibility_scope"] == event["owner_scope"]
            assert event["payload"]["member_id"] is None
            assert event["payload"]["service_principal_id"] == (
                COS_SERVICE_PRINCIPAL_ID
            )
            assert event["payload"]["workspace"] == "service"
        assert events[0]["payload"]["trusted_route_key"] == "route:" + "1" * 64
        assert events[0]["payload"]["routing_scope"] == "unresolved"
        assert events[0]["payload"]["route_assurance"] == "receiver_aligned_sender"
    finally:
        await bus.stop()
        await log.close()
