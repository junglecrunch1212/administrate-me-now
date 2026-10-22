"""Real retained enrollment/central composition with synthetic native UI only.

These tests belong in hosted CI or an authorized disposable lab, never on a
member acceptance device. Existing CoS fixture provider credentials are inert.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.bridge_enrollment import CAPABILITIES as DESCRIPTORS
from adminme.runtime.application import create_application
from adminme_bridge.enrollment_helper import MemberBridgeEnrollmentHelper
from adminme_bridge.enrollment_ui import LocalSourceChoice
from bootstrap.member_bridge_permission_controller import (
    compatible_helper_class,
    http_progress_report,
    run_controller,
)
from tests.acceptance.test_cos_member_bridge_acquisition import (
    CAPABILITIES,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    PACKS,
    REQUEST_ID,
    ROOT,
    _assert_history,
    _bridge_headers,
    _configuration_settings,
    _control,
    _enable,
    _history,
    _manual_policies,
    _observation,
    _prepare_helper,
    _settled_source_assurance,
)
from tests.acceptance.test_forwarded_channel_intake import _events


class NativeFixture:
    def __init__(self, *, retry: bool = True, deny: str | None = None) -> None:
        self.retry = retry
        self.deny = deny
        self.calls: list[tuple[str, str]] = []

    def confirm_retry(self, labels: list[str]) -> bool:
        assert labels
        self.calls.append(("retry", "selected"))
        return self.retry

    def confirm_permission(
        self, capability: str, _label: str, _requirement: str
    ) -> bool:
        self.calls.append(("permission", capability))
        return capability != self.deny

    def choose_source(self, capability: str, _label: str) -> LocalSourceChoice:
        self.calls.append(("source", capability))
        return LocalSourceChoice(
            f"fixture-{capability}-container", f"fixture-{capability}-account"
        )

    def content_free_probe(self, capability: str, _choice: Any) -> dict[str, Any]:
        self.calls.append(("probe", capability))
        return {"status": "passed", "content_exposed": False}


def _block_three(helper: Any) -> None:
    helper.record_prerequisite_missing(
        "apple_calendar", reason="local_prerequisite_unavailable"
    )
    helper.record_permission("apple_reminders", granted=False)
    helper.record_permission(
        "apple_notes", granted=True, pack_config=helper.pack_config_for("apple_notes")
    )
    helper.record_capability_degraded(
        "apple_notes", reason="local_capability_test_failed"
    )


def _composed_reporter(client: TestClient, responses: list[Any] | None = None) -> Any:
    def post(url: str, **kwargs: Any) -> Any:
        headers = kwargs.pop("headers")
        assert "Tailscale-User-Login" not in headers
        assert len(headers["X-AdministrateMe-Bridge-State-SHA256"]) == 64
        # Only this isolated TestClient supplies synthetic authenticated identity.
        response = client.post(url, headers={**MEMBER_HEADERS, **headers}, **kwargs)
        if responses is not None and response.status_code == 409:
            responses.append(response.json())
        return response

    def reporter(
        bound: Any, payload: Any, signature: str, *, state_sha256: str
    ) -> dict[str, Any]:
        return http_progress_report(
            bound, payload, signature, state_sha256=state_sha256, post=post
        )

    return reporter


def _resume(client: TestClient, helper: Any, *, reporter: Any = None) -> Any:
    return compatible_helper_class(MemberBridgeEnrollmentHelper).resume(
        helper.paths,
        repository_root=ROOT,
        test_mode=True,
        reporter=reporter or _composed_reporter(client),
        evidence_submitter=helper._evidence_submitter,
        installer=helper.installer,
    )


def _run(
    client: TestClient,
    helper: Any,
    interaction: NativeFixture,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    def status(bound: Any) -> dict[str, Any]:
        response = client.get(
            "/api/bridge/enrollment/status",
            headers={
                **(headers or MEMBER_HEADERS),
                "X-AdministrateMe-Bridge-Enrollment": bound.transfer["enrollment_id"],
            },
            params={"request_id": bound.transfer["request_id"]},
        )
        if response.status_code != 200:
            from bootstrap.member_bridge_permission_controller import PermissionFailure

            raise PermissionFailure("identity_mismatch")
        return response.json()

    return run_controller(
        helper,
        interaction,
        operation="continue",
        expected_commit=helper.transfer["release_commit"],
        expected_central_commit=helper.transfer["release_commit"],
        progress_snapshot=status,
        descriptors=DESCRIPTORS,
        snapshot=lambda _helper: {"control": _control(client, headers)},
    )


def test_three_blocked_apple_paths_recover_with_original_signed_history_and_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        helper = _resume(client, original)
        interaction = NativeFixture()
        result = _run(client, helper, interaction)
        assert result["outcome"] == "ready", result
        assert result["provider_effects"] is False
        assert interaction.calls[0] == ("retry", "selected")
        assert {
            capability for action, capability in interaction.calls if action == "probe"
        } == set(CAPABILITIES)
        record = runtime.settings.bridge_enrollments[
            MEMBER_HEADERS["Tailscale-User-Login"]
        ]
        assert set(record["authorized_packs"]) == set(PACKS.values())
        assert set(record["enabled_packs"]) == set(PACKS.values())
        assert helper.state == "settling"
        assert not (helper.paths.root / "permission-controller-pending.json").exists()
        after = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        assert after[: len(before)] == before
        for capability in CAPABILITIES:
            rows = [
                event["payload"]
                for event in after[len(before) :]
                if event["payload"].get("capability_id") == capability
            ]
            assert rows[0]["capability_state"] == "permission_required"
            assert rows[1]["capability_state"] == "permission_verified"
            assert rows[-1]["capability_state"] == "observing"
            assert all(row["signature_verified"] is True for row in rows)
        source_ids = {}
        for capability in CAPABILITIES:
            accepted = client.post(
                "/api/bridge/ingest",
                headers=_bridge_headers(transfer),
                json=_observation(
                    runtime, capability, f"permission-recovered-{capability}"
                ),
            )
            assert accepted.status_code == 202, accepted.text
            source_ids[capability] = accepted.json()["event_id"]
        assert (
            _control(client)["bridge_wizard"]["current"]["state"]
            == "active_observation"
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:member_knowledge_indexing",
            source_ids["apple_notes"],
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "source-policy-v2:shadow",
            source_ids["apple_notes"],
        )
        assurance = client.portal.call(
            _settled_source_assurance, runtime, source_ids["apple_notes"]
        )
        assert assurance["state"] == "healthy", assurance
        _assert_history(client.portal.call(_history, runtime.event_log))
        assert _control(client, OTHER_HEADERS)["bridge_wizard"]["current"] is None
        for source in _control(client)["candidate_identification"]["sources"]:
            if source["id"] in {f"adapter:{capability}" for capability in CAPABILITIES}:
                assert source["policy"]["mode"] == "manual"
                assert source["policy"]["language_enabled"] is False
        for event_type in (
            "external.sent",
            "external_sync.write_requested",
            "external_sync.write_completed",
        ):
            assert client.portal.call(_events, runtime, event_type) == []


@pytest.mark.parametrize(
    "case",
    [
        "cancel",
        "revoke",
        "repair_drift",
        "cross_owner",
        "unknown",
        "malformed_ack",
        "deny_notes",
    ],
)
def test_recovery_refusals_preserve_history_and_never_retry_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        if case in {"revoke", "repair_drift"}:
            response = client.post(
                f"/api/member/v1/settings/bridges/{REQUEST_ID}/control",
                headers=MEMBER_HEADERS,
                json={
                    "command_id": f"permission-{case}",
                    "action": "revoke" if case == "revoke" else "repair",
                },
            )
            assert response.status_code == 200, response.text
        before_receipt = original.paths.receipt_path.read_bytes()
        before_config = original.paths.config_path.read_bytes()
        before_events = client.portal.call(
            _events, runtime, "bridge.enrollment_progressed"
        )
        calls = []

        def uncertain_reporter(
            bound: Any, payload: Any, signature: str, *, state_sha256: str
        ) -> dict[str, Any]:
            calls.append(payload)
            _composed_reporter(client)(
                bound, payload, signature, state_sha256=state_sha256
            )
            if case == "malformed_ack":
                return {}
            raise TimeoutError("fixture acknowledgement lost after commit")

        helper = _resume(
            client,
            original,
            reporter=uncertain_reporter
            if case in {"unknown", "malformed_ack"}
            else None,
        )
        interaction = NativeFixture(
            retry=case != "cancel", deny="apple_notes" if case == "deny_notes" else None
        )
        result = _run(
            client,
            helper,
            interaction,
            headers=OTHER_HEADERS if case == "cross_owner" else None,
        )
        assert result["outcome"] == (
            "cancelled"
            if case == "cancel"
            else "unknown"
            if case in {"unknown", "malformed_ack"}
            else "blocked"
        ), result
        if case == "deny_notes":
            assert ("probe", "apple_notes") not in interaction.calls
            assert helper.capability_states["apple_notes"] == "permission_denied"
            assert result["outcome"] == "blocked"
        else:
            assert original.paths.receipt_path.read_bytes() == before_receipt
            assert original.paths.config_path.read_bytes() == before_config
            after = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
            if case in {"unknown", "malformed_ack"}:
                assert len(calls) == 1
                assert len(after) == len(before_events) + 1
                journal = original.paths.root / "permission-controller-pending.json"
                assert journal.is_file()
                assert "fixture acknowledgement" not in journal.read_text()
                restarted = _resume(client, original, reporter=uncertain_reporter)
                repeated = _run(client, restarted, NativeFixture())
                assert repeated["outcome"] == "unknown"
                assert len(calls) == 1
                assert (
                    client.portal.call(_events, runtime, "bridge.enrollment_progressed")
                    == after
                )
            else:
                assert after == before_events
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        assert "fixture acknowledgement" not in json.dumps(result)


def test_mixed_settling_preserves_working_source_and_retries_blocked_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        original._report(
            original.state,
            capability_id="apple_calendar",
            capability_state="permission_required",
        )
        _enable(original, "apple_calendar")
        helper = _resume(client, original)
        helper.finish_permission_phase()
        assert helper.state == "settling"
        interaction = NativeFixture()
        result = _run(client, _resume(client, helper), interaction)
        assert result["outcome"] == "ready", result
        assert not any(
            capability == "apple_calendar" for _action, capability in interaction.calls
        )
        assert result["capabilities"][0]["status"] == "preserved"


def test_local_configuration_failure_after_ack_retains_unknown_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adminme_bridge import enrollment_helper as retained

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        helper = _resume(client, original)
        original_writer = retained.atomic_write

        def failing_config(path: Path, payload: bytes, **kwargs: Any) -> Any:
            if path == helper.paths.config_path:
                raise OSError("fixture disk write failure")
            return original_writer(path, payload, **kwargs)

        monkeypatch.setattr(retained, "atomic_write", failing_config)
        result = _run(client, helper, NativeFixture())
        assert result["outcome"] == "unknown", result
        assert result["capabilities"][0]["code"] == "local_configuration_unknown"
        assert (helper.paths.root / "permission-controller-pending.json").is_file()
        restarted = _resume(client, original)
        repeated = _run(client, restarted, NativeFixture())
        assert repeated["outcome"] == "unknown"


@pytest.mark.parametrize(
    "timing",
    [
        "before_start_returns",
        "during_next_permission",
        "before_next_progress",
        "late_observing_progress",
        "unchanged_aggregate",
    ],
)
def test_real_observation_interleavings_do_not_replay_native_setup_or_lose_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timing: str
) -> None:

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        observed: set[str] = set()
        conflicts: list[dict[str, Any]] = []
        starts: list[str] = []

        def observe(capability: str) -> None:
            if capability in observed:
                return
            response = client.post(
                "/api/bridge/ingest",
                headers=_bridge_headers(transfer),
                json=_observation(
                    runtime, capability, f"permission-race-{timing}-{capability}"
                ),
            )
            assert response.status_code == 202, response.text
            observed.add(capability)

        def reporter(
            bound: Any, payload: Any, signature: str, *, state_sha256: str
        ) -> dict[str, Any]:
            if (
                timing == "before_next_progress"
                and payload.get("capability_id") == "apple_reminders"
                and payload.get("capability_state") == "permission_verified"
            ):
                observe("apple_calendar")
            if (
                timing == "late_observing_progress"
                and payload.get("capability_id") == "apple_reminders"
                and payload.get("capability_state") == "observing"
            ):
                observe("apple_reminders")
            if (
                timing == "unchanged_aggregate"
                and payload.get("capability_id") == "apple_notes"
                and payload.get("capability_state") == "permission_verified"
            ):
                observe("apple_reminders")
            return _composed_reporter(client, conflicts)(
                bound, payload, signature, state_sha256=state_sha256
            )

        helper = _resume(client, original, reporter=reporter)
        original_enable = helper.installer.enable_pack

        def enable(pack: str) -> dict[str, Any]:
            result = original_enable(pack)
            starts.append(pack)
            if timing == "before_start_returns":
                observe(
                    next(
                        capability
                        for capability, value in PACKS.items()
                        if value == pack
                    )
                )
            return result

        monkeypatch.setattr(helper.installer, "enable_pack", enable)

        class InterleavedNative(NativeFixture):
            def confirm_permission(
                self, capability: str, label: str, requirement: str
            ) -> bool:
                if capability == "apple_reminders" and timing in {
                    "during_next_permission",
                    "late_observing_progress",
                    "unchanged_aggregate",
                }:
                    observe("apple_calendar")
                return super().confirm_permission(capability, label, requirement)

        native = InterleavedNative()
        result = _run(client, helper, native)
        assert result["outcome"] == "ready", result
        assert starts == [PACKS[capability] for capability in CAPABILITIES]
        assert [
            cap for action, cap in native.calls if action == "probe"
        ] == CAPABILITIES
        assert not (helper.paths.root / "permission-controller-pending.json").exists()
        for capability in CAPABILITIES:
            observe(capability)
        # A restart is a pure proof refresh: no native re-consent/probe/start.
        restarted = _resume(client, helper, reporter=reporter)
        restart_native = NativeFixture()
        repeated = _run(client, restarted, restart_native)
        assert repeated["outcome"] == "ready", repeated
        assert restart_native.calls == []
        assert restarted.state == "active_observation"
        assert set(restarted.capability_states.values()) == {"settled"}
        assert (
            _control(client)["bridge_wizard"]["current"]["state"]
            == "active_observation"
        )
        if timing in {
            "before_next_progress",
            "late_observing_progress",
            "unchanged_aggregate",
        }:
            assert conflicts
            assert all(row["detail"]["effects"] == "none" for row in conflicts)
        progress = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        for capability in CAPABILITIES:
            settled = [
                row
                for row in progress
                if row["payload"].get("capability_id") == capability
                and row["payload"].get("capability_state") == "settled"
            ]
            assert len(settled) == 1
        _assert_history(client.portal.call(_history, runtime.event_log))
        assert client.portal.call(_events, runtime, "external.sent") == []
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )


def test_previously_attested_degraded_source_is_not_a_permission_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _enable(original, "apple_calendar")
        original.record_capability_degraded(
            "apple_calendar", reason="local_capability_test_failed"
        )
        original.record_prerequisite_missing(
            "apple_reminders", reason="local_prerequisite_unavailable"
        )
        original.record_permission("apple_notes", granted=False)
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        config = original.paths.config_path.read_bytes()
        receipt = original.paths.receipt_path.read_bytes()
        native = NativeFixture()
        result = _run(client, _resume(client, original), native)
        assert result["outcome"] == "blocked", result
        assert {row["code"] for row in result["capabilities"]} == {
            "unsupported_recovery"
        }
        assert native.calls == []
        assert original.paths.config_path.read_bytes() == config
        assert original.paths.receipt_path.read_bytes() == receipt
        assert (
            client.portal.call(_events, runtime, "bridge.enrollment_progressed")
            == before
        )
        assert client.portal.call(_events, runtime, "external.sent") == []


@pytest.mark.parametrize("stage", ["permission", "source_selection"])
def test_native_cancellation_stops_without_inventing_prerequisite_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    from bootstrap.member_bridge_permission_controller import PermissionFailure

    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, _transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        before = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        config = original.paths.config_path.read_bytes()

        class CancelNative(NativeFixture):
            def confirm_permission(
                self, capability: str, label: str, requirement: str
            ) -> bool:
                if stage == "permission":
                    raise PermissionFailure(
                        "native_cancelled", native_error_number=-128
                    )
                return super().confirm_permission(capability, label, requirement)

            def choose_source(self, capability: str, label: str) -> Any:
                raise PermissionFailure("native_cancelled", native_error_number=-128)

        native = CancelNative()
        helper = _resume(client, original)
        result = _run(client, helper, native)
        assert result["outcome"] == "cancelled", result
        assert result["capabilities"][0]["code"] == "native_cancelled"
        assert result["capabilities"][0]["native_error_number"] == -128
        assert not any(
            action == "probe" or capability in {"apple_reminders", "apple_notes"}
            for action, capability in native.calls
        )
        assert original.paths.config_path.read_bytes() == config
        after = client.portal.call(_events, runtime, "bridge.enrollment_progressed")
        assert [
            event["payload"]["capability_state"] for event in after[len(before) :]
        ] == ["permission_required"]
        assert helper.capability_states["apple_calendar"] == "permission_required"
        assert not (helper.paths.root / "permission-controller-pending.json").exists()
        assert client.portal.call(_events, runtime, "external.sent") == []


def test_unknown_observing_ack_cannot_be_replaced_by_later_settlement_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    with TestClient(
        create_application(_configuration_settings(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        _manual_policies(client)
        original, transfer = _prepare_helper(client, tmp_path, CAPABILITIES)
        _block_three(original)
        reports: list[str | None] = []

        def reporter(
            bound: Any, payload: Any, signature: str, *, state_sha256: str
        ) -> dict[str, Any]:
            result = _composed_reporter(client)(
                bound, payload, signature, state_sha256=state_sha256
            )
            reports.append(payload.get("capability_state"))
            if payload.get("capability_state") == "observing":
                raise TimeoutError("fixture observing acknowledgement lost")
            return result

        helper = _resume(client, original, reporter=reporter)
        actual_enable = helper.installer.enable_pack

        def early_observation(pack: str) -> dict[str, Any]:
            result = actual_enable(pack)
            response = client.post(
                "/api/bridge/ingest",
                headers=_bridge_headers(transfer),
                json=_observation(
                    runtime, "apple_calendar", "permission-lost-observing-ack"
                ),
            )
            assert response.status_code == 202, response.text
            return result

        monkeypatch.setattr(helper.installer, "enable_pack", early_observation)
        native = NativeFixture()
        result = _run(client, helper, native)
        assert result["outcome"] == "unknown", result
        assert helper.capability_states["apple_calendar"] == "observation_starting"
        assert (
            _control(client)["bridge_wizard"]["current"]["capability_states"][
                "apple_calendar"
            ]["state"]
            == "settled"
        )
        assert (helper.paths.root / "permission-controller-pending.json").exists()
        prior_reports = list(reports)
        receipt = helper.paths.receipt_path.read_bytes()
        restarted = _resume(client, original, reporter=reporter)
        assert _run(client, restarted, NativeFixture())["outcome"] == "unknown"
        assert reports == prior_reports
        assert helper.paths.receipt_path.read_bytes() == receipt
        assert reports.count("observing") == 1
        assert client.portal.call(_events, runtime, "external.sent") == []
