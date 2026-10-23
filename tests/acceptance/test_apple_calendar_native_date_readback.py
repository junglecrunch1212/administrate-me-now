"""Restarted native observation repairs Calendar visibility through ingestion."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from adminme.runtime.application import create_application
from adminme_bridge.adapters.base import BridgeContext
from adminme_bridge.config import load_config
from adminme_bridge.connector_packs.calendaring_apple_calendar_bridge import (
    AppleCalendarBridgeAdapter,
    wrapper as wrapper_module,
)
from adminme_bridge.ingest_client import IngestClient
from tests.acceptance.test_cos_member_bridge_acquisition import (
    MEMBER_A,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    PACKS,
    _bridge_headers,
    _configuration_settings,
    _enable,
    _manual_policies,
    _observation,
    _prepare_helper,
)
from tests.acceptance.test_forwarded_channel_intake import _events


class _NativeDate:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def timeIntervalSince1970(self) -> float:  # noqa: N802
        return self.value.timestamp()

    def __str__(self) -> str:
        return self.value.strftime("%Y-%m-%d %H:%M:%S +0000")


class _ReadOnlyEventStore:
    """Native boundary fixture: returns the existing event; no write methods."""

    def __init__(self, event: Any, calendar_identifier: str) -> None:
        self.event = event
        self.calendar_identifier = calendar_identifier
        self.reads = 0

    def fetch_events(
        self, *, calendar_identifier: str, start: Any, end: Any
    ) -> list[Any]:
        assert calendar_identifier == self.calendar_identifier
        assert start < self.event.startDate().value < end
        self.reads += 1
        return [self.event]


def test_native_calendar_reobservation_repairs_retained_snapshot_without_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    settings = _configuration_settings(tmp_path)
    app = create_application(settings)
    with TestClient(app, base_url="https://central-fixture.ts.net") as client:
        runtime = app.state.runtime
        _manual_policies(client)
        helper, transfer = _prepare_helper(client, tmp_path, ["apple_calendar"])
        _enable(helper, "apple_calendar")
        helper.finish_permission_phase()
        installed = load_config(helper.paths.config_path)
        pack_config = installed.pack_config[PACKS["apple_calendar"]]
        calendar_identifier = pack_config["calendar_identifier"]
        start = datetime(2031, 1, 2, 16, tzinfo=timezone.utc)
        end = datetime(2031, 1, 2, 16, 5, tzinfo=timezone.utc)
        modified = datetime(2031, 1, 1, 12, 0, 0, 123000, timezone.utc)
        created = datetime(2031, 1, 1, 11, tzinfo=timezone.utc)
        alarm = datetime(2031, 1, 2, 15, 55, tzinfo=timezone.utc)
        event_id = "native-date-retained-event"
        event = SimpleNamespace(
            eventIdentifier=lambda: event_id,
            title=lambda: "Fixture existing calendar event",
            startDate=lambda: _NativeDate(start),
            endDate=lambda: _NativeDate(end),
            lastModifiedDate=lambda: _NativeDate(modified),
            creationDate=lambda: _NativeDate(created),
            occurrenceDate=lambda: _NativeDate(start),
            calendar=lambda: SimpleNamespace(
                calendarIdentifier=lambda: calendar_identifier,
                title=lambda: "Fixture selected calendar",
            ),
            alarms=lambda: [
                SimpleNamespace(
                    absoluteDate=lambda: _NativeDate(alarm), relativeOffset=lambda: 0
                )
            ],
        )
        store = _ReadOnlyEventStore(event, calendar_identifier)
        # Preserve the exact already-observed malformed data as append-only
        # history. This models the deployed provider-description serialization.
        malformed = _observation(runtime, "apple_calendar", event_id)
        malformed["payload"].update(
            summary=event.title(),
            start=str(event.startDate()),
            end=str(event.endDate()),
            provider_revision=str(event.lastModifiedDate()),
            updated_at=str(event.lastModifiedDate()),
        )
        retained = client.post(
            "/api/bridge/ingest", headers=_bridge_headers(transfer), json=malformed
        )
        assert retained.status_code == 202, retained.text
        retained_id = retained.json()["event_id"]
        client.portal.call(runtime.wait_for_projection, "calendars", retained_id)
        old_fact = client.portal.call(runtime.event_log.get, retained_id)
        retained_enrollment = copy.deepcopy(runtime.settings.bridge_enrollments)

        def calendar(headers: dict[str, str] = MEMBER_HEADERS) -> dict[str, Any]:
            response = client.get(
                "/api/member/v1/surfaces/calendar",
                headers=headers,
                params={"date": "2031-01-02", "view": "day"},
            )
            assert response.status_code == 200, response.text
            return response.json()

        assert calendar()["events"] == []
        real_adapter = wrapper_module._load_central_adapter_class()

        def bounded_native_adapter(*, config: dict[str, Any]) -> Any:
            return real_adapter(
                config={**config, "poll_interval_s": 0},
                ek_event_store_factory=lambda: store,
                max_poll_cycles=1,
            )

        monkeypatch.setattr(
            wrapper_module,
            "_load_central_adapter_class",
            lambda: bounded_native_adapter,
        )

        async def restarted_observation() -> None:
            # Fresh wrapper/adapter means the ordinary startup snapshot is emitted.
            # The transport and central router remain the production composition.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://central-fixture.ts.net",
            ) as http:
                ingest = IngestClient(
                    central_endpoint_url="https://central-fixture.ts.net",
                    tailscale_identity_header=transfer["tailscale_identity"],
                    enrollment_id=transfer["enrollment_id"],
                    client=http,
                )
                wrapper = AppleCalendarBridgeAdapter(internal_config=pack_config)
                await wrapper.start(
                    ctx=BridgeContext(
                        member_id=MEMBER_A,
                        ingest_client=ingest,
                        correlation_id_factory=lambda: "native-date-fixture-read",
                        now_factory=lambda: datetime(
                            2031, 1, 1, 13, tzinfo=timezone.utc
                        ),
                        tenant_id=runtime.settings.instance.tenant_id,
                        enrollment_id=transfer["enrollment_id"],
                        configuration_sha256=hashlib.sha256(
                            json.dumps(
                                installed.pack_config,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                    )
                )
                assert wrapper.status().state == "running", wrapper.status()
                await wrapper.stop()

        client.portal.call(restarted_observation)
        observations = client.portal.call(_events, runtime, "calendar.event_added")
        assert len(observations) == 2
        corrected = observations[-1]
        assert corrected["event_id"] != retained_id
        assert corrected["payload"]["external_event_id"] == event_id
        payload = corrected["payload"]
        assert payload["start"] == "2031-01-02T16:00:00Z"
        assert payload["end"] == "2031-01-02T16:05:00Z"
        assert payload["created_at"] == "2031-01-01T11:00:00Z"
        assert (
            payload["updated_at"]
            == payload["provider_revision"]
            == "2031-01-01T12:00:00.123000Z"
        )
        assert payload["recurrence"]["original_start"] == payload["start"]
        assert payload["alarms"][0]["trigger_at"] == "2031-01-02T15:55:00Z"
        client.portal.call(
            runtime.wait_for_projection, "calendars", corrected["event_id"]
        )
        [visible] = calendar()["events"]
        assert visible["external_uid"] == event_id
        assert visible["start_at"] == payload["start"]
        assert visible["end_at"] == payload["end"]
        assert visible["last_event_id"] == corrected["event_id"]
        assert calendar(OTHER_HEADERS)["events"] == []
        assert client.portal.call(runtime.event_log.get, retained_id) == old_fact
        assert runtime.settings.bridge_enrollments == retained_enrollment
        assert store.reads == 1
        for kind in (
            "external_sync.write_requested",
            "external_sync.write_completed",
            "external_sync.write_failed",
            "external.sent",
            "calendar.event_deleted",
        ):
            assert client.portal.call(_events, runtime, kind) == []

    # Replaying/reopening the encrypted runtime retains both original facts and
    # the corrected single calendar row, without another provider acquisition.
    with TestClient(create_application(settings)) as restarted:
        response = restarted.get(
            "/api/member/v1/surfaces/calendar",
            headers=MEMBER_HEADERS,
            params={"date": "2031-01-02", "view": "day"},
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["events"]) == 1
        assert response.json()["events"][0]["start_at"] == "2031-01-02T16:00:00Z"
        assert (
            restarted.portal.call(
                restarted.app.state.runtime.event_log.get, retained_id
            )
            == old_fact
        )
        assert store.reads == 1
