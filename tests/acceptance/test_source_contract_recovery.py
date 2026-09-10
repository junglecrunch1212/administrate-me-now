"""Retained cross-contract recovery through the encrypted runtime composition."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import adminme.lib.skill_runner.fixture as skill_fixture
from adminme.obligation_assessment import assessment_for_proposal
from adminme.runtime.application import create_application
from adminme.simulations.source_pipeline_stress import _wait_for_composition_quiescence
from adminme.source_contract_recovery import (
    ASSESSMENT_CONTRACT,
    CONTRACT_ATTEMPT_TYPE,
    CONTRACT_RESULT_TYPE,
    contract_recovery_terminal,
)
from tests.acceptance.test_bounded_triage_execution import (
    AUTH,
    _assurance,
    _events,
    _fixture_runtime,
    _rewind_retained_pipeline,
    _settings,
)
from tests.fixtures.source_contract_recovery import seed_lineage, target_payload


API = "/api/system/source-assessment-contract-recoveries"


@pytest.mark.parametrize("legacy_outcome", ["failed", "no_candidate"])
def test_occupied_current_failure_requires_explicit_contract_recovery_and_replays_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_outcome: str
) -> None:
    settings = _settings(tmp_path)
    lineage = asyncio.run(seed_lineage(settings, legacy_outcome=legacy_outcome))
    calls: list[int] = []

    async def responder(request, args):
        calls.append(args["timeoutMs"])
        return skill_fixture._response(request)

    _fixture_runtime(monkeypatch, responder)
    body = {
        "request_id": f"fixture-contract-command-{legacy_outcome}",
        "required_assessment_contract": ASSESSMENT_CONTRACT,
        "targets": [target_payload(lineage)],
    }
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert calls == []
        assert _assurance(client, 1)["state"] == "degraded"
        original = {
            event_id: client.portal.call(runtime.event_log.get, event_id)
            for event_id in (
                lineage["base_assessment_event_id"],
                lineage["latest_assessment_event_id"],
            )
        }
        response = client.post(API, headers=AUTH, json=body)
        assert response.status_code == 202, response.text
        [request_id] = response.json()["request_event_ids"]
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert calls == [120_000]
        terminal = client.portal.call(
            contract_recovery_terminal, runtime.event_log, request_id
        )
        assert terminal["status"] == "completed"
        assert terminal["completed_assessment_contract"] == ASSESSMENT_CONTRACT
        assert terminal["publication_complete"] is True
        assessment = client.portal.call(
            runtime.event_log.get, terminal["assessment_event_id"]
        )
        assert (
            assessment["schema_version"] == 4
        )  # The installed predecessor can read it.
        assert (
            assessment["payload"]["supersedes_assessment_event_id"]
            == lineage["latest_assessment_event_id"]
        )
        assert assessment["payload"]["assessment_outcome"] == "eligible_candidates"
        proposals = client.portal.call(_events, runtime, "triage.item.proposed")
        assert proposals
        assert all(item["schema_version"] == 5 for item in proposals)
        for proposal in proposals:
            verified = assessment_for_proposal(proposal)
            assert verified is not None
            assert verified.independent_obligation is True
            assert proposal["payload"]["extraction_event_id"] == assessment["event_id"]
        assert _assurance(client, 0)["state"] == "healthy"
        for event_id, record in original.items():
            assert client.portal.call(runtime.event_log.get, event_id) == record
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        retained = client.portal.call(
            _events,
            runtime,
            "triage.intent.features_extracted",
            "triage.item.proposed",
            CONTRACT_ATTEMPT_TYPE,
            CONTRACT_RESULT_TYPE,
        )
        duplicate = client.post(API, headers=AUTH, json=body)
        assert duplicate.status_code == 202
        assert duplicate.json()["request_event_ids"] == [request_id]
        assert duplicate.json()["duplicate"] is True

    # Force historical redelivery as well as ordinary startup. Neither the
    # occupied failed base nor the legacy receipt can replace current success.
    asyncio.run(_rewind_retained_pipeline(settings))
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        assert calls == [120_000]
        assert (
            client.portal.call(
                _events,
                runtime,
                "triage.intent.features_extracted",
                "triage.item.proposed",
                CONTRACT_ATTEMPT_TYPE,
                CONTRACT_RESULT_TYPE,
            )
            == retained
        )
        assert _assurance(client, 0)["state"] == "healthy"


def test_exhausted_contract_command_does_not_gain_another_budget_on_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    lineage = asyncio.run(seed_lineage(settings))
    calls: list[int] = []

    async def responder(request, args):
        calls.append(args["timeoutMs"])
        raise httpx.ReadTimeout("Synthetic exhausted recovery", request=request)

    _fixture_runtime(monkeypatch, responder)
    body = {
        "request_id": "fixture-contract-exhausted-command",
        "required_assessment_contract": ASSESSMENT_CONTRACT,
        "targets": [target_payload(lineage)],
    }
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        response = client.post(API, headers=AUTH, json=body)
        assert response.status_code == 202, response.text
        [request_id] = response.json()["request_event_ids"]
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        terminal = client.portal.call(
            contract_recovery_terminal, runtime.event_log, request_id
        )
        assert terminal["status"] == "failed"
        assert terminal["completed_assessment_contract"] is None
        assert len(calls) == 3
        attempts = client.portal.call(_events, runtime, CONTRACT_ATTEMPT_TYPE)
        assert [event["payload"]["slot"] for event in attempts] == [0, 1, 2]
        assert _assurance(client, 1)["state"] == "degraded"
        retained = client.portal.call(
            _events, runtime, CONTRACT_ATTEMPT_TYPE, CONTRACT_RESULT_TYPE
        )

    asyncio.run(_rewind_retained_pipeline(settings))
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_wait_for_composition_quiescence, runtime, 60)
        duplicate = client.post(API, headers=AUTH, json=body)
        assert duplicate.status_code == 202
        assert duplicate.json()["request_event_ids"] == [request_id]
        replayed = client.portal.call(
            runtime.pipelines.dispatch_source_contract_recovery, request_id
        )
        assert replayed["status"] == "failed"
        assert len(calls) == 3
        assert (
            client.portal.call(
                _events, runtime, CONTRACT_ATTEMPT_TYPE, CONTRACT_RESULT_TYPE
            )
            == retained
        )
        assert _assurance(client, 1)["state"] == "degraded"
