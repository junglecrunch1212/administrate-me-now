"""Sealed helper-owned permission recovery for the retained 043 bridge runtime.

This component is not installed into, or imported by, the bridge daemon. Its
mutation compatibility seam is MemberBridgeEnrollmentHelper._report:
the retained implementation signs progress, obtains central acknowledgement,
and persists its receipt. We never replace it, reset a receipt, synthesize an
approval, or replay a progress call whose outcome is uncertain.
The same exact-source contract uses its read-only _identity device check and
_persist_progress for strictly validated, monotonic central observation refresh.
Such a refresh is never evidence that an unknown local operation completed.

Importing this module uses only the standard library so the launcher can
validate its bounded, content-free result without loading provider code.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib
import json
import os
import re
import runpy
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit


CONTRACT = "administrateme-bridge-permission-result-v1"
RECOVERY_CONTRACT = "administrateme-bridge-permission-recovery-v1"
RUNTIME_INTERFACE_HASHES: dict[str, frozenset[str]] = {
    "bridge/adminme_bridge/enrollment_helper.py": frozenset(
        {"8b37b4f9be805c58f1bf4923ca523cc09d4f69cd575f725471fc709124cca6a0"}
    ),
    "bridge/adminme_bridge/enrollment_ui.py": frozenset(
        {"01b7a3a719f023362933e54d1816b0dae9ef770a11ce6aff1ef222f34b5afcd6"}
    ),
    "bootstrap/bridge_enrollment.py": frozenset(
        {
            "9cdc2da8e27fb1905d3ca6b42536c7a65edb220e9ac2fd9264017ab86af656ce",
            "af0268ac567859ef8f3e8ffcade61aa8265c415d6e156641985496929bee990e",
        }
    ),
    # Retained paired runtime and reviewed coordinator-only central successor.
    # Packaging separately proves their edge-used code is unchanged.
    "adminme/bridge_enrollment.py": frozenset(
        {
            "96caaabe2320f19a2201d94dd53bf93b06f2d12957d634519637cb1cbd6f220a",
            "a6aa140fea3b3bdf63437eb818f1f9974b9cdd5f23fc09cb3352ee6b467a21e9",
        }
    ),
}
APP_TAILSCALE_ENROLLMENT_SHA256 = (
    "af0268ac567859ef8f3e8ffcade61aa8265c415d6e156641985496929bee990e"
)
APP_TAILSCALE_DEPENDENCY_SHA256 = (
    "0450494bb29928b73179028c1b33928ea8cc9b3097ea7725e440209c12b3eda5"
)
SELECTED = frozenset({"apple_calendar", "apple_reminders", "apple_notes"})
CAPABILITY_IDS = SELECTED | {
    "apple_contacts",
    "apple_voice_memos",
    "obsidian",
    "apple_messages_read",
}
BLOCKED = frozenset({"permission_denied", "prerequisite_missing", "degraded"})
PRESERVED = frozenset({"observing", "settling", "settled", "active"})
STAGES = frozenset(
    {
        "preflight",
        "retry_consent",
        "permission",
        "source_selection",
        "probe",
        "attestation",
        "start",
        "complete",
    }
)
CODES = frozenset(
    {
        "ok",
        "already_observing",
        "retry_cancelled",
        "permission_not_confirmed",
        "native_cancelled",
        "native_timeout",
        "native_permission_denied",
        "native_application_unavailable",
        "native_command_failed",
        "invalid_metadata",
        "no_source_container",
        "eventkit_unavailable",
        "eventkit_access_denied",
        "eventkit_source_mismatch",
        "source_probe_failed",
        "identity_mismatch",
        "binding_mismatch",
        "state_mismatch",
        "configuration_mismatch",
        "runtime_mismatch",
        "central_unavailable",
        "central_progress_unknown",
        "local_configuration_unknown",
        "attestation_failed",
        "start_failed",
        "request_paused",
        "request_revoked",
        "request_expired",
        "unsupported_recovery",
        "unexpected_failure",
        "invalid_result",
        "another_helper_running",
        "central_protocol_unavailable",
        "launch_binding_mismatch",
        "launch_config_mismatch",
        "launch_not_quiescent",
        "launch_state_unknown",
        "launch_stop_unknown",
        "launch_start_unknown",
        "launch_recovery_pending",
        "launch_rollback_failed",
        "launch_start_failed",
        "launch_busy",
    }
)
EXIT_CODES = {"ready": 0, "cancelled": 0, "blocked": 2, "unknown": 3}


class PermissionFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        unknown: bool = False,
        native_error_number: int | None = None,
    ) -> None:
        if code not in CODES:
            code = "unexpected_failure"
        super().__init__(code)
        self.code = code
        self.unknown = unknown
        self.native_error_number = native_error_number


class UncommittedProgressConflict(RuntimeError):
    """Only an exact, authenticated central rejection before any mutation."""


def progress_response(response: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Classify one response; arbitrary HTTP errors always remain unknown."""
    try:
        value = response.json()
    except Exception as exc:
        raise PermissionFailure("central_progress_unknown", unknown=True) from exc
    if response.status_code == 202:
        validate_progress_ack(value, payload)
        return value
    detail = value.get("detail") if isinstance(value, dict) else None
    if (
        response.status_code == 409
        and isinstance(detail, dict)
        and detail.get("contract") == RECOVERY_CONTRACT
        and detail.get("error") == "bridge_progress_stale_before_mutation"
        and detail.get("request_id") == payload.get("request_id")
        and detail.get("nonce_sha256")
        == hashlib.sha256(str(payload.get("nonce") or "").encode()).hexdigest()
        and detail.get("effects") == "none"
    ):
        raise UncommittedProgressConflict()
    raise PermissionFailure("central_progress_unknown", unknown=True)


def http_progress_report(
    transfer: dict[str, Any],
    payload: dict[str, Any],
    signature: str,
    *,
    state_sha256: str,
    initial_start_recovery_sha256: str | None = None,
    post: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Use the retained signer through its reporter injection, with normal TLS."""
    import httpx

    central = str(transfer.get("central_url") or "")
    validate_origin(central)
    headers = {
        "X-AdministrateMe-Bridge-Enrollment": str(transfer["enrollment_id"]),
        "X-AdministrateMe-Bridge-State-SHA256": state_sha256,
    }
    if initial_start_recovery_sha256 is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", initial_start_recovery_sha256):
            raise PermissionFailure("unsupported_recovery")
        headers["X-AdministrateMe-Bridge-Initial-Start-Recovery"] = (
            initial_start_recovery_sha256
        )
    try:
        response = (post or httpx.post)(
            central.rstrip("/") + "/api/bridge/enrollment/progress",
            headers=headers,
            json={**payload, "signature": signature},
            timeout=20,
            follow_redirects=False,
        )
        return progress_response(response, payload)
    except (PermissionFailure, UncommittedProgressConflict):
        raise
    except Exception as exc:
        raise PermissionFailure("central_progress_unknown", unknown=True) from exc


def validate_result(value: object, *, operation: str, exit_code: int) -> dict[str, Any]:
    """Reject extra fields, arbitrary strings and misleading terminal outcomes."""
    if not isinstance(value, dict) or set(value) != {
        "contract",
        "operation",
        "outcome",
        "capabilities",
        "content_exposed",
        "provider_effects",
    }:
        raise ValueError("invalid permission result")
    outcome = value.get("outcome")
    rows = value.get("capabilities")
    if (
        value.get("contract") != CONTRACT
        or operation not in {"initial", "continue"}
        or value.get("operation") != operation
        or outcome not in EXIT_CODES
        or EXIT_CODES[outcome] != exit_code
        or value.get("content_exposed") is not False
        or value.get("provider_effects") is not False
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= len(CAPABILITY_IDS)
    ):
        raise ValueError("invalid permission result")
    seen: set[str | None] = set()
    statuses: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"native_error_number"} != {
            "capability_id",
            "status",
            "stage",
            "code",
        }:
            raise ValueError("invalid permission result")
        if "native_error_number" in row and (
            type(row["native_error_number"]) is not int
            or not -32768 <= row["native_error_number"] <= 32767
            or not str(row.get("code", "")).startswith("native_")
        ):
            raise ValueError("invalid permission result")
        capability = row.get("capability_id")
        global_row = (
            capability is None
            and len(rows) == 1
            and row.get("stage") == "preflight"
            and row.get("status") in {"blocked", "unknown"}
        )
        if (
            (capability not in CAPABILITY_IDS and not global_row)
            or capability in seen
            or row.get("status")
            not in {"observing", "preserved", "blocked", "cancelled", "unknown"}
            or row.get("stage") not in STAGES
            or row.get("code") not in CODES
        ):
            raise ValueError("invalid permission result")
        seen.add(capability)
        statuses.add(row["status"])
    if outcome == "ready" and statuses - {"observing", "preserved"}:
        raise ValueError("invalid permission result")
    if outcome == "unknown" and "unknown" not in statuses:
        raise ValueError("invalid permission result")
    if outcome == "blocked" and "blocked" not in statuses:
        raise ValueError("invalid permission result")
    if outcome == "cancelled" and (
        "cancelled" not in statuses or statuses & {"blocked", "unknown"}
    ):
        raise ValueError("invalid permission result")
    if len(json.dumps(value).encode("utf-8")) > 8192:
        raise ValueError("invalid permission result")
    return value


def make_result(operation: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = {row["status"] for row in rows}
    outcome = (
        "unknown"
        if "unknown" in statuses
        else "blocked"
        if "blocked" in statuses
        else "cancelled"
        if "cancelled" in statuses
        else "ready"
    )
    result = {
        "contract": CONTRACT,
        "operation": operation,
        "outcome": outcome,
        "capabilities": rows,
        "content_exposed": False,
        "provider_effects": False,
    }
    return validate_result(result, operation=operation, exit_code=EXIT_CODES[outcome])


def _row(capability: str | None, status: str, stage: str, code: str) -> dict[str, Any]:
    return {"capability_id": capability, "status": status, "stage": stage, "code": code}


def compatible_helper_class(base: Any) -> Any:
    """An explicit subclass, not a patch to the retained class or installed code."""

    class Retained043PermissionHelper(base):
        state: str
        capability_states: dict[str, str]
        permission_stage = "preflight"
        _component_operation_active = False
        _known_uncommitted_report = False
        _definite_progress_stop = False
        _observation_snapshot: Any = None
        _expected_central_commit = ""
        _permission_retry_capabilities: frozenset[str] = frozenset()
        _initial_start_recovery: dict[str, dict[str, str]]
        _initial_start_preflight: frozenset[str] = frozenset()
        _initial_start_proof: str | None = None

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._initial_start_recovery = {}
            reporter = kwargs.pop("reporter", None) or http_progress_report

            def checked_reporter(
                transfer: Any, payload: Any, signature: str
            ) -> dict[str, Any]:
                # Restrict-only precondition; retained signed body is unchanged.
                digest = hashlib.sha256(
                    json.dumps(
                        {
                            "state": payload["previous_state"],
                            "capability_states": dict(self.capability_states),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                options: dict[str, Any] = {"state_sha256": digest}
                if self._initial_start_proof is not None:
                    options["initial_start_recovery_sha256"] = self._initial_start_proof
                result = reporter(transfer, payload, signature, **options)
                validate_progress_ack(result, payload)
                return result

            # Validate the acknowledgement through the retained constructor's
            # reporter dependency BEFORE its unchanged _report persists it.
            super().__init__(*args, reporter=checked_reporter, **kwargs)

        def _component_operation(
            self, action: Callable[[], Any], failure_code: str
        ) -> Any:
            if self._component_operation_active:
                return action()
            try:
                _pending_progress(self, write=True)
                self._component_operation_active = True
                self._definite_progress_stop = False
                result = action()
                _pending_progress(self, clear=True)
                return result
            except PermissionFailure as exc:
                if (
                    self._component_operation_active
                    and self._definite_progress_stop
                    and not exc.unknown
                ):
                    _pending_progress(self, clear=True)
                raise
            except Exception as exc:
                raise PermissionFailure(failure_code, unknown=True) from exc
            finally:
                self._component_operation_active = False

        def record_permission(
            self, capability_id: str, **kwargs: Any
        ) -> dict[str, Any]:
            self.reconcile_observation_progress()
            # The operation includes the local config write AFTER _report.
            # Its journal must survive a successful remote acknowledgement
            # followed by a failed/interrupting local persistence operation.
            return self._component_operation(
                lambda: super(Retained043PermissionHelper, self).record_permission(
                    capability_id, **kwargs
                ),
                "local_configuration_unknown",
            )

        def prove_and_start(self, capability_id: str) -> dict[str, Any]:
            self.reconcile_observation_progress()
            return self._component_operation(
                lambda: super(Retained043PermissionHelper, self).prove_and_start(
                    capability_id
                ),
                "start_failed",
            )

        def reconcile_observation_progress(self) -> bool:
            """Supported component seam: persist only proven central observations.

            No outstanding operation may be reconciled. The sole exception is
            an in-process, authenticated rejection of this exact POST before
            mutation; it cannot be reconstructed after a crash or unknown reply.
            """
            if self._observation_snapshot is None:
                return False
            if not self._known_uncommitted_report:
                _pending_progress(self)
            check_current_device(self)
            snapshot = self._observation_snapshot(self)
            state, capabilities = validate_observation_snapshot(
                self, snapshot, self._expected_central_commit
            )
            self._permission_retry_capabilities = frozenset(
                snapshot["permission_retry_capabilities"]
            )
            self._initial_start_recovery = dict(
                snapshot.get("initial_start_recovery", {})
            )
            if state == self.state and capabilities == self.capability_states:
                return False

            def persist() -> None:
                self.state = state
                self.capability_states = capabilities
                self._persist_progress()

            # The retained helper is the sole receipt writer. This is neither
            # a synthetic POST acknowledgement nor a replay of local actions.
            self._component_operation(persist, "local_configuration_unknown")
            return True

        def record_observing(self, capability_id: str) -> dict[str, Any]:
            self.reconcile_observation_progress()
            if self.capability_states.get(capability_id) in {"settling", "settled"}:
                return {"state": self.state, "provider_effects": False}
            return super().record_observing(capability_id)

        def finish_permission_phase(self) -> dict[str, Any]:
            self.reconcile_observation_progress()
            if self.state in {"settling", "active_observation", "partially_active"}:
                return {"state": self.state, "provider_effects": False}
            return super().finish_permission_phase()

        def _report(self, state: str, **kwargs: Any) -> dict[str, Any]:
            capability_state = kwargs.get("capability_state")
            if capability_state == "adapter_authenticated":
                self.permission_stage = "attestation"
            elif capability_state in {"observation_starting", "observing"}:
                self.permission_stage = "start"
            same_state = state == self.state

            def report_once_or_rebase() -> dict[str, Any]:
                target = state
                # Each source can settle once. Bound retries by selected sources;
                # every retry requires a new proven monotonic central advance.
                for _attempt in range(len(self.capability_states) + 1):
                    try:
                        return super(Retained043PermissionHelper, self)._report(
                            target, **kwargs
                        )
                    except UncommittedProgressConflict:
                        self._definite_progress_stop = True
                        if self._initial_start_proof is not None:
                            # A source can arrive without changing the wizard
                            # state. Never refresh and retry this start proof.
                            raise PermissionFailure("unsupported_recovery") from None
                        if self._observation_snapshot is None:
                            raise PermissionFailure("state_mismatch") from None
                        self._known_uncommitted_report = True
                        try:
                            changed = self.reconcile_observation_progress()
                        finally:
                            self._known_uncommitted_report = False
                        if not changed:
                            raise PermissionFailure("state_mismatch") from None
                        capability = kwargs.get("capability_id")
                        if (
                            capability_state == "observing"
                            and isinstance(capability, str)
                            and self.capability_states.get(capability, "")
                            in {"settling", "settled"}
                        ):
                            return {"state": self.state, "provider_effects": False}
                        if (
                            capability is None
                            and target == "settling"
                            and self.state
                            in {"settling", "active_observation", "partially_active"}
                        ):
                            return {"state": self.state, "provider_effects": False}
                        if not same_state:
                            raise PermissionFailure("state_mismatch") from None
                        target = self.state
                        self._definite_progress_stop = False
                raise PermissionFailure("state_mismatch")

            return self._component_operation(
                report_once_or_rebase, "central_progress_unknown"
            )

        def request_permission_retry(self, capability: str, *, confirmed: bool) -> None:
            if (
                not confirmed
                or capability not in SELECTED
                or capability not in self._permission_retry_capabilities
                or self.capability_states.get(capability) not in BLOCKED
                or self.state
                not in {
                    "awaiting_permissions",
                    "capabilities_testing",
                    "degraded",
                    "partially_active",
                    "settling",
                }
            ):
                raise PermissionFailure("unsupported_recovery")
            self.permission_stage = "permission"
            self._report(
                self.state,
                capability_id=capability,
                capability_state="permission_required",
            )

        def continue_initial_start(self, capability: str, *, confirmed: bool) -> None:
            self.reconcile_observation_progress()
            proof = self._initial_start_recovery.get(capability)
            if (
                not confirmed
                or capability not in self._initial_start_preflight
                or self.capability_states.get(capability) != "degraded"
                or proof is None
            ):
                raise PermissionFailure("unsupported_recovery")
            from adminme.bridge_enrollment import CAPABILITIES

            try:
                self.installer.verify_initial_start_recovery(
                    {str(CAPABILITIES[key]["pack"]) for key in self.capability_states}
                )
            except Exception as exc:
                raise PermissionFailure(
                    getattr(exc, "code", "launch_state_unknown"),
                    unknown=getattr(exc, "unknown", True),
                ) from exc

            def start() -> None:
                self.permission_stage = "start"
                self._initial_start_proof = proof["proof_sha256"]
                try:
                    self._report(
                        self.state,
                        capability_id=capability,
                        capability_state="observation_starting",
                    )
                finally:
                    self._initial_start_proof = None
                self.installer.enable_pack(str(CAPABILITIES[capability]["pack"]))

            # Preserve the whole known/unknown operation boundary, including
            # local startup after the central signed acknowledgement.
            self._component_operation(start, "start_failed")

    return Retained043PermissionHelper


def validate_progress_ack(result: Any, payload: dict[str, Any]) -> None:
    attested = payload.get("capability_state") in {
        "adapter_authenticated",
        "observation_starting",
        "observing",
        "settling",
        "settled",
    }
    if (
        not isinstance(result, dict)
        or result.get("accepted") is not True
        or not isinstance(result.get("event_id"), str)
        or not result["event_id"]
        or any(
            result.get(key) != payload.get(key)
            for key in ("request_id", "state", "capability_id", "capability_state")
        )
        or result.get("central_attested") is not attested
        or result.get("may_start_connector") is not attested
        or result.get("content_exposed") is not False
        or type(result.get("provider_effect_count")) is not int
        or result.get("provider_effect_count") != 0
    ):
        raise PermissionFailure("central_progress_unknown", unknown=True)


def _private_file(path: Path, *, may_be_missing: bool = False) -> None:
    if may_be_missing and not path.exists() and not path.is_symlink():
        return
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not path.is_file()
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise PermissionFailure("configuration_mismatch")


def _pending_progress(helper: Any, *, write: bool = False, clear: bool = False) -> None:
    """Component-owned journal; never rewrite a retained enrollment receipt."""
    from bootstrap.contracts import atomic_write

    root = helper.paths.root
    if (
        root.is_symlink()
        or not root.is_dir()
        or root.stat().st_uid != os.getuid()
        or root.stat().st_mode & 0o022
    ):
        raise PermissionFailure("configuration_mismatch")
    path = root / "permission-controller-pending.json"
    _private_file(path, may_be_missing=True)
    if path.exists() and not clear:
        # This includes process interruption and unknown HTTP acknowledgement.
        raise PermissionFailure("central_progress_unknown", unknown=True)
    if write:
        atomic_write(
            path,
            json.dumps(
                {
                    "contract": CONTRACT,
                    "request_sha256": hashlib.sha256(
                        str((helper.transfer or {}).get("request_id") or "").encode()
                    ).hexdigest(),
                    "stage": helper.permission_stage,
                    "outcome": "unknown",
                },
                sort_keys=True,
            ).encode(),
            mode=0o600,
        )
    if clear:
        path.unlink()


@contextmanager
def controller_lock(root: Path) -> Iterator[None]:
    path = root / "permission-controller.lock"
    _private_file(path, may_be_missing=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PermissionFailure("another_helper_running") from exc
        yield
    finally:
        os.close(descriptor)


def check_local_configuration(helper: Any) -> None:
    import yaml

    _private_file(helper.paths.receipt_path)
    _private_file(helper.paths.config_path)
    _pending_progress(helper)
    try:
        receipt = json.loads(helper.paths.receipt_path.read_text())
        config = yaml.safe_load(helper.paths.config_path.read_text())
        transfer = helper.transfer or {}
        if not isinstance(receipt, dict) or not isinstance(config, dict):
            raise PermissionFailure("configuration_mismatch")
        endpoint = transfer.get("central_url")
        if (
            not isinstance(endpoint, str)
            or config.get("central_endpoint") != endpoint
            or receipt.get("central_url") != endpoint
            or config.get("tailscale_identity") != transfer.get("tailscale_identity")
            or transfer.get("tenant_id") != transfer.get("instance_id")
        ):
            raise PermissionFailure("configuration_mismatch")
        validate_origin(endpoint)
        if any(
            config.get(key) != transfer.get(key)
            or receipt.get(key) != transfer.get(key)
            for key in (
                "member_id",
                "request_id",
                "enrollment_id",
                "instance_id",
                "release_commit",
                "capability_set_sha256",
            )
        ):
            raise PermissionFailure("configuration_mismatch")
        if (
            receipt.get("member_id") != transfer.get("member_id")
            or receipt.get("bridge_node_id") != helper.device_node_id
            or receipt.get("wizard_state") != helper.state
            or receipt.get("capability_states") != helper.capability_states
            or set(config.get("requested_capabilities") or [])
            != set(helper.capability_states)
        ):
            raise PermissionFailure("configuration_mismatch")
        pack_config = config.get("pack_config") or {}
        configured = config.get("configured_packs") or []
        if (
            not isinstance(pack_config, dict)
            or set(pack_config) != set(configured)
            or any(
                not isinstance(row, dict)
                or row.get("member_id") != transfer.get("member_id")
                for row in pack_config.values()
            )
        ):
            raise PermissionFailure("configuration_mismatch")
    except PermissionFailure:
        raise
    except Exception as exc:
        raise PermissionFailure("configuration_mismatch") from exc


def check_current_device(helper: Any) -> None:
    try:
        identity, node = helper._identity()
        expected = str((helper.transfer or {}).get("tailscale_identity") or "")
        if (
            not expected
            or not isinstance(identity, str)
            or identity.casefold() != expected.casefold()
            or not node
            or node != helper.device_node_id
        ):
            raise PermissionFailure("identity_mismatch")
    except PermissionFailure:
        raise
    except Exception as exc:
        raise PermissionFailure("identity_mismatch") from exc


def check_alignment(
    helper: Any, response: Any, expected_commit: str, *, compare_states: bool = True
) -> None:
    """Read-only comparison; central-only drift is never copied into a receipt."""
    transfer = helper.transfer or {}
    if not isinstance(response, dict):
        raise PermissionFailure("central_unavailable")
    control = response.get("control")
    if not isinstance(control, dict):
        raise PermissionFailure("central_unavailable")
    identity = control.get("identity") or {}
    member = transfer.get("member_id")
    if (
        identity.get("authenticated_member_id") != member
        or identity.get("view_member_id") != member
        or identity.get("actions_as") != member
        or identity.get("reads_as") != member
        or identity.get("viewing_as") is not False
        or identity.get("can_mutate") is not True
        or identity.get("authentication_method") != "Tailscale"
        or identity.get("authenticated_role") not in {"principal", "adult"}
    ):
        raise PermissionFailure("identity_mismatch")
    current = (control.get("bridge_wizard") or {}).get("current")
    if not isinstance(current, dict):
        raise PermissionFailure("binding_mismatch")
    for key in (
        "request_id",
        "enrollment_id",
        "member_id",
        "release_commit",
        "capability_set_sha256",
    ):
        if not transfer.get(key) or current.get(key) != transfer[key]:
            raise PermissionFailure("binding_mismatch")
    if transfer.get("release_commit") != expected_commit:
        raise PermissionFailure("runtime_mismatch")
    if (
        not helper.device_node_id
        or current.get("device_identity_sha256")
        != hashlib.sha256(helper.device_node_id.encode()).hexdigest()
        or set(current.get("capabilities") or []) != set(helper.capability_states)
        or current.get("observation_only") is not True
        or current.get("writes_authorized") is not False
    ):
        raise PermissionFailure("binding_mismatch")
    state = current.get("state")
    if state in {"paused", "revoked", "expired"}:
        raise PermissionFailure(f"request_{state}")
    if state not in {
        "awaiting_permissions",
        "capabilities_testing",
        "degraded",
        "partially_active",
        "settling",
        "active_observation",
    }:
        raise PermissionFailure("state_mismatch")
    central_states = current.get("capability_states")
    if not isinstance(central_states, dict) or set(central_states) != set(
        helper.capability_states
    ):
        raise PermissionFailure("state_mismatch")
    if compare_states and (
        state != helper.state
        or any(
            not isinstance(central_states[key], dict)
            or central_states[key].get("state") != value
            for key, value in helper.capability_states.items()
        )
    ):
        raise PermissionFailure("state_mismatch")


def validate_observation_snapshot(
    helper: Any, value: Any, expected_central_commit: str
) -> tuple[str, dict[str, str]]:
    """Replay only the proven observation suffix of an acknowledged local base."""
    import yaml

    if (
        not isinstance(value, dict)
        or value.get("contract") != RECOVERY_CONTRACT
        or value.get("central_release_commit") != expected_central_commit
        or value.get("content_exposed") is not False
        or value.get("provider_effects") is not False
        or not re.fullmatch(r"[0-9a-f]{40}", expected_central_commit)
    ):
        raise PermissionFailure("central_protocol_unavailable")
    current = value.get("current")
    identity = value.get("identity")
    if not isinstance(current, dict) or not isinstance(identity, dict):
        raise PermissionFailure("binding_mismatch")
    transfer = helper.transfer or {}
    check_alignment(
        helper,
        {
            "control": {
                "identity": {**identity, "authenticated_role": identity.get("role")},
                "bridge_wizard": {"current": current},
            }
        },
        str(transfer.get("release_commit") or ""),
        compare_states=False,
    )
    if (
        current.get("instance_id") != transfer.get("instance_id")
        or current.get("provider_effects") is not False
        or type(current.get("revision")) is not int
        or current["revision"] < 1
    ):
        raise PermissionFailure("binding_mismatch")
    config = yaml.safe_load(helper.paths.config_path.read_text())
    if not isinstance(config, dict) or not isinstance(config.get("pack_config"), dict):
        raise PermissionFailure("configuration_mismatch")
    from adminme.bridge_enrollment import CAPABILITIES, canonical_bytes

    expected_digests = {
        capability: hashlib.sha256(
            canonical_bytes(config["pack_config"][descriptor["pack"]])
        ).hexdigest()
        for capability in helper.capability_states
        if (descriptor := CAPABILITIES[capability])["pack"] in config["pack_config"]
    }
    if value.get("configured_source_sha256") != expected_digests:
        raise PermissionFailure("configuration_mismatch")
    retryable = value.get("permission_retry_capabilities")
    if (
        not isinstance(retryable, list)
        or any(not isinstance(item, str) for item in retryable)
        or len(set(retryable)) != len(retryable)
        or any(
            item not in helper.capability_states
            or current["capability_states"][item]["state"] not in BLOCKED
            for item in retryable
        )
    ):
        raise PermissionFailure("central_protocol_unavailable")
    starts = value.get("initial_start_recovery", {})
    if not isinstance(starts, dict) or set(starts) & set(retryable):
        raise PermissionFailure("central_protocol_unavailable")
    for capability, proof in starts.items():
        if (
            capability not in SELECTED
            or capability not in helper.capability_states
            or current["capability_states"][capability]["state"] != "degraded"
            or current["capability_states"][capability].get(
                "last_successful_observation"
            )
            is not None
            or not isinstance(proof, dict)
            or set(proof) != {"proof_sha256", "configured_source_sha256"}
            or not isinstance(proof.get("proof_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", proof["proof_sha256"])
            or proof.get("configured_source_sha256") != expected_digests.get(capability)
            or capability not in expected_digests
        ):
            raise PermissionFailure("central_protocol_unavailable")
    final_states = {
        key: row["state"] for key, row in current["capability_states"].items()
    }
    final_state = str(current["state"])
    if final_states == helper.capability_states and final_state == helper.state:
        return final_state, final_states
    reconciliation = value.get("reconciliation")
    if not isinstance(reconciliation, dict):
        raise PermissionFailure("state_mismatch")
    base = reconciliation.get("base")
    advances = reconciliation.get("advances")
    if (
        not isinstance(reconciliation.get("base_event_id"), str)
        or not reconciliation["base_event_id"]
        or not isinstance(base, dict)
        or not isinstance(base.get("state"), str)
        or not isinstance(base.get("capability_states"), dict)
        or set(base["capability_states"]) != set(helper.capability_states)
        or not isinstance(advances, list)
        or not 1 <= len(advances) <= 2 * len(helper.capability_states)
    ):
        raise PermissionFailure("state_mismatch")
    state = base["state"]
    capabilities = dict(base["capability_states"])
    matched_local = state == helper.state and capabilities == helper.capability_states
    seen = {reconciliation["base_event_id"]}
    allowed = {
        "observing": "settling",
        "settling": "settled",
    }
    terminal = {
        "settled",
        "permission_denied",
        "prerequisite_missing",
        "degraded",
        "unsupported",
        "revoked",
    }
    for advance in advances:
        if not isinstance(advance, dict):
            raise PermissionFailure("state_mismatch")
        capability = advance.get("capability_id")
        new = advance.get("capability_state")
        event_id = advance.get("event_id")
        observed_at = advance.get("last_successful_observation")
        if (
            capability not in capabilities
            or allowed.get(capabilities[capability]) != new
            or advance.get("previous_state") != state
            or not isinstance(event_id, str)
            or not event_id
            or event_id in seen
            or not isinstance(advance.get("observation_event_id"), str)
            or not advance["observation_event_id"]
            or not isinstance(advance.get("source_authorization_event_id"), str)
            or not advance["source_authorization_event_id"]
            or advance.get("configured_source_sha256")
            != expected_digests.get(capability)
            or not isinstance(advance.get("evidence_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", advance["evidence_sha256"])
            or (
                new == "settled"
                and (not isinstance(observed_at, str) or not observed_at)
            )
            or (new != "settled" and observed_at is not None)
        ):
            raise PermissionFailure("state_mismatch")
        capabilities[capability] = new
        next_state = state
        if new == "settling" and state in {
            "capabilities_testing",
            "degraded",
            "partially_active",
        }:
            next_state = "settling"
        elif new == "settled":
            states = set(capabilities.values())
            if states == {"settled"}:
                next_state = "active_observation"
            elif "settled" in states and states <= terminal:
                next_state = "partially_active"
        if advance.get("state") != next_state:
            raise PermissionFailure("state_mismatch")
        state = next_state
        matched_local = matched_local or (
            state == helper.state and capabilities == helper.capability_states
        )
        seen.add(event_id)
    if not matched_local or state != final_state or capabilities != final_states:
        raise PermissionFailure("state_mismatch")
    return state, capabilities


def observation_snapshot(helper: Any) -> dict[str, Any]:
    """Authenticated, versioned, content-free status; old central fails closed."""
    import httpx

    transfer = helper.transfer or {}
    central = str(transfer.get("central_url") or "")
    validate_origin(central)
    try:
        response = httpx.get(
            central.rstrip("/") + "/api/bridge/enrollment/status",
            params={"request_id": transfer["request_id"]},
            headers={
                "X-AdministrateMe-Bridge-Enrollment": str(transfer["enrollment_id"])
            },
            timeout=20,
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise PermissionFailure("central_protocol_unavailable")
        return response.json()
    except PermissionFailure:
        raise
    except Exception as exc:
        raise PermissionFailure("central_unavailable") from exc


def settings_snapshot(helper: Any) -> dict[str, Any]:
    """Ordinary TLS member request: no identity headers, cookies or redirects."""
    import httpx

    central = str((helper.transfer or {}).get("central_url") or "")
    validate_origin(central)
    try:
        response = httpx.get(
            central.rstrip("/") + "/api/member/v1/surfaces/settings",
            timeout=20,
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise PermissionFailure("central_unavailable")
        return response.json()
    except PermissionFailure:
        raise
    except Exception as exc:
        raise PermissionFailure("central_unavailable") from exc


def validate_origin(central: str) -> None:
    parsed = urlsplit(central)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".ts.net")
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PermissionFailure("binding_mismatch")


def native_interaction(base: Any, choice_class: Any) -> Any:
    class DiagnosedNativeInteraction(base):
        def __init__(self) -> None:
            self.event_stores: dict[str, Any] = {}

        @staticmethod
        def _run_native(arguments: list[str]) -> str:
            try:
                completed = subprocess.run(
                    arguments, capture_output=True, text=True, timeout=120, check=False
                )
            except subprocess.TimeoutExpired as exc:
                raise PermissionFailure("native_timeout") from exc
            except OSError as exc:
                raise PermissionFailure("native_application_unavailable") from exc
            if completed.returncode:
                # Only known numeric error codes escape this process. Raw
                # diagnostics may contain source names and never enter receipts.
                matched = re.search(r"\((-?\d{1,5})\)\s*$", completed.stderr)
                number = int(matched.group(1)) if matched else None
                if number is not None and not -32768 <= number <= 32767:
                    number = None
                codes = {str(number)} if number is not None else set()
                code = (
                    "native_cancelled"
                    if "-128" in codes
                    else "native_permission_denied"
                    if "-1743" in codes
                    else "native_timeout"
                    if "-1712" in codes
                    else "native_application_unavailable"
                    if codes & {"-600", "-609"}
                    else "native_command_failed"
                )
                raise PermissionFailure(code, native_error_number=number)
            return completed.stdout.strip()

        @staticmethod
        def _osascript(script: str, *arguments: str) -> str:
            return DiagnosedNativeInteraction._run_native(
                ["/usr/bin/osascript", "-e", script, *arguments]
            )

        @staticmethod
        def _jxa(script: str) -> Any:
            output = DiagnosedNativeInteraction._run_native(
                ["/usr/bin/osascript", "-l", "JavaScript", "-e", script]
            )
            try:
                return json.loads(output)
            except (ValueError, TypeError) as exc:
                raise PermissionFailure("invalid_metadata") from exc

        def confirm_retry(self, labels: list[str]) -> bool:
            try:
                answer = self._osascript(
                    'on run argv\nset answer to display dialog "Retry permissions and source selection for: " & (item 1 of argv) & return & return & "You will confirm each source again on this Mac. No provider write is authorized." buttons {"Cancel", "Retry selected sources"} default button "Retry selected sources" cancel button "Cancel" with title "AdministrateMe Bridge Helper"\nreturn button returned of answer\nend run',
                    ", ".join(labels),
                )
                return answer == "Retry selected sources"
            except PermissionFailure as exc:
                if exc.code == "native_cancelled":
                    return False
                raise

        def _eventkit_rows(self, capability: str) -> list[dict[str, str]]:
            try:
                EventKit = importlib.import_module("EventKit")
            except ImportError as exc:
                raise PermissionFailure("eventkit_unavailable") from exc
            entity = (
                EventKit.EKEntityTypeEvent
                if capability == "apple_calendar"
                else EventKit.EKEntityTypeReminder
            )
            store = self.event_stores.get(capability)
            if store is None:
                store = EventKit.EKEventStore.alloc().init()
                completed = threading.Event()
                grant: dict[str, bool] = {"granted": False}

                def finished(granted: Any, _error: Any) -> None:
                    grant["granted"] = bool(granted)
                    completed.set()

                request = getattr(
                    store,
                    "requestFullAccessToEventsWithCompletion_"
                    if capability == "apple_calendar"
                    else "requestFullAccessToRemindersWithCompletion_",
                    None,
                )
                if not callable(request):
                    raise PermissionFailure("eventkit_unavailable")
                request(finished)
                if not completed.wait(60):
                    raise PermissionFailure("native_timeout")
                if not grant["granted"]:
                    raise PermissionFailure("eventkit_access_denied")
                self.event_stores[capability] = store
            rows = []
            for calendar in store.calendarsForEntityType_(entity) or []:
                identifier, title = calendar.calendarIdentifier(), calendar.title()
                if (
                    not isinstance(identifier, str)
                    or not identifier
                    or not isinstance(title, str)
                    or not title
                ):
                    raise PermissionFailure("invalid_metadata")
                resolved = store.calendarWithIdentifier_(identifier)
                if resolved is None or resolved.calendarIdentifier() != identifier:
                    raise PermissionFailure("eventkit_source_mismatch")
                rows.append({"coordinate": identifier, "label": title})
            if len({row["coordinate"] for row in rows}) != len(rows):
                raise PermissionFailure("invalid_metadata")
            return rows

        def _containers(self, capability_id: str) -> list[dict[str, str]]:
            if capability_id in {"apple_calendar", "apple_reminders"}:
                return self._eventkit_rows(capability_id)
            return super()._containers(capability_id)

        def choose_source(self, capability_id: str, label: str) -> Any:
            if capability_id in {"apple_calendar", "apple_reminders"}:
                rows = self._containers(capability_id)
                if not rows:
                    raise PermissionFailure("no_source_container")
                # The retained picker renders names locally and returns the
                # exact EventKit identifier; no name or coordinate is logged.
            return super().choose_source(capability_id, label)

        def content_free_probe(self, capability_id: str, choice: Any) -> dict[str, Any]:
            if capability_id == "apple_notes":
                value = self._jxa(
                    'var a=Application("Notes"); JSON.stringify({available:a.accounts().length>0});'
                )
                if (
                    not isinstance(value, dict)
                    or set(value) != {"available"}
                    or not isinstance(value["available"], bool)
                ):
                    raise PermissionFailure("invalid_metadata")
                if value["available"] is not True:
                    raise PermissionFailure("no_source_container")
                return {"status": "passed", "content_exposed": False}
            return super().content_free_probe(capability_id, choice)

        def show_permission_result(self, result: dict[str, Any]) -> None:
            blocked = [
                row["capability_id"].replace("apple_", "").replace("_", " ")
                for row in result["capabilities"]
                if row["status"] in {"blocked", "unknown"}
            ]
            message = (
                "The selected sources are observing or settling. Settings shows their current status."
                if result["outcome"] == "ready"
                else "Setup was cancelled. Existing source states were preserved."
                if result["outcome"] == "cancelled"
                else "Setup needs attention for: "
                + ", ".join(blocked)
                + ". Review Settings and the private setup result before retrying."
            )
            self._osascript(
                'on run argv\ndisplay dialog (item 1 of argv) & return & return & "No provider write was authorized." buttons {"Done"} default button "Done" with title "AdministrateMe Bridge Helper"\nend run',
                message,
            )

    return DiagnosedNativeInteraction()


def run_controller(
    helper: Any,
    interaction: Any,
    *,
    operation: str,
    expected_commit: str,
    expected_central_commit: str,
    descriptors: dict[str, Any],
    snapshot: Callable[[Any], dict[str, Any]] = settings_snapshot,
    progress_snapshot: Callable[[Any], dict[str, Any]] = observation_snapshot,
    original_wizard: Any = None,
    original_interaction: Any = None,
) -> dict[str, Any]:
    selected = tuple(helper.capability_states)
    rows: list[dict[str, Any]] = []
    try:
        if not selected or set(selected) - CAPABILITY_IDS:
            raise PermissionFailure("unsupported_recovery")
        check_local_configuration(helper)
        check_current_device(helper)
        if set(selected) <= SELECTED:
            if (helper.transfer or {}).get("release_commit") != expected_commit:
                raise PermissionFailure("runtime_mismatch")
            helper._observation_snapshot = progress_snapshot
            helper._expected_central_commit = expected_central_commit
            helper.reconcile_observation_progress()
        else:
            check_alignment(helper, snapshot(helper), expected_commit)
        blocked = [key for key in selected if helper.capability_states[key] in BLOCKED]
        if set(selected) - SELECTED:
            if (
                blocked
                or operation != "initial"
                or original_wizard is None
                or original_interaction is None
            ):
                raise PermissionFailure("unsupported_recovery")
            original_wizard(helper, original_interaction).run()
            return make_result(
                operation,
                [
                    _row(
                        key,
                        "observing"
                        if helper.capability_states[key] in PRESERVED
                        else "blocked",
                        "complete",
                        "ok"
                        if helper.capability_states[key] in PRESERVED
                        else "source_probe_failed",
                    )
                    for key in selected
                ],
            )
        retry_confirmed = False
        initial_starts = set(helper._initial_start_recovery)
        if set(blocked) - helper._permission_retry_capabilities - initial_starts:
            raise PermissionFailure("unsupported_recovery")
        helper._initial_start_preflight = frozenset()
        if initial_starts:
            if len(initial_starts) != 1:
                raise PermissionFailure("unsupported_recovery")
            selected = tuple(
                sorted(selected, key=lambda key: key not in initial_starts)
            )
            # A start failure can leave a daemon that loaded temporary config.
            # Prove the whole selection quiescent before any sibling starts.
            try:
                helper.installer.verify_initial_start_recovery(
                    {str(descriptors[key]["pack"]) for key in selected}
                )
            except Exception as exc:
                raise PermissionFailure(
                    getattr(exc, "code", "launch_state_unknown"),
                    unknown=getattr(exc, "unknown", True),
                ) from exc
            helper._initial_start_preflight = frozenset(initial_starts)
        if blocked:
            if operation != "continue":
                raise PermissionFailure("unsupported_recovery")
            retry_confirmed = interaction.confirm_retry(
                [str(descriptors[key]["label"]) for key in blocked]
            )
            if not retry_confirmed:
                return make_result(
                    operation,
                    [
                        _row(
                            key,
                            "preserved"
                            if helper.capability_states[key] in PRESERVED
                            else "cancelled",
                            "retry_consent",
                            "already_observing"
                            if helper.capability_states[key] in PRESERVED
                            else "retry_cancelled",
                        )
                        for key in selected
                    ],
                )
        for capability in selected:
            helper.permission_stage = "preflight"
            try:
                helper.reconcile_observation_progress()
                state = helper.capability_states[capability]
                if state in PRESERVED:
                    rows.append(
                        _row(capability, "preserved", "complete", "already_observing")
                    )
                    continue
                if state in {"paused", "revoked", "unsupported"}:
                    raise PermissionFailure("unsupported_recovery")
                if capability in initial_starts:
                    helper.continue_initial_start(capability, confirmed=retry_confirmed)
                    helper.record_observing(capability)
                    rows.append(_row(capability, "observing", "complete", "ok"))
                    continue
                if state in BLOCKED:
                    helper.request_permission_retry(
                        capability, confirmed=retry_confirmed
                    )
                elif state not in {"requested", "permission_required"}:
                    # A crash after permission/proof/start needs exact receipt
                    # reconciliation; do not repeat those non-idempotent steps.
                    raise PermissionFailure("state_mismatch")
                helper.permission_stage = "permission"
                descriptor = descriptors[capability]
                helper.open_permission_settings(capability)
                requirement = (
                    "Choose Full Access for this helper in Calendar settings; Add Only cannot read calendars. macOS may ask you to confirm access."
                    if capability == "apple_calendar"
                    else "Allow Reminders access for this helper. macOS may ask you to confirm full access."
                    if capability == "apple_reminders"
                    else "Allow this helper to control Notes in Privacy & Security → Automation."
                )
                if not interaction.confirm_permission(
                    capability, str(descriptor["label"]), requirement
                ):
                    helper.record_permission(capability, granted=False)
                    rows.append(
                        _row(
                            capability,
                            "blocked",
                            "permission",
                            "permission_not_confirmed",
                        )
                    )
                    continue
                helper.permission_stage = "source_selection"
                choice = interaction.choose_source(capability, str(descriptor["label"]))
                config = helper.pack_config_for(
                    capability,
                    source_coordinate=choice.source_coordinate,
                    account_coordinate=choice.account_coordinate,
                    connector_config=choice.connector_config,
                )
                helper.record_permission(capability, granted=True, pack_config=config)
                helper.permission_stage = "probe"
                probe = interaction.content_free_probe(capability, choice)
                if (
                    not isinstance(probe, dict)
                    or probe.get("status") != "passed"
                    or probe.get("content_exposed") is not False
                ):
                    raise PermissionFailure("source_probe_failed")
                helper.run_capability_test(
                    capability, lambda _capability, _request: probe
                )
                helper.permission_stage = "attestation"
                helper.prove_and_start(capability)
                helper.permission_stage = "start"
                helper.record_observing(capability)
                rows.append(_row(capability, "observing", "complete", "ok"))
            except PermissionFailure as exc:
                if exc.code == "native_cancelled" and not exc.unknown:
                    rows.append(
                        _row(capability, "cancelled", helper.permission_stage, exc.code)
                    )
                    if exc.native_error_number is not None:
                        rows[-1]["native_error_number"] = exc.native_error_number
                    rows.extend(
                        _row(key, "cancelled", "preflight", "native_cancelled")
                        for key in selected[len(rows) :]
                    )
                    break
                if not exc.unknown and helper.permission_stage in {
                    "source_selection",
                    "probe",
                }:
                    try:
                        if helper.capability_states[capability] in {
                            "requested",
                            "implementation_available",
                        }:
                            helper.record_prerequisite_missing(
                                capability, reason=exc.code
                            )
                        elif (
                            helper.capability_states[capability]
                            == "permission_verified"
                        ):
                            helper.record_capability_degraded(
                                capability, reason=exc.code
                            )
                        # permission_required remains honestly pending if no
                        # permission proof could be made during a retry.
                    except PermissionFailure as progress_error:
                        exc = progress_error
                rows.append(
                    _row(
                        capability,
                        "unknown" if exc.unknown else "blocked",
                        helper.permission_stage,
                        exc.code,
                    )
                )
                if exc.native_error_number is not None:
                    rows[-1]["native_error_number"] = exc.native_error_number
                if (
                    exc.unknown
                    or helper.permission_stage == "preflight"
                    or capability in initial_starts
                ):
                    for remaining in selected[len(rows) :]:
                        rows.append(
                            _row(
                                remaining,
                                "unknown" if exc.unknown else "blocked",
                                "preflight",
                                exc.code,
                            )
                        )
                    break
            except Exception:
                # This includes a local config write failing after central
                # acknowledgement. Never turn it into a fresh progress attempt.
                code = (
                    "local_configuration_unknown"
                    if helper.permission_stage == "source_selection"
                    else "attestation_failed"
                    if helper.permission_stage == "attestation"
                    else "start_failed"
                    if helper.permission_stage == "start"
                    else "unexpected_failure"
                )
                rows.append(_row(capability, "unknown", helper.permission_stage, code))
                for remaining in selected[len(rows) :]:
                    rows.append(_row(remaining, "unknown", "preflight", code))
                break
        if rows and all(row["status"] in {"observing", "preserved"} for row in rows):
            helper.finish_permission_phase()
        return make_result(operation, rows)
    except PermissionFailure as exc:
        return make_result(
            operation,
            [
                _row(
                    key, "unknown" if exc.unknown else "blocked", "preflight", exc.code
                )
                for key in selected
                if key in CAPABILITY_IDS
            ]
            or [_row(None, "unknown", "preflight", "invalid_result")],
        )


def validate_runtime_interfaces(root: Path) -> None:
    """Accept only the reviewed exact local modules; never substitute imports."""
    for relative, approved in RUNTIME_INTERFACE_HASHES.items():
        source = root / relative
        if (
            source.is_symlink()
            or not source.is_file()
            or hashlib.sha256(source.read_bytes()).hexdigest() not in approved
        ):
            raise PermissionFailure("runtime_mismatch")

    # Only the newly reviewed enrollment implementation imports this module.
    # Retained pre-shortcut runtimes remain compatible without inventing files.
    if (
        hashlib.sha256(
            (root / "bootstrap/bridge_enrollment.py").read_bytes()
        ).hexdigest()
        == APP_TAILSCALE_ENROLLMENT_SHA256
    ):
        dependency = root / "adminme/tailscale_local.py"
        if (
            dependency.is_symlink()
            or not dependency.is_file()
            or hashlib.sha256(dependency.read_bytes()).hexdigest()
            != APP_TAILSCALE_DEPENDENCY_SHA256
        ):
            raise PermissionFailure("runtime_mismatch")


def load_launch_controller(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Load only the separately sealed helper resource, never an ambient module."""
    _private_file(path)
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        or hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256
    ):
        raise PermissionFailure("runtime_mismatch")
    module = runpy.run_path(str(path))
    if not callable(module.get("compatible_installer_class")):
        raise PermissionFailure("runtime_mismatch")
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--expected-runtime-commit", required=True)
    parser.add_argument("--expected-central-commit", required=True)
    parser.add_argument("--bridge-root", required=True, type=Path)
    parser.add_argument("--launch-controller", required=True, type=Path)
    parser.add_argument("--launch-controller-sha256", required=True)
    parser.add_argument("--operation", choices=("initial", "continue"), required=True)
    args = parser.parse_args(argv)
    selected: tuple[str, ...] = ()
    try:
        root = args.runtime_root.expanduser()
        if (
            not re.fullmatch(r"[0-9a-f]{40}", args.expected_runtime_commit)
            or not re.fullmatch(r"[0-9a-f]{40}", args.expected_central_commit)
            or root.resolve() != root
            or not root.is_dir()
        ):
            raise PermissionFailure("runtime_mismatch")
        validate_runtime_interfaces(root)
        # The launcher verifies the sealed component hash and retained archive.
        # Add only that exact retained runtime, never ambient cwd/PYTHONPATH.
        sys.path[:0] = [str(root), str(root / "bridge")]
        from adminme.bridge_enrollment import CAPABILITIES, reviewed_release
        from adminme_bridge.enrollment_helper import MemberBridgeEnrollmentHelper
        from adminme_bridge.enrollment_ui import (
            LocalSourceChoice,
            MacOSEnrollmentInteraction,
            MemberBridgeEnrollmentWizard,
        )
        from bootstrap.bridge_enrollment import MemberBridgeInstaller, MemberBridgePaths

        if (
            reviewed_release(
                root, deployment_release_commit=args.expected_runtime_commit
            )
            != args.expected_runtime_commit
        ):
            raise PermissionFailure("runtime_mismatch")
        paths = MemberBridgePaths.from_root(args.bridge_root, production=True)
        launch_module = load_launch_controller(
            args.launch_controller, args.launch_controller_sha256
        )
        installer = launch_module["compatible_installer_class"](MemberBridgeInstaller)(
            paths, root=root
        )
        helper = compatible_helper_class(MemberBridgeEnrollmentHelper).resume(
            paths, repository_root=root, installer=installer
        )
        selected = tuple(helper.capability_states)
        interaction = native_interaction(MacOSEnrollmentInteraction, LocalSourceChoice)
        with controller_lock(paths.root):
            result = run_controller(
                helper,
                interaction,
                operation=args.operation,
                expected_commit=args.expected_runtime_commit,
                expected_central_commit=args.expected_central_commit,
                descriptors=CAPABILITIES,
                original_wizard=MemberBridgeEnrollmentWizard,
                original_interaction=MacOSEnrollmentInteraction(),
            )
    except Exception as exc:
        code = exc.code if isinstance(exc, PermissionFailure) else "unexpected_failure"
        result = make_result(
            args.operation,
            [
                _row(key, "unknown", "preflight", code)
                for key in selected
                if key in CAPABILITY_IDS
            ]
            or [_row(None, "unknown", "preflight", code)],
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return EXIT_CODES[result["outcome"]]


if __name__ == "__main__":
    raise SystemExit(main())
