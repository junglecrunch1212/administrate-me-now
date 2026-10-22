"""Hosted production-composition races: no native provider or family access."""

from __future__ import annotations

import asyncio
import copy
import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from adminme.bridge_enrollment import (
    PROGRESS_CONTRACT,
    canonical_bytes,
    progress_signature,
)
from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import create_application
from tests.acceptance.test_cos_member_bridge_acquisition import (
    CAPABILITIES,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    PACKS,
    REQUEST_ID,
    _assert_history,
    _bridge_headers,
    _configuration_settings,
    _control,
    _enable,
    _history,
    _manual_policies,
    _observation,
    _prepare_helper,
)
from tests.acceptance.test_forwarded_channel_intake import _events


def _signed(
    helper: Any, capability: str, capability_state: str, **changes: Any
) -> dict[str, Any]:
    payload = {
        "contract": PROGRESS_CONTRACT,
        **{
            key: helper.transfer[key]
            for key in (
                "request_id",
                "enrollment_id",
                "member_id",
                "instance_id",
                "release_commit",
                "capability_set_sha256",
            )
        },
        "previous_state": helper.state,
        "state": helper.state,
        "nonce": "fixture-progress-" + capability + "-" + capability_state,
        "reported_at": EventEnvelope.now_utc_iso(),
        "device_node_id": helper.device_node_id,
        "enrollment_event_id": None,
        "capability_id": capability,
        "capability_state": capability_state,
        "reason": None,
        "last_successful_observation": None,
        "pack_config": None,
        "capability_event_id": None,
        **changes,
    }
    return {**payload, "signature": progress_signature(helper._proof_key(), payload)}


def _status(client: TestClient, transfer: dict[str, Any]) -> dict[str, Any]:
    response = client.get(
        "/api/bridge/enrollment/status",
        params={"request_id": REQUEST_ID},
        headers=_bridge_headers(transfer),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _begin_source(helper: Any, capability: str) -> None:
    helper.record_permission(
        capability,
        granted=True,
        pack_config=helper.pack_config_for(
            capability,
            source_coordinate=f"fixture-{capability}-container",
            account_coordinate=f"fixture-{capability}-account",
        ),
    )
    helper.run_capability_test(
        capability, lambda *_: {"status": "passed", "content_exposed": False}
    )
    helper.prove_and_start(capability)


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_first_observation_before_helper_report_has_durable_ordered_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability: str
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        helper, transfer = _prepare_helper(client, tmp_path, [capability])
        _begin_source(helper, capability)
        assert helper.capability_states[capability] == "observation_starting"
        before = _status(client, transfer)
        assert before["reconciliation"]["advances"] == []
        accepted = client.post(
            "/api/bridge/ingest",
            headers=_bridge_headers(transfer),
            json=_observation(runtime, capability, "early-fixture-observation"),
        )
        assert accepted.status_code == 202, accepted.text
        source_id = accepted.json()["event_id"]
        early = _status(client, transfer)
        assert (
            early["current"]["capability_states"][capability]["state"]
            == "observation_starting"
        )
        assert early["reconciliation"]["advances"] == []
        acknowledgement = helper.record_observing(capability)
        assert acknowledgement["state"] == helper.state == "capabilities_testing"
        assert acknowledgement["capability_state"] == "observing"
        status = _status(client, transfer)
        assert status["current"]["state"] == "active_observation"
        assert status["current"]["capability_states"][capability]["state"] == "settled"
        assert status["identity"]["authenticated_member_id"] == transfer["member_id"]
        assert status["reconciliation"]["base"] == {
            "state": helper.state,
            "capability_states": {capability: "observing"},
        }
        advances = status["reconciliation"]["advances"]
        assert [row["capability_state"] for row in advances] == ["settling", "settled"]
        observed = client.portal.call(runtime.event_log.get, source_id)
        for row in advances:
            assert row["observation_event_id"] == source_id
            assert row["source_authorization_event_id"] < source_id < row["event_id"]
            assert (
                row["evidence_sha256"]
                == hashlib.sha256(canonical_bytes(observed)).hexdigest()
            )
            assert (
                row["configured_source_sha256"]
                == status["configured_source_sha256"][capability]
            )
        # The retained old helper safely receives refusal. A new helper can use
        # the explicit status proof; neither server nor old client regresses it.
        frozen = (
            runtime.bridge_enrollment.vault.path.read_bytes(),
            runtime.bridge_enrollment.index_path.read_bytes(),
        )
        events = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        payload = _signed(helper, capability, "observing")
        refused = client.post(
            "/api/bridge/enrollment/progress",
            headers=_bridge_headers(transfer),
            json=payload,
        )
        assert refused.status_code == 409
        assert (
            refused.json()["detail"]["error"] == "bridge_progress_stale_before_mutation"
        )
        assert (
            refused.json()["detail"]["nonce_sha256"]
            == hashlib.sha256(payload["nonce"].encode()).hexdigest()
        )
        assert frozen == (
            runtime.bridge_enrollment.vault.path.read_bytes(),
            runtime.bridge_enrollment.index_path.read_bytes(),
        )
        assert events == client.portal.call(
            _events, runtime, "bridge.enrollment_progressed"
        )
        assert helper.capability_states[capability] == "observing"
        assert all(
            row["payload"].get("capability_state") in {"settling", "settled"}
            for row in events
            if row["source_adapter"] == "member-bridge-enrollment-runtime"
        )
        # A later source forces the predecessor's unchanged historical audit
        # through the catch-up lifecycle, preserving rollback compatibility.
        subsequent = client.post(
            "/api/bridge/ingest",
            headers=_bridge_headers(transfer),
            json=_observation(runtime, capability, "second-fixture-observation"),
        )
        assert subsequent.status_code == 202, subsequent.text
        _assert_history(client.portal.call(_history, runtime.event_log), source_count=2)
        # Ordinary self identity is mandatory, even with the real enrollment id.
        for headers in (
            {},
            {
                **OTHER_HEADERS,
                "X-AdministrateMe-Bridge-Enrollment": transfer["enrollment_id"],
            },
            {
                **_bridge_headers(transfer),
                "X-AdminMe-View-Member": "member-fixture-other",
            },
        ):
            denied = client.get(
                "/api/bridge/enrollment/status",
                params={"request_id": REQUEST_ID},
                headers=headers,
            )
            assert denied.status_code in {401, 403}


def test_capability_only_stale_and_control_drift_never_mutate_or_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        helper, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        for capability in CAPABILITIES:
            _enable(helper, capability)
        helper.finish_permission_phase()
        response = client.post(
            "/api/bridge/ingest",
            headers=_bridge_headers(transfer),
            json=_observation(runtime, "apple_calendar", "first-of-three"),
        )
        assert response.status_code == 202
        assert (
            _status(client, transfer)["current"]["state"] == helper.state == "settling"
        )
        frozen = copy.deepcopy(runtime.settings.bridge_enrollments)
        secret = runtime.bridge_enrollment.vault.path.read_bytes()
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        response = client.post(
            "/api/bridge/enrollment/progress",
            headers=_bridge_headers(transfer),
            json=_signed(helper, "apple_calendar", "observing"),
        )
        assert response.status_code == 409
        assert response.json()["detail"]["reason"] == "capability_observation_advanced"
        assert response.json()["detail"]["effects"] == "none"
        assert runtime.settings.bridge_enrollments == frozen
        assert runtime.bridge_enrollment.vault.path.read_bytes() == secret
        assert (
            client.portal.call(_events, runtime, "bridge.enrollment_progressed")
            == before
        )
        for action in ("pause", "resume"):
            controlled = client.post(
                f"/api/member/v1/settings/bridges/{REQUEST_ID}/control",
                headers=MEMBER_HEADERS,
                json={"command_id": f"sync-fixture-{action}", "action": action},
            )
            assert controlled.status_code == 200, controlled.text
        # Even an aggregate round trip to the same state is not a central-only
        # observation suffix, so a stale receipt cannot silently adopt it.
        assert _status(client, transfer)["reconciliation"] is None


@pytest.mark.parametrize("batch", [False, True])
def test_admission_and_revoke_are_serialized_for_single_and_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, batch: bool
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        helper, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        for capability in CAPABILITIES:
            _enable(helper, capability)
        helper.finish_permission_phase()
        observations = [
            _observation(runtime, cap, f"race-{cap}")
            for cap in CAPABILITIES[: 2 if batch else 1]
        ]
        original_write = runtime.events.write_batch

        async def race() -> tuple[int, int]:
            entered, release = asyncio.Event(), asyncio.Event()

            async def suspended(envelopes: list[Any], **kwargs: Any) -> list[str]:
                if envelopes[0].type == observations[0]["type"]:
                    assert runtime.settings_command_locks["settings-control"].locked()
                    entered.set()
                    await release.wait()
                return await original_write(envelopes, **kwargs)

            monkeypatch.setattr(runtime.events, "write_batch", suspended)
            transport = httpx.ASGITransport(app=client.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="https://central-fixture.ts.net"
            ) as remote:
                incoming = asyncio.create_task(
                    remote.post(
                        "/api/bridge/ingest/batch" if batch else "/api/bridge/ingest",
                        headers=_bridge_headers(transfer),
                        json={"envelopes": observations} if batch else observations[0],
                    )
                )
                await asyncio.wait_for(entered.wait(), 5)
                revoke = asyncio.create_task(
                    remote.post(
                        f"/api/member/v1/settings/bridges/{REQUEST_ID}/control",
                        headers=MEMBER_HEADERS,
                        json={
                            "command_id": "concurrent-revoke-fixture",
                            "action": "revoke",
                        },
                    )
                )
                await asyncio.sleep(0)
                assert not revoke.done()
                release.set()
                accepted, revoked = await asyncio.gather(incoming, revoke)
                return accepted.status_code, revoked.status_code

        assert client.portal.call(race) == (202, 200)
        _assert_history(
            client.portal.call(_history, runtime.event_log),
            source_count=len(observations),
        )
        before = client.portal.call(_events, runtime, observations[0]["type"])
        blocked = client.post(
            "/api/bridge/ingest/batch" if batch else "/api/bridge/ingest",
            headers=_bridge_headers(transfer),
            json={"envelopes": observations} if batch else observations[0],
        )
        assert blocked.status_code == 403
        assert client.portal.call(_events, runtime, observations[0]["type"]) == before
        assert _control(client)["bridge_wizard"]["current"]["state"] == "revoked"


@pytest.mark.parametrize(
    ("running", "configuring", "expected_aggregate"),
    [
        ("apple_calendar", "apple_reminders", "partial"),
        ("apple_reminders", "apple_calendar", "partial"),
        ("apple_calendar", "apple_notes", "attested"),
        ("apple_notes", "apple_calendar", "not_attested"),
    ],
)
def test_progress_lock_spans_index_mutation_append_and_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    running: str,
    configuring: str,
    expected_aggregate: str,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        helper, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _enable(helper, running)
        helper._report(
            helper.state,
            capability_id=configuring,
            capability_state="implementation_available",
        )
        helper._report(
            helper.state,
            capability_id=configuring,
            capability_state="permission_required",
        )
        config = helper.pack_config_for(
            configuring,
            source_coordinate=f"fixture-{configuring}-container",
            account_coordinate=f"fixture-{configuring}-account",
        )
        progress = _signed(
            helper, configuring, "permission_verified", pack_config=config
        )
        original_write = runtime.events.write_batch

        async def race() -> tuple[int, int]:
            entered, release = asyncio.Event(), asyncio.Event()

            async def suspended(envelopes: list[Any], **kwargs: Any) -> list[str]:
                if (
                    envelopes[0].type == "bridge.enrollment_progressed"
                    and envelopes[0].payload.get("capability_state")
                    == "permission_verified"
                ):
                    assert runtime.settings_command_locks["settings-control"].locked()
                    assert (
                        PACKS[configuring]
                        in runtime.settings.bridge_enrollments[
                            MEMBER_HEADERS["Tailscale-User-Login"]
                        ]["configured_packs"]
                    )
                    record = runtime.settings.bridge_enrollments[
                        MEMBER_HEADERS["Tailscale-User-Login"]
                    ]
                    assert record["provider_capability_state"] == expected_aggregate
                    assert PACKS[running] in record["authorized_packs"]
                    assert PACKS[configuring] not in record["authorized_packs"]
                    entered.set()
                    await release.wait()
                return await original_write(envelopes, **kwargs)

            monkeypatch.setattr(runtime.events, "write_batch", suspended)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=client.app),
                base_url="https://central-fixture.ts.net",
            ) as remote:
                changing = asyncio.create_task(
                    remote.post(
                        "/api/bridge/enrollment/progress",
                        headers=_bridge_headers(transfer),
                        json=progress,
                    )
                )
                await asyncio.wait_for(entered.wait(), 5)
                observation = asyncio.create_task(
                    remote.post(
                        "/api/bridge/ingest",
                        headers=_bridge_headers(transfer),
                        json=_observation(runtime, running, "concurrent-source"),
                    )
                )
                await asyncio.sleep(0)
                assert not observation.done()
                release.set()
                changed, observed = await asyncio.gather(changing, observation)
                assert changed.status_code == 202, changed.text
                assert observed.status_code == 202, observed.text
                assert changed.json()["state"] == helper.state
                return changed.status_code, observed.status_code

        assert client.portal.call(race) == (202, 202)
        current = _status(client, transfer)["current"]
        assert (
            current["capability_states"][configuring]["state"] == "permission_verified"
        )
        assert current["capability_states"][running]["state"] == "settled"
        _assert_history(client.portal.call(_history, runtime.event_log), source_count=1)

        blocked = client.post(
            "/api/bridge/ingest",
            headers=_bridge_headers(transfer),
            json=_observation(runtime, configuring, "still-unattested-source"),
        )
        assert blocked.status_code == 403, blocked.text


@pytest.mark.parametrize(
    "defect", ["source_before_authority", "wrong_device", "wrong_container"]
)
def test_catch_up_never_counts_old_or_unattributed_source_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        helper, transfer = _prepare_helper(client, tmp_path, ["apple_calendar"])
        row = _observation(runtime, "apple_calendar", "unattributed-catch-up-fixture")
        row["actor_identity"] = (
            "bridge-device:"
            + hashlib.sha256(helper.device_node_id.encode()).hexdigest()
        )
        if defect == "source_before_authority":
            # Deliberately retained, unattributed history predates authorization;
            # it cannot become evidence merely because today's config matches.
            client.portal.call(runtime.events.write, EventEnvelope(**row))
        _begin_source(helper, "apple_calendar")
        if defect == "wrong_device":
            row["actor_identity"] = "bridge-device:" + "a" * 64
        elif defect == "wrong_container":
            row["payload"]["calendar_external_id"] = "unapproved-fixture-container"
        if defect != "source_before_authority":
            client.portal.call(runtime.events.write, EventEnvelope(**row))
        helper.record_observing("apple_calendar")
        status = _status(client, transfer)
        assert (
            status["current"]["capability_states"]["apple_calendar"]["state"]
            == "observing"
        )
        assert status["reconciliation"]["advances"] == []
        accepted = client.post(
            "/api/bridge/ingest",
            headers=_bridge_headers(transfer),
            json=_observation(
                runtime, "apple_calendar", "actual-admitted-catch-up-fixture"
            ),
        )
        assert accepted.status_code == 202, accepted.text
        status = _status(client, transfer)
        assert (
            status["current"]["capability_states"]["apple_calendar"]["state"]
            == "settled"
        )
        assert all(
            row["observation_event_id"] == accepted.json()["event_id"]
            for row in status["reconciliation"]["advances"]
        )


def test_unchanged_aggregate_state_vector_refuses_before_all_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        helper, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        for capability in CAPABILITIES[:2]:
            _enable(helper, capability)
        helper.finish_permission_phase()
        helper._report(
            helper.state,
            capability_id="apple_notes",
            capability_state="implementation_available",
        )
        helper._report(
            helper.state,
            capability_id="apple_notes",
            capability_state="permission_required",
        )
        first = client.post(
            "/api/bridge/ingest",
            headers=_bridge_headers(transfer),
            json=_observation(
                runtime, "apple_calendar", "unchanged-aggregate-calendar"
            ),
        )
        assert first.status_code == 202
        baseline = _status(client, transfer)["current"]
        local_vector = {
            "state": baseline["state"],
            "capability_states": {
                capability: row["state"]
                for capability, row in baseline["capability_states"].items()
            },
        }
        headers = {
            **_bridge_headers(transfer),
            "X-AdministrateMe-Bridge-State-SHA256": hashlib.sha256(
                canonical_bytes(local_vector)
            ).hexdigest(),
        }
        second = client.post(
            "/api/bridge/ingest",
            headers=_bridge_headers(transfer),
            json=_observation(
                runtime, "apple_reminders", "unchanged-aggregate-reminders"
            ),
        )
        assert second.status_code == 202
        current = _status(client, transfer)["current"]
        assert current["state"] == baseline["state"] == helper.state == "settling"
        assert current["capability_states"]["apple_reminders"]["state"] == "settled"
        frozen = (
            runtime.bridge_enrollment.vault.path.read_bytes(),
            runtime.bridge_enrollment.index_path.read_bytes(),
        )
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        progress = _signed(
            helper,
            "apple_notes",
            "permission_verified",
            pack_config=helper.pack_config_for("apple_notes"),
        )
        refused = client.post(
            "/api/bridge/enrollment/progress", headers=headers, json=progress
        )
        assert refused.status_code == 409
        assert refused.json()["detail"]["reason"] == "state_snapshot_mismatch"
        assert refused.json()["detail"]["effects"] == "none"
        for invalid_headers in (
            {**headers, "X-AdministrateMe-Bridge-State-SHA256": "bad"},
            {**headers, **OTHER_HEADERS},
        ):
            response = client.post(
                "/api/bridge/enrollment/progress",
                headers=invalid_headers,
                json=progress,
            )
            assert response.status_code in {403, 422}
        assert frozen == (
            runtime.bridge_enrollment.vault.path.read_bytes(),
            runtime.bridge_enrollment.index_path.read_bytes(),
        )
        assert before == client.portal.call(
            _events, runtime, "bridge.enrollment_progressed"
        )
        # This nonce has not been consumed. A freshly reconciled restrictive
        # precondition can accompany the same still-valid signed command.
        vector = {
            "state": current["state"],
            "capability_states": {
                key: row["state"] for key, row in current["capability_states"].items()
            },
        }
        accepted = client.post(
            "/api/bridge/enrollment/progress",
            headers={
                **headers,
                "X-AdministrateMe-Bridge-State-SHA256": hashlib.sha256(
                    canonical_bytes(vector)
                ).hexdigest(),
            },
            json=progress,
        )
        assert accepted.status_code == 202, accepted.text
        assert (
            _status(client, transfer)["current"]["capability_states"]["apple_notes"][
                "state"
            ]
            == "permission_verified"
        )


def test_initial_retry_eligibility_excludes_previously_attested_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        helper, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _enable(helper, "apple_calendar")
        helper.record_capability_degraded(
            "apple_calendar", reason="fixture_source_stopped"
        )
        helper.record_prerequisite_missing(
            "apple_reminders", reason="fixture_missing_permission"
        )
        helper.record_permission(
            "apple_notes",
            granted=True,
            pack_config=helper.pack_config_for("apple_notes"),
        )
        helper.record_capability_degraded(
            "apple_notes", reason="fixture_initial_probe_failed"
        )
        frozen = (
            runtime.bridge_enrollment.vault.path.read_bytes(),
            runtime.bridge_enrollment.index_path.read_bytes(),
        )
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        status = _status(client, transfer)
        assert status["permission_retry_capabilities"] == [
            "apple_notes",
            "apple_reminders",
        ]
        assert frozen == (
            runtime.bridge_enrollment.vault.path.read_bytes(),
            runtime.bridge_enrollment.index_path.read_bytes(),
        )
        assert before == client.portal.call(
            _events, runtime, "bridge.enrollment_progressed"
        )


def test_status_observation_proof_survives_encrypted_runtime_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    settings = _configuration_settings(tmp_path)
    with TestClient(
        create_application(settings), base_url="https://central-fixture.ts.net"
    ) as client:
        _manual_policies(client)
        helper, transfer = _prepare_helper(client, tmp_path, ["apple_notes"])
        _begin_source(helper, "apple_notes")
        response = client.post(
            "/api/bridge/ingest",
            headers=_bridge_headers(transfer),
            json=_observation(
                client.app.state.runtime, "apple_notes", "restart-source"
            ),
        )
        assert response.status_code == 202
        helper.record_observing("apple_notes")
        before = _status(client, transfer)
        assert before["current"]["state"] == "active_observation"
        assert len(before["reconciliation"]["advances"]) == 2
    with TestClient(
        create_application(settings), base_url="https://central-fixture.ts.net"
    ) as restarted:
        after = _status(restarted, transfer)
        assert after == before
        assert after["provider_effects"] is False
        assert after["current"]["writes_authorized"] is False
