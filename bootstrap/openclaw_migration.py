"""One reviewed native Doctor migration inside the existing activation journal.

This owns no OpenClaw update run, schema migrator, scheduler or write authority.
The public CLI owns migration. Our receipt proves only the invoked process and
captured managed state; a changed/unknown generation never authorizes rewind.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import time
import uuid
from contextlib import closing
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any, Callable

from bootstrap.contracts import DeploymentPaths, atomic_write, load_versions

CONTRACT = "administrateme-openclaw-native-migration-v1"
_HEX = re.compile(r"[0-9a-f]{64}")
_MAX_FILES = 100_000
_MAX_RESULT_BYTES = 4 * 1024 * 1024
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_STATES = {
    "captured",
    "doctor_starting",
    "doctor_settled",
    "candidate_admitted",
    "contained",
}


class NativeMigrationError(RuntimeError):
    pass


def required(source: Path, target: Path) -> bool:
    before = load_versions(source)["openclaw"]
    after = load_versions(target)["openclaw"]
    return (before["version"], before["plugin_api"]) != (
        after["version"],
        after["plugin_api"],
    )


def validate_receipt(value: Any, journal: dict[str, Any]) -> None:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "contract",
            "source_commit",
            "target_commit",
            "snapshot_sha256",
            "candidate_admitted",
            "configuration_sha256",
            "state",
            "doctor",
        }
        or type(value.get("candidate_admitted")) is not bool
        or (
            value.get("configuration_sha256") is not None
            and (
                not isinstance(value["configuration_sha256"], str)
                or _HEX.fullmatch(value["configuration_sha256"]) is None
            )
        )
        or value.get("contract") != CONTRACT
        or value.get("source_commit") != journal["predecessor_commit"]
        or value.get("target_commit") != journal["target_commit"]
        or not isinstance(value.get("state"), str)
        or value["state"] not in _STATES
        or not isinstance(value.get("snapshot_sha256"), str)
        or not _HEX.fullmatch(value["snapshot_sha256"])
        or (value.get("doctor") is not None and not isinstance(value["doctor"], dict))
    ):
        raise NativeMigrationError("native migration journal binding is invalid")
    if (
        value["state"] == "candidate_admitted"
        and value["candidate_admitted"] is not True
    ):
        raise NativeMigrationError("native candidate admission evidence is missing")
    doctor = value["doctor"]
    if value["state"] == "captured" and doctor is not None:
        raise NativeMigrationError("unstarted native migration has a process receipt")
    if value["state"] not in {"captured", "contained"} and not isinstance(doctor, dict):
        raise NativeMigrationError("native migration is missing process custody")
    if doctor is not None:
        expected = {
            "settled",
            "result_path",
            "nonce",
            "pid",
            "returncode",
            "classification",
        }
        if doctor.get("settled") is True:
            expected.add("result_sha256")
        if (
            set(doctor) != expected
            or type(doctor.get("settled")) is not bool
            or not isinstance(doctor.get("nonce"), str)
            or re.fullmatch(r"[0-9a-f-]{36}", doctor["nonce"]) is None
            or not isinstance(doctor.get("result_path"), str)
            or re.fullmatch(
                r"/tmp/openclaw/openclaw-update-doctor-[0-9]+-[0-9a-f-]{36}\.json",
                doctor["result_path"],
            )
            is None
            or (
                doctor["pid"] is not None
                and (type(doctor["pid"]) is not int or doctor["pid"] <= 0)
            )
            or (
                doctor["returncode"] is not None
                and type(doctor["returncode"]) is not int
            )
            or (
                doctor["settled"]
                and (
                    doctor["pid"] is None
                    or doctor["returncode"] is None
                    or not isinstance(doctor["classification"], str)
                    or doctor["classification"] not in {"ok", "error", "advisory"}
                    or not isinstance(doctor.get("result_sha256"), str)
                    or _HEX.fullmatch(doctor["result_sha256"]) is None
                )
            )
            or (not doctor["settled"] and doctor["classification"] is not None)
            or (
                value["state"] in {"doctor_settled", "candidate_admitted"}
                and doctor["settled"] is not True
            )
        ):
            raise NativeMigrationError("native migration process receipt is invalid")


def _json(payload: bytes) -> dict[str, Any]:
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise NativeMigrationError(
                    "native migration receipt has duplicate keys"
                )
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise NativeMigrationError("native migration receipt is unreadable") from exc
    if not isinstance(value, dict):
        raise NativeMigrationError("native migration receipt is not an object")
    return value


def _private(path: Path, *, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    if (
        path.is_symlink()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    ):
        raise NativeMigrationError("native migration path ownership is unsafe")
    return info


def _read(path: Path, *, limit: int = _MAX_RESULT_BYTES) -> bytes:
    before = _private(path)
    if before.st_size > limit or before.st_nlink != 1:
        raise NativeMigrationError("native migration receipt exceeds its bound")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        payload = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    public = path.lstat()
    if (
        any(
            (
                entry.st_dev,
                entry.st_ino,
                entry.st_size,
                entry.st_mtime_ns,
                entry.st_ctime_ns,
            )
            != (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            for entry in (opened, after, public)
        )
        or len(payload) != before.st_size
    ):
        raise NativeMigrationError("native migration receipt changed while reading")
    return payload


def _directory(paths: DeploymentPaths, journal: dict[str, Any]) -> Path:
    identifier = journal["transaction_id"]
    if re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
        raise NativeMigrationError("native migration transaction is invalid")
    return paths.app_dir / "activation" / f"native-{identifier}"


def _inventory(paths: DeploymentPaths) -> dict[str, Any]:
    """Conservative byte/inode generations, never opening a live SQLite store.

    All managed native state is covered, including sessions, cron and workspaces.
    Managed links into reviewed app artifacts are recorded but not traversed;
    internal state links point into a tree already covered by this inventory.
    """
    root = paths.instance_dir / "openclaw"
    _private(root, directory=True)
    result: dict[str, Any] = {}
    for directory, subdirs, names in os.walk(root, followlinks=False):
        parent = Path(directory)
        _private(parent, directory=True)
        for name in sorted([*subdirs, *names]):
            path = parent / name
            relative = path.relative_to(root).as_posix()
            # The source installer owns this exact artifact selector receipt.
            if relative in {
                "source-install.json",
                "native-roster-migration.json",
                "native-roster-expansion.json",
            }:
                continue
            info = path.lstat()
            if info.st_uid != os.getuid() or (
                not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022
            ):
                raise NativeMigrationError(
                    "native state contains an unsafe owner or mode"
                )
            row: dict[str, Any] = {
                "mode": stat.S_IMODE(info.st_mode),
                "uid": info.st_uid,
            }
            if stat.S_ISLNK(info.st_mode):
                target = path.resolve(strict=True)
                if not any(
                    target.is_relative_to(base)
                    for base in (
                        root,
                        paths.app_dir / "releases",
                        paths.app_dir / "openclaw",
                    )
                ):
                    raise NativeMigrationError(
                        "native state link escapes managed state/artifacts"
                    )
                row.update(kind="link", target=os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                row["kind"] = "directory"
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    opened = os.fstat(stream.fileno())
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                    after = os.fstat(stream.fileno())
                public = path.lstat()

                def coordinate(entry: os.stat_result) -> tuple[int, ...]:
                    return (
                        entry.st_dev,
                        entry.st_ino,
                        entry.st_size,
                        entry.st_mtime_ns,
                        entry.st_ctime_ns,
                    )

                if any(
                    coordinate(item) != coordinate(info)
                    for item in (opened, after, public)
                ):
                    raise NativeMigrationError(
                        "native state changed while fingerprinting"
                    )
                row.update(
                    kind="file",
                    sha256=digest.hexdigest(),
                    generation=list(coordinate(info)),
                )
            else:
                raise NativeMigrationError("native state contains an unsupported entry")
            result[relative] = row
            if len(result) > _MAX_FILES:
                raise NativeMigrationError("native state inventory exceeds its bound")
    return result


def _assert_managed_config(paths: DeploymentPaths) -> None:
    root = paths.instance_dir / "openclaw"
    config = _json(_read(root / "runtime" / "openclaw.json", limit=16 * 1024 * 1024))
    # A public Doctor can visit configured external stores. Do not claim our
    # managed-state snapshot covers a custom store outside this exact tree.
    from adminme.openclaw_roster import agent_rows

    try:
        rows = agent_rows(config)
    except ValueError as exc:
        raise NativeMigrationError(
            "native roster is not a bounded migration input"
        ) from exc
    agents = config.get("agents", {})
    defaults = agents.get("defaults", {})
    if not isinstance(defaults, dict):
        raise NativeMigrationError("native agent defaults are invalid")
    for row in [defaults, *rows]:
        for key in ("agentDir", "workspace"):
            raw = row.get(key)
            if raw is not None and (
                not isinstance(raw, str)
                or not Path(raw).is_absolute()
                or not Path(raw).resolve().is_relative_to(root)
            ):
                raise NativeMigrationError("native agent path escapes captured state")
    session = config.get("session", {})
    if not isinstance(session, dict) or session.get("store") is not None:
        raise NativeMigrationError(
            "custom native session stores require a separate migration"
        )
    # Read only the copied, settled registry. Never open or checkpoint the actual
    # native database through Python, and never let a URI override query flags.
    # Registered agent databases may differ from configured agentDir coordinates.


def _assert_captured_registry(snapshot: Path, original: Path) -> None:
    registry = snapshot / "runtime/state/openclaw.sqlite"
    if not registry.exists():
        return
    try:
        # SQLite read-only mode can create shared-memory sidecars. Inspect a
        # disposable copy so neither the original nor immutable capsule changes.
        with TemporaryDirectory(prefix="registry-", dir=snapshot.parent) as scratch:
            copied = Path(scratch) / "registry.sqlite"
            shutil.copyfile(registry, copied)
            wal = Path(str(registry) + "-wal")
            if wal.exists():
                shutil.copyfile(wal, Path(str(copied) + "-wal"))
            with closing(
                sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True)
            ) as database:
                _validate_registry_rows(database, original)
    except sqlite3.Error as exc:
        raise NativeMigrationError(
            "captured native database registry is unreadable"
        ) from exc


def _validate_registry_rows(database: sqlite3.Connection, original: Path) -> None:
    database.execute("PRAGMA query_only=ON")
    table = database.execute(
        "SELECT type FROM sqlite_master WHERE name='agent_databases'"
    ).fetchone()
    if table is None:
        return  # The oldest retained schemas did not register agent DBs.
    if table != ("table",):
        raise NativeMigrationError("native database registry is not a table")
    rows = database.execute("SELECT path FROM agent_databases LIMIT 100001").fetchall()
    if len(rows) > _MAX_FILES:
        raise NativeMigrationError("native database registry exceeds its bound")
    for (value,) in rows:
        if (
            not isinstance(value, str)
            or not Path(value).is_absolute()
            or not Path(value).resolve().is_relative_to(original)
        ):
            raise NativeMigrationError(
                "registered native database escapes captured state"
            )


def capture(paths: DeploymentPaths, journal: dict[str, Any]) -> dict[str, Any]:
    _assert_managed_config(paths)
    before = _inventory(paths)
    directory = _directory(paths, journal)
    directory.mkdir(mode=0o700)
    _private(directory, directory=True)
    snapshot = directory / "state"
    size = sum(row["generation"][2] for row in before.values() if row["kind"] == "file")
    if shutil.disk_usage(directory).free < size + 256 * 1024 * 1024:
        raise NativeMigrationError("native snapshot has insufficient disk headroom")
    shutil.copytree(paths.instance_dir / "openclaw", snapshot, symlinks=True)
    # Source and snapshot must contain identical bytes. Inodes intentionally differ.
    for relative, row in before.items():
        if row["kind"] == "file":
            with (snapshot / relative).open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                os.fsync(stream.fileno())
            if digest != row["sha256"]:
                raise NativeMigrationError("native snapshot bytes do not match source")
    _assert_captured_registry(snapshot, paths.instance_dir / "openclaw")
    if _inventory(paths) != before:
        raise NativeMigrationError("native state changed during migration snapshot")
    payload = json.dumps(before, sort_keys=True, separators=(",", ":")).encode()
    atomic_write(directory / "snapshot.json", payload, mode=0o600)
    return {
        "contract": CONTRACT,
        "source_commit": journal["predecessor_commit"],
        "target_commit": journal["target_commit"],
        "snapshot_sha256": hashlib.sha256(payload).hexdigest(),
        "state": "captured",
        "candidate_admitted": False,
        "configuration_sha256": None,
        "doctor": None,
    }


def unchanged(paths: DeploymentPaths, journal: dict[str, Any]) -> bool:
    receipt = journal["openclaw_migration"]
    validate_receipt(receipt, journal)
    payload = _read(
        _directory(paths, journal) / "snapshot.json", limit=64 * 1024 * 1024
    )
    if hashlib.sha256(payload).hexdigest() != receipt["snapshot_sha256"]:
        raise NativeMigrationError("native snapshot identity changed")
    return _inventory(paths) == _json(payload)


def may_restore_predecessor(paths: DeploymentPaths, journal: dict[str, Any]) -> bool:
    receipt = journal["openclaw_migration"]
    validate_receipt(receipt, journal)
    if receipt["candidate_admitted"] or receipt["state"] in {
        "candidate_admitted",
        "contained",
    }:
        return False
    doctor = receipt["doctor"]
    if receipt["state"] != "captured" and (
        not doctor or doctor.get("settled") is not True
    ):
        return False
    return unchanged(paths, journal)


def doctor_environment(
    paths: DeploymentPaths, result: Path, node: Path
) -> dict[str, str]:
    # No ambient provider secrets, inherited updater run/phase or native update
    # authority. The exact CLI may read its own retained native credentials.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL"}
    }
    environment.update(
        {
            "PATH": f"{node.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            "OPENCLAW_STATE_DIR": str(paths.instance_dir / "openclaw" / "runtime"),
            "OPENCLAW_CONFIG_PATH": str(
                paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
            ),
            "OPENCLAW_SUPERVISOR_MODE": "external",
            "OPENCLAW_UPDATE_IN_PROGRESS": "1",
            "OPENCLAW_UPDATE_DEFER_CONFIGURED_PLUGIN_INSTALL_REPAIR": "1",
            "OPENCLAW_UPDATE_PARENT_SUPPORTS_DOCTOR_CONFIG_WRITE": "1",
            "OPENCLAW_UPDATE_PARENT_SUPPORTS_GATEWAY_RESTART": "1",
            "OPENCLAW_UPDATE_PARENT_ALLOWS_GATEWAY_SERVICE_REPAIR": "0",
            "OPENCLAW_UPDATE_PARENT_ALLOWS_GATEWAY_ACTIVATION": "0",
            "OPENCLAW_SERVICE_REPAIR_POLICY": "external",
            "OPENCLAW_UPDATE_POST_INSTALL_DOCTOR_RESULT_PATH": str(result),
        }
    )
    return environment


def _target_command(paths: DeploymentPaths, release: Path) -> tuple[Path, Path]:
    versions = load_versions(release)
    node = Path("/opt/homebrew/opt") / versions["node"]["formula"] / "bin/node"
    entry = paths.app_dir / "openclaw" / versions["openclaw"]["commit"] / "openclaw.mjs"
    if not node.is_file() or not entry.is_file() or entry.is_symlink():
        raise NativeMigrationError("exact target Doctor executable is unavailable")
    expected_version = "v" + versions["node"]["preferred"]
    observed = subprocess.run(
        [str(node), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if observed.returncode or observed.stdout.strip() != expected_version:
        raise NativeMigrationError(
            "target Doctor requires the approved exact Node patch"
        )
    return node, entry


def run_doctor(
    paths: DeploymentPaths,
    release: Path,
    journal: dict[str, Any],
    *,
    publish: Callable[[dict[str, Any]], None],
    timeout: float = 180,
) -> dict[str, Any]:
    node, entry = _target_command(paths, release)
    temp = Path("/tmp/openclaw")
    temp.mkdir(mode=0o700, exist_ok=True)
    _private(temp, directory=True)
    result = temp / f"openclaw-update-doctor-{os.getpid()}-{uuid.uuid4()}.json"
    custody = Path(str(result) + ".processes")
    nonce = str(uuid.uuid4())
    with custody.open("xb") as stream:
        os.chmod(custody, 0o600)
        stream.write(
            json.dumps({"nonce": nonce, "runId": "", "pid": 0, "slots": []}).encode()
        )
        stream.flush()
        os.fsync(stream.fileno())
    record: dict[str, Any] = {
        "settled": False,
        "result_path": str(result),
        "nonce": nonce,
        "pid": None,
        "returncode": None,
        "classification": None,
    }
    publish(record)
    directory = _directory(paths, journal)
    output = directory / "doctor-output.log"
    with output.open("xb") as stream:
        os.chmod(output, 0o600)
        child = subprocess.Popen(
            [
                str(node),
                str(entry),
                "doctor",
                "--repair",
                "--non-interactive",
                "--no-workspace-suggestions",
            ],
            cwd=entry.parent,
            env=doctor_environment(paths, result, node),
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        record["pid"] = child.pid
        publish(record)
        deadline = time.monotonic() + timeout
        exceeded: str | None = None
        while child.poll() is None:
            if time.monotonic() >= deadline:
                exceeded = "deadline"
            elif os.fstat(stream.fileno()).st_size > _MAX_OUTPUT_BYTES:
                exceeded = "output bound"
            if exceeded:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                record["returncode"] = child.returncode
                publish(record)
                raise NativeMigrationError(
                    f"native Doctor exceeded {exceeded}; custody remains unproven"
                )
            try:
                child.wait(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        stream.flush()
        os.fsync(stream.fileno())
    record["returncode"] = child.returncode
    try:
        custody_payload, result_payload = _read(custody), _read(result)
        owned = _json(custody_payload)
        classified = _json(result_payload)
        # The child must acknowledge this parent's reservation. A missing receipt,
        # PID mismatch or unresolved slot never means successful settlement.
        if (
            owned.get("nonce") != nonce
            or owned.get("runId") != ""
            or owned.get("pid") != child.pid
            or owned.get("slots") != []
        ):
            raise NativeMigrationError("native Doctor process custody did not settle")
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise NativeMigrationError("native Doctor process group is still present")
        status = classified.get("status")
        if not isinstance(status, str) or status not in {"ok", "error", "advisory"}:
            raise NativeMigrationError("native Doctor result has no classification")
        acceptable = status == "ok" and child.returncode == 0
        if status == "advisory":
            advisory = classified.get("advisory", {})
            acceptable = (
                child.returncode in {0, 86}
                and advisory.get("kind") == "package-post-install-doctor"
                and advisory.get("reason") == "deferred-configured-plugin-repair"
            )
        record.update(
            settled=True,
            classification=status,
            result_sha256=hashlib.sha256(result_payload).hexdigest(),
        )
        atomic_write(directory / "doctor-result.json", result_payload, mode=0o600)
        atomic_write(directory / "doctor-custody.json", custody_payload, mode=0o600)
        publish(record)
        if (
            not acceptable
            or classified.get("maintenanceRefusal")
            or classified.get("configWriteRefusal")
        ):
            raise NativeMigrationError(
                "native Doctor did not complete its classified migration"
            )
        return record
    except (OSError, ValueError) as exc:
        publish(record)
        raise NativeMigrationError(
            "native Doctor result or custody is unavailable"
        ) from exc


def configuration_origin(
    paths: DeploymentPaths, journal: dict[str, Any]
) -> dict[str, Any]:
    """Earliest retained classified ownership transition, never a rewritten parent."""
    from bootstrap.native_recovery import lineage

    chain = lineage(paths, journal)
    for item in reversed(chain):
        if (item.get("openclaw_migration") or {}).get("configuration_sha256"):
            return item
    return journal


def _configuration_source(paths: DeploymentPaths, journal: dict[str, Any]) -> Path:
    from bootstrap.native_recovery import lineage

    chain = lineage(paths, journal)
    # A settled failed Doctor may have changed native stores before config
    # classification. A correction can classify the entire original→result
    # transition, against the original copied bytes and its own genuine IPC.
    ancestors = chain[1:]
    source = (
        chain[-1]
        if ancestors
        and not any(
            (item.get("openclaw_migration") or {}).get("configuration_sha256")
            for item in ancestors
        )
        else journal
    )
    return _directory(paths, source) / "state/runtime/openclaw.json"


def record_configuration(
    paths: DeploymentPaths,
    release: Path,
    journal: dict[str, Any],
    *,
    retain_primary: bool = False,
) -> str:
    """Bind exact native output before any application-owned config selection."""
    from adminme.openclaw_roster_migration import CONTRACT as ROSTER_CONTRACT
    from adminme.openclaw_roster_migration import (
        canonical,
        classify,
        digest,
        validate_record,
    )

    migration = journal["openclaw_migration"]
    validate_receipt(migration, journal)
    doctor = migration["doctor"]
    if (
        doctor is None
        or doctor.get("settled") is not True
        or doctor.get("classification") not in {"ok", "advisory"}
    ):
        raise NativeMigrationError("native config transition has no classified process")
    directory = _directory(paths, journal)
    result_bytes = _read(directory / "doctor-result.json")
    if hashlib.sha256(result_bytes).hexdigest() != doctor["result_sha256"]:
        raise NativeMigrationError("native Doctor classification receipt changed")
    source = _json(_read(_configuration_source(paths, journal), limit=16 * 1024 * 1024))
    runtime = paths.instance_dir / "openclaw/runtime"
    result = _json(_read(runtime / "openclaw.json", limit=16 * 1024 * 1024))
    record = {
        "contract": ROSTER_CONTRACT,
        "kind": "doctor",
        "source": source,
        "result": result,
        "source_sha256": digest(source),
        "result_sha256": digest(result),
        "fallbacks": classify(source, result, runtime=runtime),
        "requested_entries": None,
        "native_commit": load_versions(release)["openclaw"]["commit"],
        "process_receipt_sha256": doctor["result_sha256"],
    }
    validate_record(record, runtime=runtime)
    payload = canonical(record)
    atomic_write(directory / "config-transition.json", payload, mode=0o600)
    destination = runtime.parent / "native-roster-migration.json"
    if retain_primary and destination.exists():
        # Keep the first classified ownership transition; the new transaction
        # retains its own record and actual native process receipt separately.
        return hashlib.sha256(payload).hexdigest()
    if destination.exists() and _read(destination, limit=32 * 1024 * 1024) != payload:
        raise NativeMigrationError(
            "a different native roster migration is already retained"
        )
    atomic_write(destination, payload, mode=0o600)
    return hashlib.sha256(payload).hexdigest()


def configuration_transition(
    paths: DeploymentPaths, journal: dict[str, Any]
) -> dict[str, Any]:
    from adminme.openclaw_roster_migration import validate_record

    receipt = journal["openclaw_migration"]
    validate_receipt(receipt, journal)
    payload = _read(
        _directory(paths, journal) / "config-transition.json", limit=32 * 1024 * 1024
    )
    if hashlib.sha256(payload).hexdigest() != receipt["configuration_sha256"]:
        raise NativeMigrationError(
            "native configuration transition does not match transaction"
        )
    result = validate_record(
        _json(payload), runtime=paths.instance_dir / "openclaw/runtime"
    )
    original = _json(
        _read(
            _configuration_source(paths, journal),
            limit=16 * 1024 * 1024,
        )
    )
    if (
        json.dumps(result["source"], sort_keys=True)
        != json.dumps(original, sort_keys=True)
        or result["native_commit"]
        != load_versions(Path(journal["target_path"]))["openclaw"]["commit"]
        or not isinstance(receipt["doctor"], dict)
        or result["process_receipt_sha256"] != receipt["doctor"].get("result_sha256")
    ):
        raise NativeMigrationError(
            "native config transition source or process binding changed"
        )
    return result


def expand_sole_roster(
    paths: DeploymentPaths, release: Path, entries: dict[str, Any]
) -> dict[str, Any]:
    """Let the public native CAS writer materialize sole-agent ownership."""
    from adminme.openclaw_roster_migration import CONTRACT as ROSTER_CONTRACT
    from adminme.openclaw_roster_migration import (
        canonical,
        classify,
        digest,
        validate_record,
    )

    runtime = paths.instance_dir / "openclaw/runtime"
    config_path = runtime / "openclaw.json"
    source_bytes = _read(config_path, limit=16 * 1024 * 1024)
    source = _json(source_bytes)
    node, entry = _target_command(paths, release)
    expected = source.get("agents", {}).get("entries")
    expectation = (
        ["--expect-current-absent"]
        if expected is None
        else ["--expect-current-json", canonical(expected).decode()]
    )
    environment = doctor_environment(paths, Path("/tmp/openclaw/unused"), node)
    environment = {
        key: value
        for key, value in environment.items()
        if not key.startswith("OPENCLAW_UPDATE_")
    }
    command = [
        str(node),
        str(entry),
        "config",
        "set",
        "agents.entries",
        canonical(entries).decode(),
        "--strict-json",
        "--replace",
        *expectation,
    ]
    result = subprocess.run(
        command,
        cwd=entry.parent,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise NativeMigrationError("public native roster write did not complete")
    selected = _json(_read(config_path, limit=16 * 1024 * 1024))
    # Exit zero is not the state proof: classify exact read-back against the CAS
    # input and requested entries. This command receipt grants no rewind right.
    receipt = {
        "contract": ROSTER_CONTRACT,
        "kind": "sole-agent-expansion",
        "source": source,
        "result": selected,
        "source_sha256": digest(source),
        "result_sha256": digest(selected),
        "fallbacks": classify(
            source, selected, runtime=runtime, requested_entries=entries
        ),
        "requested_entries": entries,
        "native_commit": load_versions(release)["openclaw"]["commit"],
        "process_receipt_sha256": digest(
            {
                "command": "config-set-agents.entries",
                "returncode": 0,
                "source": digest(source),
                "result": digest(selected),
            }
        ),
    }
    validate_record(receipt, runtime=runtime)
    destination = runtime.parent / "native-roster-expansion.json"
    payload = canonical(receipt)
    if destination.exists() and _read(destination, limit=32 * 1024 * 1024) != payload:
        raise NativeMigrationError(
            "a different native roster expansion is already retained"
        )
    atomic_write(destination, payload, mode=0o600)
    return selected
