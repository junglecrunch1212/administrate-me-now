"""Journaled, separately authorized CoS channel activation after deployment.

Run from the clean, merged checkout matching the selected immutable release,
using that release's Python. Inputs are private operator records, never argv
contact coordinates. This command does not create accounts or claim enrollment.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import os
import stat
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
import yaml

from adminme.adapters.cos_channel_acquisition import (
    CONTRACT as ACQUISITION_CONTRACT,
    policy_from_config,
    restrict_cos_adapter_config,
)
from adminme.adapters.cos_reminders_write_policy import (
    policy_from_config as reminders_write_policy,
    validate_authorized_change as validate_reminders_write_change,
)
from adminme.adapters.cos_apple_reminders import (
    selected_entries as selected_reminders_entries,
    validate_authorized_change as validate_reminders_change,
)
from adminme.adapters.cos_google_workspace import (
    selected_entries as selected_workspace_entries,
    validate_authorized_change as validate_workspace_change,
)
from adminme.adapters.member_google_connections import (
    Access,
    connection_config,
    policy_from_config as google_policy_from_config,
    selected_entries,
    validate_authorized_change,
)
from adminme.adapters.disposable_test_acquisition import (
    CONFIG_KEY as TEST_POLICY_KEY,
    DisposableTestAcquisition,
)
from adminme.cos_channel_operation import (
    ACTIVATED_EVENT,
    DEACTIVATED_EVENT,
    RECEIPT_FILENAME,
    CosChannelActivation,
    CosChannelActivationV2,
    SenderProof,
    aware_time,
    live_authorization,
    parse_activation,
)
from adminme.cos_response_policy import (
    POLICY_EVENT_TYPE,
    policy_from_event_log,
    validate_recipient_entries,
)
from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.lib.observation import ObservationManager
from bootstrap.activation_transaction import ActivationJournalError
from bootstrap.backup_restore import (
    BackupManager,
    _MAX_BACKUP_BYTES,
    _sha256_private_regular_file,
)
from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    capture_deployment_snapshot,
    provider_effect_window_evidence,
)
from bootstrap.cos_recovery_history import (
    RecoveryHistoryRepairRequest,
    derive_recovery_history,
    initial_readiness,
    window_vector,
)
from bootstrap.storage_headroom import StorageHeadroomError, require_deployment_headroom
from bootstrap.contracts import DeploymentPaths, atomic_write, load_versions
from bootstrap.deployment_budget import deployment_clock
from bootstrap.secrets import secret_store
from bootstrap.service_settlement import (
    probe_release_tooling,
    run_release_doctor,
    wait_for_settled_doctor,
)
from bootstrap.services import ServiceManager
from bootstrap.source_identity import (
    _run_git,
    load_git_tree,
    reviewed_candidate_commit,
    verify_release_source_identity,
)
from bootstrap.upgrade import UpgradeManager
from bootstrap.submission_repair import (
    SubmissionRepairManifest,
    load_submission_repair_manifest,
    offline_submission_repair,
    repair_binding,
    validate_submission_repair_diagnostics,
    wait_for_submission_repair,
)


from bootstrap.effect_context_repair import (
    EffectContextRepairRequest,
    EffectContextProof,
    contained_plan_history,
    retain_successor_plan,
    prepare_proof,
    verify_proof,
    wait_contained,
    require_disabled,
)


class CoSActivationError(RuntimeError):
    pass


class ActivationRequest(BaseModel):
    model_config = {"extra": "forbid", "strict": True}

    adapter_config: dict[str, Any]
    sender_proofs: list[SenderProof] = Field(min_length=1, max_length=2)
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    owner_member_id: str = Field(min_length=1, max_length=240)
    recipient_entries: list[dict[str, str]] | None = None
    allow_owner_self_replies: bool | None = None
    allow_owner_self_reminders: bool | None = None
    prepared_openclaw_config: dict[str, Any] | None = None
    cos_imsg_guard_config: dict[str, Any] | None = None


def owner_self_reply_bindings(
    request: ActivationRequest,
    prepared: dict[str, Any],
    recipients: list[dict[str, str]],
    previous_bindings: list[str],
    *,
    enable_live: bool,
) -> list[str]:
    """Retain an existing dual grant, explicitly opt in, or explicitly revoke.

    Defaults never turn a recipient ceiling into a standing grant. Routine
    reactivation retains only matching prior host/domain grants for this owner.
    """
    from adminme.cos_self_replies import self_reply_binding

    if not enable_live and request.allow_owner_self_replies:
        raise CoSActivationError("standing replies require live guarded iMessage")
    bindings = set()
    owner_bindings = set()
    for actor, member in prepared.get("openclaw_actor_identities", {}).items():
        parts = actor.split("|")
        if len(parts) != 3 or parts[0] != "imessage":
            continue
        if not any(
            row == {"member_id": member, "channel": "imessage", "address": parts[2]}
            for row in recipients
        ):
            continue
        binding = self_reply_binding(member, parts[1], parts[2])
        if member == request.owner_member_id:
            owner_bindings.add(binding)
            if request.allow_owner_self_replies is False:
                continue
            if request.allow_owner_self_replies is True:
                bindings.add(binding)
        if binding in previous_bindings:
            bindings.add(binding)
    if request.allow_owner_self_replies and not bindings.intersection(owner_bindings):
        raise CoSActivationError("owner has no exact allowlisted private reply binding")
    return sorted(bindings)


class EnterObservationRequest(BaseModel):
    """Content-free binding to the live operation being paused for deployment."""

    model_config = {"extra": "forbid", "strict": True}

    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    owner_member_id: str = Field(min_length=1, max_length=240)
    expected_release_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    expected_activation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class MaintenancePreparationRequest(BaseModel):
    """Existing authority and optional fresh proofs; never new permissions."""

    model_config = {"extra": "forbid", "strict": True}

    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    owner_member_id: str = Field(min_length=1, max_length=240)
    effect_context_repair: EffectContextRepairRequest | None = None
    sender_proofs: list[SenderProof] | None = Field(
        default=None, min_length=1, max_length=2
    )


class MaintenanceRestorationPlan(MaintenancePreparationRequest):
    """Private pre-maintenance artifact consumed by the ordinary transactions."""

    contract: Literal["cos-maintenance-restoration-v1"] = (
        "cos-maintenance-restoration-v1"
    )
    effect_context_proof: EffectContextProof | None = None
    plan_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    prepared_at: str
    expires_at: str
    target_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    predecessor_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    prior_activation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    deployment_snapshot: dict[str, str]
    adapter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    member_mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipient_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gateway_sha256: str | None = None
    gateway_compatibility_sha256: str | None = None
    guard_config: dict[str, Any] | None = None
    self_reply_bindings: list[str] = Field(default_factory=list, max_length=20)
    self_reminder_bindings: list[str] = Field(default_factory=list, max_length=20)

    def digest(self) -> str:
        return _sha(_payload(self.model_dump(mode="json")))

    def observation_request(self) -> EnterObservationRequest:
        return EnterObservationRequest(
            owner_authorization_sha256=self.owner_authorization_sha256,
            operator_principal_id=self.operator_principal_id,
            owner_member_id=self.owner_member_id,
            expected_release_commit=self.predecessor_commit,
            expected_activation_id=self.prior_activation_id,
            expected_configuration_fingerprint=self.deployment_snapshot[
                "configuration_fingerprint"
            ],
        )


def _maintenance_gateway_fingerprint(gateway: dict[str, Any], release: Path) -> str:
    """Ignore only updater-owned registration metadata and restored grant fields."""
    value = copy.deepcopy(gateway)
    metadata = value.setdefault("meta", {})
    metadata.pop("lastTouchedAt", None)
    metadata.pop("lastTouchedVersion", None)
    plugins = value.setdefault("plugins", {})
    plugins.setdefault("installs", {}).pop("administrateme", None)
    load = plugins.setdefault("load", {})
    load["paths"] = [
        "<selected-administrateme-plugin>"
        if path == str(release / "openclaw/administrateme")
        else path
        for path in load.get("paths", [])
    ]
    entry = plugins.setdefault("entries", {}).setdefault("administrateme", {})
    config = entry.setdefault("config", {})
    config.pop("standingSelfReplyBindings", None)
    config.pop("scheduledSelfReminderBindings", None)
    entry.setdefault("hooks", {}).pop("allowConversationAccess", None)
    for account in (
        value.get("channels", {}).get("imessage", {}).get("accounts", {}).values()
    ):
        if account.get("cliPath") == str(
            release / "openclaw/administrateme/bin/cos-imsg-guard.mjs"
        ):
            account["cliPath"] = "<selected-cos-guard>"
    return _sha(_payload(value))


def _private_bytes(path: Path) -> bytes:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o077
        or metadata.st_size > 4 * 1024 * 1024
    ):
        raise CoSActivationError("operation input is not an owner-only regular file")
    return path.read_bytes()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(_private_bytes(path))
    if not isinstance(value, dict):
        raise CoSActivationError("operation document is not an object")
    return value


def _observation_control_bytes(config_dir: Path, name: str) -> bytes:
    """Read only the predecessor's exact legacy non-secret observation file.

    Call after selected-source and configuration-binding verification. Older
    ObservationManager versions published this one file as 0644 under umask
    022. Every other coordinate retains the ordinary owner-only requirement.
    The operation journals these bytes and the fixed writer republishes 0600.
    """
    path = config_dir / name
    if name != "runtime.yaml" or stat.S_IMODE(path.lstat().st_mode) != 0o644:
        return _private_bytes(path)
    parent = config_dir.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise CoSActivationError("legacy observation control parent is not private")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o644
            or metadata.st_size > 4096
        ):
            raise CoSActivationError("legacy observation control coordinate is unsafe")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(4097)
        if len(payload) != metadata.st_size:
            raise CoSActivationError("legacy observation control changed during read")
    finally:
        os.close(descriptor)
    value = yaml.safe_load(payload)
    state = value.get("observation_mode_override") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != {"observation_mode_override"}
        or not isinstance(state, dict)
        or set(state) != {"active", "enabled_at", "enabled_by"}
        or state["active"] is not False
        or aware_time(state["enabled_at"]) is None
        or not isinstance(state["enabled_by"], str)
        or not state["enabled_by"]
        or len(state["enabled_by"]) > 240
        # The predecessor writer emits this exact canonical shape. Requiring
        # its bytes also rejects duplicate/merge keys hidden by YAML loading.
        or payload != yaml.safe_dump(value, sort_keys=True).encode("utf-8")
    ):
        raise CoSActivationError("legacy observation control is not exact toggle state")
    return payload


def _payload(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def prepare_configuration(
    previous: dict[str, Any], requested: dict[str, Any], *, enable_live: bool
) -> dict[str, Any]:
    """Preserve history and admit only the CoS and exact Google connection selections."""

    retained = DisposableTestAcquisition.parse(previous.get(TEST_POLICY_KEY))
    if requested.get(TEST_POLICY_KEY) != previous.get(TEST_POLICY_KEY):
        raise CoSActivationError(
            "the original test acquisition selection must not change"
        )
    policy = policy_from_config(requested)
    if (
        policy is None
        or policy.retained_test_policy_sha256 != retained.receipt_digest()
    ):
        raise CoSActivationError(
            "the CoS overlay does not preserve the original selection"
        )
    reminders_write_policy(requested)
    selected_workspace = selected_workspace_entries(
        requested
    ) | selected_reminders_entries(requested)
    prior_workspace = selected_workspace_entries(previous) | selected_reminders_entries(
        previous
    )
    original_ids = [
        entry.get("id")
        for entry in previous.get("adapters", [])
        if isinstance(entry, dict)
    ]
    requested_ids = [
        entry.get("id")
        for entry in requested.get("adapters", [])
        if isinstance(entry, dict)
    ]
    if (
        any(
            not isinstance(value, str) or not value
            for value in original_ids + requested_ids
        )
        or len(set(original_ids)) != len(original_ids)
        or len(set(requested_ids)) != len(requested_ids)
        or not set(original_ids) <= set(requested_ids)
        or not (set(requested_ids) - set(original_ids)) <= selected_workspace.keys()
    ):
        raise CoSActivationError(
            "channel activation can add only exact CoS workspace sources and cannot remove records"
        )
    result = restrict_cos_adapter_config(requested)
    selected_google = selected_entries(result)
    google_policy = google_policy_from_config(result)
    google_access: dict[str, Access] = (
        {row.adapter_id: row.access for row in google_policy.selections}
        if google_policy
        else {}
    )
    for entry in result["adapters"]:
        config = entry.get("config") or {}
        if entry.get("id") == policy.gmail_adapter_id:
            entry["enabled"] = True
            config["enable_writes"] = enable_live
            config["read_only"] = not enable_live
            if enable_live and "https://www.googleapis.com/auth/gmail.send" not in (
                config.get("permission_scope") or []
            ):
                raise CoSActivationError("live Gmail requires an enrolled send grant")
        elif entry.get("id") in selected_workspace:
            if entry["id"] in original_ids and entry["id"] not in prior_workspace:
                before = next(
                    item for item in previous["adapters"] if item["id"] == entry["id"]
                )
                if before.get("config") != config or before.get("kind") != entry.get(
                    "kind"
                ):
                    raise CoSActivationError(
                        "cannot repurpose an existing adapter as a CoS workspace source"
                    )
        else:
            before = next(
                item
                for item in previous["adapters"]
                if item.get("id") == entry.get("id")
            )
            # Connection access is separately checked against this operation's
            # exact authorizing member. Preserve capabilities without replacing
            # enrolled account credentials, source selections or cursor IDs.
            selected = entry.get("id") in selected_google
            expected_config = (
                connection_config(before, access=google_access[entry["id"]])
                if selected
                else before.get("config")
            )
            if not selected:
                entry["enabled"] = False
            if entry.get("config") != expected_config:
                raise CoSActivationError(
                    "channel activation cannot alter member adapters"
                )
    for channel in result["service_identities"]["channels"]:
        selected = channel.get("channel_id") in {
            policy.gmail_channel_id,
            policy.imessage_channel_id,
        }
        if selected:
            channel["state"] = "healthy" if enable_live else "observation"
            channel["observation_active"] = not enable_live
            channel["sending_enabled"] = enable_live
    return result


class CoSChannelActivator:
    def __init__(
        self, paths: DeploymentPaths, *, repository: Path, test_mode: bool = False
    ) -> None:
        self.paths = paths
        self.repository = repository.resolve()
        self.test_mode = test_mode
        self.services = ServiceManager(paths, test_mode=test_mode)
        self.backups = BackupManager(paths, root=repository, test_mode=test_mode)
        self.directory = paths.app_dir / "cos-channel-operation"
        self.journal_path = self.directory / "active.json"
        self.config_dir = paths.instance_dir / "config"

    def _selection(self) -> tuple[str, Path]:
        pointer = _json(self.paths.app_dir / "current-release.json")
        commit = reviewed_candidate_commit(self.repository)
        release = Path(pointer["path"])
        if pointer.get("commit") != commit:
            raise CoSActivationError(
                "operation source must match the active merged release"
            )
        if ACQUISITION_CONTRACT not in load_versions(self.repository)[
            "administrateme"
        ].get("supported_acquisition_contracts", []):
            raise CoSActivationError(
                "selected release cannot preserve CoS-only acquisition"
            )
        if not self.test_mode:
            verify_release_source_identity(
                release,
                repository=self.repository,
                commit=commit,
                allow_legacy_tree=False,
                require_tracked_tree=True,
                retained_by_commit=commit,
            )
        return commit, release

    def _observation_selection(self) -> tuple[str, Path, str]:
        """Permit a merged successor controller to contain its live predecessor.

        This runs before candidate readiness can be collected. It neither
        publishes nor starts the candidate and uses the selected Python.
        """
        controller_commit = reviewed_candidate_commit(self.repository)
        pointer = _json(self.paths.app_dir / "current-release.json")
        commit, release = pointer.get("commit"), Path(pointer["path"])
        if (
            not isinstance(commit, str)
            or release != self.paths.app_dir / "releases" / commit
        ):
            raise CoSActivationError("selected release coordinate is invalid")
        if not self.test_mode:
            main_commit = (
                _run_git(self.repository, ["rev-parse", "refs/remotes/origin/main"])
                .stdout.decode("ascii")
                .strip()
            )
            if (
                main_commit != controller_commit
                or Path(__file__).resolve().parents[1] != self.repository
                or Path(sys.prefix).resolve() != (release / ".venv").resolve()
            ):
                raise CoSActivationError(
                    "enter-observation requires clean protected-main source and selected Python"
                )
            verify_release_source_identity(
                release,
                repository=self.repository,
                commit=commit,
                allow_legacy_tree=False,
                require_tracked_tree=True,
                retained_by_commit=controller_commit,
            )
        for root in (self.repository, release):
            if ACQUISITION_CONTRACT not in load_versions(root)["administrateme"].get(
                "supported_acquisition_contracts", []
            ):
                raise CoSActivationError(
                    "observation transition requires CoS-compatible releases"
                )
        return commit, release, controller_commit

    def _bound_snapshot(self, commit: str) -> dict[str, str]:
        snapshot = capture_deployment_snapshot(self.paths)
        binding = _json(self.config_dir / "deployment-readiness.json").get(
            "deployment_binding"
        )
        if snapshot.predecessor_commit != commit or binding != {
            "release_commit": commit,
            "instance_fingerprint": snapshot.instance_fingerprint,
            "host_fingerprint": snapshot.host_fingerprint,
            "configuration_fingerprint": snapshot.configuration_fingerprint,
        }:
            raise CoSActivationError(
                "selected deployment configuration binding is stale"
            )
        return snapshot.public()

    async def _live_predecessor(self, release: Path) -> dict[str, Any]:
        adapters = _json(self.config_dir / "adapters.json")
        policy = policy_from_config(adapters)
        if policy is None:
            raise CoSActivationError("live CoS acquisition policy is missing")
        if policy.imessage_channel_id is not None:
            from bootstrap.cos_imessage import (
                validate_prepared_channel,
                verify_guard_files,
            )

            guard = _json(self.config_dir / "cos-imsg-guard.json")
            validate_prepared_channel(
                _json(self.paths.instance_dir / "openclaw/runtime/openclaw.json"),
                guard,
                adapter_config=adapters,
            )
            verify_guard_files(guard, reviewed_release=release)
        log = self._event_log(read_only=True)
        try:
            result = await live_authorization(
                log,
                load_instance_config(self.paths.instance_dir).tenant_id,
                adapters,
                _json(self.config_dir / RECEIPT_FILENAME),
            )
        finally:
            await log.close()
        if result.get("authorized") is not True:
            raise CoSActivationError(
                "predecessor has no current durable CoS live authority"
            )
        return result

    async def _effect_count(self) -> int:
        from adminme.source_assurance import audit_source_assurance

        log = self._event_log(read_only=True)
        try:
            report = await audit_source_assurance(
                log, repository_root=self.repository, capabilities={}
            )
        finally:
            await log.close()
        effects = report["provider_effects"]
        count = effects["counts"].get("provider_called_true")
        if effects.get("state") != "healthy" or type(count) is not int or count < 0:
            raise CoSActivationError("provider-effect integrity is incomplete")
        return count

    async def _recovery_history_events(self) -> list[dict[str, Any]]:
        log = self._event_log(read_only=True)
        try:
            return [
                event
                async for event in log.read_since(
                    None,
                    types=[
                        ACTIVATED_EVENT,
                        DEACTIVATED_EVENT,
                        "observation.enabled",
                        "observation.disabled",
                    ],
                )
            ]
        finally:
            await log.close()

    def _derive_history_repair(
        self, request: RecoveryHistoryRepairRequest, original: bytes
    ) -> dict[str, Any]:
        if _sha(original) != request.readiness_sha256:
            raise CoSActivationError("history repair initial readiness digest differs")
        journals = []
        for reference in (
            request.failed_operation,
            request.activation_operation,
            request.maintenance_operation,
        ):
            payload = _private_bytes(
                self.directory / "history" / f"{reference.operation_id}.json"
            )
            if _sha(payload) != reference.sha256:
                raise CoSActivationError(
                    "history repair archived journal digest differs"
                )
            journals.append(json.loads(payload))
        try:
            return derive_recovery_history(
                request,
                json.loads(original),
                journals[0],
                journals[1],
                journals[2],
                asyncio.run(self._recovery_history_events()),
                tenant_id=load_instance_config(self.paths.instance_dir).tenant_id,
            )
        except (ValueError, CandidateReadinessError) as exc:
            raise CoSActivationError(
                "history repair retained proof is invalid"
            ) from exc

    async def _repair_interval_effects(self, repaired: dict[str, Any]) -> None:
        from adminme.source_assurance import audit_source_assurance

        prior = repaired["prior_provider_effect_windows"]
        log = self._event_log(read_only=True)
        try:
            report = await audit_source_assurance(
                log,
                repository_root=self.repository,
                capabilities={},
                provider_effect_window_started_at=prior[-3]["started_at"],
                provider_effect_window_ended_at=prior[-1]["ended_at"],
            )
        finally:
            await log.close()
        effects = report["provider_effects"]
        window = effects["observation_window"]
        if (
            effects.get("state") != "healthy"
            or window.get("state") != "healthy"
            or window["counts"].get("provider_called_true") != 0
        ):
            raise CoSActivationError(
                "history repair interval has incomplete or nonzero effects"
            )

    def _require_repair_observation(self) -> None:
        state = yaml.safe_load(_private_bytes(self.config_dir / "runtime.yaml"))
        if (
            not isinstance(state, dict)
            or (state.get("observation_mode_override") or {}).get("active") is not True
            or _json(self.config_dir / "observation.json").get("active") is not True
        ):
            raise CoSActivationError(
                "history repair requires explicit observation mode"
            )

    def repair_recovery_history(
        self, request: RecoveryHistoryRepairRequest
    ) -> dict[str, Any]:
        """Repair only the proved legacy overwrite, without editing its facts."""
        updater = UpgradeManager(
            self.paths, root=self.repository, test_mode=self.test_mode
        )
        with updater._operation_lock():
            if (
                self.journal_path.exists()
                or (self.paths.app_dir / "activation/active.json").exists()
            ):
                raise CoSActivationError("recover the interrupted operation first")
            commit, release, controller_commit = self._observation_selection()
            if (commit, controller_commit) != (
                request.expected_release_commit,
                request.expected_controller_commit,
            ) or self._bound_snapshot(commit) != request.initial_snapshot:
                raise CoSActivationError("history repair source or snapshot differs")
            members = {
                row["member_id"]: row["role"]
                for row in _json(self.config_dir / "member-mapping.json")["members"]
            }
            if members.get(request.owner_member_id) != "principal":
                raise CoSActivationError(
                    "history repair requires its exact authorizing principal"
                )
            self._require_repair_observation()
            before = self.services.runtime_diagnostics() or {}
            if (
                before.get("ready") is not True
                or before.get("observation_active") is not True
            ):
                raise CoSActivationError(
                    "history repair requires healthy observation containment"
                )
            original = _private_bytes(self.config_dir / "deployment-readiness.json")
            repaired = self._derive_history_repair(request, original)
            asyncio.run(self._repair_interval_effects(repaired))
            policy = asyncio.run(self._policy())
            journal = {
                "contract": "cos-channel-operation-journal-v1",
                "operation": "repair_recovery_history",
                "activation_id": uuid.uuid4().hex,
                "release_commit": commit,
                "controller_commit": controller_commit,
                "operator_principal_id": request.operator_principal_id,
                "owner_member_id": request.owner_member_id,
                "owner_authorization_sha256": request.owner_authorization_sha256,
                "request": request.model_dump(mode="json"),
                "initial_snapshot": request.initial_snapshot,
                "initial_policy": {
                    "event_id": policy["event_id"],
                    "revision": policy["revision"],
                },
                "initial_readiness": base64.b64encode(original).decode(),
                "repaired_readiness": base64.b64encode(_payload(repaired)).decode(),
                "repaired_readiness_sha256": _sha(_payload(repaired)),
                "diagnostic_provider_calls_before": self._provider_calls(before),
            }
            self._write_journal(journal, "prepared")
            return self._complete_recovery_history_repair(journal, release=release)

    def _verify_history_backup(self, journal: dict[str, Any]) -> None:
        backup, rehearsal = journal["backup"], journal["rehearsal"]
        path = Path(backup["path"])
        if (
            path.parent != self.paths.backup_dir
            or not path.name.startswith(
                f"cos-history-repair-{journal['activation_id']}-"
            )
            or path.suffix != ".ambackup"
            or backup.get("encrypted") is not True
            or rehearsal.get("status") != "healthy"
            or (rehearsal.get("artifact") or {}).get("authenticated") is not True
            or (rehearsal.get("candidate") or {}).get("commit")
            != journal["controller_commit"]
            or rehearsal.get("deployment_binding") != journal["initial_snapshot"]
            or _sha256_private_regular_file(path, maximum_size=_MAX_BACKUP_BYTES)
            != rehearsal["artifact"].get("sha256")
        ):
            raise CoSActivationError("history repair backup or restore binding differs")

    def _complete_recovery_history_repair(
        self, journal: dict[str, Any], *, release: Path
    ) -> dict[str, Any]:
        """Resume the same exact repair; a failure retains observation and its hold."""
        try:
            self.services.stop()
            request = RecoveryHistoryRepairRequest.model_validate(journal["request"])
            selected = self._observation_selection()
            if selected != (
                request.expected_release_commit,
                release,
                request.expected_controller_commit,
            ):
                raise CoSActivationError("history repair source or selection changed")
            if any(
                journal.get(key) != expected
                for key, expected in {
                    "operation": "repair_recovery_history",
                    "release_commit": request.expected_release_commit,
                    "controller_commit": request.expected_controller_commit,
                    "owner_member_id": request.owner_member_id,
                    "operator_principal_id": request.operator_principal_id,
                    "owner_authorization_sha256": request.owner_authorization_sha256,
                    "initial_snapshot": request.initial_snapshot,
                }.items()
            ):
                raise CoSActivationError("history repair journal binding differs")
            self._require_repair_observation()
            if (
                self._bound_snapshot(request.expected_release_commit)
                != request.initial_snapshot
            ):
                raise CoSActivationError("history repair configuration changed")
            policy = asyncio.run(self._policy())
            if {
                "event_id": policy["event_id"],
                "revision": policy["revision"],
            } != journal["initial_policy"]:
                raise CoSActivationError("history repair recipient policy changed")
            original = base64.b64decode(journal["initial_readiness"], validate=True)
            repaired = self._derive_history_repair(request, original)
            proposed = _payload(repaired)
            if (
                proposed
                != base64.b64decode(journal["repaired_readiness"], validate=True)
                or _sha(proposed) != journal["repaired_readiness_sha256"]
            ):
                raise CoSActivationError("history repair immutable plan differs")
            path = self.config_dir / "deployment-readiness.json"
            if _private_bytes(path) not in (original, proposed):
                raise CoSActivationError("history repair readiness changed")
            asyncio.run(self._repair_interval_effects(repaired))
            effects = asyncio.run(self._effect_count())
            if "prior_provider_calls" not in journal:
                if effects != journal["diagnostic_provider_calls_before"]:
                    raise CoSActivationError(
                        "history repair found an unsettled provider effect"
                    )
                journal["prior_provider_calls"] = effects
                self._write_journal(journal, "quiesced")
            if effects != journal["prior_provider_calls"]:
                raise CoSActivationError("history repair provider-effect count changed")
            if "backup" not in journal:
                backup = self.backups.create(
                    self.paths.backup_dir
                    / f"cos-history-repair-{journal['activation_id']}-{uuid.uuid4().hex}.ambackup"
                )
                rehearsal = self.backups.rehearse(
                    Path(backup["path"]), candidate=self.repository
                )
                if (
                    backup.get("encrypted") is not True
                    or rehearsal.get("status") != "healthy"
                    or (rehearsal.get("artifact") or {}).get("authenticated")
                    is not True
                ):
                    raise CoSActivationError(
                        "history repair encrypted backup restore proof failed"
                    )
                journal.update(backup=backup, rehearsal=rehearsal)
                self._verify_history_backup(journal)
                self._write_journal(journal, "backup_verified")
            self._verify_history_backup(journal)
            # Revalidate after the lengthy backup/rehearsal. All earlier files,
            # facts and provider configuration remain untouched by this repair.
            if (
                self._observation_selection() != selected
                or self._bound_snapshot(request.expected_release_commit)
                != request.initial_snapshot
                or _private_bytes(path) not in (original, proposed)
                or _payload(self._derive_history_repair(request, original)) != proposed
                or asyncio.run(self._effect_count()) != effects
                or asyncio.run(self._policy()).get("event_id") != policy["event_id"]
            ):
                raise CoSActivationError(
                    "history repair evidence changed during backup verification"
                )
            self._require_repair_observation()
            self._write_journal(journal, "repairing_history")
            if _private_bytes(path) != proposed:
                self._refresh_binding(repaired, request.expected_release_commit)
            if _private_bytes(path) != proposed:
                raise CoSActivationError(
                    "history repair changed more than its exact window plan"
                )
            self._write_journal(journal, "verifying")
            self.services.start()
            journal["doctor"] = self._settle(release)
            after = self.services.runtime_diagnostics() or {}
            if (
                after.get("ready") is not True
                or after.get("observation_active") is not True
                or self._provider_calls(after) != effects
                or asyncio.run(self._effect_count()) != effects
                or self._bound_snapshot(request.expected_release_commit)
                != request.initial_snapshot
                or asyncio.run(self._policy()).get("event_id") != policy["event_id"]
                or self._observation_selection() != selected
                or _private_bytes(path) != proposed
                or _payload(self._derive_history_repair(request, original)) != proposed
            ):
                raise CoSActivationError(
                    "history repair did not settle without drift or effects"
                )
            asyncio.run(self._repair_interval_effects(repaired))
            self._require_repair_observation()
            history = self.directory / "history" / f"{journal['activation_id']}.json"
            if history.exists():
                prior = _json(history)
                if prior.get("phase") != "committed" or any(
                    prior.get(key) != value
                    for key, value in journal.items()
                    if key not in {"phase", "doctor"}
                ):
                    raise CoSActivationError("history repair archive differs")
                self._finish(prior, "committed")
            else:
                self._finish(journal, "committed")
            return {
                "status": "recovery_history_repaired",
                "operation_id": journal["activation_id"],
                "release_commit": request.expected_release_commit,
                "observation_active": True,
                "provider_effects": 0,
                "retained_events_and_journals_unchanged": True,
            }
        except BaseException:
            self.services.stop()
            # The fresh journal continues to hold channel authority. Recovery
            # rechecks the same exact input/output instead of guessing or editing
            # the malformed historical evidence a second time.
            raise

    def _maintenance_controls(self) -> dict[str, Any]:
        adapters = _json(self.config_dir / "adapters.json")
        policy = policy_from_config(adapters)
        receipt = _json(self.config_dir / RECEIPT_FILENAME)
        activation = parse_activation(receipt["activation"])
        gateway = None
        guard = None
        replies: list[str] = []
        reminders: list[str] = []
        if policy is not None and policy.imessage_channel_id is not None:
            gateway = _json(self.paths.instance_dir / "openclaw/runtime/openclaw.json")
            guard = _json(self.config_dir / "cos-imsg-guard.json")
            config = (
                gateway.get("plugins", {})
                .get("entries", {})
                .get("administrateme", {})
                .get("config", {})
            )
            replies = sorted(
                set(getattr(activation, "self_reply_bindings", [])).intersection(
                    config.get("standingSelfReplyBindings", [])
                )
            )
            reminders = sorted(
                set(replies).intersection(
                    config.get("scheduledSelfReminderBindings", [])
                )
            )
        return {
            "adapters": adapters,
            "receipt": receipt,
            "activation": activation,
            "members": _json(self.config_dir / "member-mapping.json"),
            "policy": asyncio.run(self._policy()),
            "gateway": gateway,
            "guard": guard,
            "replies": replies,
            "reminders": reminders,
        }

    @staticmethod
    def _maintenance_proof_expiry(
        proofs: list[SenderProof], prior: CosChannelActivation
    ) -> datetime:
        if sorted(
            (proof.channel, proof.provider_account_sha256) for proof in proofs
        ) != sorted(
            (proof.channel, proof.provider_account_sha256) for proof in prior.channels
        ):
            raise CoSActivationError(
                "maintenance cannot change enrolled sender accounts"
            )
        now = datetime.now(timezone.utc)
        observed = [aware_time(proof.observed_at) for proof in proofs]
        if any(
            value is None or not 0 <= (now - value).total_seconds() < 86400
            for value in observed
        ):
            raise CoSActivationError(
                "refresh actual sender/transport evidence before maintenance"
            )
        return min(value + timedelta(days=1) for value in observed if value is not None)

    def prepare_maintenance(
        self, request: MaintenancePreparationRequest
    ) -> MaintenanceRestorationPlan:
        """Read live authority before stopping services; do not mutate the instance."""
        updater = UpgradeManager(
            self.paths, root=self.repository, test_mode=self.test_mode
        )
        with updater._operation_lock():
            if (
                self.journal_path.exists()
                or (self.paths.app_dir / "activation/active.json").exists()
            ):
                raise CoSActivationError(
                    "complete the current operation before preparing maintenance"
                )
            commit, release, target = self._observation_selection()
            if commit == target:
                raise CoSActivationError("maintenance target is already selected")
            if not self.test_mode:
                probe_release_tooling(self.paths, release, self.repository)
            snapshot = self._bound_snapshot(commit)
            before = self.services.runtime_diagnostics() or {}
            effect_proof = None
            if request.effect_context_repair is not None:
                effect_proof = prepare_proof(
                    self.paths,
                    self.repository,
                    request.effect_context_repair,
                    before,
                    predecessor=commit,
                    candidate=target,
                    test_mode=self.test_mode,
                )
            elif (
                before.get("ready") is not True
                or before.get("observation_active") is not False
            ):
                raise CoSActivationError(
                    "maintenance preparation requires healthy live operation"
                )
            live = asyncio.run(self._live_predecessor(release))
            controls = self._maintenance_controls()
            if not any(
                row.get("member_id") == request.owner_member_id
                and row.get("role") == "principal"
                for row in controls["members"]["members"]
            ):
                raise CoSActivationError(
                    "operation requires its exact authorizing principal"
                )
            proofs = request.sender_proofs or controls["activation"].channels
            expires = self._maintenance_proof_expiry(proofs, controls["activation"])
            gateway = controls["gateway"]
            expected_gateway = None
            if gateway is not None:
                from bootstrap.openclaw_activation import prepare_service_topology

                expected_gateway = prepare_service_topology(
                    gateway, self.paths, self.repository
                )
            plan = MaintenanceRestorationPlan(
                **request.model_dump(mode="json", exclude={"sender_proofs"}),
                sender_proofs=proofs,
                effect_context_proof=effect_proof,
                plan_id=uuid.uuid4().hex,
                prepared_at=datetime.now(timezone.utc).isoformat(),
                expires_at=expires.isoformat(),
                target_commit=target,
                predecessor_commit=commit,
                prior_activation_id=live["activation_id"],
                deployment_snapshot=snapshot,
                adapter_sha256=_sha(_payload(controls["adapters"])),
                receipt_sha256=_sha(_payload(controls["receipt"])),
                member_mapping_sha256=_sha(_payload(controls["members"])),
                recipient_policy_sha256=_sha(_payload(controls["policy"])),
                gateway_sha256=_sha(_payload(gateway)) if gateway is not None else None,
                gateway_compatibility_sha256=_maintenance_gateway_fingerprint(
                    expected_gateway, release
                )
                if expected_gateway is not None
                else None,
                guard_config=controls["guard"],
                self_reply_bindings=controls["replies"],
                self_reminder_bindings=controls["reminders"],
            )
            self._validate_maintenance_plan(
                plan, commit=commit, release=release, restoring=False
            )
            return plan

    def _validate_maintenance_plan(
        self,
        plan: MaintenanceRestorationPlan,
        *,
        commit: str,
        release: Path,
        restoring: bool,
    ) -> dict[str, Any]:
        controls = self._maintenance_controls()
        proof = plan.effect_context_proof
        if (plan.effect_context_repair is None) != (proof is None) or (
            proof is not None
            and (
                proof.request != plan.effect_context_repair
                or proof.predecessor_commit != plan.predecessor_commit
                or proof.candidate_commit != plan.target_commit
            )
        ):
            raise CoSActivationError("maintenance effect-context proof differs")
        if proof is not None:
            require_disabled(self.paths, proof.request_actions_sha256)
        if proof is not None and not restoring:
            verify_proof(
                self.paths,
                self.repository,
                proof,
                test_mode=self.test_mode,
                diagnostics=self.services.runtime_diagnostics() or {},
                observation=False,
            )
        prepared = aware_time(plan.prepared_at)
        expires = aware_time(plan.expires_at)
        now = datetime.now(timezone.utc)
        if (
            prepared is None
            or expires is None
            or not prepared <= now < expires
            or plan.sender_proofs is None
            or self._maintenance_proof_expiry(
                plan.sender_proofs, controls["activation"]
            )
            != expires
        ):
            raise CoSActivationError(
                "maintenance restoration plan expired or has invalid proof times"
            )
        if commit != (plan.target_commit if restoring else plan.predecessor_commit):
            raise CoSActivationError(
                "maintenance restoration belongs to another release"
            )
        for expected, value in (
            (plan.adapter_sha256, controls["adapters"]),
            (plan.receipt_sha256, controls["receipt"]),
            (plan.member_mapping_sha256, controls["members"]),
            (plan.recipient_policy_sha256, controls["policy"]),
        ):
            if expected != _sha(_payload(value)):
                raise CoSActivationError(
                    "maintenance authority or configuration drifted; prepare a new request"
                )
        if controls["activation"].activation_id != plan.prior_activation_id:
            raise CoSActivationError("maintenance predecessor authority changed")
        gateway = controls["gateway"]
        if not restoring:
            from bootstrap.openclaw_activation import prepare_service_topology
            from bootstrap.workspace_instructions import (
                WorkspaceInstructionsError,
                prepare_transition,
            )

            try:
                # Read and discard the private byte plan. The updater captures
                # its own transactional plan later; no instructions are printed
                # or installed during maintenance preparation or containment.
                prepare_transition(
                    self.paths,
                    source_release=release,
                    target_release=self.repository,
                    adapter_config=controls["adapters"],
                )
            except WorkspaceInstructionsError as exc:
                raise CoSActivationError(str(exc)) from exc
            if gateway is not None:
                native_hook = (
                    gateway.get("plugins", {})
                    .get("entries", {})
                    .get("administrateme", {})
                    .get("hooks", {})
                    .get("allowConversationAccess")
                )
                scheduler = gateway.get("cron", {}).get("triggers", {}).get("enabled")
                if (plan.self_reply_bindings and native_hook is not True) or (
                    plan.self_reminder_bindings and scheduler is not True
                ):
                    raise CoSActivationError(
                        "maintenance cannot re-enable a disabled native hook or scheduler"
                    )
            # A recipient revocation can leave old host/domain binding hashes
            # behind. Discover an unrestorable grant before entering maintenance.
            assert plan.sender_proofs is not None
            authority = ActivationRequest(
                adapter_config=controls["adapters"],
                sender_proofs=plan.sender_proofs,
                owner_authorization_sha256=plan.owner_authorization_sha256,
                operator_principal_id=plan.operator_principal_id,
                owner_member_id=plan.owner_member_id,
            )
            eligible = owner_self_reply_bindings(
                authority,
                controls["adapters"],
                controls["policy"]["entries"],
                plan.self_reply_bindings,
                enable_live=True,
            )
            if eligible != plan.self_reply_bindings or not set(
                plan.self_reminder_bindings
            ).issubset(eligible):
                raise CoSActivationError(
                    "private reply grants no longer match current recipient routes"
                )
            if (
                self._bound_snapshot(commit) != plan.deployment_snapshot
                or controls["guard"] != plan.guard_config
                or (_sha(_payload(gateway)) if gateway is not None else None)
                != plan.gateway_sha256
                or (
                    _maintenance_gateway_fingerprint(
                        prepare_service_topology(gateway, self.paths, self.repository),
                        release,
                    )
                    if gateway is not None
                    else None
                )
                != plan.gateway_compatibility_sha256
                or controls["replies"] != plan.self_reply_bindings
                or controls["reminders"] != plan.self_reminder_bindings
            ):
                raise CoSActivationError(
                    "maintenance configuration or grants drifted before observation"
                )
        else:
            history = _json(self.directory / "history" / f"{plan.plan_id}.json")
            if proof is not None:
                contained_plan_history(
                    self.paths, self.repository, plan, test_mode=self.test_mode
                )
            elif (
                history.get("phase") != "committed"
                or history.get("operation") != "enter_observation"
                or history.get("maintenance_plan_sha256") != plan.digest()
                or history.get("controller_commit") != plan.target_commit
                or history.get("prior_activation_id") != plan.prior_activation_id
                or history.get("initial_snapshot") != plan.deployment_snapshot
            ):
                raise CoSActivationError(
                    "maintenance restoration has no matching completed observation operation"
                )
            events = asyncio.run(self._recovery_history_events())
            tenant_id = load_instance_config(self.paths.instance_dir).tenant_id
            changes = [
                row
                for row in events
                if row.get("type") in {ACTIVATED_EVENT, DEACTIVATED_EVENT}
                and row.get("tenant_id") == tenant_id
            ]
            latest = changes[-1] if changes else {}
            if (
                latest.get("type") != DEACTIVATED_EVENT
                or latest.get("payload")
                != {
                    "activation_id": plan.prior_activation_id,
                    "operator_principal_id": plan.operator_principal_id,
                    "reason": "deployment_observation",
                    "observation_active": True,
                }
                or latest.get("idempotency_key")
                != f"cos-operation:{plan.plan_id}:observation"
            ):
                raise CoSActivationError(
                    "live authority changed after planned observation"
                )
            snapshot = self._bound_snapshot(commit)
            updater = UpgradeManager(
                self.paths, root=self.repository, test_mode=self.test_mode
            )
            try:
                deployment = updater.activation_journal.committed_activation(
                    target_commit=plan.target_commit,
                    predecessor_commit=plan.predecessor_commit,
                )
            except ActivationJournalError as exc:
                raise CoSActivationError(
                    "maintenance requires the exact completed updater transaction"
                ) from exc
            prior_binding = {
                "release_commit": plan.predecessor_commit,
                **{
                    key: plan.deployment_snapshot[key]
                    for key in (
                        "instance_fingerprint",
                        "host_fingerprint",
                        "configuration_fingerprint",
                    )
                },
            }
            current_binding = {
                "release_commit": plan.target_commit,
                **{
                    key: snapshot[key]
                    for key in (
                        "instance_fingerprint",
                        "host_fingerprint",
                        "configuration_fingerprint",
                    )
                },
            }
            if (
                deployment.get("initial_pointer_sha256")
                != plan.deployment_snapshot["pointer_sha256"]
                or (deployment.get("initial_readiness") or {}).get("deployment_binding")
                != prior_binding
                or (deployment.get("active_readiness") or {}).get("deployment_binding")
                != current_binding
            ):
                raise CoSActivationError(
                    "maintenance configuration changed outside the completed updater transaction"
                )
            if any(
                snapshot[key] != plan.deployment_snapshot[key]
                for key in (
                    "instance_fingerprint",
                    "host_fingerprint",
                    "service_account_fingerprint",
                )
            ):
                raise CoSActivationError(
                    "maintenance restoration crossed an instance or host boundary"
                )
            expected_guard = copy.deepcopy(plan.guard_config)
            if expected_guard is not None:
                wrapper = release / "openclaw/administrateme/bin/cos-imsg-guard.mjs"
                expected_guard.update(
                    wrapper_path=str(wrapper), wrapper_sha256=_sha(wrapper.read_bytes())
                )
            if (
                controls["guard"] != expected_guard
                or (
                    _maintenance_gateway_fingerprint(gateway, release)
                    if gateway is not None
                    else None
                )
                != plan.gateway_compatibility_sha256
            ):
                raise CoSActivationError(
                    "maintenance gateway routing or configuration drifted"
                )
            if gateway is not None:
                entry = (
                    gateway.get("plugins", {})
                    .get("entries", {})
                    .get("administrateme", {})
                )
                hooks = entry.get("hooks", {})
                if (
                    plan.self_reply_bindings
                    and "allowConversationAccess" in hooks
                    and hooks["allowConversationAccess"] is not True
                ):
                    raise CoSActivationError(
                        "maintenance cannot override an explicit native hook denial"
                    )
                config = entry.get("config", {})
                for key, previous in (
                    ("standingSelfReplyBindings", plan.self_reply_bindings),
                    ("scheduledSelfReminderBindings", plan.self_reminder_bindings),
                ):
                    if config.get(key, []) not in ([], previous):
                        raise CoSActivationError("maintenance host grants changed")
        return controls

    def pause_maintenance(self, plan: MaintenanceRestorationPlan) -> dict[str, Any]:
        return self.enter_observation(plan.observation_request(), maintenance_plan=plan)

    def _restoration_request(
        self, plan: MaintenanceRestorationPlan, *, commit: str, release: Path
    ) -> ActivationRequest:
        controls = self._validate_maintenance_plan(
            plan, commit=commit, release=release, restoring=True
        )
        assert plan.sender_proofs is not None
        return ActivationRequest(
            adapter_config=controls["adapters"],
            sender_proofs=plan.sender_proofs,
            owner_authorization_sha256=plan.owner_authorization_sha256,
            operator_principal_id=plan.operator_principal_id,
            owner_member_id=plan.owner_member_id,
            prepared_openclaw_config=controls["gateway"],
            cos_imsg_guard_config=controls["guard"],
        )

    def enter_observation(
        self,
        request: EnterObservationRequest,
        *,
        submission_repair_manifest: Path | None = None,
        maintenance_plan: MaintenanceRestorationPlan | None = None,
    ) -> dict[str, Any]:
        updater = UpgradeManager(
            self.paths, root=self.repository, test_mode=self.test_mode
        )
        with updater._operation_lock():
            if self.journal_path.exists():
                raise CoSActivationError("recover the interrupted CoS operation first")
            if (self.paths.app_dir / "activation/active.json").exists():
                raise CoSActivationError("release activation must complete first")
            commit, release, controller_commit = self._observation_selection()
            snapshot = self._bound_snapshot(commit)
            if maintenance_plan is not None:
                if (
                    controller_commit != maintenance_plan.target_commit
                    or request != maintenance_plan.observation_request()
                ):
                    raise CoSActivationError(
                        "maintenance request does not match the prepared candidate"
                    )
                self._validate_maintenance_plan(
                    maintenance_plan, commit=commit, release=release, restoring=False
                )
                if (
                    self.directory / "history" / f"{maintenance_plan.plan_id}.json"
                ).exists():
                    raise CoSActivationError("maintenance plan was already paused")
            before = self.services.runtime_diagnostics() or {}
            repair = (
                load_submission_repair_manifest(
                    submission_repair_manifest,
                    candidate=self.repository,
                    active_commit=commit,
                    test_mode=self.test_mode,
                )
                if submission_repair_manifest is not None
                else None
            )
            if repair is not None:
                if (
                    repair.operator_principal_id,
                    repair.owner_member_id,
                    repair.owner_authorization_sha256,
                ) != (
                    request.operator_principal_id,
                    request.owner_member_id,
                    request.owner_authorization_sha256,
                ):
                    raise CoSActivationError(
                        "containment request and repair authority differ"
                    )
                validate_submission_repair_diagnostics(
                    repair, before, observation_active=False
                )
                offline_submission_repair(
                    self.paths,
                    repair,
                    repository_root=self.repository,
                    candidate_commit=controller_commit,
                    test_mode=self.test_mode,
                )
            elif (
                maintenance_plan is not None
                and maintenance_plan.effect_context_proof is not None
            ):
                verify_proof(
                    self.paths,
                    self.repository,
                    maintenance_plan.effect_context_proof,
                    diagnostics=before,
                    observation=False,
                    test_mode=self.test_mode,
                )
            elif (
                before.get("ready") is not True
                or before.get("observation_active") is not False
            ):
                raise CoSActivationError(
                    "enter-observation requires a healthy live predecessor"
                )
            if not self.test_mode:
                probe_release_tooling(self.paths, release, self.repository)
            live = asyncio.run(self._live_predecessor(release))
            members = {
                row["member_id"]: row["role"]
                for row in _json(self.config_dir / "member-mapping.json")["members"]
            }
            if members.get(request.owner_member_id) != "principal":
                raise CoSActivationError(
                    "operation requires its exact authorizing principal"
                )
            if (
                request.expected_release_commit != commit
                or request.expected_activation_id != live["activation_id"]
                or request.expected_configuration_fingerprint
                != snapshot["configuration_fingerprint"]
            ):
                raise CoSActivationError(
                    "enter-observation request does not match live predecessor"
                )
            readiness = _json(self.config_dir / "deployment-readiness.json")
            prior_window = readiness.get("provider_effect_window") or {}
            receipt = parse_activation(
                _json(self.config_dir / RECEIPT_FILENAME)["activation"]
            )
            if (
                prior_window.get("ended_at") != live["enabled_at"]
                or prior_window.get("started_at")
                != receipt.observation_window_started_at
            ):
                raise CoSActivationError(
                    "live predecessor observation interval is not exact"
                )
            allowed = asyncio.run(self._policy())
            journal = {
                "contract": "cos-channel-operation-journal-v1",
                "operation": "enter_observation",
                "activation_id": maintenance_plan.plan_id
                if maintenance_plan is not None
                else uuid.uuid4().hex,
                "prior_activation_id": live["activation_id"],
                "release_commit": commit,
                "controller_commit": controller_commit,
                "request": request.model_dump(mode="json"),
                "operator_principal_id": request.operator_principal_id,
                "owner_member_id": request.owner_member_id,
                "owner_authorization_sha256": request.owner_authorization_sha256,
                "initial_snapshot": snapshot,
                "initial_policy": {
                    "event_id": allowed["event_id"],
                    "revision": allowed["revision"],
                },
                "initial_files": {
                    name: base64.b64encode(
                        _observation_control_bytes(self.config_dir, name)
                    ).decode()
                    for name in (
                        "adapters.json",
                        "observation.json",
                        "runtime.yaml",
                        "deployment-readiness.json",
                        RECEIPT_FILENAME,
                    )
                },
                "diagnostic_provider_calls_before": self._provider_calls(before),
            }
            if maintenance_plan is not None:
                journal["maintenance_plan_sha256"] = maintenance_plan.digest()
                if maintenance_plan.effect_context_proof is not None:
                    journal["effect_context_proof"] = (
                        maintenance_plan.effect_context_proof.model_dump(mode="json")
                    )
            if repair is not None:
                journal["submission_repair_manifest"] = repair.model_dump(mode="json")
                journal["submission_repair"] = repair_binding(repair)
            try:
                require_deployment_headroom(
                    self.paths,
                    backup_copies=3 if maintenance_plan is not None else 1,
                    release_sources=(release, self.repository)
                    if maintenance_plan is not None
                    else (),
                )
            except StorageHeadroomError as exc:
                raise CoSActivationError(str(exc)) from exc
            self._write_journal(journal, "prepared")
            return self._complete_enter_observation(journal, release=release)

    def _complete_enter_observation(
        self,
        journal: dict[str, Any],
        *,
        release: Path,
        recovery_controller: str | None = None,
    ) -> dict[str, Any]:
        """Complete containment after interruption; never restore live writes."""
        try:
            self.services.stop()
            selected = self._observation_selection()
            if selected != (
                journal["release_commit"],
                release,
                recovery_controller or journal["controller_commit"],
            ):
                raise CoSActivationError(
                    "observation operation source or selection changed"
                )
            # Provider/account/routing/receipt coordinates are immutable here.
            if (
                capture_deployment_snapshot(self.paths).public()
                != journal["initial_snapshot"]
            ):
                raise CoSActivationError(
                    "configuration drifted during observation transition"
                )
            if (
                asyncio.run(self._policy()).get("event_id")
                != journal["initial_policy"]["event_id"]
            ):
                raise CoSActivationError(
                    "recipient policy changed during observation transition"
                )
            repair = None
            if "submission_repair_manifest" in journal:
                repair = SubmissionRepairManifest.model_validate(
                    journal["submission_repair_manifest"]
                )
                if (
                    repair_binding(repair) != journal.get("submission_repair")
                    or repair.candidate_commit != journal["controller_commit"]
                    or repair.predecessor_commit != journal["release_commit"]
                ):
                    raise CoSActivationError("contained repair journal binding differs")
                offline_submission_repair(
                    self.paths,
                    repair,
                    repository_root=self.repository,
                    candidate_commit=journal["controller_commit"],
                    test_mode=self.test_mode,
                )
            effect_proof = (
                EffectContextProof.model_validate(journal["effect_context_proof"])
                if "effect_context_proof" in journal
                else None
            )
            if effect_proof is not None:
                if (
                    effect_proof.predecessor_commit != journal["release_commit"]
                    or effect_proof.candidate_commit != journal["controller_commit"]
                ):
                    raise CoSActivationError(
                        "effect-context repair journal binding differs"
                    )
                journal["effect_context_quiesced"] = verify_proof(
                    self.paths, self.repository, effect_proof, test_mode=self.test_mode
                )
            effects = asyncio.run(self._effect_count())
            if "prior_provider_calls" not in journal:
                if effects < journal["diagnostic_provider_calls_before"]:
                    raise CoSActivationError(
                        "provider-effect history regressed during quiescence"
                    )
                # A governed live reply may have finished between the cached
                # diagnostic and the journal hold. The stopped, audited log is
                # authoritative for the new observation boundary.
                journal["prior_provider_calls"] = effects
                self._write_journal(journal, "quiesced")
            if effects != journal["prior_provider_calls"]:
                raise CoSActivationError(
                    "observation transition found an unsettled provider effect"
                )
            if "backup" not in journal:
                # An interrupted rehearsal may leave its immutable encrypted
                # artifact behind. A new attempt never overwrites that evidence.
                backup = self.backups.create(
                    self.paths.backup_dir
                    / f"cos-operation-{journal['activation_id']}-{uuid.uuid4().hex}.ambackup"
                )
                rehearsal = self.backups.rehearse(
                    Path(backup["path"]), candidate=self.repository
                )
                if (
                    backup.get("encrypted") is not True
                    or rehearsal.get("status") != "healthy"
                    or (rehearsal.get("artifact") or {}).get("authenticated")
                    is not True
                ):
                    raise CoSActivationError("encrypted backup restore proof failed")
                journal.update(backup=backup, rehearsal=rehearsal)
                self._write_journal(journal, "backup_verified")
            if "observation_started_at" not in journal:
                if (
                    capture_deployment_snapshot(self.paths).public()
                    != journal["initial_snapshot"]
                ):
                    raise CoSActivationError(
                        "configuration changed during backup verification"
                    )
                for name, encoded in journal["initial_files"].items():
                    if _observation_control_bytes(
                        self.config_dir, name
                    ) != base64.b64decode(encoded, validate=True):
                        raise CoSActivationError(
                            "operation control changed during backup verification"
                        )
                journal["observation_started_at"] = datetime.now(
                    timezone.utc
                ).isoformat()
                self._write_journal(journal, "observation_enabling")
            asyncio.run(
                self._audit_and_toggle(
                    None, journal=journal, deactivate_reason="deployment_observation"
                )
            )
            observation = json.loads(
                base64.b64decode(journal["initial_files"]["observation.json"])
            )
            observation.update(active=True, trusted=False)
            observation.pop("cos_activation_id", None)
            atomic_write(self.config_dir / "observation.json", _payload(observation))
            readiness = json.loads(
                base64.b64decode(journal["initial_files"]["deployment-readiness.json"])
            )
            readiness.setdefault("prior_provider_effect_windows", []).append(
                copy.deepcopy(readiness["provider_effect_window"])
            )
            readiness["provider_effect_window"] = {
                "started_at": journal["observation_started_at"],
                "reason": "cos_enter_observation",
            }
            self._refresh_binding(readiness, journal["release_commit"])
            self._write_journal(journal, "verifying")
            self.services.start()
            journal["doctor"] = (
                wait_for_submission_repair(
                    repair,
                    diagnostics=self.services.runtime_diagnostics,
                    doctor=lambda: run_release_doctor(
                        self.paths,
                        release,
                        test_mode=self.test_mode,
                        include_synthetic_proof=True,
                    ),
                    deadline=deployment_clock() + 600,
                )
                if repair is not None
                else wait_contained(
                    self.paths,
                    self.repository,
                    effect_proof,
                    diagnostics=self.services.runtime_diagnostics,
                    doctor=lambda: run_release_doctor(
                        self.paths,
                        release,
                        test_mode=self.test_mode,
                        include_synthetic_proof=True,
                    ),
                    deadline=deployment_clock() + 600,
                    test_mode=self.test_mode,
                )
                if effect_proof is not None
                else self._settle(release)
            )
            after = self.services.runtime_diagnostics() or {}
            if repair is not None:
                validate_submission_repair_diagnostics(
                    repair, after, observation_active=True, doctor=journal["doctor"]
                )
                offline_submission_repair(
                    self.paths,
                    repair,
                    repository_root=self.repository,
                    candidate_commit=journal["controller_commit"],
                    test_mode=self.test_mode,
                )
            elif effect_proof is not None:
                verify_proof(
                    self.paths,
                    self.repository,
                    effect_proof,
                    diagnostics=after,
                    doctor=journal["doctor"],
                    test_mode=self.test_mode,
                )
            elif (
                after.get("ready") is not True
                or after.get("observation_active") is not True
            ):
                raise CoSActivationError("observation transition did not settle")
            if (
                self._provider_calls(after) != journal["prior_provider_calls"]
                or asyncio.run(self._effect_count()) != journal["prior_provider_calls"]
            ):
                raise CoSActivationError(
                    "observation transition caused an unintended provider effect"
                )
            if (
                capture_deployment_snapshot(self.paths).public()
                != journal["initial_snapshot"]
                or asyncio.run(self._policy()).get("event_id")
                != journal["initial_policy"]["event_id"]
            ):
                raise CoSActivationError(
                    "observation transition configuration or policy changed"
                )
            if self._observation_selection() != selected:
                raise CoSActivationError(
                    "observation operation source or selection changed"
                )
            history = self.directory / "history" / f"{journal['activation_id']}.json"
            if history.exists():
                prior_commit = _json(history)
                if prior_commit.get("phase") != "committed" or any(
                    prior_commit.get(key) != journal.get(key)
                    for key in (
                        "operation",
                        "activation_id",
                        "prior_activation_id",
                        "release_commit",
                        "controller_commit",
                        "initial_snapshot",
                        "initial_policy",
                        "initial_files",
                        "observation_started_at",
                        "prior_provider_calls",
                    )
                ):
                    raise CoSActivationError(
                        "observation operation history does not match"
                    )
                # A crash between durable history publication and journal
                # removal rechecks containment, then keeps the original proof.
                self._finish(prior_commit, "committed")
            else:
                self._finish(journal, "committed")
            return {
                "status": "contained_degraded"
                if repair is not None or effect_proof is not None
                else "observation_entered",
                "observation_active": True,
                **(
                    {"submission_repair": repair_binding(repair)}
                    if repair is not None
                    else {}
                ),
                "release_commit": journal["release_commit"],
                "prior_activation_id": journal["prior_activation_id"],
                "provider_test_messages_sent": 0,
                "allowlist_unchanged": True,
                "enrollment_unchanged": True,
            }
        except BaseException:
            # The active journal holds replies even if a process cannot stop.
            # Explicit recovery completes this same observation boundary.
            self.services.stop()
            raise

    def _event_log(self, *, read_only: bool) -> EventLog:
        references = _json(self.config_dir / "secret-references.json")["references"]
        key = secret_store(self.paths, test_mode=self.test_mode).get(
            references["event_log"]
        )
        return EventLog(
            load_instance_config(self.paths.instance_dir), key, read_only=read_only
        )

    async def _policy(self) -> dict[str, Any]:
        log = self._event_log(read_only=True)
        try:
            instance = load_instance_config(self.paths.instance_dir)
            return await policy_from_event_log(log, instance.tenant_id)
        finally:
            await log.close()

    async def _write_policy(
        self,
        journal: dict[str, Any],
        entries: list[dict[str, str]] | None,
        *,
        restore: bool,
    ) -> dict[str, Any]:
        log = self._event_log(read_only=False)
        instance = load_instance_config(self.paths.instance_dir)
        bus = EventBus(log, instance.bus_checkpoint_path)
        writer = EventWriter(log, bus)
        try:
            policy = await policy_from_event_log(log, instance.tenant_id)
            key = f"cos-operation:{journal['activation_id']}:allowlist"
            seeded = await log.get_by_idempotency(instance.tenant_id, key)
            if restore and (
                seeded is None or seeded["event_id"] != policy.get("event_id")
            ):
                # A later human edit is never overwritten by compensation.
                return policy
            if not restore and seeded is not None:
                return policy
            if not restore and policy.get("event_id") != journal["initial_policy"].get(
                "event_id"
            ):
                raise CoSActivationError(
                    "recipient policy changed before stopped-service seed"
                )
            if restore:
                initial = journal["initial_policy"]
                if initial["revision"] == 0 and initial["event_id"] is None:
                    entries = []
                else:
                    previous = await log.get(initial["event_id"])
                    if (
                        previous is None
                        or previous["tenant_id"] != instance.tenant_id
                        or previous["type"] != POLICY_EVENT_TYPE
                        or previous["payload"]["revision"] != initial["revision"]
                    ):
                        raise CoSActivationError(
                            "original recipient policy evidence is unavailable"
                        )
                    entries = validate_recipient_entries(
                        previous["payload"]["entries"], None
                    )
            if entries is None:
                raise CoSActivationError("recipient policy entries are unavailable")
            now = datetime.now(timezone.utc).isoformat()
            event = EventEnvelope(
                event_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                tenant_id=instance.tenant_id,
                type=POLICY_EVENT_TYPE,
                schema_version=1,
                occurred_at=now,
                source_adapter="cos-channel-operation",
                source_account_id="system",
                owner_scope="shared:household",
                visibility_scope="shared:household",
                sensitivity="privileged",
                actor_identity=journal["operator_principal_id"],
                idempotency_key=key + (":restore" if restore else ""),
                payload={
                    "contract": policy["contract"],
                    "service_principal_id": policy["service_principal_id"],
                    "revision": policy["revision"] + 1,
                    "entries": entries,
                    "changed_by_member_id": journal["owner_member_id"],
                    "changed_at": now,
                    "grants_provider_authority": False,
                    "provider_effects": False,
                },
            )
            await writer.write(event)
            return await policy_from_event_log(log, instance.tenant_id)
        finally:
            await bus.stop()
            await log.close()

    def _write_journal(self, journal: dict[str, Any], phase: str) -> None:
        journal["phase"] = phase
        atomic_write(self.journal_path, _payload(journal))

    def _finish(self, journal: dict[str, Any], phase: str) -> None:
        self._write_journal(journal, phase)
        history = self.directory / "history" / f"{journal['activation_id']}.json"
        if history.exists():
            if _private_bytes(history) != _private_bytes(self.journal_path):
                raise CoSActivationError("operation history already exists")
        else:
            atomic_write(history, _private_bytes(self.journal_path))
        self.journal_path.unlink()

    def _refresh_binding(self, readiness: dict[str, Any], commit: str) -> None:
        snapshot = capture_deployment_snapshot(self.paths)
        binding = readiness.get("deployment_binding") or {}
        if (
            snapshot.predecessor_commit != commit
            or binding.get("release_commit") != commit
            or binding.get("instance_fingerprint") != snapshot.instance_fingerprint
            or binding.get("host_fingerprint") != snapshot.host_fingerprint
        ):
            raise CoSActivationError(
                "operational transition crossed a deployment boundary"
            )
        binding["configuration_fingerprint"] = snapshot.configuration_fingerprint
        atomic_write(self.config_dir / "deployment-readiness.json", _payload(readiness))

    def _settle(self, release: Path) -> dict[str, Any]:
        result = wait_for_settled_doctor(
            run_doctor=lambda **kwargs: run_release_doctor(
                self.paths,
                release,
                test_mode=self.test_mode,
                controller_root=self.repository,
                **kwargs,
            ),
            services=self.services,
            test_mode=self.test_mode,
            timeout_seconds=600,
        )
        if result.get("status") != "healthy":
            raise CoSActivationError(
                "CoS channel activation failed bounded Doctor settlement"
            )
        return result

    async def _audit_and_toggle(
        self,
        activation: CosChannelActivation | None,
        *,
        journal: dict[str, Any],
        deactivate_reason: str | None = None,
    ) -> str:
        log = self._event_log(read_only=False)
        instance = load_instance_config(self.paths.instance_dir)
        bus = EventBus(log, instance.bus_checkpoint_path)
        writer = EventWriter(log, bus)
        manager = ObservationManager(writer, self.config_dir / "runtime.yaml")
        now = (
            journal.get("observation_started_at")
            or journal.get("recovery_window_plan", {}).get("started_at")
            or datetime.now(timezone.utc).isoformat()
        )
        try:
            payload = (
                activation.model_dump(mode="json")
                if activation is not None
                else {
                    "activation_id": journal.get(
                        "prior_activation_id", journal["activation_id"]
                    ),
                    "operator_principal_id": journal["operator_principal_id"],
                    "reason": journal.get("recovery_window_plan", {}).get(
                        "reason", deactivate_reason
                    ),
                    "observation_active": True,
                }
            )
            envelope = EventEnvelope(
                event_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                tenant_id=instance.tenant_id,
                type=ACTIVATED_EVENT if activation is not None else DEACTIVATED_EVENT,
                schema_version=(
                    2
                    if isinstance(activation, CosChannelActivationV2)
                    or (
                        activation is None
                        and deactivate_reason == "deployment_observation"
                    )
                    else 1
                ),
                occurred_at=now,
                source_adapter="cos-channel-operation",
                source_account_id="system",
                owner_scope="shared:household",
                visibility_scope="shared:household",
                sensitivity="normal",
                actor_identity=journal["operator_principal_id"],
                idempotency_key=(
                    f"cos-operation:{journal['activation_id']}:observation"
                    if journal.get("operation") == "enter_observation"
                    else (
                        f"cos-operation:{journal['activation_id']}:recovery"
                        if activation is None and "recovery_window_plan" in journal
                        else None
                    )
                ),
                payload=payload,
            )
            event_id = await writer.write(envelope)
            if activation is not None:
                await manager.disable(
                    journal["operator_principal_id"],
                    "owner-authorized CoS channel replies",
                )
            else:
                reason = (
                    f"CoS deployment observation:{journal['activation_id']}"
                    if journal.get("operation") == "enter_observation"
                    else (
                        f"CoS operation recovery:{journal['activation_id']}"
                        if "recovery_window_plan" in journal
                        else "CoS operation recovery"
                    )
                )
                already_enabled = False
                if (
                    journal.get("operation") == "enter_observation"
                    or "recovery_window_plan" in journal
                ):
                    async for previous in log.read_since(
                        None, types=["observation.enabled"]
                    ):
                        if (
                            previous.get("tenant_id") == instance.tenant_id
                            and previous.get("actor_identity")
                            == journal["operator_principal_id"]
                            and previous.get("payload", {}).get("reason") == reason
                        ):
                            already_enabled = True
                if not already_enabled or not await manager.is_active():
                    await manager.enable(journal["operator_principal_id"], reason)
            return event_id
        finally:
            await bus.stop()
            await log.close()

    @staticmethod
    def _provider_calls(diagnostics: dict[str, Any]) -> int:
        evidence = (
            ((diagnostics.get("deployment_readiness") or {}).get("dimensions") or {})
            .get("observation_effects", {})
            .get("evidence", {})
        )
        value = evidence.get("provider_called_true")
        if type(value) is not int or value < 0:
            raise CoSActivationError(
                "complete provider-effect accounting is unavailable"
            )
        return value

    def activate(
        self,
        request: ActivationRequest | MaintenanceRestorationPlan,
        *,
        enable_live: bool,
        _tooling_restore: bool = False,
    ) -> dict[str, Any]:
        updater = UpgradeManager(
            self.paths, root=self.repository, test_mode=self.test_mode
        )
        with updater._operation_lock():
            if self.journal_path.exists():
                raise CoSActivationError("recover the interrupted CoS operation first")
            if (self.paths.app_dir / "activation" / "active.json").exists():
                raise CoSActivationError("release activation must complete first")
            maintenance_plan = (
                request if isinstance(request, MaintenanceRestorationPlan) else None
            )
            tooling_selection = None
            if _tooling_restore:
                if maintenance_plan is None or not enable_live:
                    raise CoSActivationError(
                        "tooling restoration requires its original maintenance plan"
                    )
                history = _json(
                    self.directory / "history" / f"{maintenance_plan.plan_id}.json"
                )
                commit, release, controller, controls = self._tooling_maintenance_state(
                    maintenance_plan, history, committed=True
                )
                tooling_selection = (commit, release, controller)
                self._require_tooling_doctor(release)
                assert maintenance_plan.sender_proofs is not None
                request = ActivationRequest(
                    adapter_config=controls["adapters"],
                    sender_proofs=maintenance_plan.sender_proofs,
                    owner_authorization_sha256=maintenance_plan.owner_authorization_sha256,
                    operator_principal_id=maintenance_plan.operator_principal_id,
                    owner_member_id=maintenance_plan.owner_member_id,
                    prepared_openclaw_config=controls["gateway"],
                    cos_imsg_guard_config=controls["guard"],
                )
            else:
                commit, release = self._selection()
            if maintenance_plan is not None and not _tooling_restore:
                if not enable_live:
                    raise CoSActivationError(
                        "maintenance restoration requires the live activation path"
                    )
                request = self._restoration_request(
                    maintenance_plan, commit=commit, release=release
                )
            assert isinstance(request, ActivationRequest)
            before = self.services.runtime_diagnostics() or {}
            if (
                before.get("ready") is not True
                or before.get("observation_active") is not True
            ):
                raise CoSActivationError(
                    "activation requires a ready runtime in observation"
                )
            prior_provider_calls = self._provider_calls(before)
            original = _json(self.config_dir / "adapters.json")
            try:
                validate_authorized_change(
                    original,
                    request.adapter_config,
                    owner_member_id=request.owner_member_id,
                    owner_authorization_sha256=request.owner_authorization_sha256,
                )
                validate_workspace_change(
                    original,
                    request.adapter_config,
                    owner_authorization_sha256=request.owner_authorization_sha256,
                )
                validate_reminders_change(
                    original,
                    request.adapter_config,
                    owner_authorization_sha256=request.owner_authorization_sha256,
                )
                validate_reminders_write_change(
                    original,
                    request.adapter_config,
                    owner_authorization_sha256=request.owner_authorization_sha256,
                )
            except ValueError as exc:
                raise CoSActivationError(str(exc)) from exc
            prepared = prepare_configuration(
                original, request.adapter_config, enable_live=enable_live
            )
            policy = policy_from_config(prepared)
            assert policy is not None
            allowed = asyncio.run(self._policy())
            members = {
                member["member_id"]: member["role"]
                for member in _json(self.config_dir / "member-mapping.json")["members"]
            }
            if members.get(request.owner_member_id) != "principal":
                raise CoSActivationError(
                    "operation requires its exact authorizing principal"
                )
            recipients = (
                validate_recipient_entries(request.recipient_entries, members)
                if request.recipient_entries is not None
                else allowed["entries"]
            )
            if enable_live and not recipients:
                raise CoSActivationError(
                    "configure the nonempty recipient allowlist first"
                )
            prepared_openclaw = request.prepared_openclaw_config
            if policy.imessage_channel_id is not None:
                if (
                    request.prepared_openclaw_config is None
                    or request.cos_imsg_guard_config is None
                ):
                    raise CoSActivationError(
                        "Messages activation requires the guarded transport"
                    )
                from bootstrap.cos_imessage import (
                    prepare_channel,
                    validate_prepared_channel,
                    verify_guard_files,
                )
                from bootstrap.openclaw_activation import (
                    prepare_existing_member_topology,
                )

                prepared_openclaw = prepare_existing_member_topology(
                    request.prepared_openclaw_config,
                    adapter_config=prepared,
                    openclaw_root=self.paths.instance_dir / "openclaw",
                    known_members=set(members),
                )
                account_prefix = (
                    f"imessage|{request.cos_imsg_guard_config['account_id']}|"
                )
                exact_senders = [
                    route.removeprefix(account_prefix)
                    for route in prepared.get("openclaw_actor_identities", {})
                    if route.startswith(account_prefix)
                ]
                expected_openclaw = prepare_existing_member_topology(
                    prepare_channel(
                        _json(
                            self.paths.instance_dir / "openclaw/runtime/openclaw.json"
                        ),
                        request.cos_imsg_guard_config,
                        allowed_senders=exact_senders,
                    ),
                    adapter_config=prepared,
                    openclaw_root=self.paths.instance_dir / "openclaw",
                    known_members=set(members),
                )
                if prepared_openclaw != expected_openclaw:
                    raise CoSActivationError(
                        "Messages enrollment changes unrelated gateway configuration"
                    )
                validate_prepared_channel(
                    prepared_openclaw,
                    request.cos_imsg_guard_config,
                    adapter_config=prepared,
                )
                verify_guard_files(
                    request.cos_imsg_guard_config, reviewed_release=release
                )
            elif (
                request.prepared_openclaw_config is not None
                or request.cos_imsg_guard_config is not None
            ):
                raise CoSActivationError(
                    "unselected Messages configuration cannot change"
                )
            # The owner separately authorizes their own exact private endpoints.
            # Host approval and domain activation retain independent copies.
            reply_bindings: list[str] = []
            if prepared_openclaw is not None:
                from bootstrap.cos_reply_hooks import configure_reply_hook

                plugin_config = prepared_openclaw["plugins"]["entries"][
                    "administrateme"
                ].setdefault("config", {})
                previous_bindings: list[str] = []
                try:
                    prior = parse_activation(
                        _json(self.config_dir / RECEIPT_FILENAME)["activation"]
                    )
                    host_bindings = plugin_config.get("standingSelfReplyBindings", [])
                    previous_bindings = [
                        value
                        for value in getattr(prior, "self_reply_bindings", [])
                        if value in host_bindings
                    ]
                except (OSError, KeyError, TypeError, ValueError):
                    pass
                if maintenance_plan is not None:
                    previous_bindings = maintenance_plan.self_reply_bindings
                reply_bindings = owner_self_reply_bindings(
                    request,
                    prepared,
                    recipients,
                    previous_bindings,
                    enable_live=enable_live,
                )
                plugin_config.pop("standingSelfReplyBindings", None)
                if reply_bindings:
                    plugin_config["standingSelfReplyBindings"] = reply_bindings
                previous_reminders = set(
                    plugin_config.get("scheduledSelfReminderBindings", [])
                )
                if maintenance_plan is not None:
                    if reply_bindings != maintenance_plan.self_reply_bindings:
                        raise CoSActivationError(
                            "maintenance cannot restore the exact private reply grants"
                        )
                    previous_reminders = set(maintenance_plan.self_reminder_bindings)
                from adminme.cos_self_replies import self_reply_binding

                owner_bindings = set()
                for actor, member in prepared.get(
                    "openclaw_actor_identities", {}
                ).items():
                    parts = actor.split("|")
                    if (
                        member == request.owner_member_id
                        and len(parts) == 3
                        and parts[0] == "imessage"
                    ):
                        owner_bindings.add(
                            self_reply_binding(member, parts[1], parts[2])
                        )
                reminder_bindings = previous_reminders.intersection(reply_bindings)
                if request.allow_owner_self_reminders is True:
                    if not owner_bindings.intersection(reply_bindings):
                        raise CoSActivationError(
                            "requested reminders require the owner's enabled private reply permission"
                        )
                    reminder_bindings.update(
                        owner_bindings.intersection(reply_bindings)
                    )
                elif request.allow_owner_self_reminders is False:
                    reminder_bindings.difference_update(owner_bindings)
                plugin_config.pop("scheduledSelfReminderBindings", None)
                if reminder_bindings:
                    plugin_config["scheduledSelfReminderBindings"] = sorted(
                        reminder_bindings
                    )
                    prepared_openclaw.setdefault("cron", {}).setdefault("triggers", {})[
                        "enabled"
                    ] = True
                configure_reply_hook(
                    prepared_openclaw["plugins"]["entries"]["administrateme"],
                    reply_bindings,
                )
            elif request.allow_owner_self_replies or request.allow_owner_self_reminders:
                raise CoSActivationError(
                    "standing replies require live guarded iMessage"
                )
            proof_channels = {proof.channel for proof in request.sender_proofs}
            expected = {"email"} | (
                {"imessage"} if policy.imessage_channel_id else set()
            )
            if proof_channels != expected or len(proof_channels) != len(
                request.sender_proofs
            ):
                raise CoSActivationError(
                    "every selected channel requires its own proof"
                )
            gmail_proof = next(
                proof for proof in request.sender_proofs if proof.channel == "email"
            )
            if gmail_proof.provider_account_sha256 != _sha(
                policy.gmail_provider_account_id.encode()
            ):
                raise CoSActivationError(
                    "Gmail sender proof belongs to another account"
                )
            proof_check_at = datetime.now(timezone.utc)
            for proof in request.sender_proofs:
                observed = aware_time(proof.observed_at)
                if (
                    observed is None
                    or not 0 <= (proof_check_at - observed).total_seconds() <= 86400
                ):
                    raise CoSActivationError(
                        "sender or transport verification has expired"
                    )
                if proof.channel == "imessage":
                    channel = next(
                        item
                        for item in prepared["service_identities"]["channels"]
                        if item["channel_id"] == policy.imessage_channel_id
                    )
                    if channel.get("provider_account_reference") != (
                        "provider-account:" + proof.provider_account_sha256
                    ):
                        raise CoSActivationError(
                            "Messages proof belongs to another account"
                        )
            if _tooling_restore and (
                prepared != original
                or prepared_openclaw != controls["gateway"]
                or request.cos_imsg_guard_config != controls["guard"]
            ):
                raise CoSActivationError(
                    "tooling restoration cannot change native preferences, routes or grants"
                )
            readiness = _json(self.config_dir / "deployment-readiness.json")
            activation_id = uuid.uuid4().hex
            initial_files = {}
            for name in (
                "adapters.json",
                "observation.json",
                "runtime.yaml",
                "deployment-readiness.json",
                RECEIPT_FILENAME,
            ):
                path = self.config_dir / name
                initial_files[name] = (
                    base64.b64encode(_private_bytes(path)).decode()
                    if path.exists()
                    else None
                )
            journal: dict[str, Any] = {
                "contract": "cos-channel-operation-journal-v1",
                "activation_id": activation_id,
                "release_commit": commit,
                "operator_principal_id": request.operator_principal_id,
                "owner_member_id": request.owner_member_id,
                "initial_files": initial_files,
                "initial_external_files": {},
                "prepared_adapter_configuration_sha256": _sha(_payload(prepared)),
                # Contacts remain only in the encrypted event log. Recovery
                # resolves this immutable reference instead of caching them.
                "initial_policy": {
                    "event_id": allowed["event_id"],
                    "revision": allowed["revision"],
                },
                "seed_policy": request.recipient_entries is not None,
                "enabled_live": enable_live,
                "owner_authorization_sha256": request.owner_authorization_sha256,
                "sender_proofs": [
                    proof.model_dump(mode="json") for proof in request.sender_proofs
                ],
            }
            if maintenance_plan is not None:
                journal["maintenance_plan_sha256"] = maintenance_plan.digest()
            if _tooling_restore:
                journal["tooling_restoration"] = copy.deepcopy(
                    history["tooling_recovery_controller"]
                )
            if policy.imessage_channel_id is not None:
                for relative in (
                    "openclaw/runtime/openclaw.json",
                    "config/cos-imsg-guard.json",
                ):
                    path = self.paths.instance_dir / relative
                    journal["initial_external_files"][relative] = (
                        base64.b64encode(_private_bytes(path)).decode()
                        if path.exists()
                        else None
                    )
            try:
                require_deployment_headroom(self.paths, backup_copies=1)
            except StorageHeadroomError as exc:
                raise CoSActivationError(str(exc)) from exc
            self._write_journal(journal, "prepared")
            try:
                self.services.stop()
                backup = self.backups.create(
                    self.paths.backup_dir / f"cos-operation-{activation_id}.ambackup"
                )
                rehearsal = self.backups.rehearse(
                    Path(backup["path"]), candidate=self.repository
                )
                if (
                    rehearsal.get("status") != "healthy"
                    or (rehearsal.get("artifact") or {}).get("authenticated")
                    is not True
                ):
                    raise CoSActivationError("encrypted backup restore proof failed")
                journal["backup"] = backup
                journal["rehearsal"] = rehearsal
                self._write_journal(journal, "backup_verified")
                if request.recipient_entries is not None:
                    allowed = asyncio.run(
                        self._write_policy(journal, recipients, restore=False)
                    )
                    journal["seed_event_id"] = allowed["event_id"]
                    self._write_journal(journal, "allowlist_seeded")
                if _tooling_restore:
                    assert maintenance_plan is not None
                    if (
                        capture_deployment_snapshot(self.paths).public()
                        != maintenance_plan.deployment_snapshot
                    ):
                        raise CoSActivationError(
                            "tooling restoration configuration drifted during backup"
                        )
                else:
                    atomic_write(self.config_dir / "adapters.json", _payload(prepared))
                if policy.imessage_channel_id is not None and not _tooling_restore:
                    atomic_write(
                        self.paths.instance_dir / "openclaw/runtime/openclaw.json",
                        _payload(prepared_openclaw),
                    )
                    atomic_write(
                        self.config_dir / "cos-imsg-guard.json",
                        _payload(request.cos_imsg_guard_config),
                    )
                if enable_live:
                    now = datetime.now(timezone.utc).isoformat()
                    activation = CosChannelActivationV2(
                        self_reply_bindings=sorted(reply_bindings),
                        activation_id=activation_id,
                        release_commit=commit,
                        enabled_at=now,
                        acquisition_sha256=policy.digest(),
                        allowlist_event_id=allowed["event_id"],
                        allowlist_revision=allowed["revision"],
                        channels=request.sender_proofs,
                        backup_sha256=rehearsal["artifact"]["sha256"],
                        rehearsal_sha256=_sha(_payload(rehearsal)),
                        owner_authorization_sha256=request.owner_authorization_sha256,
                        operator_principal_id=request.operator_principal_id,
                        observation_window_started_at=readiness[
                            "provider_effect_window"
                        ]["started_at"],
                    )
                    event_id = asyncio.run(
                        self._audit_and_toggle(activation, journal=journal)
                    )
                    atomic_write(
                        self.config_dir / RECEIPT_FILENAME,
                        _payload(
                            {
                                "event_id": event_id,
                                "activation": activation.model_dump(mode="json"),
                            }
                        ),
                    )
                    observation = _json(self.config_dir / "observation.json")
                    observation.update(
                        active=False, trusted=False, cos_activation_id=activation_id
                    )
                    atomic_write(
                        self.config_dir / "observation.json", _payload(observation)
                    )
                    readiness["provider_effect_window"]["ended_at"] = now
                self._refresh_binding(readiness, commit)
                self._write_journal(journal, "verifying")
                self.services.start()
                journal["doctor"] = self._settle(release)
                after = self.services.runtime_diagnostics() or {}
                if after.get("observation_active") is enable_live:
                    raise CoSActivationError("runtime observation state did not settle")
                if self._provider_calls(after) != prior_provider_calls:
                    raise CoSActivationError(
                        "activation caused an unintended provider effect"
                    )
                if asyncio.run(self._policy()).get("event_id") != allowed.get(
                    "event_id"
                ):
                    raise CoSActivationError(
                        "recipient policy changed during activation"
                    )
                if _tooling_restore:
                    if (
                        _json(self.config_dir / "adapters.json") != controls["adapters"]
                        or (
                            controls["gateway"] is not None
                            and _json(
                                self.paths.instance_dir
                                / "openclaw/runtime/openclaw.json"
                            )
                            != controls["gateway"]
                        )
                        or (
                            controls["guard"] is not None
                            and _json(self.config_dir / "cos-imsg-guard.json")
                            != controls["guard"]
                        )
                    ):
                        raise CoSActivationError(
                            "tooling restoration configuration changed"
                        )
                    if self._observation_selection() != tooling_selection:
                        raise CoSActivationError(
                            "tooling restoration source selection changed"
                        )
                else:
                    self._selection()
                self._finish(journal, "committed")
                return {
                    "status": "activated",
                    "activation_id": activation_id,
                    "channels": sorted(expected),
                    "observation_active": not enable_live,
                    "personal_channel_polling": any(
                        entry.get("enabled") is not False
                        for entry in selected_entries(prepared).values()
                    ),
                    "retained_test_policy_sha256": policy.retained_test_policy_sha256,
                    "provider_test_messages_sent": 0,
                }
            except BaseException:
                self._recover_locked(
                    journal, release=release, reason="operation_failed"
                )
                raise

    def _recover_locked(
        self, journal: dict[str, Any], *, release: Path, reason: str
    ) -> dict[str, Any]:
        self.services.stop()
        current_readiness = _json(self.config_dir / "deployment-readiness.json")
        if "recovery_window_plan" not in journal:
            # Record the complete observed history BEFORE restoring controls.
            # Retry must reuse this boundary, including after failed settlement.
            before = provider_effect_window_evidence(
                current_readiness, now=datetime.now(timezone.utc)
            )
            after = copy.deepcopy(before)
            started_at = datetime.now(timezone.utc).isoformat()
            if journal.get("enabled_live"):
                prior = copy.deepcopy(before["provider_effect_window"])
                prior.setdefault("ended_at", started_at)
                after["prior_provider_effect_windows"].append(prior)
                after["provider_effect_window"] = {
                    "started_at": started_at,
                    "reason": "cos_operation_recovery",
                }
                provider_effect_window_evidence(after, now=datetime.now(timezone.utc))
            journal["recovery_window_plan"] = {
                "contract": "cos-recovery-window-plan-v1",
                "started_at": started_at,
                "reason": reason,
                "before": before,
                "after": after,
            }
        plan = journal["recovery_window_plan"]
        if (
            plan.get("contract") != "cos-recovery-window-plan-v1"
            or plan.get("reason") not in {"operation_failed", "interrupted_operation"}
            or aware_time(plan.get("started_at")) is None
            or window_vector(current_readiness)
            not in (plan.get("before"), plan.get("after"))
        ):
            raise CoSActivationError("recovery window plan or retained history changed")
        expected = provider_effect_window_evidence(
            plan["before"], now=datetime.now(timezone.utc)
        )
        if journal.get("enabled_live"):
            previous = copy.deepcopy(expected["provider_effect_window"])
            previous.setdefault("ended_at", plan["started_at"])
            expected["prior_provider_effect_windows"].append(previous)
            expected["provider_effect_window"] = {
                "started_at": plan["started_at"],
                "reason": "cos_operation_recovery",
            }
        if expected != plan["after"]:
            raise CoSActivationError(
                "recovery window plan is not the exact retained continuation"
            )
        provider_effect_window_evidence(plan["after"], now=datetime.now(timezone.utc))
        self._write_journal(journal, "recovering")
        for name, encoded in journal["initial_files"].items():
            if name not in {
                "adapters.json",
                "observation.json",
                "runtime.yaml",
                "deployment-readiness.json",
                RECEIPT_FILENAME,
            }:
                raise CoSActivationError(
                    "journal contains an unexpected control coordinate"
                )
            # Never transiently replace the complete retained window vector
            # with the initial snapshot: a crash there would lose its proof.
            if name == "deployment-readiness.json":
                continue
            path = self.config_dir / name
            if encoded is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, base64.b64decode(encoded, validate=True))
        for relative, encoded in journal.get("initial_external_files", {}).items():
            if relative not in {
                "openclaw/runtime/openclaw.json",
                "config/cos-imsg-guard.json",
            }:
                raise CoSActivationError(
                    "journal contains an unexpected transport coordinate"
                )
            path = self.paths.instance_dir / relative
            if encoded is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, base64.b64decode(encoded, validate=True))
        if journal.get("seed_policy"):
            asyncio.run(self._write_policy(journal, None, restore=True))
        # Recovery pauses all polling. It never resumes a member account or
        # deletes newly acquired facts, the old selection, or its lifetime ledger.
        adapters = _json(self.config_dir / "adapters.json")
        for entry in adapters["adapters"]:
            entry["enabled"] = False
        atomic_write(self.config_dir / "adapters.json", _payload(adapters))
        asyncio.run(
            self._audit_and_toggle(None, journal=journal, deactivate_reason=reason)
        )
        observation = _json(self.config_dir / "observation.json")
        observation.update(active=True, trusted=False)
        observation.pop("cos_activation_id", None)
        atomic_write(self.config_dir / "observation.json", _payload(observation))
        readiness = initial_readiness(journal)
        readiness.update(copy.deepcopy(plan["after"]))
        self._refresh_binding(readiness, journal["release_commit"])
        self.services.start()
        journal["recovery_doctor"] = self._settle(release)
        history = self.directory / "history" / f"{journal['activation_id']}.json"
        if history.exists():
            completed = _json(history)
            if completed.get("phase") != "recovered" or any(
                completed.get(key) != value
                for key, value in journal.items()
                if key not in {"phase", "recovery_doctor"}
            ):
                raise CoSActivationError("recovery operation history does not match")
            self._finish(completed, "recovered")
        else:
            self._finish(journal, "recovered")
        return {
            "status": "recovered",
            "observation_active": True,
            "all_polling_paused": True,
        }

    def prepare_effect_context_successor(
        self, original: MaintenanceRestorationPlan
    ) -> MaintenanceRestorationPlan:
        """Bind a protected repair successor without repeating containment or consent."""
        updater = UpgradeManager(
            self.paths, root=self.repository, test_mode=self.test_mode
        )
        with updater._operation_lock():
            if self.journal_path.exists() or updater.activation_journal.path.exists():
                raise CoSActivationError(
                    "complete the current operation before continuing maintenance"
                )
            commit, _release, target = self._observation_selection()
            proof = original.effect_context_proof
            history = contained_plan_history(
                self.paths, self.repository, original, test_mode=self.test_mode
            )
            prepared, expires = (
                aware_time(original.prepared_at),
                aware_time(original.expires_at),
            )
            if (
                proof is None
                or proof.request != original.effect_context_repair
                or proof.candidate_commit != original.target_commit
                or proof.predecessor_commit != original.predecessor_commit
                or commit != original.predecessor_commit
                or target == original.target_commit
                or history.get("maintenance_plan_sha256") != original.digest()
                or prepared is None
                or expires is None
                or not prepared <= datetime.now(timezone.utc) < expires
            ):
                raise CoSActivationError(
                    "successor requires the original unexpired effect repair plan"
                )
            load_git_tree(
                self.repository, original.target_commit, retained_by_commit=target
            )
            controls = self._maintenance_controls()
            if (
                original.sender_proofs is None
                or self._maintenance_proof_expiry(
                    original.sender_proofs, controls["activation"]
                )
                != expires
                or any(
                    expected != _sha(_payload(value))
                    for expected, value in (
                        (original.adapter_sha256, controls["adapters"]),
                        (original.receipt_sha256, controls["receipt"]),
                        (original.member_mapping_sha256, controls["members"]),
                        (original.recipient_policy_sha256, controls["policy"]),
                    )
                )
                or capture_deployment_snapshot(self.paths).public()
                != original.deployment_snapshot
            ):
                raise CoSActivationError("successor authority or configuration changed")
            observation = _json(self.config_dir / "observation.json")
            window = _json(self.config_dir / "deployment-readiness.json").get(
                "provider_effect_window"
            )
            if observation.get("active") is not True or window != {
                "started_at": history.get("observation_started_at"),
                "reason": "cos_enter_observation",
            }:
                raise CoSActivationError(
                    "successor requires the original observation boundary"
                )
            verify_proof(
                self.paths,
                self.repository,
                proof,
                diagnostics=self.services.runtime_diagnostics() or {},
                test_mode=self.test_mode,
            )
            if (
                capture_deployment_snapshot(self.paths).public()
                != original.deployment_snapshot
            ):
                raise CoSActivationError("successor configuration changed during audit")
            successor = original.model_copy(
                update={
                    "target_commit": target,
                    "effect_context_proof": proof.model_copy(
                        update={"candidate_commit": target}
                    ),
                }
            )
            retain_successor_plan(self.paths, original, successor)
            contained_plan_history(
                self.paths, self.repository, successor, test_mode=self.test_mode
            )
            return successor

    def recover_effect_context_controller(
        self, plan: MaintenanceRestorationPlan
    ) -> dict[str, Any]:
        """Continue this exact paused repair with a protected successor controller.

        The original candidate, plan and proof remain immutable. This does not
        admit a different deployment or restore write authority.
        """
        updater = UpgradeManager(
            self.paths, root=self.repository, test_mode=self.test_mode
        )
        with updater._operation_lock():
            if updater.activation_journal.path.exists():
                raise CoSActivationError("release activation must complete first")
            journal = _json(self.journal_path)
            commit, release, successor = self._observation_selection()
            proof = plan.effect_context_proof
            prepared, expires = (
                aware_time(plan.prepared_at),
                aware_time(plan.expires_at),
            )
            if (
                proof is None
                or plan.effect_context_repair != proof.request
                or proof.candidate_commit != plan.target_commit
                or proof.predecessor_commit != plan.predecessor_commit
                or commit != plan.predecessor_commit
                or successor == plan.target_commit
                or journal.get("contract") != "cos-channel-operation-journal-v1"
                or journal.get("operation") != "enter_observation"
                or journal.get("phase") != "verifying"
                or journal.get("activation_id") != plan.plan_id
                or journal.get("controller_commit") != plan.target_commit
                or journal.get("release_commit") != plan.predecessor_commit
                or journal.get("maintenance_plan_sha256") != plan.digest()
                or journal.get("effect_context_proof") != proof.model_dump(mode="json")
                or journal.get("request")
                != plan.observation_request().model_dump(mode="json")
                or journal.get("prior_activation_id") != plan.prior_activation_id
                or journal.get("operator_principal_id") != plan.operator_principal_id
                or journal.get("owner_member_id") != plan.owner_member_id
                or journal.get("owner_authorization_sha256")
                != plan.owner_authorization_sha256
                or journal.get("initial_snapshot") != plan.deployment_snapshot
                or not journal.get("observation_started_at")
                or "submission_repair_manifest" in journal
                or prepared is None
                or expires is None
                or not prepared <= datetime.now(timezone.utc) < expires
            ):
                raise CoSActivationError(
                    "successor recovery requires the exact unexpired paused effect repair"
                )
            # This also rejects replacement refs and non-ancestor controllers.
            load_git_tree(
                self.repository, plan.target_commit, retained_by_commit=successor
            )
            controls = self._maintenance_controls()
            if (
                plan.sender_proofs is None
                or self._maintenance_proof_expiry(
                    plan.sender_proofs, controls["activation"]
                )
                != expires
                or any(
                    expected != _sha(_payload(value))
                    for expected, value in (
                        (plan.adapter_sha256, controls["adapters"]),
                        (plan.receipt_sha256, controls["receipt"]),
                        (plan.member_mapping_sha256, controls["members"]),
                        (plan.recipient_policy_sha256, controls["policy"]),
                    )
                )
            ):
                raise CoSActivationError(
                    "successor recovery authority or configuration changed"
                )
            observation = _json(self.config_dir / "observation.json")
            window = _json(self.config_dir / "deployment-readiness.json").get(
                "provider_effect_window"
            )
            if observation.get("active") is not True or window != {
                "started_at": journal["observation_started_at"],
                "reason": "cos_enter_observation",
            }:
                raise CoSActivationError(
                    "successor recovery requires the original observation boundary"
                )
            verify_proof(self.paths, self.repository, proof, test_mode=self.test_mode)
            receipt = {
                "controller_commit": successor,
                "original_controller_commit": plan.target_commit,
                "maintenance_plan_sha256": plan.digest(),
            }
            if journal.get("effect_context_recovery_controller", receipt) != receipt:
                raise CoSActivationError("successor recovery controller changed")
            journal["effect_context_recovery_controller"] = receipt
            self._write_journal(journal, "verifying")
            return self._complete_enter_observation(
                journal, release=release, recovery_controller=successor
            )

    def _tooling_maintenance_state(
        self,
        plan: MaintenanceRestorationPlan,
        journal: dict[str, Any],
        *,
        committed: bool,
    ) -> tuple[str, Path, str, dict[str, Any]]:
        """Validate one unchanged predecessor and its exact original authority."""
        from bootstrap.doctor_tooling import RETAINED_DOCTORS

        commit, release, controller = self._observation_selection()
        prepared, expires = aware_time(plan.prepared_at), aware_time(plan.expires_at)
        if (
            commit not in RETAINED_DOCTORS
            or commit != plan.predecessor_commit
            or controller == plan.target_commit
            or plan.effect_context_repair is not None
            or plan.effect_context_proof is not None
            or journal.get("contract") != "cos-channel-operation-journal-v1"
            or journal.get("operation") != "enter_observation"
            or journal.get("phase") != ("committed" if committed else "verifying")
            or journal.get("activation_id") != plan.plan_id
            or journal.get("controller_commit") != plan.target_commit
            or journal.get("release_commit") != commit
            or journal.get("maintenance_plan_sha256") != plan.digest()
            or journal.get("request")
            != plan.observation_request().model_dump(mode="json")
            or journal.get("prior_activation_id") != plan.prior_activation_id
            or journal.get("operator_principal_id") != plan.operator_principal_id
            or journal.get("owner_member_id") != plan.owner_member_id
            or journal.get("owner_authorization_sha256")
            != plan.owner_authorization_sha256
            or journal.get("initial_snapshot") != plan.deployment_snapshot
            or not journal.get("observation_started_at")
            or "submission_repair_manifest" in journal
            or "effect_context_proof" in journal
            or prepared is None
            or expires is None
            or not prepared <= datetime.now(timezone.utc) < expires
        ):
            raise CoSActivationError(
                "tooling recovery requires the exact unexpired paused maintenance"
            )
        load_git_tree(
            self.repository, plan.target_commit, retained_by_commit=controller
        )
        controls = self._maintenance_controls()
        if (
            plan.sender_proofs is None
            or self._maintenance_proof_expiry(
                plan.sender_proofs, controls["activation"]
            )
            != expires
            or controls["activation"].activation_id != plan.prior_activation_id
            or any(
                expected != _sha(_payload(value))
                for expected, value in (
                    (plan.adapter_sha256, controls["adapters"]),
                    (plan.receipt_sha256, controls["receipt"]),
                    (plan.member_mapping_sha256, controls["members"]),
                    (plan.recipient_policy_sha256, controls["policy"]),
                )
            )
            or controls["guard"] != plan.guard_config
            or (
                _sha(_payload(controls["gateway"]))
                if controls["gateway"] is not None
                else None
            )
            != plan.gateway_sha256
            or controls["replies"] != plan.self_reply_bindings
            or controls["reminders"] != plan.self_reminder_bindings
            or capture_deployment_snapshot(self.paths).public()
            != plan.deployment_snapshot
            or controls["policy"]["event_id"] != journal["initial_policy"]["event_id"]
        ):
            raise CoSActivationError(
                "tooling recovery authority or configuration changed"
            )
        observation = _json(self.config_dir / "observation.json")
        window = _json(self.config_dir / "deployment-readiness.json").get(
            "provider_effect_window"
        )
        diagnostics = self.services.runtime_diagnostics()
        # A crash after durable admission may leave both services stopped. This
        # is not health evidence: completion still stops/starts and proves a
        # fresh full Doctor, while the retained admission is revalidated below.
        admitted_restart = (
            not committed
            and "tooling_recovery_admission" in journal
            and "tooling_recovery_controller" in journal
            and diagnostics is None
        )
        if (
            observation.get("active") is not True
            or window
            != {
                "started_at": journal["observation_started_at"],
                "reason": "cos_enter_observation",
            }
            or (
                not admitted_restart
                and (
                    not diagnostics
                    or diagnostics.get("ready") is not True
                    or diagnostics.get("observation_active") is not True
                    or self._provider_calls(diagnostics)
                    != journal.get("prior_provider_calls")
                )
            )
            or asyncio.run(self._effect_count()) != journal.get("prior_provider_calls")
        ):
            raise CoSActivationError(
                "tooling recovery observation or provider boundary changed"
            )
        binding = {
            "contract": "cos-tooling-maintenance-recovery-v1",
            "controller_commit": controller,
            "original_plan": plan.model_dump(mode="json"),
            "original_plan_sha256": plan.digest(),
        }
        if journal.get("tooling_recovery_controller", binding) != binding or (
            committed and "tooling_recovery_controller" not in journal
        ):
            raise CoSActivationError(
                "tooling recovery controller or original plan changed"
            )
        return commit, release, controller, controls

    def _require_tooling_doctor(self, release: Path) -> dict[str, Any]:
        from bootstrap.doctor_tooling import COMPATIBILITY_KEY

        result = run_release_doctor(
            self.paths,
            release,
            test_mode=self.test_mode,
            include_synthetic_proof=False,
            controller_root=self.repository,
        )
        decision = result.get(COMPATIBILITY_KEY, {})
        if (
            result.get("status") != "healthy"
            or decision.get("decision") != "compatible"
            or decision.get("release_commit") != release.name
            or decision.get("controller_commit")
            != reviewed_candidate_commit(self.repository)
        ):
            raise CoSActivationError(
                "tooling recovery has no current qualified retained Doctor report"
            )
        return result

    def _recover_tooling_maintenance_locked(
        self, plan: MaintenanceRestorationPlan
    ) -> dict[str, Any]:
        if (self.paths.app_dir / "activation/active.json").exists():
            raise CoSActivationError("release activation must complete first")
        history = self.directory / "history" / f"{plan.plan_id}.json"
        committed = not self.journal_path.exists()
        journal = _json(history if committed else self.journal_path)
        _commit, release, controller, _controls = self._tooling_maintenance_state(
            plan, journal, committed=committed
        )
        if self.services.runtime_diagnostics() is None and not committed:
            from bootstrap.doctor_tooling import (
                COMPATIBILITY_KEY,
                retained_tooling_report,
            )

            report = journal.get("tooling_recovery_admission", {})
            decision = report.get(COMPATIBILITY_KEY, {})
            original = decision.get("original_report", {})
            recomputed = retained_tooling_report(
                self.paths,
                release,
                self.repository,
                original,
                include_synthetic_proof=False,
            )
            if (
                recomputed != report
                or recomputed.get("status") != "healthy"
                or decision.get("decision") != "compatible"
                or decision.get("controller_commit") != controller
            ):
                raise CoSActivationError("interrupted tooling admission is not valid")
        else:
            report = self._require_tooling_doctor(release)
        if committed:
            return {
                "status": "observation_entered",
                "observation_active": True,
                "release_commit": plan.predecessor_commit,
                "maintenance_plan_sha256": plan.digest(),
                "replayed": True,
            }
        journal.setdefault(
            "tooling_recovery_controller",
            {
                "contract": "cos-tooling-maintenance-recovery-v1",
                "controller_commit": controller,
                "original_plan": plan.model_dump(mode="json"),
                "original_plan_sha256": plan.digest(),
            },
        )
        journal.setdefault("tooling_recovery_admission", report)
        self._write_journal(journal, "verifying")
        return self._complete_enter_observation(
            journal, release=release, recovery_controller=controller
        )

    def recover_tooling_maintenance(
        self, plan: MaintenanceRestorationPlan
    ) -> dict[str, Any]:
        """Complete the original pause, retaining its target, evidence and history."""
        updater = UpgradeManager(
            self.paths, root=self.repository, test_mode=self.test_mode
        )
        with updater._operation_lock():
            return self._recover_tooling_maintenance_locked(plan)

    def restore_tooling_maintenance(
        self, plan: MaintenanceRestorationPlan
    ) -> dict[str, Any]:
        """Restore the unchanged predecessor's exact prior normal authority."""
        return self.activate(plan, enable_live=True, _tooling_restore=True)

    def recover(self) -> dict[str, Any]:
        updater = UpgradeManager(
            self.paths, root=self.repository, test_mode=self.test_mode
        )
        with updater._operation_lock():
            journal = _json(self.journal_path)
            if journal.get("operation") == "repair_recovery_history":
                _commit, release, _controller_commit = self._observation_selection()
                return self._complete_recovery_history_repair(journal, release=release)
            if (
                journal.get("operation") == "enter_observation"
                and "tooling_recovery_controller" in journal
            ):
                plan = MaintenanceRestorationPlan.model_validate(
                    journal["tooling_recovery_controller"]["original_plan"]
                )
                return self._recover_tooling_maintenance_locked(plan)
            if journal.get("operation") == "enter_observation":
                commit, release, controller_commit = self._observation_selection()
                if (
                    journal.get("release_commit") != commit
                    or journal.get("controller_commit") != controller_commit
                ):
                    raise CoSActivationError(
                        "interrupted observation operation belongs to another source"
                    )
                return self._complete_enter_observation(journal, release=release)
            if "tooling_restoration" in journal:
                binding = journal["tooling_restoration"]
                plan = MaintenanceRestorationPlan.model_validate(
                    binding["original_plan"]
                )
                commit, release, controller = self._observation_selection()
                if (
                    binding.get("contract") != "cos-tooling-maintenance-recovery-v1"
                    or binding.get("controller_commit") != controller
                    or binding.get("original_plan_sha256") != plan.digest()
                    or journal.get("maintenance_plan_sha256") != plan.digest()
                    or commit != plan.predecessor_commit
                    or _json(self.directory / "history" / f"{plan.plan_id}.json").get(
                        "tooling_recovery_controller"
                    )
                    != binding
                ):
                    raise CoSActivationError(
                        "interrupted tooling restoration binding changed"
                    )
            else:
                commit, release = self._selection()
            if journal.get("release_commit") != commit:
                raise CoSActivationError(
                    "interrupted operation belongs to another release"
                )
            return self._recover_locked(
                journal, release=release, reason="interrupted_operation"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--enable-live", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--enter-observation", action="store_true")
    mode.add_argument("--repair-recovery-history", action="store_true")
    mode.add_argument("--recover", action="store_true")
    mode.add_argument("--recover-effect-context-controller", action="store_true")
    mode.add_argument("--recover-tooling-maintenance", action="store_true")
    mode.add_argument("--restore-tooling-maintenance", action="store_true")
    mode.add_argument("--prepare-maintenance", action="store_true")
    mode.add_argument("--prepare-effect-context-successor", action="store_true")
    mode.add_argument("--pause-maintenance", action="store_true")
    mode.add_argument("--restore-maintenance", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--submission-repair-manifest", type=Path)
    arguments = parser.parse_args()
    maintenance = (
        arguments.prepare_maintenance
        or arguments.prepare_effect_context_successor
        or arguments.pause_maintenance
        or arguments.restore_maintenance
        or arguments.recover_tooling_maintenance
        or arguments.restore_tooling_maintenance
    )
    if maintenance and (
        arguments.enable_live or arguments.submission_repair_manifest is not None
    ):
        parser.error(
            "maintenance plans preserve existing grants; do not combine activation options"
        )
    if bool(arguments.output) != (
        arguments.prepare_maintenance or arguments.prepare_effect_context_successor
    ):
        parser.error("--output is required only for maintenance preparation")
    if arguments.recover == bool(arguments.request):
        parser.error("choose one private request or interrupted-operation recovery")
    if arguments.repair_recovery_history and (
        arguments.enable_live
        or arguments.recover
        or arguments.enter_observation
        or arguments.submission_repair_manifest is not None
    ):
        parser.error("history repair is a separate private-request operation")
    if arguments.enter_observation and (arguments.enable_live or arguments.recover):
        parser.error("enter-observation is a separate private-request operation")
    if (
        arguments.submission_repair_manifest is not None
        and not arguments.enter_observation
    ):
        parser.error("submission repair manifest is only for entering observation")
    if (
        arguments.recover or arguments.recover_effect_context_controller
    ) and arguments.enable_live:
        parser.error("recovery cannot enable live operation")
    controller = CoSChannelActivator(
        DeploymentPaths.from_root(arguments.instance_root, production=True),
        repository=Path(__file__).resolve().parents[1],
    )
    if arguments.prepare_maintenance or arguments.prepare_effect_context_successor:
        plan = (
            controller.prepare_effect_context_successor(
                MaintenanceRestorationPlan.model_validate(_json(arguments.request))
            )
            if arguments.prepare_effect_context_successor
            else controller.prepare_maintenance(
                MaintenancePreparationRequest.model_validate(_json(arguments.request))
            )
        )
        descriptor = os.open(
            arguments.output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_payload(plan.model_dump(mode="json")))
            handle.flush()
            os.fsync(handle.fileno())
        result = {
            "status": "effect_context_successor_prepared"
            if arguments.prepare_effect_context_successor
            else "maintenance_prepared",
            "plan_id": plan.plan_id,
            "target_commit": plan.target_commit,
            "expires_at": plan.expires_at,
            "runtime_changed": False,
        }
    elif arguments.pause_maintenance:
        result = controller.pause_maintenance(
            MaintenanceRestorationPlan.model_validate(_json(arguments.request))
        )
    elif arguments.restore_maintenance:
        result = controller.activate(
            MaintenanceRestorationPlan.model_validate(_json(arguments.request)),
            enable_live=True,
        )
    elif arguments.recover_tooling_maintenance or arguments.restore_tooling_maintenance:
        plan = MaintenanceRestorationPlan.model_validate(_json(arguments.request))
        result = (
            controller.recover_tooling_maintenance(plan)
            if arguments.recover_tooling_maintenance
            else controller.restore_tooling_maintenance(plan)
        )
    elif arguments.recover_effect_context_controller:
        result = controller.recover_effect_context_controller(
            MaintenanceRestorationPlan.model_validate(_json(arguments.request))
        )
    elif arguments.recover:
        result = controller.recover()
    elif arguments.repair_recovery_history:
        result = controller.repair_recovery_history(
            RecoveryHistoryRepairRequest.model_validate(_json(arguments.request))
        )
    elif arguments.enter_observation:
        request = EnterObservationRequest.model_validate(_json(arguments.request))
        result = controller.enter_observation(
            request, submission_repair_manifest=arguments.submission_repair_manifest
        )
    else:
        activation_request = ActivationRequest.model_validate(_json(arguments.request))
        result = controller.activate(
            activation_request, enable_live=arguments.enable_live
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
