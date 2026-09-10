"""Authenticated contract recovery through the production HTTP/event runtime."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.runtime.application import RuntimeSettings, create_application
from adminme.source_contract_recovery import (
    ASSESSMENT_CONTRACT,
    CONTRACT_REQUEST_TYPE,
    contract_recovery_terminal,
)
from tests.fixtures.source_contract_recovery import seed_lineage, target_payload


ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-contract-api-owner"  # fixture:tenant_data:ok
OTHER = "fixture-contract-api-other"  # fixture:tenant_data:ok
CHILD = "fixture-contract-api-child"  # fixture:tenant_data:ok
ENDPOINT = "/api/system/source-assessment-contract-recoveries"
HEADERS = {"Tailscale-User-Login": "contract-owner.ts.net"}
FORBIDDEN = [
    "triage.item.confirmed",
    "triage.item.materialized",
    "task.created",
    "commitment.confirmed",
    "calendar.event_added",
    "calendar.event_updated",
    "calendar.event_deleted",
    "external_sync.write_requested",
    "external_sync.write_completed",
]


def _settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=b"c" * 32,
        repository_root=ROOT,
        fixture_member_id=MEMBER,
        member_identities={
            "contract-owner.ts.net": MEMBER,
            "contract-other.ts.net": OTHER,
            "contract-child.ts.net": CHILD,
        },
        member_roles={MEMBER: "principal", OTHER: "principal", CHILD: "child"},
        deterministic_openclaw_fixture=True,
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
    )


def _body(lineage: dict[str, str]) -> dict[str, Any]:
    return {
        "request_id": "fixture-contract-api-request",
        "required_assessment_contract": ASSESSMENT_CONTRACT,
        "targets": [target_payload(lineage)],
    }


async def _settle(runtime: Any) -> None:
    deadline = asyncio.get_running_loop().time() + 20
    previous = None
    stable = 0
    while asyncio.get_running_loop().time() < deadline:
        statuses = [
            await runtime.bus.subscriber_status(name)
            for name in runtime.bus._subscribers  # noqa: SLF001
        ]
        assert statuses and not any(item["degraded"] for item in statuses), statuses
        latest = await runtime.event_log.latest_event_id()
        stable = (
            stable + 1
            if latest == previous and all(item["lag_count"] == 0 for item in statuses)
            else 0
        )
        if stable >= 2:
            return
        previous = latest
        await asyncio.sleep(0.01)
    raise AssertionError("contract API fixture failed to settle")


async def _snapshot(runtime: Any, source_id: str) -> dict[str, Any]:
    causal = await runtime.event_log.get_by_causation(source_id)
    return {
        "classifier_events": [
            event["event_id"]
            for event in causal
            if event["type"] in {"skill.call.recorded", "skill.call.failed"}
            and event["payload"].get("skill_name") == "skill:classify_message_nature"
        ],
        "commands": await runtime.event_log.count_since(
            None, types=[CONTRACT_REQUEST_TYPE]
        ),
        "forbidden": await runtime.event_log.count_since(None, types=FORBIDDEN),
    }


@pytest.mark.parametrize("legacy_outcome", ["failed", "no_candidate"])
def test_contract_api_concurrent_duplicate_and_cold_reopen_are_idempotent(
    tmp_path: Path, legacy_outcome: Any
) -> None:
    settings = _settings(tmp_path)
    lineage = asyncio.run(seed_lineage(settings, legacy_outcome=legacy_outcome))
    body = _body(lineage)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime)
        before = client.portal.call(_snapshot, runtime, lineage["source_event_id"])
        # The three failed current attempts and predecessor receipt(s) are all
        # history. Startup does not invent another current-classifier attempt.
        assert len(before["classifier_events"]) == (
            6 if legacy_outcome == "failed" else 4
        )
        assert before["commands"] == before["forbidden"] == 0

        def submit() -> Any:
            return client.post(ENDPOINT, headers=HEADERS, json=body)

        with ThreadPoolExecutor(max_workers=2) as workers:
            first, second = [
                future.result()
                for future in (workers.submit(submit), workers.submit(submit))
            ]
        assert first.status_code == second.status_code == 202
        receipts = [first.json(), second.json()]
        assert sorted(receipt["duplicate"] for receipt in receipts) == [False, True]
        assert receipts[0]["request_event_ids"] == receipts[1]["request_event_ids"]
        request_id = receipts[0]["request_event_ids"][0]
        assert all(
            receipt["accepted"] == 1
            and receipt["content_exposed"] is False
            and receipt["canonical_objects_created"] == 0
            and receipt["external_write_requests"] == 0
            for receipt in receipts
        )
        client.portal.call(_settle, runtime)
        terminal = client.portal.call(
            contract_recovery_terminal, runtime.event_log, request_id
        )
        assert terminal["status"] == "completed", terminal
        assert terminal["completed_assessment_contract"] == ASSESSMENT_CONTRACT
        after = client.portal.call(_snapshot, runtime, lineage["source_event_id"])
        assert len(after["classifier_events"]) == len(before["classifier_events"]) + 1
        assert after["commands"] == 1 and after["forbidden"] == 0
        for event_id in (
            lineage["base_assessment_event_id"],
            lineage["latest_assessment_event_id"],
        ):
            assert client.portal.call(runtime.event_log.get, event_id) is not None

        changed = {
            **body,
            "targets": [
                {
                    **body["targets"][0],
                    "expected_latest_assessment_event_id": "fixture-changed-assessment",
                }
            ],
        }
        assert client.post(ENDPOINT, headers=HEADERS, json=changed).status_code == 409

    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime)
        duplicate = client.post(ENDPOINT, headers=HEADERS, json=body)
        assert duplicate.status_code == 202 and duplicate.json()["duplicate"] is True
        assert duplicate.json()["request_event_ids"] == [request_id]
        assert (
            client.portal.call(_snapshot, runtime, lineage["source_event_id"]) == after
        )
        assert (
            client.portal.call(
                contract_recovery_terminal, runtime.event_log, request_id
            )
            == terminal
        )


def test_contract_api_refuses_cross_owner_view_as_and_invalid_batch_atomically(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    lineage = asyncio.run(seed_lineage(settings))
    body = _body(lineage)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime)
        before = client.portal.call(_snapshot, runtime, lineage["source_event_id"])
        cases = [
            ({}, 401),
            ({"Tailscale-User-Login": "contract-child.ts.net"}, 403),
            ({"Tailscale-User-Login": "contract-other.ts.net"}, 409),
            (
                {
                    "Tailscale-User-Login": "contract-other.ts.net",
                    "X-AdminMe-View-Member": MEMBER,
                },
                403,
            ),
            ({**HEADERS, "X-AdminMe-View-Member": CHILD}, 403),
        ]
        for headers, status in cases:
            response = client.post(ENDPOINT, headers=headers, json=body)
            assert response.status_code == status, response.text
            assert "required preparation form" not in response.text
        invalid = {
            **body,
            "targets": [
                body["targets"][0],
                {**body["targets"][0], "source_event_id": "fixture-absent-source"},
            ],
        }
        assert client.post(ENDPOINT, headers=HEADERS, json=invalid).status_code == 409
        for invalid in (
            {**body, "required_assessment_contract": "fixture-unreviewed-contract"},
            {**body, "targets": body["targets"] * 9},
            {**body, "targets": [{**body["targets"][0], "source_text": "untrusted"}]},
        ):
            assert (
                client.post(ENDPOINT, headers=HEADERS, json=invalid).status_code == 422
            )
        assert (
            client.portal.call(_snapshot, runtime, lineage["source_event_id"]) == before
        )


def test_contract_api_requires_observation_before_command_append(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    lineage = asyncio.run(seed_lineage(settings))
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime)
        before = client.portal.call(_snapshot, runtime, lineage["source_event_id"])
        client.portal.call(
            runtime.observation.disable, MEMBER, "Synthetic refusal proof"
        )
        response = client.post(ENDPOINT, headers=HEADERS, json=_body(lineage))
        assert response.status_code == 409
        assert (
            response.json()["detail"]
            == "source contract recovery requires observation mode"
        )
        assert (
            client.portal.call(_snapshot, runtime, lineage["source_event_id"]) == before
        )
