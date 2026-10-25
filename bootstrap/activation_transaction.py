"""Durable, content-free ownership for one activation or rollback transaction.

The journal is intentionally mechanical.  It does not decide whether deployment
is authorized and it does not promote candidate-pending evidence.  The guarded
updater creates it only after independently validating those prerequisites, then
records intent before every active selector or service-state mutation.  A later
guarded invocation can therefore converge to the exact captured predecessor.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path
from typing import Any

from bootstrap.recovery_readiness import validate_baseline
from bootstrap.contracts import DeploymentPaths
from bootstrap.provider_binding_transition import valid_provider_binding_transition


JOURNAL_CONTRACT = "administrateme-activation-transaction-v1"
JOURNAL_SCHEMA_VERSION = 1
_HEX_32 = re.compile(r"[0-9a-f]{32}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_MAX_JOURNAL_BYTES = 4 * 1024 * 1024
_OPERATIONS = frozenset({"activate", "rollback"})
_TERMINAL_PHASES = frozenset({"committed", "recovered"})
_ACTIVATE_PHASES = frozenset(
    {
        "created",
        "backup_creating",
        "backup_created",
        "backup_rehearsing",
        "backup_rehearsed",
        "rollback_eligible",
        "selector_mutation_authorized",
        "services_quiescing_for_repair",
        "services_quiesced_for_repair",
        "preparation_consuming",
        "preparation_consumed",
        "native_migration_captured",
        "native_doctor_running",
        "native_doctor_settled",
        "native_forward_repair_required",
        "openclaw_selecting",
        "openclaw_selected",
        "services_quiescing",
        "services_quiesced",
        "provider_config_selecting",
        "provider_config_selected",
        "cos_guard_selecting",
        "cos_guard_selected",
        "workspace_instructions_selecting",
        "workspace_instructions_selected",
        "plugin_selecting",
        "plugin_selected",
        "pointer_selecting",
        "pointer_selected",
        "readiness_promoting",
        "readiness_promoted",
        "llm_tool_selecting",
        "llm_tool_selected",
        "runtime_binding_refreshing",
        "runtime_binding_refreshed",
        "launchagents_selecting",
        "launchagents_selected",
        "services_starting",
        "services_started",
        "doctor_running",
        "doctor_healthy",
        "services_restopping",
        "services_restopped",
        "recovering",
        "recovered",
        "committed",
    }
)
_ROLLBACK_PHASES = frozenset(
    {
        "created",
        "openclaw_selecting",
        "openclaw_selected",
        "services_quiescing",
        "services_quiesced",
        "provider_config_selecting",
        "provider_config_selected",
        "cos_guard_selecting",
        "cos_guard_selected",
        "workspace_instructions_selecting",
        "workspace_instructions_selected",
        "plugin_selecting",
        "plugin_selected",
        "pointer_selecting",
        "pointer_selected",
        "readiness_selecting",
        "readiness_selected",
        "settings_control_selecting",
        "settings_control_selected",
        "launchagents_selecting",
        "launchagents_selected",
        "services_starting",
        "doctor_running",
        "doctor_healthy",
        "baseline_restored",
        "services_restopped",
        "recovering",
        "recovered",
        "committed",
    }
)


class ActivationJournalError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _private_directory(path: Path, *, create: bool = False) -> os.stat_result:
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ActivationJournalError(
            f"activation journal directory is unavailable: {path.name}"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ActivationJournalError(
            f"activation journal directory is unsafe: {path.name}"
        )
    return metadata


def _open_private_directory(path: Path) -> int:
    descriptor: int | None = None
    try:
        public = path.lstat()
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o700
            or (opened.st_dev, opened.st_ino) != (public.st_dev, public.st_ino)
        ):
            raise ActivationJournalError(
                f"activation journal directory is unsafe: {path.name}"
            )
        return descriptor
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ActivationJournalError(
            f"activation journal directory is unavailable: {path.name}"
        ) from exc
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _assert_directory_coordinate(descriptor: int, path: Path) -> None:
    try:
        opened = os.fstat(descriptor)
        public = path.lstat()
    except OSError as exc:
        raise ActivationJournalError(
            f"activation journal directory changed: {path.name}"
        ) from exc
    if (opened.st_dev, opened.st_ino) != (public.st_dev, public.st_ino):
        raise ActivationJournalError(
            f"activation journal directory changed: {path.name}"
        )


def _read_private_at(
    directory_descriptor: int,
    name: str,
) -> tuple[bytes, os.stat_result]:
    descriptor: int | None = None
    try:
        public_before = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_descriptor,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size <= 0
            or before.st_size > _MAX_JOURNAL_BYTES
            or (before.st_dev, before.st_ino)
            != (public_before.st_dev, public_before.st_ino)
        ):
            raise ActivationJournalError("activation journal is unsafe")
        payload = bytearray()
        while len(payload) < before.st_size:
            chunk = os.read(descriptor, min(64 * 1024, before.st_size - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        public_after = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            len(payload) != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (after.st_dev, after.st_ino)
            != (public_after.st_dev, public_after.st_ino)
        ):
            raise ActivationJournalError("activation journal changed while reading")
        return bytes(payload), before
    except OSError as exc:
        raise ActivationJournalError("activation journal is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_private_at(
    directory_descriptor: int,
    name: str,
    payload: bytes,
    *,
    replace: bool,
) -> None:
    if not payload or len(payload) > _MAX_JOURNAL_BYTES:
        raise ActivationJournalError("activation journal payload is invalid")
    temporary = f".{name}.{secrets.token_hex(16)}"
    descriptor: int | None = None
    published = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ActivationJournalError("activation journal write was incomplete")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        temporary_metadata = os.fstat(descriptor)
        named_temporary = os.stat(
            temporary,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (temporary_metadata.st_dev, temporary_metadata.st_ino) != (
            named_temporary.st_dev,
            named_temporary.st_ino,
        ):
            raise ActivationJournalError("activation journal staging changed")
        if replace:
            os.replace(
                temporary,
                name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            published = True
        else:
            os.link(
                temporary,
                name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            os.unlink(temporary, dir_fd=directory_descriptor)
            published = True
        target = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(target.st_mode)
            or target.st_uid != os.getuid()
            or target.st_nlink != 1
            or stat.S_IMODE(target.st_mode) != 0o600
            or (target.st_dev, target.st_ino)
            != (temporary_metadata.st_dev, temporary_metadata.st_ino)
        ):
            raise ActivationJournalError("activation journal publication is unsafe")
        os.fsync(directory_descriptor)
    except FileExistsError as exc:
        raise ActivationJournalError(
            "an activation transaction already requires recovery"
        ) from exc
    except OSError as exc:
        raise ActivationJournalError("activation journal publication failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not published:
            try:
                os.unlink(temporary, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        elif replace:
            try:
                os.unlink(temporary, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass


def _json_no_duplicates(payload: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in items:
            if key in value:
                raise ActivationJournalError("activation journal has duplicate keys")
            value[key] = child
        return value

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationJournalError("activation journal is invalid") from exc
    if not isinstance(value, dict):
        raise ActivationJournalError("activation journal is invalid")
    return value


def validate_journal(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ActivationJournalError("activation journal is invalid")
    required = {
        "contract",
        "schema_version",
        "transaction_id",
        "operation",
        "phase",
        "created_at_ns",
        "updated_at_ns",
        "uid",
        "candidate_commit",
        "candidate_path",
        "target_commit",
        "target_path",
        "predecessor_commit",
        "predecessor_path",
        "initial_pointer",
        "initial_pointer_bytes",
        "initial_pointer_sha256",
        "initial_readiness",
        "initial_readiness_bytes",
        "initial_readiness_sha256",
        "was_running",
        "pending_attestation_id",
        "pending_attestation_sha256",
        "fresh_backup",
        "rollback_eligibility",
        "active_readiness",
        "content_exposed",
    }
    transition_fields = {
        "initial_adapter_config",
        "initial_adapter_config_bytes",
        "initial_adapter_config_sha256",
        "selected_adapter_config",
        "selected_adapter_config_bytes",
        "selected_adapter_config_sha256",
        "provider_binding_transition",
    }
    settings_fields = {
        "initial_settings_control",
        "initial_settings_control_bytes",
        "initial_settings_control_sha256",
    }
    keys = set(value)
    extras = keys - required
    known_extras = (
        transition_fields
        | settings_fields
        | {
            "initial_readiness_baseline",
            "restored_readiness",
            "cos_imessage_rebind",
            "workspace_instructions",
            "submission_repair_manifest",
            "submission_repair",
            "submission_repair_request_event_ids",
            "effect_context_proof",
            "effect_context_plan_sha256",
            "openclaw_migration",
            "native_recovery",
        }
    )
    has_config_transition = transition_fields <= keys
    has_settings_snapshot = settings_fields <= keys
    if (
        not required <= keys
        or not extras <= known_extras
        or (bool(extras & transition_fields) != has_config_transition)
        or (bool(extras & settings_fields) != has_settings_snapshot)
    ):
        raise ActivationJournalError("activation journal schema is invalid")
    if (
        value.get("contract") != JOURNAL_CONTRACT
        or value.get("schema_version") != JOURNAL_SCHEMA_VERSION
        or value.get("operation") not in _OPERATIONS
        or not isinstance(value.get("created_at_ns"), int)
        or not isinstance(value.get("updated_at_ns"), int)
        or value["created_at_ns"] <= 0
        or value["updated_at_ns"] < value["created_at_ns"]
        or value.get("uid") != os.getuid()
        or value.get("content_exposed") is not False
        or not isinstance(value.get("was_running"), bool)
    ):
        raise ActivationJournalError("activation journal header is invalid")
    operation = value["operation"]
    phase = value.get("phase")
    allowed_phases = _ACTIVATE_PHASES if operation == "activate" else _ROLLBACK_PHASES
    if not isinstance(phase, str) or phase not in allowed_phases:
        raise ActivationJournalError("activation journal phase is invalid")
    if _HEX_32.fullmatch(str(value.get("transaction_id"))) is None:
        raise ActivationJournalError("activation transaction identifier is invalid")
    for name in ("candidate_commit", "target_commit", "predecessor_commit"):
        if _HEX_40.fullmatch(str(value.get(name))) is None:
            raise ActivationJournalError("activation release identity is invalid")
    for name in ("candidate_path", "target_path", "predecessor_path"):
        raw = value.get(name)
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            raise ActivationJournalError("activation release coordinate is invalid")
    recovery = value.get("native_recovery")
    if recovery is not None:
        from bootstrap.native_recovery import valid_binding

        if (
            not valid_binding(recovery)
            or recovery["candidate_commit"] != value["candidate_commit"]
            or recovery["parent_transaction_id"] == value["transaction_id"]
        ):
            raise ActivationJournalError("native recovery parent binding is invalid")
    migration = value.get("openclaw_migration")
    if migration is not None:
        from bootstrap.openclaw_migration import NativeMigrationError, validate_receipt

        try:
            validate_receipt(migration, value)
        except NativeMigrationError as exc:
            raise ActivationJournalError("native migration journal is invalid") from exc
    elif isinstance(phase, str) and phase.startswith("native_") and recovery is None:
        raise ActivationJournalError("native migration journal is missing")
    rebind = value.get("cos_imessage_rebind")
    if rebind is not None:
        from bootstrap.cos_guard_release import validate_rebind

        try:
            validate_rebind(
                rebind,
                source_release=(
                    Path(value["predecessor_path"]).parent
                    / recovery["failed_target_commit"]
                    if recovery is not None
                    and rebind.get("source_release")
                    == str(
                        Path(value["predecessor_path"]).parent
                        / recovery["failed_target_commit"]
                    )
                    else Path(value["predecessor_path"])
                ),
                target_release=Path(value["target_path"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ActivationJournalError(
                "activation CoS guard transition is invalid"
            ) from exc
    elif phase in {"cos_guard_selecting", "cos_guard_selected"}:
        raise ActivationJournalError("activation CoS guard transition is missing")
    instructions = value.get("workspace_instructions")
    if instructions is not None:
        from bootstrap.workspace_instructions import (
            WorkspaceInstructionsError,
            validate_transition,
        )

        try:
            validate_transition(instructions)
        except WorkspaceInstructionsError as exc:
            raise ActivationJournalError(
                "activation workspace instructions are invalid"
            ) from exc
    elif phase in {
        "workspace_instructions_selecting",
        "workspace_instructions_selected",
    }:
        raise ActivationJournalError("activation workspace instructions are missing")
    if not isinstance(value.get("initial_pointer"), dict):
        raise ActivationJournalError("activation predecessor pointer is invalid")
    pointer_encoded = value.get("initial_pointer_bytes")
    if not isinstance(pointer_encoded, str):
        raise ActivationJournalError("activation predecessor pointer bytes are invalid")
    try:
        pointer_payload = base64.b64decode(pointer_encoded, validate=True)
    except (TypeError, ValueError) as exc:
        raise ActivationJournalError(
            "activation predecessor pointer bytes are invalid"
        ) from exc
    try:
        pointer_document = _json_no_duplicates(pointer_payload)
    except ActivationJournalError as exc:
        raise ActivationJournalError(
            "activation predecessor pointer bytes are invalid"
        ) from exc
    if (
        pointer_document != value["initial_pointer"]
        or value.get("initial_pointer_sha256")
        != hashlib.sha256(pointer_payload).hexdigest()
    ):
        raise ActivationJournalError("activation predecessor pointer digest is invalid")
    pointer_commit = pointer_document.get("commit")
    pointer_path = pointer_document.get("path")
    if operation == "activate":
        coordinate_matches = (
            pointer_commit == value["predecessor_commit"]
            and pointer_path == value["predecessor_path"]
            and value["target_commit"] == value["candidate_commit"]
        )
    else:
        previous = pointer_document.get("previous")
        coordinate_matches = (
            pointer_commit == value["candidate_commit"]
            and pointer_path == value["candidate_path"]
            and value["predecessor_commit"] == value["candidate_commit"]
            and value["predecessor_path"] == value["candidate_path"]
            and isinstance(previous, dict)
            and previous.get("commit") == value["target_commit"]
            and previous.get("path") == value["target_path"]
        )
    if not coordinate_matches:
        raise ActivationJournalError("activation release binding is inconsistent")
    readiness = value.get("initial_readiness")
    readiness_encoded = value.get("initial_readiness_bytes")
    readiness_hash = value.get("initial_readiness_sha256")
    if readiness is None:
        if readiness_hash is not None or readiness_encoded is not None:
            raise ActivationJournalError("activation predecessor readiness is invalid")
    else:
        if not isinstance(readiness_encoded, str):
            raise ActivationJournalError("activation predecessor readiness is invalid")
        try:
            readiness_payload = base64.b64decode(readiness_encoded, validate=True)
            readiness_document = _json_no_duplicates(readiness_payload)
        except (
            TypeError,
            ValueError,
            ActivationJournalError,
        ) as exc:
            raise ActivationJournalError(
                "activation predecessor readiness is invalid"
            ) from exc
        if (
            not isinstance(readiness, dict)
            or readiness_document != readiness
            or not isinstance(readiness_hash, str)
            or _HEX_64.fullmatch(readiness_hash) is None
            or readiness_hash != hashlib.sha256(readiness_payload).hexdigest()
        ):
            raise ActivationJournalError("activation predecessor readiness is invalid")
    if (
        "submission_repair_request_event_ids" in value
        and "submission_repair_manifest" not in value
    ):
        raise ActivationJournalError(
            "submission request ids require the exact manifest"
        )
    if "submission_repair_manifest" in value or "submission_repair" in value:
        from bootstrap.submission_repair import SubmissionRepairManifest, repair_binding

        try:
            repair = SubmissionRepairManifest.model_validate(
                value.get("submission_repair_manifest")
            )
        except ValueError as exc:
            raise ActivationJournalError(
                "activation submission repair manifest is invalid"
            ) from exc
        request_ids = value.get("submission_repair_request_event_ids")
        if request_ids is not None and (
            not isinstance(request_ids, list)
            or len(request_ids) != len(repair.targets)
            or len(set(request_ids)) != len(request_ids)
            or any(not isinstance(item, str) or not item for item in request_ids)
        ):
            raise ActivationJournalError(
                "activation submission repair request inventory is invalid"
            )
        if (
            value.get("submission_repair") != repair_binding(repair)
            or repair.candidate_commit != value["candidate_commit"]
            or repair.predecessor_commit != value["predecessor_commit"]
            or value["operation"] != "activate"
            or value["was_running"] is not True
            or value.get("initial_readiness_baseline") is not None
        ):
            raise ActivationJournalError(
                "activation submission repair binding is invalid"
            )
    if "effect_context_proof" in value or "effect_context_plan_sha256" in value:
        from bootstrap.effect_context_repair import EffectContextProof

        try:
            effect_proof = EffectContextProof.model_validate(
                value.get("effect_context_proof")
            )
        except ValueError as exc:
            raise ActivationJournalError(
                "activation effect-context proof is invalid"
            ) from exc
        if (
            effect_proof.candidate_commit != value["candidate_commit"]
            or effect_proof.predecessor_commit != value["predecessor_commit"]
            or value["operation"] != "activate"
            or value["was_running"] is not True
            or value.get("initial_readiness_baseline") is not None
            or "submission_repair_manifest" in value
            or _HEX_64.fullmatch(str(value.get("effect_context_plan_sha256"))) is None
        ):
            raise ActivationJournalError(
                "activation effect-context proof binding is invalid"
            )
    baseline = value.get("initial_readiness_baseline")
    if baseline is not None:
        try:
            validate_baseline(baseline, readiness_hash)
        except ValueError as exc:
            raise ActivationJournalError(
                "activation recovery readiness baseline is invalid"
            ) from exc
        if value["was_running"] is not True:
            raise ActivationJournalError(
                "recovery readiness baseline requires running services"
            )
    if "restored_readiness" in value and (
        value["phase"] not in {"committed", "recovered"}
        or value["restored_readiness"]
        not in {
            "healthy",
            "preexisting_attestation_gaps",
            "not_running",
            "contained_degraded",
        }
    ):
        raise ActivationJournalError("activation restored readiness result is invalid")
    if (
        value.get("restored_readiness") == "contained_degraded"
        and "submission_repair_manifest" not in value
        and "effect_context_proof" not in value
    ):
        raise ActivationJournalError(
            "contained recovery requires exact repair authority"
        )
    if has_config_transition:
        decoded_configs: dict[str, bytes] = {}
        for prefix in ("initial", "selected"):
            document = value.get(f"{prefix}_adapter_config")
            encoded = value.get(f"{prefix}_adapter_config_bytes")
            digest = value.get(f"{prefix}_adapter_config_sha256")
            if not isinstance(document, dict) or not isinstance(encoded, str):
                raise ActivationJournalError(
                    "activation adapter configuration evidence is invalid"
                )
            try:
                payload = base64.b64decode(encoded, validate=True)
                decoded = _json_no_duplicates(payload)
            except (TypeError, ValueError, ActivationJournalError) as exc:
                raise ActivationJournalError(
                    "activation adapter configuration evidence is invalid"
                ) from exc
            if (
                decoded != document
                or not isinstance(digest, str)
                or _HEX_64.fullmatch(digest) is None
                or hashlib.sha256(payload).hexdigest() != digest
            ):
                raise ActivationJournalError(
                    "activation adapter configuration evidence is invalid"
                )
            decoded_configs[prefix] = payload
        transition = value.get("provider_binding_transition")
        if transition is None:
            if decoded_configs["initial"] != decoded_configs["selected"]:
                raise ActivationJournalError(
                    "activation adapter configuration changed without a transition"
                )
        elif not valid_provider_binding_transition(transition):
            raise ActivationJournalError(
                "activation provider-binding transition is invalid"
            )
        elif operation == "activate" and (
            transition["source_sha256"]
            != hashlib.sha256(decoded_configs["initial"]).hexdigest()
            or transition["prepared_sha256"]
            != hashlib.sha256(decoded_configs["selected"]).hexdigest()
        ):
            raise ActivationJournalError(
                "activation provider-binding transition bytes do not match"
            )
        elif operation == "rollback" and (
            transition["prepared_sha256"]
            != hashlib.sha256(decoded_configs["initial"]).hexdigest()
            or transition["source_sha256"]
            != hashlib.sha256(decoded_configs["selected"]).hexdigest()
        ):
            raise ActivationJournalError(
                "rollback provider-binding transition bytes do not match"
            )
    if has_settings_snapshot:
        settings = value.get("initial_settings_control")
        settings_encoded = value.get("initial_settings_control_bytes")
        settings_hash = value.get("initial_settings_control_sha256")
        if settings is None:
            if settings_encoded is not None or settings_hash is not None:
                raise ActivationJournalError(
                    "activation predecessor settings control is invalid"
                )
        else:
            if not isinstance(settings, dict) or not isinstance(settings_encoded, str):
                raise ActivationJournalError(
                    "activation predecessor settings control is invalid"
                )
            try:
                settings_payload = base64.b64decode(settings_encoded, validate=True)
                settings_document = _json_no_duplicates(settings_payload)
            except (TypeError, ValueError, ActivationJournalError) as exc:
                raise ActivationJournalError(
                    "activation predecessor settings control is invalid"
                ) from exc
            if (
                settings_document != settings
                or not isinstance(settings_hash, str)
                or _HEX_64.fullmatch(settings_hash) is None
                or settings_hash != hashlib.sha256(settings_payload).hexdigest()
            ):
                raise ActivationJournalError(
                    "activation predecessor settings control is invalid"
                )
    pending_id = value.get("pending_attestation_id")
    pending_hash = value.get("pending_attestation_sha256")
    if operation == "activate":
        if (
            _HEX_32.fullmatch(str(pending_id)) is None
            or _HEX_64.fullmatch(str(pending_hash)) is None
        ):
            raise ActivationJournalError(
                "activation pending evidence binding is invalid"
            )
    elif pending_id is not None or pending_hash is not None:
        raise ActivationJournalError("rollback journal cannot bind pending evidence")
    for name in ("fresh_backup", "rollback_eligibility", "active_readiness"):
        if value.get(name) is not None and not isinstance(value[name], dict):
            raise ActivationJournalError(f"activation journal {name} is invalid")
    if (
        operation == "activate"
        and phase == "committed"
        and any(
            not isinstance(value.get(name), dict)
            for name in ("fresh_backup", "rollback_eligibility", "active_readiness")
        )
    ):
        raise ActivationJournalError(
            "committed activation journal lacks mechanical evidence"
        )
    return value


class ActivationJournal:
    """One private active journal plus append-only terminal history."""

    def __init__(self, paths: DeploymentPaths) -> None:
        self.paths = paths
        self.directory = paths.app_dir / "activation"
        self.history = self.directory / "history"
        self.path = self.directory / "active.json"

    def _ensure_directories(self) -> None:
        _private_directory(self.paths.root)
        _private_directory(self.paths.app_dir)
        if not os.path.lexists(self.directory):
            self.directory.mkdir(mode=0o700)
        _private_directory(self.directory)
        if not os.path.lexists(self.history):
            self.history.mkdir(mode=0o700)
        _private_directory(self.history)

    def _open_directories(self) -> tuple[int, int]:
        self._ensure_directories()
        directory_descriptor: int | None = None
        history_descriptor: int | None = None
        try:
            directory_descriptor = _open_private_directory(self.directory)
            history_descriptor = _open_private_directory(self.history)
            _assert_directory_coordinate(directory_descriptor, self.directory)
            _assert_directory_coordinate(history_descriptor, self.history)
            return directory_descriptor, history_descriptor
        except BaseException:
            for descriptor in (history_descriptor, directory_descriptor):
                if descriptor is not None:
                    os.close(descriptor)
            raise

    @staticmethod
    def _close_directories(*descriptors: int) -> None:
        first_error: OSError | None = None
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise ActivationJournalError(
                "activation journal directory cleanup failed"
            ) from first_error

    @staticmethod
    def _load_at(directory_descriptor: int) -> dict[str, Any] | None:
        try:
            os.stat(
                "active.json",
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ActivationJournalError("activation journal is unavailable") from exc
        payload, _metadata = _read_private_at(directory_descriptor, "active.json")
        return validate_journal(_json_no_duplicates(payload))

    def load(self) -> dict[str, Any] | None:
        directory_descriptor, history_descriptor = self._open_directories()
        failed = False
        try:
            result = self._load_at(directory_descriptor)
            _assert_directory_coordinate(directory_descriptor, self.directory)
            _assert_directory_coordinate(history_descriptor, self.history)
            return result
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self._close_directories(history_descriptor, directory_descriptor)
            except ActivationJournalError:
                if not failed:
                    raise

    def committed_activation(
        self,
        *,
        target_commit: str,
        predecessor_commit: str,
    ) -> dict[str, Any]:
        """Find the exact activation that made ``target_commit`` active."""

        directory_descriptor, history_descriptor = self._open_directories()
        failed = False
        try:
            try:
                entries = tuple(os.listdir(history_descriptor))
            except OSError as exc:
                raise ActivationJournalError(
                    "activation history cannot be enumerated"
                ) from exc
            matches: list[dict[str, Any]] = []
            for name in entries:
                if re.fullmatch(r"[0-9a-f]{32}\.json", name) is None:
                    raise ActivationJournalError("activation history contains residue")
                payload, _metadata = _read_private_at(history_descriptor, name)
                value = validate_journal(_json_no_duplicates(payload))
                if (
                    value["operation"] == "activate"
                    and value["phase"] == "committed"
                    and value["target_commit"] == target_commit
                    and value["predecessor_commit"] == predecessor_commit
                ):
                    matches.append(value)
            _assert_directory_coordinate(directory_descriptor, self.directory)
            _assert_directory_coordinate(history_descriptor, self.history)
            if len(matches) != 1:
                raise ActivationJournalError(
                    "exact predecessor readiness history is unavailable or ambiguous"
                )
            return matches[0]
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self._close_directories(history_descriptor, directory_descriptor)
            except ActivationJournalError:
                if not failed:
                    raise

    def native_successor(
        self, *, target_commit: str, original_target: str, original_predecessor: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from bootstrap.native_recovery import lineage

        directory_descriptor, history_descriptor = self._open_directories()
        try:
            matches = []
            names = os.listdir(history_descriptor)
            if len(names) > 10000:
                raise ActivationJournalError(
                    "activation history exceeds native lookup bound"
                )
            for name in names:
                if re.fullmatch(r"[0-9a-f]{32}\.json", name) is None:
                    raise ActivationJournalError("activation history contains residue")
                payload, _metadata = _read_private_at(history_descriptor, name)
                value = validate_journal(_json_no_duplicates(payload))
                if (
                    value["operation"] == "activate"
                    and value["phase"] == "committed"
                    and value["target_commit"] == target_commit
                    and value.get("native_recovery")
                ):
                    chain = lineage(self.paths, value)
                    origin = chain[-1]
                    if (
                        origin["target_commit"] == original_target
                        and origin["predecessor_commit"] == original_predecessor
                    ):
                        matches.append((value, origin))
            if len(matches) != 1:
                raise ActivationJournalError(
                    "exact native successor restoration lineage is unavailable"
                )
            return matches[0]
        finally:
            self._close_directories(history_descriptor, directory_descriptor)

    def create(
        self, value: dict[str, Any], *, contained_parent: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        fixed = {
            **value,
            "contract": JOURNAL_CONTRACT,
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "created_at_ns": time.time_ns(),
            "updated_at_ns": time.time_ns(),
            "uid": os.getuid(),
            "content_exposed": False,
        }
        fixed["updated_at_ns"] = max(fixed["updated_at_ns"], fixed["created_at_ns"])
        validate_journal(fixed)
        directory_descriptor, history_descriptor = self._open_directories()
        failed = False
        try:
            if contained_parent is not None:
                current = self._load_at(directory_descriptor)
                link = fixed.get("native_recovery") or {}
                if (
                    current != contained_parent
                    or current["phase"] != "native_forward_repair_required"
                    or link.get("parent_transaction_id") != current["transaction_id"]
                    or link.get("parent_journal_sha256") != sha256_json(current)
                    or link.get("failed_target_commit") != current["target_commit"]
                ):
                    raise ActivationJournalError(
                        "contained native parent changed before successor admission"
                    )
                # Archive the exact failed journal, without a successful/terminal
                # phase rewrite. A crash here leaves the old active owner; retry
                # accepts only the identical immutable archive.
                name = f"{current['transaction_id']}.json"
                encoded = json.dumps(current, indent=2, sort_keys=True).encode("utf-8")
                try:
                    os.stat(name, dir_fd=history_descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    _write_private_at(history_descriptor, name, encoded, replace=False)
                else:
                    existing, _metadata = _read_private_at(history_descriptor, name)
                    if existing != encoded:
                        raise ActivationJournalError(
                            "retained contained journal conflicts"
                        )
            elif fixed.get("native_recovery") is not None:
                raise ActivationJournalError(
                    "native recovery requires retained parent admission"
                )
            # Atomic replacement makes either parent or linked successor own the
            # transaction; there is never an unowned interval between journals.
            _write_private_at(
                directory_descriptor,
                "active.json",
                json.dumps(fixed, indent=2, sort_keys=True).encode("utf-8"),
                replace=contained_parent is not None,
            )
            _assert_directory_coordinate(directory_descriptor, self.directory)
            _assert_directory_coordinate(history_descriptor, self.history)
            return fixed
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self._close_directories(history_descriptor, directory_descriptor)
            except ActivationJournalError:
                if not failed:
                    raise

    def transition(
        self, value: dict[str, Any], phase: str, **updates: Any
    ) -> dict[str, Any]:
        directory_descriptor, history_descriptor = self._open_directories()
        failed = False
        try:
            current = self._load_at(directory_descriptor)
            if current is None or current["transaction_id"] != value["transaction_id"]:
                raise ActivationJournalError("activation journal ownership changed")
            next_value = {
                **current,
                **updates,
                "phase": phase,
                "updated_at_ns": max(time.time_ns(), current["updated_at_ns"]),
            }
            prior_native = current.get("openclaw_migration")
            next_native = next_value.get("openclaw_migration")
            if (
                isinstance(prior_native, dict)
                and prior_native.get("candidate_admitted") is True
                and (
                    not isinstance(next_native, dict)
                    or next_native.get("candidate_admitted") is not True
                )
            ):
                raise ActivationJournalError(
                    "native candidate admission evidence cannot be cleared"
                )
            validate_journal(next_value)
            _write_private_at(
                directory_descriptor,
                "active.json",
                json.dumps(next_value, indent=2, sort_keys=True).encode("utf-8"),
                replace=True,
            )
            _assert_directory_coordinate(directory_descriptor, self.directory)
            _assert_directory_coordinate(history_descriptor, self.history)
            return next_value
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self._close_directories(history_descriptor, directory_descriptor)
            except ActivationJournalError:
                if not failed:
                    raise

    def finish(self, value: dict[str, Any]) -> Path:
        directory_descriptor, history_descriptor = self._open_directories()
        failed = False
        try:
            terminal = self._load_at(directory_descriptor)
            if (
                terminal is None
                or terminal["transaction_id"] != value["transaction_id"]
                or terminal["phase"] not in _TERMINAL_PHASES
            ):
                raise ActivationJournalError("activation transaction is not terminal")
            name = f"{terminal['transaction_id']}.json"
            target = self.history / name
            encoded = json.dumps(terminal, indent=2, sort_keys=True).encode("utf-8")
            try:
                os.stat(name, dir_fd=history_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                _write_private_at(
                    history_descriptor,
                    name,
                    encoded,
                    replace=False,
                )
            except OSError as exc:
                raise ActivationJournalError(
                    "activation history entry is unavailable"
                ) from exc
            else:
                existing, _metadata = _read_private_at(history_descriptor, name)
                if existing != encoded:
                    raise ActivationJournalError("activation history entry conflicts")
            current, _metadata = _read_private_at(
                directory_descriptor,
                "active.json",
            )
            if current != encoded:
                raise ActivationJournalError(
                    "activation journal changed before archival"
                )
            _assert_directory_coordinate(directory_descriptor, self.directory)
            _assert_directory_coordinate(history_descriptor, self.history)
            os.unlink("active.json", dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
            return target
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self._close_directories(history_descriptor, directory_descriptor)
            except ActivationJournalError:
                if not failed:
                    raise


__all__ = [
    "ActivationJournal",
    "ActivationJournalError",
    "JOURNAL_CONTRACT",
    "canonical_json",
    "sha256_json",
    "validate_journal",
]
