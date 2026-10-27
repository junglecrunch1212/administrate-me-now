"""Exact native schema upgrade and stopped-generation recovery.

OpenClaw owns its migrations. This controller only proves their filesystem
coverage, retains one complete stopped generation, and runs native Doctor.
It never edits SQLite schemas or restores AdministrateMe's append-only log.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.prepared_runtime import (
    NativeArtifactInventory,
    native_artifact_inventory,
)

CONTRACT = "administrateme-openclaw-generation-v1"
PREDECESSOR = ("2026.7.2", "ce67ffb70e83e5ebb56ea02e814e577153f84f61")
SUCCESSOR = ("2026.10.8", "dc2523e5a5c7a309517fd7fa834add938231d526")
SCHEMAS = {
    PREDECESSOR[1]: {"state": 6, "agent": 16},
    SUCCESSOR[1]: {"state": 20, "agent": 25},
}
_HEX32 = re.compile(r"[a-f0-9]{32}")
_HEX64 = re.compile(r"[a-f0-9]{64}")


class NativeStateError(RuntimeError):
    pass


def _identity(value: dict[str, Any]) -> tuple[object, object]:
    return value.get("version"), value.get("commit")


def declared_transition(active: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Only this reviewed cross-version migration, never a broad version waiver."""
    return (
        _identity(active) == PREDECESSOR
        and active.get("plugin_api") == PREDECESSOR[0]
        and _identity(incoming) == SUCCESSOR
        and incoming.get("plugin_api") == SUCCESSOR[0]
        and incoming.get("supersedes_commit") == PREDECESSOR[1]
        and incoming.get("state_schema_versions") == SCHEMAS[SUCCESSOR[1]]
        and isinstance(incoming.get("predecessor"), dict)
        and all(
            incoming["predecessor"].get(key) == active.get(key)
            for key in (
                "version",
                "commit",
                "plugin_api",
                "source_archive_url",
                "source_archive_sha256",
                "package_manager",
                "gateway_port",
            )
        )
        and incoming["predecessor"].get("state_schema_versions")
        == SCHEMAS[PREDECESSOR[1]]
    )


def native_source_fingerprint(paths: DeploymentPaths, manifest: dict[str, Any]) -> str:
    """Bind prepared native bytes; no CLI or provider access occurs here."""
    return native_source_inventory(paths, manifest)["artifact_sha256"]


def native_source_inventory(
    paths: DeploymentPaths, manifest: dict[str, Any]
) -> NativeArtifactInventory:
    """Inventory the exact native artifact, retaining its complete tree coverage."""
    commit = manifest.get("commit", "")
    if commit != SUCCESSOR[1]:
        raise NativeStateError("native artifact is outside the reviewed transition")
    source = paths.app_dir / "openclaw" / commit
    _directory(source)
    if source.resolve() != source:
        raise NativeStateError("native source artifact is aliased")
    return native_artifact_inventory(source)


def _coordinate(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_private_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or not 0 < info.st_size <= 4 * 1024 * 1024
            ):
                raise NativeStateError(
                    "native configuration is not a private bounded regular file"
                )
            with os.fdopen(fd, "rb", closefd=False) as stream:
                payload = stream.read(4 * 1024 * 1024 + 1)
            if len(payload) != info.st_size or _coordinate(os.fstat(fd)) != _coordinate(
                info
            ):
                raise NativeStateError("native configuration changed during read")
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise NativeStateError("native configuration must be an object")
            return value, hashlib.sha256(payload).hexdigest()
        finally:
            os.close(fd)
    except (OSError, ValueError) as exc:
        raise NativeStateError(
            "native configuration requires supported JSON read-back"
        ) from exc


def _json(path: Path) -> dict[str, Any]:
    return _read_private_json(path)[0]


def _directory(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        raise NativeStateError(
            "native generation directory ownership or mode is unsafe"
        )


def _covered(value: object, root: Path) -> None:
    if not isinstance(value, str) or not value or "${" in value or "~" in value:
        raise NativeStateError("native mutable path is unresolved")
    candidate = Path(value)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise NativeStateError("native mutable path is not absolute")
    try:
        candidate.resolve().relative_to(root)
    except (ValueError, RuntimeError) as exc:
        raise NativeStateError(
            "native mutable path is outside the retained generation"
        ) from exc


def inspect_coverage(paths: DeploymentPaths) -> dict[str, Any]:
    """Read configuration only; no candidate CLI, DB connection or credential read."""
    root = paths.instance_dir / "openclaw"
    _directory(root)
    if root.resolve() != root:
        raise NativeStateError("native generation root is aliased")
    runtime = root / "runtime"
    _directory(runtime)
    config, config_digest = _read_private_json(runtime / "openclaw.json")

    # The managed installation authors one complete config. Included config and
    # environment-resolved storage require a separately qualified capture plan.
    def inspect(value: object, trail: tuple[str, ...] = ()) -> None:
        if isinstance(value, list):
            for item in value:
                inspect(item, trail)
        elif isinstance(value, dict):
            for key, item in value.items():
                coordinate = (*trail, key)
                if key == "$include":
                    raise NativeStateError(
                        "native included configuration needs explicit recovery coverage"
                    )
                if key in {
                    "workspace",
                    "agentDir",
                    "oauthDir",
                    "stateDir",
                    "configPath",
                    "authDir",
                    "codexHome",
                    "remoteWorkspaceRoot",
                }:
                    if item is not None:
                        _covered(item, root)
                if (
                    key == "store"
                    and trail in {("session",), ("cron",)}
                    and item is not None
                ):
                    if isinstance(item, dict):
                        for location in item.values():
                            _covered(location, root)
                    else:
                        _covered(item, root)
                if (
                    key == "path"
                    and ("secrets" in trail or "store" in trail)
                    and item is not None
                ):
                    _covered(item, root)
                if key == "env" and trail[-1:] == ("appServer",) and item:
                    raise NativeStateError(
                        "custom Codex child environment needs explicit recovery coverage"
                    )
                if key in {
                    "OPENCLAW_HOME",
                    "OPENCLAW_STATE_DIR",
                    "OPENCLAW_CONFIG_PATH",
                    "OPENCLAW_OAUTH_DIR",
                    "OPENCLAW_AGENT_DIR",
                    "PI_CODING_AGENT_DIR",
                    "CODEX_HOME",
                }:
                    raise NativeStateError(
                        "native storage environment override is unsupported"
                    )
                if coordinate[-2:] == ("appServer", "homeScope") and item != "agent":
                    raise NativeStateError(
                        "native user-scoped Codex state is outside this recovery contract"
                    )
                if coordinate[-2:] == ("appServer", "transport") and item != "stdio":
                    raise NativeStateError(
                        "remote Codex state is outside this recovery contract"
                    )
                inspect(item, coordinate)

    inspect(config)
    for key in (
        "OPENCLAW_HOME",
        "OPENCLAW_OAUTH_DIR",
        "OPENCLAW_AGENT_DIR",
        "PI_CODING_AGENT_DIR",
        "CODEX_HOME",
    ):
        if os.environ.get(key):
            raise NativeStateError("ambient native storage override is unsupported")
    for key, expected in (
        ("OPENCLAW_STATE_DIR", runtime),
        ("OPENCLAW_CONFIG_PATH", runtime / "openclaw.json"),
    ):
        if os.environ.get(key) not in (None, "", str(expected)):
            raise NativeStateError(
                "ambient native selection differs from the managed generation"
            )
    # Native global dotenv files can otherwise introduce storage overrides after
    # this check. Managed installations use service environment/Keychain; do not
    # read possible credential contents or let an unqualified dotenv steer migration.
    if (runtime / ".env").exists() or (runtime / ".env").is_symlink():
        raise NativeStateError("native dotenv needs explicit recovery coverage")
    # Per-agent defaults must not fall back to a workspace in the service home.
    agents = config.get("agents", {})
    if not isinstance(agents, dict):
        raise NativeStateError("native agent roster is invalid")
    defaults = agents.get("defaults", {})
    default_workspace = (
        defaults.get("workspace") if isinstance(defaults, dict) else None
    )
    entries = agents.get("entries", agents.get("list"))
    rows = list(entries.values()) if isinstance(entries, dict) else entries
    if not isinstance(rows, list) or not rows:
        raise NativeStateError("native agent roster has no explicit recovery coverage")
    for entry in rows:
        if not isinstance(entry, dict):
            raise NativeStateError("native agent entry is invalid")
        _covered(entry.get("workspace", default_workspace), root)
    _inventory(root, authority=root, paths=paths, hash_content=False)
    return {
        "contract": CONTRACT,
        "config_sha256": config_digest,
        "agent_count": len(rows),
        "content_exposed": False,
    }


def validate_migrated_authority(before_path: Path, after_path: Path) -> None:
    """Allow only pinned roster/owner normalization, never grant mutation by Doctor.

    Upstream dc252 legacy.default-agent-roles materializes the existing default
    member's ambient responsibilities. Normal managed topology reconciliation
    then retains only the originally authorized exact channel/peer bindings.
    """
    from adminme.openclaw_topology import runtime_agent_rows

    before, after = _json(before_path), _json(after_path)
    old_rows, new_rows = runtime_agent_rows(before), runtime_agent_rows(after)
    marked = [row["id"] for row in old_rows if row.get("default") is True]
    if len(marked) > 1:
        raise NativeStateError("native predecessor has ambiguous default ownership")
    owner = marked[0] if marked else None
    old_agents = before.get("agents", {})
    new_agents = after.get("agents", {})
    if "entries" not in new_agents or "list" in new_agents:
        raise NativeStateError("native migration did not produce its canonical roster")

    def canonical(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            row["id"]: {
                key: value for key, value in row.items() if key not in {"id", "default"}
            }
            for row in rows
        }

    if canonical(old_rows) != canonical(new_rows) or any(
        "default" in row for row in new_rows
    ):
        raise NativeStateError(
            "native migration changed agent identity, workspace or policy"
        )
    for key in (
        "channels",
        "tools",
        "approvals",
        "browser",
        "gateway",
        "plugins",
        "session",
    ):
        if before.get(key) != after.get(key):
            raise NativeStateError(
                "native migration changed a retained authorization surface"
            )
    defaults = dict(new_agents.get("defaults", {}))
    old_defaults = old_agents.get("defaults", {})
    for key in ("systemAgent", "sessionStore", "authInheritance", "heartbeat"):
        if key not in old_defaults and key in defaults:
            if owner is None or defaults[key] != {"agentId": owner}:
                raise NativeStateError("native migration changed a retained role owner")
            del defaults[key]
    if defaults != old_defaults:
        raise NativeStateError("native migration changed shared agent policy")
    for key in set(old_agents) | set(new_agents):
        if key not in {"list", "entries", "defaults", "ownership"} and old_agents.get(
            key
        ) != new_agents.get(key):
            raise NativeStateError("native migration changed roster policy")
    if new_agents.get("ownership") not in (old_agents.get("ownership"), "explicit"):
        raise NativeStateError("native migration ownership mode is invalid")
    old_talk, new_talk = before.get("talk"), after.get("talk")
    if old_talk != new_talk:
        expected = {**(old_talk or {}), "agentId": owner}
        if (
            owner is None
            or (old_talk or {}).get("agentId") is not None
            or new_talk != expected
        ):
            raise NativeStateError("native migration changed talk owner")
    old_bindings = before.get("bindings", [])
    new_bindings = after.get("bindings", [])
    if not isinstance(old_bindings, list) or not isinstance(new_bindings, list):
        raise NativeStateError("native migration binding shape is invalid")
    retained = list(new_bindings)
    for binding in old_bindings:
        if binding not in retained:
            raise NativeStateError(
                "native migration removed an exact authorized binding"
            )
        retained.remove(binding)
    channels = before.get("channels", {})
    for binding in retained:
        match = binding.get("match", {}) if isinstance(binding, dict) else {}
        channel = match.get("channel")
        if (
            owner is None
            or binding
            != {"agentId": owner, "match": {"channel": channel, "accountId": "*"}}
            or channel not in channels
            or not isinstance(channels[channel], dict)
            or channels[channel].get("enabled") is False
        ):
            raise NativeStateError("native migration added an unauthorized binding")
    if len(retained) != len({json.dumps(row, sort_keys=True) for row in retained}):
        raise NativeStateError("native migration duplicated a fallback binding")


def _managed_link(
    original: Path, resolved: Path, authority: Path, paths: DeploymentPaths
) -> bool:
    if resolved.is_relative_to(authority):
        return True
    if original.parent != authority / "runtime" / "plugin-skills":
        return False
    # These are immutable selected plugin/source skill artifacts, never arbitrary
    # external workspaces. Codex tmp links are disposable cache excluded below.
    try:
        parts = resolved.relative_to(paths.app_dir).parts
    except ValueError:
        return False
    source_skill = (
        len(parts) >= 5
        and parts[0] == "openclaw"
        and parts[1] in {PREDECESSOR[1], SUCCESSOR[1]}
        and parts[2:4] == ("dist", "extensions")
    )
    release_skill = (
        len(parts) == 6
        and parts[0] == "releases"
        and re.fullmatch(r"[a-f0-9]{40}", parts[1]) is not None
        and parts[2:]
        == ("openclaw", "administrateme", "skills", "administrateme-household")
    )
    if not (source_skill or release_skill) or not resolved.is_dir():
        return False
    for parent in (resolved, *resolved.parents):
        _directory(parent)
        if parent == paths.app_dir:
            return True
    return False


def _file_digest(item: Path, before: os.stat_result) -> str:
    fd = os.open(item, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if _coordinate(os.fstat(fd)) != _coordinate(before):
            raise NativeStateError("native generation entry changed during read")
        digest = hashlib.sha256()
        count = 0
        while chunk := os.read(fd, 1024 * 1024):
            count += len(chunk)
            digest.update(chunk)
        if count != before.st_size or _coordinate(os.fstat(fd)) != _coordinate(before):
            raise NativeStateError("native generation entry changed during read")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _inventory(
    root: Path, *, authority: Path, paths: DeploymentPaths, hash_content: bool = True
) -> list[dict[str, Any]]:
    from bootstrap.backup_restore import _is_generated_openclaw_codex_cache

    _directory(root)
    rows: list[dict[str, Any]] = []
    observed: dict[Path, tuple[int, ...]] = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        _directory(Path(directory))
        for name in sorted([*dirs, *files]):
            item = Path(directory) / name
            relative = item.relative_to(root)
            original = authority / relative
            if _is_generated_openclaw_codex_cache(
                original, state_root=paths.instance_dir
            ):
                if name in dirs:
                    dirs.remove(name)
                continue
            info = item.lstat()
            observed[item] = _coordinate(info)
            if info.st_uid != os.getuid():
                raise NativeStateError("native generation entry ownership is unsafe")
            row: dict[str, Any] = {
                "path": relative.as_posix(),
                "mode": stat.S_IMODE(info.st_mode),
            }
            if stat.S_ISLNK(info.st_mode):
                # Symlink modes commonly report 0777; ownership and the exact
                # resolved immutable target carry their protection instead.
                target = os.readlink(item)
                resolved = (original.parent / target).resolve()
                if not _managed_link(original, resolved, authority, paths):
                    raise NativeStateError(
                        "native generation contains an uncovered symbolic link"
                    )
                row.update(kind="link", target=target)
                if name in dirs:
                    dirs.remove(name)
            elif info.st_mode & 0o022:
                raise NativeStateError("native generation entry is writable by others")
            elif stat.S_ISDIR(info.st_mode):
                row.update(kind="directory")
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                row.update(kind="file", size=info.st_size)
                if hash_content:
                    row["sha256"] = _file_digest(item, info)
            else:
                raise NativeStateError(
                    "native generation contains a live or unsupported filesystem object"
                )
            rows.append(row)
    if hash_content and any(
        _coordinate(path.lstat()) != before for path, before in observed.items()
    ):
        raise NativeStateError("native generation changed while captured")
    return sorted(rows, key=lambda row: row["path"])


def _digest(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _copy(source: Path, target: Path, rows: list[dict[str, Any]]) -> None:
    target.mkdir(mode=0o700)
    directories = [(target, 0o700)]
    for row in rows:
        destination = target / row["path"]
        if row["kind"] == "directory":
            destination.mkdir(mode=0o700)
            directories.append((destination, row["mode"]))
        elif row["kind"] == "link":
            destination.symlink_to(row["target"])
            if stat.S_IMODE(destination.lstat().st_mode) != row["mode"]:
                os.chmod(destination, row["mode"], follow_symlinks=False)
        else:
            incoming = os.open(
                source / row["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            outgoing = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                row["mode"],
            )
            try:
                before = os.fstat(incoming)
                if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid():
                    raise NativeStateError("native copy source changed")
                digest = hashlib.sha256()
                count = 0
                while chunk := os.read(incoming, 1024 * 1024):
                    digest.update(chunk)
                    count += len(chunk)
                    remaining = memoryview(chunk)
                    while remaining:
                        remaining = remaining[os.write(outgoing, remaining) :]
                if (
                    count != row["size"]
                    or digest.hexdigest() != row["sha256"]
                    or _coordinate(os.fstat(incoming)) != _coordinate(before)
                ):
                    raise NativeStateError("native copy source changed")
                os.fchmod(outgoing, row["mode"])
                os.fsync(outgoing)
            finally:
                os.close(incoming)
                os.close(outgoing)
    # Persist nested names before the receipt can durably claim this generation.
    for directory, mode in sorted(
        directories, key=lambda value: len(value[0].parts), reverse=True
    ):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _sync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def capture_generation(paths: DeploymentPaths, transaction_id: str) -> dict[str, Any]:
    """Caller owns stopped services and durable activation intent before entry."""
    if not _HEX32.fullmatch(transaction_id):
        raise NativeStateError("native generation transaction is invalid")
    coverage = inspect_coverage(paths)
    root = paths.instance_dir / "openclaw"
    generation = paths.rollback_dir / f"openclaw-{transaction_id}"
    _directory(paths.rollback_dir)
    generation.mkdir(mode=0o700)
    rows = _inventory(root, authority=root, paths=paths)
    _copy(root, generation / "before", rows)
    digest = _digest(rows)
    if (
        _digest(_inventory(root, authority=root, paths=paths)) != digest
        or _digest(_inventory(generation / "before", authority=root, paths=paths))
        != digest
    ):
        raise NativeStateError("native generation copy did not verify")
    record = {
        "contract": CONTRACT,
        "transaction_id": transaction_id,
        "from_commit": PREDECESSOR[1],
        "to_commit": SUCCESSOR[1],
        "inventory_sha256": digest,
        "file_count": sum(row["kind"] == "file" for row in rows),
        "coverage": coverage,
        "content_exposed": False,
    }
    atomic_write(
        generation / "receipt.json",
        json.dumps(record, sort_keys=True).encode(),
        mode=0o600,
    )
    _sync(generation)
    _sync(paths.rollback_dir)
    return record


def validate_receipt(record: object, transaction_id: str) -> dict[str, Any]:
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "contract",
            "transaction_id",
            "from_commit",
            "to_commit",
            "inventory_sha256",
            "file_count",
            "coverage",
            "content_exposed",
        }
        or record.get("contract") != CONTRACT
        or record.get("transaction_id") != transaction_id
        or not _HEX32.fullmatch(transaction_id)
        or record.get("from_commit") != PREDECESSOR[1]
        or record.get("to_commit") != SUCCESSOR[1]
        or not isinstance(record.get("inventory_sha256"), str)
        or not _HEX64.fullmatch(record["inventory_sha256"])
        or type(record.get("file_count")) is not int
        or record["file_count"] < 1
        or record.get("content_exposed") is not False
    ):
        raise NativeStateError("native generation receipt is invalid")
    coverage = record.get("coverage")
    if (
        not isinstance(coverage, dict)
        or set(coverage)
        != {"contract", "config_sha256", "agent_count", "content_exposed"}
        or coverage.get("contract") != CONTRACT
        or not isinstance(coverage.get("config_sha256"), str)
        or not _HEX64.fullmatch(coverage["config_sha256"])
        or type(coverage.get("agent_count")) is not int
        or coverage["agent_count"] < 1
        or coverage.get("content_exposed") is not False
    ):
        raise NativeStateError("native generation coverage receipt is invalid")
    return record


def restore_generation(
    paths: DeploymentPaths, record: dict[str, Any], transaction_id: str
) -> None:
    """Only uncommitted activation recovery; retain both generations without cleanup."""
    validate_receipt(record, transaction_id)
    generation = paths.rollback_dir / f"openclaw-{transaction_id}"
    _directory(paths.rollback_dir)
    _directory(generation)
    if generation.resolve() != generation:
        raise NativeStateError("native recovery generation is aliased")
    if _json(generation / "receipt.json") != record:
        raise NativeStateError("native generation receipt changed")
    root = paths.instance_dir / "openclaw"
    before = generation / "before"
    rows = _inventory(before, authority=root, paths=paths)
    if _digest(rows) != record["inventory_sha256"]:
        raise NativeStateError("native recovery generation changed")
    restored = generation / "restored.json"
    if restored.exists():
        info = root.lstat()
        if _json(restored) != {"device": info.st_dev, "inode": info.st_ino}:
            raise NativeStateError("restored native generation identity changed")
        _directory(root)
        return
    failed = generation / "failed"
    if failed.exists() or failed.is_symlink():
        _directory(failed)
    if failed.exists() and root.exists():
        if (
            _digest(_inventory(root, authority=root, paths=paths))
            != record["inventory_sha256"]
        ):
            raise NativeStateError(
                "interrupted native restoration has ambiguous active state"
            )
    else:
        stage = Path(tempfile.mkdtemp(prefix="restore-", dir=generation)) / "openclaw"
        _copy(before, stage, rows)
        if (
            _digest(_inventory(stage, authority=root, paths=paths))
            != record["inventory_sha256"]
        ):
            raise NativeStateError("native restoration copy did not verify")
        if not failed.exists():
            _directory(root)
            os.rename(root, failed)
            _sync(root.parent)
            _sync(generation)
        os.rename(stage, root)
        _sync(root.parent)
    info = root.stat()
    atomic_write(
        restored,
        json.dumps({"device": info.st_dev, "inode": info.st_ino}).encode(),
        mode=0o600,
    )
    _sync(generation)
