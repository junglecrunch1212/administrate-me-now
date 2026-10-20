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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from adminme.adapters.cos_channel_acquisition import (
    CONTRACT as ACQUISITION_CONTRACT,
    policy_from_config,
    restrict_cos_adapter_config,
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
    SenderProof,
    aware_time,
    live_authorization,
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
from bootstrap.backup_restore import BackupManager
from bootstrap.candidate_readiness import capture_deployment_snapshot
from bootstrap.contracts import DeploymentPaths, atomic_write, load_versions
from bootstrap.secrets import secret_store
from bootstrap.service_settlement import run_release_doctor, wait_for_settled_doctor
from bootstrap.services import ServiceManager
from bootstrap.source_identity import (
    _run_git,
    reviewed_candidate_commit,
    verify_release_source_identity,
)
from bootstrap.upgrade import UpgradeManager


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
    prepared_openclaw_config: dict[str, Any] | None = None
    cos_imsg_guard_config: dict[str, Any] | None = None


class EnterObservationRequest(BaseModel):
    """Content-free binding to the live operation being paused for deployment."""

    model_config = {"extra": "forbid", "strict": True}

    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    owner_member_id: str = Field(min_length=1, max_length=240)
    expected_release_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    expected_activation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


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


def _payload(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def prepare_configuration(
    previous: dict[str, Any], requested: dict[str, Any], *, enable_live: bool
) -> dict[str, Any]:
    """Preserve the old selection and force every non-CoS adapter off."""

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
        or set(original_ids) != set(requested_ids)
    ):
        raise CoSActivationError(
            "channel activation cannot add or remove adapter records"
        )
    result = restrict_cos_adapter_config(requested)
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
        else:
            entry["enabled"] = False
            # Account enrollment and cursors belonging to members are immutable.
            before = next(
                item
                for item in previous["adapters"]
                if item.get("id") == entry.get("id")
            )
            if entry.get("config") != before.get("config"):
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

    def enter_observation(self, request: EnterObservationRequest) -> dict[str, Any]:
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
            before = self.services.runtime_diagnostics() or {}
            if (
                before.get("ready") is not True
                or before.get("observation_active") is not False
            ):
                raise CoSActivationError(
                    "enter-observation requires a healthy live predecessor"
                )
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
            receipt = CosChannelActivation.model_validate(
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
                "activation_id": uuid.uuid4().hex,
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
                        _private_bytes(self.config_dir / name)
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
            self._write_journal(journal, "prepared")
            return self._complete_enter_observation(journal, release=release)

    def _complete_enter_observation(
        self, journal: dict[str, Any], *, release: Path
    ) -> dict[str, Any]:
        """Complete containment after interruption; never restore live writes."""
        try:
            self.services.stop()
            selected = self._observation_selection()
            if selected != (
                journal["release_commit"],
                release,
                journal["controller_commit"],
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
                    if _private_bytes(self.config_dir / name) != base64.b64decode(
                        encoded, validate=True
                    ):
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
            journal["doctor"] = self._settle(release)
            after = self.services.runtime_diagnostics() or {}
            if (
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
                "status": "observation_entered",
                "observation_active": True,
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
                self.paths, release, test_mode=self.test_mode, **kwargs
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
                    "reason": deactivate_reason,
                    "observation_active": True,
                }
            )
            envelope = EventEnvelope(
                event_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                tenant_id=instance.tenant_id,
                type=ACTIVATED_EVENT if activation is not None else DEACTIVATED_EVENT,
                schema_version=1,
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
                    else None
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
                    else "CoS operation recovery"
                )
                already_enabled = False
                if journal.get("operation") == "enter_observation":
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
        self, request: ActivationRequest, *, enable_live: bool
    ) -> dict[str, Any]:
        updater = UpgradeManager(
            self.paths, root=self.repository, test_mode=self.test_mode
        )
        with updater._operation_lock():
            if self.journal_path.exists():
                raise CoSActivationError("recover the interrupted CoS operation first")
            if (self.paths.app_dir / "activation" / "active.json").exists():
                raise CoSActivationError("release activation must complete first")
            commit, release = self._selection()
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
                atomic_write(self.config_dir / "adapters.json", _payload(prepared))
                if policy.imessage_channel_id is not None:
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
                    activation = CosChannelActivation(
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
                self._selection()
                self._finish(journal, "committed")
                return {
                    "status": "activated",
                    "activation_id": activation_id,
                    "channels": sorted(expected),
                    "observation_active": not enable_live,
                    "personal_channel_polling": False,
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
        prior_window = (
            _json(self.config_dir / "deployment-readiness.json").get(
                "provider_effect_window"
            )
            or {}
        )
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
        readiness = _json(self.config_dir / "deployment-readiness.json")
        if journal.get("enabled_live"):
            prior = copy.deepcopy(prior_window)
            readiness.setdefault("prior_provider_effect_windows", []).append(prior)
            readiness["provider_effect_window"] = {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "reason": "cos_operation_recovery",
            }
        self._refresh_binding(readiness, journal["release_commit"])
        self.services.start()
        journal["recovery_doctor"] = self._settle(release)
        self._finish(journal, "recovered")
        return {
            "status": "recovered",
            "observation_active": True,
            "all_polling_paused": True,
        }

    def recover(self) -> dict[str, Any]:
        updater = UpgradeManager(
            self.paths, root=self.repository, test_mode=self.test_mode
        )
        with updater._operation_lock():
            journal = _json(self.journal_path)
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
    parser.add_argument("--enter-observation", action="store_true")
    parser.add_argument("--recover", action="store_true")
    arguments = parser.parse_args()
    if arguments.recover == bool(arguments.request):
        parser.error("choose one private request or interrupted-operation recovery")
    if arguments.enter_observation and (arguments.enable_live or arguments.recover):
        parser.error("enter-observation is a separate private-request operation")
    if arguments.recover and arguments.enable_live:
        parser.error("recovery cannot enable live operation")
    controller = CoSChannelActivator(
        DeploymentPaths.from_root(arguments.instance_root, production=True),
        repository=Path(__file__).resolve().parents[1],
    )
    if arguments.recover:
        result = controller.recover()
    elif arguments.enter_observation:
        request = EnterObservationRequest.model_validate(_json(arguments.request))
        result = controller.enter_observation(request)
    else:
        activation_request = ActivationRequest.model_validate(_json(arguments.request))
        result = controller.activate(
            activation_request, enable_live=arguments.enable_live
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
