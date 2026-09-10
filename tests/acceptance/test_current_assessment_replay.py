"""Current negative assessments remain immutable as later thread facts arrive."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import adminme.lib.skill_runner.fixture as skill_fixture
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.runtime.application import create_application
from adminme.simulations.source_pipeline_stress import _wait_for_composition_quiescence
from tests.acceptance.test_bounded_triage_execution import (
    AUTH,
    OTHER_AUTH,
    MEMBER,
    OTHER,
    SUBSCRIBER,
    _assurance,
    _events,
    _fixture_runtime,
    _rewind_retained_pipeline,
    _settings,
    _source,
)


def _message(settings, index, member, *, receipt):
    event = _source(settings, index, owner=member)
    event.payload["thread_id"] = f"fixture-negative-replay-thread:{member}"
    if receipt:
        event.payload["body_text"] = (
            "[fixture:attention-controls:receipt:informational:1]\n"
            "Your payment was received. No action is required."
        )
    return event


def test_current_negative_survives_later_thread_anchor_and_full_redelivery(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    calls = []

    async def respond(request, args):
        calls.append(args["input"]["body_text"])
        return skill_fixture._response(request)

    _fixture_runtime(monkeypatch, respond)
    source_ids = {}
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        for offset, member in enumerate((MEMBER, OTHER)):
            first = client.portal.call(
                runtime.events.write,
                _message(settings, offset * 2, member, receipt=True),
            )
            source_ids[member] = [first]
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        negative = client.portal.call(
            _events, runtime, "triage.intent.features_extracted"
        )
        assert len(negative) == 2
        assert all(
            event["schema_version"] == 3
            and event["payload"]["assessment_outcome"] == "no_candidate"
            and event["payload"]["extraction_state"] == "complete"
            and event["payload"]["candidates"] == []
            for event in negative
        )
        assert len(calls) == 2
        for offset, member in enumerate((MEMBER, OTHER)):
            second = client.portal.call(
                runtime.events.write,
                _message(settings, offset * 2 + 1, member, receipt=False),
            )
            source_ids[member].append(second)
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert len(calls) == 4
        proposals = client.portal.call(_events, runtime, "triage.item.proposed")
        assert {event["payload"]["source_event_id"] for event in proposals} == {
            ids[1] for ids in source_ids.values()
        }
        assert all(event["schema_version"] == 5 for event in proposals)
        retained = client.portal.call(
            _events, runtime, "triage.intent.features_extracted", "triage.item.proposed"
        )
        assert _assurance(client, 0)["state"] == "healthy"

    asyncio.run(_rewind_retained_pipeline(settings))
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert len(calls) == 4
        assert (
            client.portal.call(
                _events,
                runtime,
                "triage.intent.features_extracted",
                "triage.item.proposed",
            )
            == retained
        )
        assert _assurance(client, 0)["state"] == "healthy"
        for member, headers in ((MEMBER, AUTH), (OTHER, OTHER_AUTH)):
            response = client.get("/api/core/v1/activity?limit=200", headers=headers)
            assert response.status_code == 200
            assert all(source in response.text for source in source_ids[member])
            other = OTHER if member == MEMBER else MEMBER
            assert all(source not in response.text for source in source_ids[other])
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )


async def _seed_invalid(settings, invalid):
    log = EventLog(settings.instance, settings.encryption_key)
    try:
        source = _message(settings, 0, MEMBER, receipt=True)
        source_id = await log.append(source)
        values = source.model_dump(mode="json")
        values.update(
            type="triage.intent.features_extracted",
            schema_version=3,
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            idempotency_key=f"triage:message-features:v23:{source_id}",
            payload={
                "intent_id": f"source:{source_id}",
                "source_event_id": source_id,
                "skill_call_event_id": None,
                "extractor_id": "message-candidate-eligibility-v1",
                "language": None,
                "candidates": [],
                "extraction_state": "complete",
                "extracted_at": EventEnvelope.now_utc_iso(),
                "assessment_outcome": "no_candidate",
                "reason_codes": [
                    "language_assessment_completed",
                    "no_eligible_candidate",
                ],
            },
        )
        if invalid == "owner":
            values.update(
                owner_scope=f"private:{OTHER}", visibility_scope=f"private:{OTHER}"
            )
        elif invalid == "producer":
            values["source_adapter"] = "fixture-unrecognized-assessor"
        elif invalid == "schema":
            values["schema_version"] = 2
        else:
            values["payload"]["extraction_state"] = "unavailable"
        await log.append(EventEnvelope.model_validate(values), causation_id=source_id)
        return source_id
    finally:
        await log.close()


@pytest.mark.parametrize("invalid", ["owner", "producer", "schema", "incomplete"])
def test_invalid_occupied_negative_refuses_before_any_language_attempt(
    tmp_path, monkeypatch, invalid
):
    settings = _settings(tmp_path)
    source_id = asyncio.run(_seed_invalid(settings, invalid))
    calls = []

    async def respond(request, args):
        calls.append(args)
        return skill_fixture._response(request)

    _fixture_runtime(monkeypatch, respond)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        deadline = time.monotonic() + 15
        status = {}
        while time.monotonic() < deadline:
            status = client.portal.call(runtime.bus.subscriber_status, SUBSCRIBER)
            if status["poison_event_id"] == source_id:
                break
            time.sleep(0.02)
        assert status["degraded"] is True
        assert status["poison_event_id"] == source_id
        assert status["consecutive_failures"] == 5
        assert calls == []
        assert (
            len(
                client.portal.call(_events, runtime, "triage.intent.features_extracted")
            )
            == 1
        )
        assert (
            client.portal.call(
                _events,
                runtime,
                "triage.item.proposed",
                "external_sync.write_requested",
            )
            == []
        )
