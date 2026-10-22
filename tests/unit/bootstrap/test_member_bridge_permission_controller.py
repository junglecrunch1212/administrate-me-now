"""Helper component boundaries; execute only in isolated CI/lab."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from adminme_bridge.enrollment_ui import LocalSourceChoice, MacOSEnrollmentInteraction
from bootstrap.member_bridge_permission_controller import (
    EXIT_CODES,
    PermissionFailure,
    RUNTIME_INTERFACE_HASHES,
    check_alignment,
    check_current_device,
    controller_lock,
    make_result,
    native_interaction,
    run_controller,
    validate_origin,
    validate_runtime_interfaces,
    validate_progress_ack,
    validate_result,
)


ROOT = Path(__file__).resolve().parents[3]


def test_retained_interface_bytes_match_the_explicit_component_contract() -> None:
    for relative, expected in RUNTIME_INTERFACE_HASHES.items():
        assert len(expected) == (2 if relative == "adminme/bridge_enrollment.py" else 1)
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() in expected
    assert (
        "96caaabe2320f19a2201d94dd53bf93b06f2d12957d634519637cb1cbd6f220a"
        in RUNTIME_INTERFACE_HASHES["adminme/bridge_enrollment.py"]
    )


@pytest.mark.parametrize("changed", tuple(RUNTIME_INTERFACE_HASHES))
def test_unknown_interface_bytes_are_refused_even_with_same_runtime_commit(
    tmp_path: Path, changed: str
) -> None:
    for relative in RUNTIME_INTERFACE_HASHES:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    validate_runtime_interfaces(tmp_path)
    selected = tmp_path / changed
    selected.write_bytes(selected.read_bytes() + b"\n# unreviewed fixture mutation\n")
    with pytest.raises(PermissionFailure, match="runtime_mismatch"):
        validate_runtime_interfaces(tmp_path)


@pytest.mark.parametrize(
    "outcome,status",
    [
        ("ready", "observing"),
        ("blocked", "blocked"),
        ("unknown", "unknown"),
        ("cancelled", "cancelled"),
    ],
)
def test_result_is_strict_and_content_free(outcome: str, status: str) -> None:
    result = make_result(
        "continue",
        [
            {
                "capability_id": "apple_notes",
                "status": status,
                "stage": "probe",
                "code": "ok",
            }
        ],
    )
    assert result["outcome"] == outcome
    assert (
        validate_result(result, operation="continue", exit_code=EXIT_CODES[outcome])
        == result
    )
    for altered in (
        {**result, "raw_stderr": "PRIVATE"},
        {**result, "content_exposed": True},
        {**result, "outcome": "ready"}
        if status != "observing"
        else {**result, "operation": "initial"},
    ):
        with pytest.raises(ValueError):
            validate_result(
                altered, operation="continue", exit_code=EXIT_CODES[outcome]
            )
    changed = copy.deepcopy(result)
    changed["capabilities"][0]["code"] = "private source name"
    with pytest.raises(ValueError):
        validate_result(changed, operation="continue", exit_code=EXIT_CODES[outcome])


def test_global_failure_does_not_invent_selected_source() -> None:
    value = make_result(
        "initial",
        [
            {
                "capability_id": None,
                "status": "unknown",
                "stage": "preflight",
                "code": "runtime_mismatch",
            }
        ],
    )
    assert value["capabilities"][0]["capability_id"] is None
    assert "apple_calendar" not in json.dumps(value)


@pytest.mark.parametrize(
    "native_code,expected",
    [
        ("-1743", "native_permission_denied"),
        ("-128", "native_cancelled"),
        ("-1712", "native_timeout"),
        ("-600", "native_application_unavailable"),
        ("-99999", "native_command_failed"),
    ],
)
def test_native_error_preserves_only_whitelisted_code(
    monkeypatch: pytest.MonkeyPatch, native_code: str, expected: str
) -> None:
    def run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            [], 1, "PRIVATE BODY", f"PRIVATE SOURCE ({native_code})"
        )

    monkeypatch.setattr(subprocess, "run", run)
    interaction = native_interaction(MacOSEnrollmentInteraction, LocalSourceChoice)
    with pytest.raises(PermissionFailure) as raised:
        interaction._jxa("metadata only")
    assert raised.value.code == expected
    assert "PRIVATE" not in str(raised.value)


@pytest.mark.parametrize(
    "output",
    ["private invalid metadata", '{"available":false}', '{"available":"secret"}'],
)
def test_notes_missing_or_malformed_metadata_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, output, ""),
    )
    interaction = native_interaction(MacOSEnrollmentInteraction, LocalSourceChoice)
    with pytest.raises(PermissionFailure) as raised:
        interaction.content_free_probe("apple_notes", LocalSourceChoice())
    assert raised.value.code in {"invalid_metadata", "no_source_container"}
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    "capability,entity", [("apple_calendar", 0), ("apple_reminders", 1)]
)
@pytest.mark.parametrize("granted", [True, False])
def test_eventkit_uses_native_container_identifiers_after_full_grant(
    monkeypatch: pytest.MonkeyPatch, capability: str, entity: int, granted: bool
) -> None:
    calls: list[str] = []
    calendar = SimpleNamespace(
        calendarIdentifier=lambda: "eventkit-native-container",
        title=lambda: "Local fixture container",
    )

    class Store:
        @classmethod
        def alloc(cls) -> Any:
            return cls()

        def init(self) -> Any:
            return self

        def requestFullAccessToEventsWithCompletion_(self, callback: Any) -> None:
            calls.append("events-full-access")
            callback(granted, None)

        def requestFullAccessToRemindersWithCompletion_(self, callback: Any) -> None:
            calls.append("reminders-full-access")
            callback(granted, None)

        def calendarsForEntityType_(self, selected: int) -> list[Any]:
            assert granted and selected == entity
            calls.append("metadata")
            return [calendar]

        def calendarWithIdentifier_(self, selected: str) -> Any:
            assert selected == "eventkit-native-container"
            calls.append("identifier-roundtrip")
            return calendar

    monkeypatch.setitem(
        sys.modules,
        "EventKit",
        SimpleNamespace(
            EKEventStore=Store, EKEntityTypeEvent=0, EKEntityTypeReminder=1
        ),
    )
    interaction = native_interaction(MacOSEnrollmentInteraction, LocalSourceChoice)
    if granted:
        assert interaction._containers(capability) == [
            {
                "label": "Local fixture container",
                "coordinate": "eventkit-native-container",
            }
        ]
        assert calls[1:] == ["metadata", "identifier-roundtrip"]
    else:
        with pytest.raises(PermissionFailure, match="eventkit_access_denied"):
            interaction._containers(capability)
        assert len(calls) == 1


def _aligned() -> tuple[Any, dict[str, Any]]:
    transfer = {
        "member_id": "fixture-member",
        "request_id": "fixture-request",
        "enrollment_id": "fixture-enrollment",
        "instance_id": "fixture-instance",
        "release_commit": "a" * 40,
        "capability_set_sha256": "b" * 64,
    }
    helper = SimpleNamespace(
        transfer=transfer,
        state="degraded",
        device_node_id="fixture-device",
        capability_states={"apple_notes": "degraded"},
    )
    response = {
        "control": {
            "identity": {
                "authenticated_member_id": transfer["member_id"],
                "view_member_id": transfer["member_id"],
                "actions_as": transfer["member_id"],
                "reads_as": transfer["member_id"],
                "viewing_as": False,
                "can_mutate": True,
                "authentication_method": "Tailscale",
                "authenticated_role": "principal",
            },
            "bridge_wizard": {
                "current": {
                    **transfer,
                    "state": helper.state,
                    "capabilities": ["apple_notes"],
                    "capability_states": {"apple_notes": {"state": "degraded"}},
                    "device_identity_sha256": hashlib.sha256(
                        helper.device_node_id.encode()
                    ).hexdigest(),
                    "observation_only": True,
                    "writes_authorized": False,
                }
            },
        }
    }
    return helper, response


@pytest.mark.parametrize(
    "change,expected",
    [
        ("identity", "identity_mismatch"),
        ("paused", "request_paused"),
        ("revoked", "request_revoked"),
        ("expired", "request_expired"),
        ("state", "state_mismatch"),
        ("device", "binding_mismatch"),
        ("release", "binding_mismatch"),
    ],
)
def test_alignment_refuses_drift_without_changing_local_state(
    change: str, expected: str
) -> None:
    helper, response = _aligned()
    check_alignment(helper, response, "a" * 40)
    before = copy.deepcopy(helper.__dict__)
    current = response["control"]["bridge_wizard"]["current"]
    if change == "identity":
        response["control"]["identity"]["actions_as"] = "other-fixture-member"
    elif change in {"paused", "revoked", "expired"}:
        current["state"] = change
    elif change == "state":
        current["state"] = "capabilities_testing"
    elif change == "device":
        current["device_identity_sha256"] = "0" * 64
    else:
        current["release_commit"] = "c" * 40
    with pytest.raises(PermissionFailure, match=expected):
        check_alignment(helper, response, "a" * 40)
    assert helper.__dict__ == before


def test_controller_lock_releases_on_interruption(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        with controller_lock(tmp_path):
            with pytest.raises(PermissionFailure, match="another_helper_running"):
                with controller_lock(tmp_path):
                    pytest.fail("second helper acquired lock")
            raise RuntimeError("fixture interruption")
    with controller_lock(tmp_path):
        pass


@pytest.mark.parametrize(
    "login,node",
    [
        ("other-fixture.ts.net", "fixture-device"),
        ("fixture.ts.net", "other-fixture-device"),
        ("fixture.ts.net", ""),
    ],
)
def test_live_device_identity_must_match_receipt(login: str, node: str) -> None:
    helper = SimpleNamespace(
        transfer={"tailscale_identity": "fixture.ts.net"},
        device_node_id="fixture-device",
        _identity=lambda: (login, node),
    )
    with pytest.raises(PermissionFailure, match="identity_mismatch"):
        check_current_device(helper)


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.com",
        "https://fixture.ts.net:443",
        "http://fixture.ts.net",
        "https://member@example.test",
        "https://fixture.ts.net/path",
        "https://fixture.ts.net/?key=secret",
    ],
)
def test_only_retained_private_origin_contract_is_accepted(origin: str) -> None:
    with pytest.raises(PermissionFailure, match="binding_mismatch"):
        validate_origin(origin)
    validate_origin("https://fixture.ts.net")


@pytest.mark.parametrize(
    "capability_state",
    [
        "permission_required",
        "permission_verified",
        "adapter_authenticated",
        "observation_starting",
        "observing",
        None,
    ],
)
def test_acknowledgement_must_match_exact_progress_before_persistence(
    capability_state: str | None,
) -> None:
    payload = {
        "request_id": "fixture-request",
        "state": "capabilities_testing",
        "capability_id": "apple_notes" if capability_state else None,
        "capability_state": capability_state,
    }
    attested = capability_state in {
        "adapter_authenticated",
        "observation_starting",
        "observing",
    }
    acknowledged = {
        **payload,
        "accepted": True,
        "event_id": "fixture-event",
        "central_attested": attested,
        "may_start_connector": attested,
        "content_exposed": False,
        "provider_effect_count": 0,
    }
    validate_progress_ack(acknowledged, payload)
    for value in (
        {},
        {**acknowledged, "request_id": "other-request"},
        {**acknowledged, "state": "degraded"},
        {**acknowledged, "event_id": ""},
        {**acknowledged, "may_start_connector": not attested},
        {**acknowledged, "provider_effect_count": True},
    ):
        with pytest.raises(
            PermissionFailure, match="central_progress_unknown"
        ) as raised:
            validate_progress_ack(value, payload)
        assert raised.value.unknown is True


def test_other_capabilities_use_original_interaction_without_patching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bootstrap import member_bridge_permission_controller as component

    helper = SimpleNamespace(capability_states={"obsidian": "requested"})
    monkeypatch.setattr(component, "check_local_configuration", lambda _helper: None)
    monkeypatch.setattr(component, "check_current_device", lambda _helper: None)
    monkeypatch.setattr(component, "check_alignment", lambda *_args: None)
    original = object()

    class OriginalWizard:
        def __init__(self, actual_helper: Any, actual_interaction: Any) -> None:
            assert actual_helper is helper
            assert actual_interaction is original

        def run(self) -> dict[str, Any]:
            helper.capability_states["obsidian"] = "observing"
            return {}

    result = run_controller(
        helper,
        object(),
        operation="initial",
        expected_commit="a" * 40,
        expected_central_commit="a" * 40,
        descriptors={},
        snapshot=lambda _helper: {},
        original_wizard=OriginalWizard,
        original_interaction=original,
    )
    assert result["outcome"] == "ready"
    helper.capability_states["obsidian"] = "degraded"
    stopped = run_controller(
        helper,
        object(),
        operation="continue",
        expected_commit="a" * 40,
        expected_central_commit="a" * 40,
        descriptors={},
        snapshot=lambda _helper: {},
        original_wizard=OriginalWizard,
        original_interaction=original,
    )
    assert stopped["outcome"] == "blocked"
    assert stopped["capabilities"][0]["code"] == "unsupported_recovery"


def _observation_status(tmp_path: Path) -> tuple[Any, dict[str, Any]]:
    from bootstrap.member_bridge_permission_controller import RECOVERY_CONTRACT

    helper, settings = _aligned()
    helper.state = "capabilities_testing"
    helper.capability_states = {"apple_notes": "observing"}
    helper.paths = SimpleNamespace(config_path=tmp_path / "config.yaml")
    config = {
        "member_id": "fixture-member",
        "automation_mode": "notes_scripting",
        "emit_schema_version": 2,
    }
    helper.paths.config_path.write_text(
        json.dumps({"pack_config": {"knowledge-apple-notes": config}})
    )
    digest = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    current = settings["control"]["bridge_wizard"]["current"]
    current.update(
        state="active_observation",
        revision=12,
        provider_effects=False,
        capability_states={"apple_notes": {"state": "settled"}},
    )
    advances = []
    previous = helper.state
    for index, (capability_state, state) in enumerate(
        (
            ("settling", "settling"),
            ("settled", "active_observation"),
        )
    ):
        advances.append(
            {
                "event_id": f"fixture-advance-{index}",
                "capability_id": "apple_notes",
                "capability_state": capability_state,
                "previous_state": previous,
                "state": state,
                "observation_event_id": "fixture-observation",
                "source_authorization_event_id": "fixture-authority",
                "evidence_sha256": "c" * 64,
                "configured_source_sha256": digest,
                "last_successful_observation": "2031-01-01T12:00:00+00:00"
                if capability_state == "settled"
                else None,
            }
        )
        previous = state
    return helper, {
        "contract": RECOVERY_CONTRACT,
        "central_release_commit": "d" * 40,
        "content_exposed": False,
        "provider_effects": False,
        "identity": {**settings["control"]["identity"], "role": "principal"},
        "current": current,
        "configured_source_sha256": {"apple_notes": digest},
        "permission_retry_capabilities": [],
        "reconciliation": {
            "base_event_id": "fixture-base",
            "base": {
                "state": helper.state,
                "capability_states": dict(helper.capability_states),
            },
            "advances": advances,
        },
    }


@pytest.mark.parametrize("prefix", [0, 1, 2])
def test_authenticated_observation_suffix_accepts_only_known_monotonic_prefix(
    tmp_path: Path, prefix: int
) -> None:
    from bootstrap.member_bridge_permission_controller import (
        validate_observation_snapshot,
    )

    helper, status = _observation_status(tmp_path)
    if prefix:
        advance = status["reconciliation"]["advances"][prefix - 1]
        helper.state = advance["state"]
        helper.capability_states["apple_notes"] = advance["capability_state"]
    before = copy.deepcopy(helper.capability_states)
    assert validate_observation_snapshot(helper, status, "d" * 40) == (
        "active_observation",
        {"apple_notes": "settled"},
    )
    assert helper.capability_states == before


@pytest.mark.parametrize(
    "tamper",
    [
        "protocol",
        "central_release",
        "owner",
        "paused",
        "configuration",
        "proof",
        "authority",
        "source",
        "base",
        "state",
        "unselected",
        "central_observing",
    ],
)
def test_observation_reconciliation_never_accepts_unproven_or_unrelated_drift(
    tmp_path: Path, tamper: str
) -> None:
    from bootstrap.member_bridge_permission_controller import (
        validate_observation_snapshot,
    )

    helper, status = _observation_status(tmp_path)
    if tamper == "protocol":
        status["contract"] = "old-central"
    elif tamper == "central_release":
        status["central_release_commit"] = "e" * 40
    elif tamper == "owner":
        status["identity"]["reads_as"] = "other-member"
    elif tamper == "paused":
        status["current"]["state"] = "paused"
    elif tamper == "configuration":
        status["configured_source_sha256"]["apple_notes"] = "f" * 64
    elif tamper == "base":
        status["reconciliation"]["base"]["capability_states"]["apple_notes"] = (
            "permission_required"
        )
    else:
        row = status["reconciliation"]["advances"][0]
        key, value = {
            "proof": ("evidence_sha256", ""),
            "authority": ("source_authorization_event_id", None),
            "source": ("configured_source_sha256", "f" * 64),
            "state": ("state", "degraded"),
            "unselected": ("capability_id", "apple_calendar"),
            "central_observing": ("capability_state", "observing"),
        }[tamper]
        row[key] = value
    with pytest.raises(PermissionFailure):
        validate_observation_snapshot(helper, status, "d" * 40)
    assert helper.capability_states == {"apple_notes": "observing"}


def test_only_exact_typed_no_effect_rejection_can_be_retried() -> None:
    import httpx
    from bootstrap.member_bridge_permission_controller import (
        RECOVERY_CONTRACT,
        UncommittedProgressConflict,
        progress_response,
    )

    payload = {"request_id": "fixture-request", "nonce": "fixture-nonce"}
    detail = {
        "contract": RECOVERY_CONTRACT,
        "error": "bridge_progress_stale_before_mutation",
        "request_id": payload["request_id"],
        "effects": "none",
        "nonce_sha256": hashlib.sha256(payload["nonce"].encode()).hexdigest(),
    }
    with pytest.raises(UncommittedProgressConflict):
        progress_response(httpx.Response(409, json={"detail": detail}), payload)
    for altered in (
        "bridge_progress_previous_state_mismatch",
        {**detail, "effects": "unknown"},
        {**detail, "nonce_sha256": "f" * 64},
        {**detail, "request_id": "other-request"},
        {**detail, "contract": "old-central"},
    ):
        with pytest.raises(PermissionFailure) as raised:
            progress_response(httpx.Response(409, json={"detail": altered}), payload)
        assert raised.value.unknown


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "unknown_source",
        "configuration",
        "proof",
        "prior_observation",
        "permission_overlap",
        "extra_field",
    ],
)
def test_initial_start_proof_is_separate_and_bound_to_exact_unobserved_source(
    tmp_path: Path, defect: str | None
) -> None:
    from bootstrap.member_bridge_permission_controller import (
        validate_observation_snapshot,
    )

    helper, status = _observation_status(tmp_path)
    helper.state = "degraded"
    helper.capability_states = {"apple_notes": "degraded"}
    status["current"].update(
        state="degraded",
        capability_states={
            "apple_notes": {"state": "degraded", "last_successful_observation": None}
        },
    )
    proof = {
        "proof_sha256": "e" * 64,
        "configured_source_sha256": status["configured_source_sha256"]["apple_notes"],
    }
    status["initial_start_recovery"] = {"apple_notes": proof}
    if defect == "unknown_source":
        status["initial_start_recovery"] = {"apple_calendar": proof}
    elif defect == "configuration":
        proof["configured_source_sha256"] = "f" * 64
    elif defect == "proof":
        proof["proof_sha256"] = "unbounded source data"
    elif defect == "prior_observation":
        status["current"]["capability_states"]["apple_notes"][
            "last_successful_observation"
        ] = "2031-01-01T12:00:00Z"
    elif defect == "permission_overlap":
        status["permission_retry_capabilities"] = ["apple_notes"]
    elif defect == "extra_field":
        proof["reset_permission"] = True
    if defect is None:
        assert validate_observation_snapshot(helper, status, "d" * 40) == (
            "degraded",
            {"apple_notes": "degraded"},
        )
    else:
        with pytest.raises(PermissionFailure, match="central_protocol_unavailable"):
            validate_observation_snapshot(helper, status, "d" * 40)


@pytest.mark.parametrize("defect", ["bytes", "symlink", "public"])
def test_launch_resource_must_match_sealed_hash_and_private_owner(
    tmp_path: Path, defect: str
) -> None:
    from bootstrap.member_bridge_permission_controller import load_launch_controller

    source = tmp_path / "member_bridge_launch.py"
    source.write_text("def compatible_installer_class(base): return base\n")
    source.chmod(0o600)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert callable(
        load_launch_controller(source, digest)["compatible_installer_class"]
    )
    if defect == "bytes":
        source.write_text("raise RuntimeError('unreviewed launch module executed')\n")
    elif defect == "symlink":
        outside = source.with_name("outside.py")
        source.rename(outside)
        source.symlink_to(outside)
    else:
        source.chmod(0o644)
    with pytest.raises(
        PermissionFailure, match="runtime_mismatch|configuration_mismatch"
    ):
        load_launch_controller(source, digest)
