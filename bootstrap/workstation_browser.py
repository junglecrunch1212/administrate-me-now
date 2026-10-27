"""Prepare a browser route, private tab policy and closed readiness reconciliations.

Only an operator uses this command after the guarded component is deployed.
Preparation does not activate the Gateway, install/pair an extension, import a
browser session, or enable any agent tool. Tab evidence comes from the actual
native route and verified intended profile, never a guessed browser label.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import sys
from contextlib import ExitStack
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Literal

from adminme.openclaw_topology import service_agent_id
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.workstation_browser import (
    BROWSER_POLICY_FILE,
    BrowserPolicy,
    browser_resource,
    assert_single_extension_profile,
    prepare_browser_gateway,
)
from adminme.workstation_inventory import (
    INVENTORY_FILE,
    IdentityInventory,
    read_private_document,
)
from adminme.workstation_source_policy import BrowserSourceProvision, PROVISION_FILE
from bootstrap.contracts import DeploymentPaths, atomic_write, load_versions
from bootstrap.activation_transaction import (
    ActivationJournalError,
    _assert_directory_coordinate,
    _open_private_directory,
    _write_private_at,
)
from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    DeploymentSnapshot,
    _canonical,
    _digest_domain,
    _json_no_duplicates,
    _read_private_file,
    capture_deployment_snapshot,
    deployment_operation_lock,
)
from bootstrap.source_identity import (
    SourceIdentityError,
    _run_git,
    reviewed_candidate_commit,
    verify_release_source_identity,
)


READINESS_TRANSITION = "browser-readiness-reconciliation-v1"
READINESS_JOURNAL = "browser-readiness-transition.json"
PREFERENCE_TRANSITION = "native-preference-readiness-reconciliation-v1"
NativeComponent = Literal["browser", "computer-use"]
COMPUTER_USE_TRANSITION = "computer-use-readiness-reconciliation-v1"
COMPUTER_USE_CONFIG: dict[str, Any] = {
    "enabled": True,
    "autoInstall": True,
    # The reviewed native harness discovers one desktop distribution and binds
    # its executable, signed service and unified MCP runtime together. An
    # explicit legacy marketplace path prevents that supported conversion.
    "strictReadiness": True,
    "healthCheckEnabled": False,
    "autoRepair": False,
}
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_MAX_BROWSER_RECORD_BYTES = 4 * 1024 * 1024


def _transition_contract(
    component: NativeComponent, *, preferences: bool = False
) -> str:
    if preferences:
        if component != "browser":
            raise ValueError("native preferences cannot combine a component transition")
        return PREFERENCE_TRANSITION
    if component == "browser":
        return READINESS_TRANSITION
    if component == "computer-use":
        return COMPUTER_USE_TRANSITION
    raise ValueError("only browser or computer-use configuration is supported")


def _computer_use_config(gateway: dict[str, Any]) -> dict[str, Any]:
    try:
        entry = gateway["plugins"]["entries"]["codex"]
        config = entry["config"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "an enabled Codex plugin with explicit account policy is required"
        ) from exc
    if (
        not isinstance(entry, dict)
        or entry.get("enabled") is not True
        or not isinstance(config, dict)
        or not isinstance(config.get("codexPlugins"), dict)
        or config["codexPlugins"].get("enabled") is not False
    ):
        raise ValueError("Computer Use must preserve disabled ambient account plugins")
    return config


def prepare_computer_use_patch(
    snapshot: dict[str, Any], *, disable: bool = False
) -> dict[str, Any]:
    """Add the one reviewed official plugin object, or remove that exact object.

    This does not install a plugin, grant OS permission, or change app-server
    sandbox/execution policy. A different existing object needs separate review.
    Native config.patch uses JSON merge-patch null to remove the field.
    """
    if (
        snapshot.get("valid") is not True
        or not isinstance(snapshot.get("hash"), str)
        or _HEX_64.fullmatch(snapshot["hash"]) is None
        or not isinstance(snapshot.get("config"), dict)
    ):
        raise ValueError("a fresh valid native config.get snapshot is required")
    original = snapshot["config"]
    meta = original.get("meta", {})
    # The exact pin stamps these fields on every config.patch write. Refuse
    # before generating a patch if that would also migrate unrelated metadata
    # or the legacy model allowlist; reconciliation never blesses normalization.
    if (
        not isinstance(meta, dict)
        or meta.get("lastTouchedVersion") != load_versions()["openclaw"]["version"]
        or not isinstance(meta.get("migrations"), dict)
        or meta["migrations"].get("modelPolicyAllowlist") is not True
        or meta["migrations"].get("utilityModelSeparation") is not True
    ):
        raise ValueError("Computer Use requires already-current native write metadata")
    config = _computer_use_config(original)
    if disable:
        if _canonical(config.get("computerUse")) != _canonical(COMPUTER_USE_CONFIG):
            raise ValueError("disable requires the exact reviewed Computer Use object")
        value = None
    else:
        if "computerUse" in config:
            raise ValueError("Computer Use addition requires an absent field")
        value = copy.deepcopy(COMPUTER_USE_CONFIG)
    return {
        "baseHash": snapshot["hash"],
        "raw": json.dumps(
            {"plugins": {"entries": {"codex": {"config": {"computerUse": value}}}}},
            separators=(",", ":"),
        ),
    }


def _computer_use_delta(
    previous: dict[str, Any], patch: dict[str, Any]
) -> dict[str, Any]:
    """Validate one closed patch; do not normalize any unrelated gateway field."""
    for disable in (False, True):
        try:
            prepared = prepare_computer_use_patch(
                {"valid": True, "hash": "0" * 64, "config": previous},
                disable=disable,
            )
        except ValueError:
            continue
        if _canonical(patch) != _canonical(json.loads(prepared["raw"])):
            continue
        result = copy.deepcopy(previous)
        config = _computer_use_config(result)
        if disable:
            del config["computerUse"]
        else:
            config["computerUse"] = copy.deepcopy(COMPUTER_USE_CONFIG)
        return result
    raise ValueError("native delta is not the exact reviewed Computer Use preparation")


def _browser_require_settled(paths: DeploymentPaths) -> None:
    for name in (
        "activation/active.json",
        "cos-channel-operation/active.json",
        "disposable-test-reset-active.json",
    ):
        if os.path.lexists(paths.app_dir / name):
            raise ValueError("recover the pending deployment operation first")


def _browser_authority(reference: str) -> str:
    if (
        not 1 <= len(reference) <= 240
        or reference.strip() != reference
        or any(ord(character) < 32 or ord(character) == 127 for character in reference)
    ):
        raise ValueError(
            "an exact bounded existing authorization reference is required"
        )
    return reference


def _browser_private(path: Path) -> bytes:
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("browser evidence must use an exact private path")
    descriptor = _open_private_directory(path.parent)
    try:
        payload = _read_private_file(path, maximum_size=_MAX_BROWSER_RECORD_BYTES)[0]
        _assert_directory_coordinate(descriptor, path.parent)
        return payload
    finally:
        os.close(descriptor)


def _browser_source_selection(paths: DeploymentPaths) -> tuple[Path, dict[str, Any]]:
    """A protected successor controller may reconcile its installed predecessor."""
    repository = Path(__file__).resolve().parents[1]
    controller = reviewed_candidate_commit(repository)
    main = _run_git(repository, ["rev-parse", "refs/remotes/origin/main"]).stdout
    pointer = _json_no_duplicates(
        _browser_private(paths.app_dir / "current-release.json"),
        label="release pointer",
    )
    commit = pointer.get("commit")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("installed release identity is invalid")
    release = paths.app_dir / "releases" / commit
    if (
        main.decode("ascii").strip() != controller
        or pointer.get("path") != str(release)
        or Path(sys.prefix).resolve() != (release / ".venv").resolve()
    ):
        raise ValueError("use clean protected-main controller and selected Python")
    proof = verify_release_source_identity(
        release,
        repository=repository,
        commit=commit,
        allow_legacy_tree=False,
        require_tracked_tree=True,
        retained_by_commit=controller,
    )
    return release, {"controller_commit": controller, "installed_source": proof}


def _browser_binding(snapshot: DeploymentSnapshot) -> dict[str, str]:
    return {
        "release_commit": snapshot.predecessor_commit,
        "instance_fingerprint": snapshot.instance_fingerprint,
        "host_fingerprint": snapshot.host_fingerprint,
        "configuration_fingerprint": snapshot.configuration_fingerprint,
    }


def _merge_browser_patch(
    previous: dict[str, Any], patch: dict[str, Any]
) -> dict[str, Any]:
    """Apply only an already validated native browser patch (including id arrays)."""
    result = copy.deepcopy(previous)
    for key, value in patch.items():
        if isinstance(value, dict):
            result[key] = _merge_browser_patch(result.get(key, {}), value)
        elif key == "list" and isinstance(value, list):
            rows = result[key]
            for addition in value:
                matches = [
                    index
                    for index, row in enumerate(rows)
                    if row.get("id") == addition["id"]
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "native browser patch has ambiguous agent identity"
                    )
                rows[matches[0]] = _merge_browser_patch(rows[matches[0]], addition)
        else:
            result[key] = copy.deepcopy(value)
    return result


# These are already-present native preferences, not agent/tool/provider grants.
# Only adding these exact typed values is supported by this reconciliation.
_NATIVE_PREFERENCE_ADDITIONS: tuple[tuple[tuple[str, ...], Any], ...] = (
    (("agents", "defaults", "thinkingDefault"), "high"),
    (("logging", "audit", "messages"), "direct"),
    (("skills", "install", "nodeManager"), "npm"),
    (("tools", "loopDetection", "enabled"), True),
    (("tools", "swarm", "enabled"), True),
    (("tools", "toolSearch"), {"enabled": True, "mode": "directory"}),
)


def _require_preference_additions(
    previous: dict[str, Any], current: dict[str, Any], patch: dict[str, Any]
) -> None:
    admitted: dict[str, Any] = {}
    expected = copy.deepcopy(previous)
    for coordinate, value in _NATIVE_PREFERENCE_ADDITIONS:
        proposed: Any = patch
        for name in coordinate:
            if not isinstance(proposed, dict) or name not in proposed:
                break
            proposed = proposed[name]
        else:
            if _canonical(proposed) != _canonical(value):
                raise ValueError("native preference value is not reviewed")
            before = previous
            for name in coordinate[:-1]:
                if name not in before:
                    before = {}
                    break
                if not isinstance(before[name], dict):
                    raise ValueError("native preference parent is not an object")
                before = before[name]
            if coordinate[-1] in before:
                raise ValueError("native preferences may only add absent values")
            for document in (admitted, expected):
                target = document
                for name in coordinate[:-1]:
                    target = target.setdefault(name, {})
                target[coordinate[-1]] = copy.deepcopy(value)
    if (
        not admitted
        or _canonical(admitted) != _canonical(patch)
        or _canonical(expected) != _canonical(current)
    ):
        raise ValueError("native delta is not exact reviewed preference additions")


def _native_reconciliation_plan(
    paths: DeploymentPaths,
    *,
    previous_gateway: bytes,
    approved_patch: bytes,
    approved_patch_sha256: str,
    authorization_reference: str,
    native_snapshot: dict[str, Any],
    previous_readiness: bytes,
    preferences: bool = False,
    component: NativeComponent = "browser",
) -> tuple[dict[str, Any], bytes]:
    transition = _transition_contract(component, preferences=preferences)
    _browser_require_settled(paths)
    authorization_reference = _browser_authority(authorization_reference)
    release, source = _browser_source_selection(paths)
    gateway_path = paths.instance_dir / "openclaw/runtime/openclaw.json"
    current_gateway = _browser_private(gateway_path)
    previous_hash = hashlib.sha256(previous_gateway).hexdigest()
    current_hash = hashlib.sha256(current_gateway).hexdigest()
    if (
        _HEX_64.fullmatch(approved_patch_sha256) is None
        or hashlib.sha256(approved_patch).hexdigest() != approved_patch_sha256
        or native_snapshot.get("valid") is not True
        or native_snapshot.get("hash") != current_hash
    ):
        raise ValueError("approved browser patch or current native hash differs")
    approved = _json_no_duplicates(approved_patch, label="approved native patch")
    if (
        set(approved) != {"baseHash", "raw"}
        or approved["baseHash"] != previous_hash
        or not isinstance(approved["raw"], str)
    ):
        raise ValueError(
            "approved native patch does not bind the authentic prior raw config"
        )
    previous = _json_no_duplicates(previous_gateway, label="prior native config")
    current = _json_no_duplicates(current_gateway, label="current native config")
    patch = _json_no_duplicates(approved["raw"].encode(), label="native browser delta")
    if preferences:
        _require_preference_additions(previous, current, patch)
    elif component == "computer-use":
        expected = _computer_use_delta(previous, patch)
        if _canonical(expected) != _canonical(current):
            raise ValueError(
                "native delta changes unrelated Computer Use configuration"
            )
    else:
        snapshot = {"valid": True, "hash": previous_hash, "config": previous}
        candidates = []
        for disable in (False, True):
            try:
                prepared = prepare_native_patch(
                    snapshot, paths, release, disable=disable
                )
            except (ValueError, KeyError, TypeError):
                continue
            candidate = _json_no_duplicates(
                prepared["raw"].encode(), label="prepared delta"
            )
            candidates.append(candidate)
            if "agents" in candidate:
                # An earlier transport-only preparation remains valid after a route
                # is provisioned; it cannot acquire the newly admissible tool grant.
                candidates.append(
                    {key: value for key, value in candidate.items() if key != "agents"}
                )
        if patch not in candidates or _merge_browser_patch(previous, patch) != current:
            raise ValueError(
                "native delta is not the exact reviewed browser preparation"
            )
    before = capture_deployment_snapshot(
        paths, gateway_payload_override=previous_gateway
    )
    after = capture_deployment_snapshot(paths, gateway_payload_override=current_gateway)
    if {
        key: value
        for key, value in before.public().items()
        if key != "configuration_fingerprint"
    } != {
        key: value
        for key, value in after.public().items()
        if key != "configuration_fingerprint"
    }:
        raise ValueError("deployment identity changed during browser reconstruction")
    evidence = _json_no_duplicates(previous_readiness, label="original readiness")
    if evidence.get("contract") != "deployment-readiness-v1" or evidence.get(
        "deployment_binding"
    ) != _browser_binding(before):
        raise ValueError("authentic prior complete readiness binding does not match")
    updated = copy.deepcopy(evidence)
    updated["deployment_binding"]["configuration_fingerprint"] = (
        after.configuration_fingerprint
    )
    next_readiness = _canonical(updated) + b"\n"
    plan = {
        "contract": transition,
        "source": source,
        "previous_snapshot": before.public(),
        "next_snapshot": after.public(),
        "previous_gateway_sha256": previous_hash,
        "current_gateway_sha256": current_hash,
        "approved_patch_sha256": approved_patch_sha256,
        "authorization_reference": authorization_reference,
        "previous_readiness_base64": base64.b64encode(previous_readiness).decode(
            "ascii"
        ),
        "next_readiness_sha256": hashlib.sha256(next_readiness).hexdigest(),
    }
    return plan, next_readiness


def _reconcile_readiness(
    paths: DeploymentPaths,
    *,
    previous_gateway_path: Path,
    approved_patch_path: Path,
    approved_patch_sha256: str,
    authorization_reference: str,
    native_snapshot_path: Path,
    plan_sha256: str | None = None,
    apply: bool = False,
    preferences: bool = False,
    component: NativeComponent = "browser",
) -> dict[str, Any]:
    """Publish a closed native transition under the same recovery fence.

    Native configuration is never written. Applying requires the exact preflight
    digest. Repeating the same command completes an interrupted publication or
    verifies its immutable receipt, without refreshing any acceptance evidence.
    """
    if (apply and plan_sha256 is None) or (
        plan_sha256 is not None and _HEX_64.fullmatch(plan_sha256) is None
    ):
        raise ValueError(
            "applying browser reconciliation requires its exact preflight hash"
        )
    transition = _transition_contract(component, preferences=preferences)
    prefix = "native-preference-readiness" if preferences else f"{component}-readiness"
    with deployment_operation_lock(paths), ExitStack() as stack:
        for directory in (
            paths.root,
            paths.instance_dir,
            paths.instance_dir / "openclaw",
        ):
            descriptor = _open_private_directory(directory)
            stack.callback(os.close, descriptor)
        app = _open_private_directory(paths.app_dir)
        config = _open_private_directory(paths.instance_dir / "config")
        stack.callback(os.close, app)
        stack.callback(os.close, config)
        previous_gateway = _browser_private(previous_gateway_path)
        approved_patch = _browser_private(approved_patch_path)
        native_snapshot = _json_no_duplicates(
            _browser_private(native_snapshot_path), label="current native snapshot"
        )
        readiness_path = paths.instance_dir / "config/deployment-readiness.json"
        current_readiness = _browser_private(readiness_path)
        active_path = paths.app_dir / READINESS_JOURNAL
        receipt_path = (
            paths.app_dir / f"{prefix}-{plan_sha256}.json" if plan_sha256 else None
        )
        completed = receipt_path is not None and os.path.lexists(receipt_path)
        retained: dict[str, Any] | None = None
        for path in (active_path, receipt_path):
            if path is not None and os.path.lexists(path):
                value = _json_no_duplicates(
                    _browser_private(path), label="browser reconciliation journal"
                )
                if (
                    set(value) != {"plan", "plan_sha256"}
                    or not isinstance(value["plan"], dict)
                    or value["plan"].get("contract") != transition
                    or value["plan_sha256"] != _digest_domain(transition, value["plan"])
                ):
                    raise ValueError("browser reconciliation journal is invalid")
                if retained is not None and retained != value:
                    raise ValueError("browser reconciliation journals disagree")
                retained = value
        if retained is not None:
            if plan_sha256 != retained["plan_sha256"]:
                raise ValueError("resume only the exact pending browser reconciliation")
            try:
                previous_readiness = base64.b64decode(
                    retained["plan"]["previous_readiness_base64"], validate=True
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError("original readiness evidence is invalid") from exc
        else:
            previous_readiness = current_readiness
        plan, next_readiness = _native_reconciliation_plan(
            paths,
            previous_gateway=previous_gateway,
            approved_patch=approved_patch,
            approved_patch_sha256=approved_patch_sha256,
            native_snapshot=native_snapshot,
            authorization_reference=authorization_reference,
            previous_readiness=previous_readiness,
            preferences=preferences,
            component=component,
        )
        digest = _digest_domain(transition, plan)
        record = {"plan": plan, "plan_sha256": digest}
        encoded = _canonical(record) + b"\n"
        if len(encoded) > _MAX_BROWSER_RECORD_BYTES:
            raise ValueError(
                "browser reconciliation exceeds its recoverable journal boundary"
            )
        if (plan_sha256 is not None and plan_sha256 != digest) or (
            retained is not None and retained != record
        ):
            raise ValueError(
                "browser reconciliation source, authority or deployment changed"
            )
        if current_readiness not in (previous_readiness, next_readiness):
            raise ValueError(
                "readiness changed independently of browser reconciliation"
            )
        if completed and current_readiness != next_readiness:
            raise ValueError(
                "completed reconciliation cannot replay an earlier readiness state"
            )
        if apply:
            receipt_path = paths.app_dir / f"{prefix}-{digest}.json"
            if not os.path.lexists(active_path) and not os.path.lexists(receipt_path):
                _write_private_at(app, READINESS_JOURNAL, encoded, replace=False)
            # Recheck the entire binding immediately before the single data write.
            check, _ = _native_reconciliation_plan(
                paths,
                previous_gateway=previous_gateway,
                approved_patch=approved_patch,
                approved_patch_sha256=approved_patch_sha256,
                native_snapshot=native_snapshot,
                authorization_reference=authorization_reference,
                previous_readiness=previous_readiness,
                preferences=preferences,
                component=component,
            )
            if check != plan or _browser_private(readiness_path) != current_readiness:
                raise ValueError("browser reconciliation changed before publication")
            _assert_directory_coordinate(app, paths.app_dir)
            _assert_directory_coordinate(config, readiness_path.parent)
            if current_readiness != next_readiness:
                _write_private_at(
                    config, readiness_path.name, next_readiness, replace=True
                )
            check, _ = _native_reconciliation_plan(
                paths,
                previous_gateway=previous_gateway,
                approved_patch=approved_patch,
                approved_patch_sha256=approved_patch_sha256,
                native_snapshot=native_snapshot,
                authorization_reference=authorization_reference,
                previous_readiness=previous_readiness,
                preferences=preferences,
                component=component,
            )
            if check != plan or _browser_private(readiness_path) != next_readiness:
                raise ValueError(
                    "browser reconciliation changed during publication; recover exact plan"
                )
            if not os.path.lexists(receipt_path):
                _write_private_at(app, receipt_path.name, encoded, replace=False)
            if os.path.lexists(active_path):
                os.unlink(READINESS_JOURNAL, dir_fd=app)
                os.fsync(app)
        return {
            "state": f"{prefix.replace('-', '_')}_{'reconciled' if apply else 'preflight'}",
            "plan_sha256": digest,
            "installed_commit": plan["previous_snapshot"]["predecessor_commit"],
            "controller_commit": plan["source"]["controller_commit"],
            "previous_gateway_sha256": plan["previous_gateway_sha256"],
            "current_gateway_sha256": plan["current_gateway_sha256"],
            "approved_patch_sha256": approved_patch_sha256,
            "provider_effect_count": 0,
            "native_config_written": False,
            "acceptance_evidence_refreshed": False,
        }


def reconcile_browser_readiness(
    paths: DeploymentPaths,
    *,
    previous_gateway_path: Path,
    approved_patch_path: Path,
    approved_patch_sha256: str,
    authorization_reference: str,
    native_snapshot_path: Path,
    plan_sha256: str | None = None,
    apply: bool = False,
    component: NativeComponent = "browser",
) -> dict[str, Any]:
    """Reconcile only the exact approved browser or Computer Use preparation."""
    return _reconcile_readiness(
        paths,
        previous_gateway_path=previous_gateway_path,
        approved_patch_path=approved_patch_path,
        approved_patch_sha256=approved_patch_sha256,
        authorization_reference=authorization_reference,
        native_snapshot_path=native_snapshot_path,
        plan_sha256=plan_sha256,
        apply=apply,
        preferences=False,
        component=component,
    )


def reconcile_native_preferences_readiness(
    paths: DeploymentPaths,
    *,
    previous_gateway_path: Path,
    approved_patch_path: Path,
    approved_patch_sha256: str,
    authorization_reference: str,
    native_snapshot_path: Path,
    plan_sha256: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Retain only the closed already-present native preference additions."""
    return _reconcile_readiness(
        paths,
        previous_gateway_path=previous_gateway_path,
        approved_patch_path=approved_patch_path,
        approved_patch_sha256=approved_patch_sha256,
        authorization_reference=authorization_reference,
        native_snapshot_path=native_snapshot_path,
        plan_sha256=plan_sha256,
        apply=apply,
        preferences=True,
    )


def prepare_config(previous: Path, output: Path) -> dict[str, Any]:
    """Write a new private candidate config; never edit active configuration."""
    prepared = prepare_browser_gateway(read_private_document(previous))
    descriptor = os.open(
        output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(prepared, handle, sort_keys=True)
        handle.write("\n")
    return {
        "state": "prepared_not_activated",
        "agent_tools_changed": False,
        "session_imported": False,
        "provider_effect_count": 0,
    }


def provision(
    config_dir: Path, document: dict[str, Any], *, expected_revision: int
) -> dict[str, Any]:
    policy = BrowserPolicy.model_validate(document)
    if (
        config_dir.is_symlink()
        or not config_dir.is_dir()
        or config_dir.resolve() != config_dir
    ):
        raise ValueError("existing private configuration directory is required")
    metadata = config_dir.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError("browser configuration directory must be private")
    import fcntl

    descriptor = os.open(config_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        destination = config_dir / BROWSER_POLICY_FILE
        try:
            current = BrowserPolicy.model_validate(
                read_private_document(destination)
            ).revision
        except FileNotFoundError:
            current = 0
        if expected_revision != current or policy.revision != current + 1:
            raise ValueError("browser policy revision changed; review current policy")
        # Disabling remains possible when account evidence has expired or vanished.
        if policy.enabled:
            inventory = IdentityInventory.model_validate(
                read_private_document(config_dir / INVENTORY_FILE)
            )
            if not policy.resources:
                raise ValueError("enabled browser policy needs a verified native tab")
            for resource in policy.resources:
                browser_resource(policy, inventory, resource.resource_id)
        atomic_write(
            destination, (policy.model_dump_json(indent=2) + "\n").encode(), mode=0o600
        )
    finally:
        os.close(descriptor)
    return {
        "state": "policy_provisioned",
        "revision": policy.revision,
        "enabled": policy.enabled,
        "resource_count": len(policy.resources),
        "native_tool_execution_verified": False,
        "provider_effect_count": 0,
    }


def native_service_tools(
    gateway: dict[str, Any], paths: DeploymentPaths, release: Path
) -> list[str]:
    """An explicit private route enables only browser, under installed guards.

    Missing, disabled, stale or invalid setup removes native access without
    blocking unrelated deployment. This is availability, not execution proof.
    """
    try:
        manifest_path = release / "openclaw" / "administrateme" / "openclaw.plugin.json"
        if manifest_path.is_symlink():
            return []
        manifest = json.loads(manifest_path.read_text("utf-8"))
        if set(manifest.get("contracts", {}).get("agentToolResultMiddleware", [])) != {
            "openclaw",
            "codex",
        }:
            return []
        plugins = gateway.get("plugins", {})
        entry = plugins.get("entries", {}).get("administrateme", {})
        if (
            plugins.get("enabled") is False
            or entry.get("enabled") is not True
            or entry.get("hooks", {}).get("allowPromptInjection") is not True
            or entry.get("hooks", {}).get("allowConversationAccess") is not True
            or plugins.get("entries", {}).get("browser", {}).get("enabled") is False
            or (
                isinstance(plugins.get("allow"), list)
                and not {"administrateme", "browser"}.issubset(plugins["allow"])
            )
        ):
            return []
        directory = paths.instance_dir / "config"
        policy = BrowserPolicy.model_validate(
            read_private_document(directory / BROWSER_POLICY_FILE)
        )
        if not policy.enabled or not policy.resources:
            return []
        inventory = IdentityInventory.model_validate(
            read_private_document(directory / INVENTORY_FILE)
        )
        assert_single_extension_profile(gateway)
        if gateway["browser"].get("evaluateEnabled") is not True:
            return []
        for resource in policy.resources:
            browser_resource(policy, inventory, resource.resource_id)
        return ["browser"]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def prepare_native_patch(
    snapshot: dict[str, Any],
    paths: DeploymentPaths,
    release: Path,
    *,
    disable: bool = False,
) -> dict[str, Any]:
    """Produce exact native config.patch params, never edit active configuration.

    The operator obtains a fresh config.get snapshot and applies this artifact
    through OpenClaw's authenticated, hash-checked config.patch/restart path.
    Only browser settings and an additive service browser tool can change.
    """
    if (
        snapshot.get("valid") is not True
        or not isinstance(snapshot.get("hash"), str)
        or len(snapshot["hash"]) != 64
    ):
        raise ValueError("a fresh valid native config.get snapshot is required")
    original = snapshot.get("config")
    if not isinstance(original, dict):
        raise ValueError("native configuration snapshot is unavailable")
    manifest = json.loads(
        (release / "openclaw" / "administrateme" / "openclaw.plugin.json").read_text(
            "utf-8"
        )
    )
    if set(manifest.get("contracts", {}).get("agentToolResultMiddleware", [])) != {
        "openclaw",
        "codex",
    }:
        raise ValueError("the deployed release must contain the reviewed browser guard")
    if disable:
        patch: dict[str, Any] = {"browser": {"enabled": False}}
    else:
        entry = original.get("plugins", {}).get("entries", {}).get("administrateme", {})
        if any(
            entry.get("hooks", {}).get(name) is not True
            for name in ("allowPromptInjection", "allowConversationAccess")
        ):
            raise ValueError("the deployed finite worker policy hook must be enabled")
        prepared = prepare_browser_gateway(original)
        patch = {
            "browser": prepared["browser"],
            "plugins": {"entries": {"browser": {"enabled": True}}},
        }
        if "allow" in prepared["plugins"]:
            patch["plugins"]["allow"] = prepared["plugins"]["allow"]
        tools = native_service_tools(prepared, paths, release)
        if tools:
            agents = original.get("agents")
            if not isinstance(agents, dict) or ("list" in agents) == (
                "entries" in agents
            ):
                raise ValueError(
                    "one unambiguous native agent representation is required"
                )
            agent_id = service_agent_id(COS_SERVICE_PRINCIPAL_ID)
            if "entries" in agents:
                entries = agents["entries"]
                if not isinstance(entries, dict) or any(
                    not isinstance(row, dict) for row in entries.values()
                ):
                    raise ValueError("native keyed agent entries are malformed")
                matches = [
                    row
                    for key, row in entries.items()
                    if key == agent_id or row.get("id") == agent_id
                ]
                if (
                    len(matches) != 1
                    or agent_id not in entries
                    or entries[agent_id].get("id", agent_id) != agent_id
                ):
                    raise ValueError(
                        "one current CoS worker must be deployed before browser admission"
                    )
            else:
                rows = agents["list"]
                if not isinstance(rows, list) or any(
                    not isinstance(row, dict) for row in rows
                ):
                    raise ValueError("native agent list is malformed")
                matches = [row for row in rows if row.get("id") == agent_id]
                if len(matches) != 1:
                    raise ValueError(
                        "one current CoS worker must be deployed before browser admission"
                    )
            current_tools = matches[0].get("tools")
            allowed = (
                current_tools.get("allow") if isinstance(current_tools, dict) else None
            )
            if not isinstance(allowed, list) or any(
                not isinstance(name, str) for name in allowed
            ):
                raise ValueError(
                    "the CoS worker needs an explicit existing tool allowlist"
                )
            additive = {"tools": {"allow": list(dict.fromkeys([*allowed, *tools]))}}
            patch["agents"] = (
                {"entries": {agent_id: additive}}
                if "entries" in agents
                else {"list": [{"id": agent_id, **additive}]}
            )
    return {
        "baseHash": snapshot["hash"],
        "raw": json.dumps(patch, separators=(",", ":")),
    }


def write_patch(
    snapshot_path: Path,
    paths: DeploymentPaths,
    output: Path,
    *,
    previous_config_output: Path,
    disable: bool,
    component: NativeComponent = "browser",
) -> dict[str, Any]:
    """Retain exact reconciliation inputs before supported native config.patch.

    Preparation neither establishes owner consent nor applies the native patch.
    The operator supplies the existing approval and resulting fresh native hash
    to reconcile-readiness after the exact prepared patch is applied.
    """
    _transition_contract(component)
    with deployment_operation_lock(paths), ExitStack() as stack:
        _browser_require_settled(paths)
        release, source = _browser_source_selection(paths)
        snapshot = capture_deployment_snapshot(paths)
        readiness = _browser_private(
            paths.instance_dir / "config/deployment-readiness.json"
        )
        evidence = _json_no_duplicates(readiness, label="current readiness")
        if evidence.get("contract") != "deployment-readiness-v1" or evidence.get(
            "deployment_binding"
        ) != _browser_binding(snapshot):
            raise ValueError("browser preparation requires current bound readiness")
        native = _json_no_duplicates(
            _browser_private(snapshot_path), label="native snapshot"
        )
        prior = _browser_private(paths.instance_dir / "openclaw/runtime/openclaw.json")
        prior_hash = hashlib.sha256(prior).hexdigest()
        if native.get("valid") is not True or native.get("hash") != prior_hash:
            raise ValueError("native snapshot does not bind current raw configuration")
        raw_snapshot = {
            "valid": True,
            "hash": prior_hash,
            "config": _json_no_duplicates(prior, label="native configuration"),
        }
        document = (
            prepare_computer_use_patch(raw_snapshot, disable=disable)
            if component == "computer-use"
            else prepare_native_patch(raw_snapshot, paths, release, disable=disable)
        )
        encoded = _canonical(document) + b"\n"
        destinations = []
        for destination in (previous_config_output, output):
            if not destination.is_absolute() or destination.resolve() != destination:
                raise ValueError("browser artifacts require exact private output paths")
            descriptor = _open_private_directory(destination.parent)
            stack.callback(os.close, descriptor)
            if os.path.lexists(destination):
                raise FileExistsError("browser preparation output already exists")
            destinations.append((destination, descriptor))
        if previous_config_output == output:
            raise ValueError(
                "browser patch and prior configuration need separate outputs"
            )
        if (
            capture_deployment_snapshot(paths) != snapshot
            or _browser_private(paths.instance_dir / "config/deployment-readiness.json")
            != readiness
        ):
            raise ValueError("deployment changed during browser preparation")
        for (destination, descriptor), payload in zip(
            destinations, (prior, encoded), strict=True
        ):
            _assert_directory_coordinate(descriptor, destination.parent)
            _write_private_at(descriptor, destination.name, payload, replace=False)
    return {
        "state": "native_patch_prepared_not_applied",
        "disable": disable,
        "previous_gateway_sha256": prior_hash,
        "approved_patch_sha256": hashlib.sha256(encoded).hexdigest(),
        "readiness_sha256": hashlib.sha256(readiness).hexdigest(),
        "controller_commit": source["controller_commit"],
        "session_imported": False,
        "provider_effect_count": 0,
    }


def stage_source_request(config_dir: Path, document: dict[str, Any]) -> dict[str, Any]:
    staged = BrowserSourceProvision.model_validate(document)
    if not staged.current(datetime.now(timezone.utc)):
        raise ValueError("source request must expire within the next hour")
    if (
        config_dir.is_symlink()
        or not config_dir.is_dir()
        or config_dir.resolve() != config_dir
    ):
        raise ValueError("existing private configuration directory is required")
    metadata = config_dir.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError("configuration directory must be private")
    destination = config_dir / PROVISION_FILE
    if destination.is_symlink():
        raise ValueError("source request cannot be a symlink")
    atomic_write(
        destination, (staged.model_dump_json(indent=2) + "\n").encode(), mode=0o600
    )
    return {
        "state": "source_policy_request_staged_not_applied",
        "request_id": staged.request_id,
        "provider_effect_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema")
    prepare = commands.add_parser("prepare-native-config")
    prepare.add_argument("--previous-config", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    patch = commands.add_parser("prepare-native-patch")
    patch.add_argument("--instance-root", type=Path, required=True)
    patch.add_argument("--snapshot", type=Path, required=True)
    patch.add_argument("--output", type=Path, required=True)
    patch.add_argument("--previous-config-output", type=Path, required=True)
    patch.add_argument("--disable", action="store_true")
    patch.add_argument(
        "--component", choices=("browser", "computer-use"), default="browser"
    )
    for command in ("reconcile-readiness", "reconcile-native-preferences"):
        reconcile = commands.add_parser(command)
        reconcile.add_argument("--instance-root", type=Path, required=True)
        reconcile.add_argument("--previous-native-config", type=Path, required=True)
        reconcile.add_argument("--approved-patch", type=Path, required=True)
        reconcile.add_argument("--approved-patch-sha256", required=True)
        reconcile.add_argument("--authorization-reference", required=True)
        reconcile.add_argument("--current-snapshot", type=Path, required=True)
        reconcile.add_argument("--plan-sha256")
        reconcile.add_argument("--apply", action="store_true")
        if command == "reconcile-readiness":
            reconcile.add_argument(
                "--component", choices=("browser", "computer-use"), default="browser"
            )
    stage = commands.add_parser("stage-source-policy")
    stage.add_argument("--instance-root", type=Path, required=True)
    stage.add_argument("--input", type=Path, required=True)
    configure = commands.add_parser("provision-policy")
    configure.add_argument("--instance-root", type=Path, required=True)
    configure.add_argument("--input", type=Path, required=True)
    configure.add_argument("--expected-revision", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.command == "schema":
            print(json.dumps(BrowserPolicy.model_json_schema(), indent=2))
            return 0
        if args.command == "prepare-native-config":
            result = prepare_config(args.previous_config, args.output)
        elif args.command == "prepare-native-patch":
            result = write_patch(
                args.snapshot,
                DeploymentPaths.from_root(args.instance_root),
                args.output,
                previous_config_output=args.previous_config_output,
                disable=args.disable,
                component=args.component,
            )
        elif args.command in ("reconcile-readiness", "reconcile-native-preferences"):
            reconcile_operation = (
                reconcile_native_preferences_readiness
                if args.command == "reconcile-native-preferences"
                else reconcile_browser_readiness
            )
            component_options = (
                {"component": args.component}
                if args.command == "reconcile-readiness"
                else {}
            )
            result = reconcile_operation(
                DeploymentPaths.from_root(args.instance_root),
                previous_gateway_path=args.previous_native_config,
                approved_patch_path=args.approved_patch,
                approved_patch_sha256=args.approved_patch_sha256,
                authorization_reference=args.authorization_reference,
                native_snapshot_path=args.current_snapshot,
                plan_sha256=args.plan_sha256,
                apply=args.apply,
                **component_options,
            )
        elif args.command == "stage-source-policy":
            result = stage_source_request(
                DeploymentPaths.from_root(args.instance_root).instance_dir / "config",
                read_private_document(args.input),
            )
        else:
            result = provision(
                DeploymentPaths.from_root(args.instance_root).instance_dir / "config",
                read_private_document(args.input),
                expected_revision=args.expected_revision,
            )
    except (
        OSError,
        ValueError,
        ActivationJournalError,
        CandidateReadinessError,
        SourceIdentityError,
    ):
        # Neither private policy, credentials nor validation input goes to logs.
        parser.exit(
            1,
            "Browser preparation rejected; review private inputs, permissions and revisions.\n",
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
