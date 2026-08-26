"""Consistent encrypted backups and validated rollback-safe restores."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import io
import json
import multiprocessing as mp
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.projections.runner import (
    DEFAULT_PROJECTION_CLASSES,
    ProjectionRunner,
    register_default_projections,
)
from adminme.projections.xlsx_workbooks import (
    FINANCE_WORKBOOK_NAME,
    OPS_WORKBOOK_NAME,
)
from bootstrap.contracts import (
    BACKUP_FORMAT_VERSION,
    PROJECTION_NAMES,
    DeploymentPaths,
    atomic_write,
    ensure_within,
    repository_root,
    sha256_file,
)
from bootstrap.doctor import Doctor
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager


class BackupError(RuntimeError):
    pass


_BACKUP_FORMAT = "administrateme-backup-v1"
_MANIFEST_FORMAT = "administrateme-backup-manifest-v1"
_REHEARSAL_CONTRACT = "backup-rehearsal-v1"
_REHEARSAL_MARKER = ".administrateme-rehearsal-v1"
_REHEARSAL_ACTIVITY_LOCK = ".activity.lock"
_REHEARSAL_EXTRACTION_COMPLETE = ".extraction-complete"
_REHEARSAL_GUARDIAN_PID = ".guardian.pid"
_MAX_BACKUP_BYTES = 8 * 1024 * 1024 * 1024
_MAX_UNPACKED_BYTES = 16 * 1024 * 1024 * 1024
_MAX_ARCHIVE_FILES = 100_000
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_regular_file_once(
    path: Path, *, maximum_size: int
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupError("backup source is not a regular file")
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise BackupError("backup source ownership or mode is unsafe")
        if metadata.st_size <= 0 or metadata.st_size > maximum_size:
            raise BackupError("backup source exceeds the supported size boundary")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(maximum_size + 1)
        if len(payload) != metadata.st_size:
            raise BackupError("backup source changed while it was read")
        return payload, metadata
    finally:
        os.close(descriptor)


def _sha256_private_regular_file(path: Path, *, maximum_size: int) -> str:
    """Hash one owner-only regular file through a no-follow descriptor."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupError("backup source is not a regular file")
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise BackupError("backup source ownership or mode is unsafe")
        if metadata.st_size <= 0 or metadata.st_size > maximum_size:
            raise BackupError("backup source exceeds the supported size boundary")
        digest = hashlib.sha256()
        read_size = 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                read_size += len(chunk)
                digest.update(chunk)
        if read_size != metadata.st_size:
            raise BackupError("backup source changed while it was hashed")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _write_new_private_file(path: Path, payload: bytes) -> None:
    """Atomically publish one owner-only file without replacing evidence."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    temp_exists = True
    published = False

    def _sync_parent() -> None:
        directory = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            metadata = os.fstat(directory)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
            ):
                raise BackupError("rehearsal evidence directory is unsafe")
            os.fsync(directory)
        finally:
            os.close(directory)

    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o600)
        os.link(temp_path, path, follow_symlinks=False)
        published = True
        temp_path.unlink()
        temp_exists = False
        _sync_parent()
    except BaseException as exc:
        cleanup_error: BaseException | None = None
        if published:
            try:
                path.unlink()
                published = False
            except BaseException as unlink_exc:
                cleanup_error = unlink_exc
        if temp_exists:
            try:
                temp_path.unlink()
                temp_exists = False
            except BaseException as unlink_exc:
                cleanup_error = cleanup_error or unlink_exc
        try:
            _sync_parent()
        except BaseException as fsync_exc:
            cleanup_error = cleanup_error or fsync_exc
        if cleanup_error is not None:
            raise BackupError(
                "failed receipt publication could not be durably removed"
            ) from exc
        if isinstance(exc, FileExistsError):
            raise BackupError(
                "refusing to replace existing rehearsal evidence"
            ) from exc
        raise


def _fsync_private_directory(path: Path, *, label: str) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise BackupError(f"{label} directory ownership is unsafe")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _authenticate_backup(
    source: Path,
    backup_key: bytes,
    *,
    expected_artifact_sha256: str | None = None,
) -> tuple[dict[str, Any], bytes, str]:
    encoded, _metadata = _read_regular_file_once(
        source,
        maximum_size=_MAX_BACKUP_BYTES,
    )
    artifact_sha256 = hashlib.sha256(encoded).hexdigest()
    if (
        expected_artifact_sha256 is not None
        and artifact_sha256 != expected_artifact_sha256
    ):
        raise BackupError("backup source identity changed")
    try:
        envelope = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup envelope is invalid") from exc
    if not isinstance(envelope, dict) or set(envelope) != {
        "format",
        "version",
        "created_at",
        "manifest_sha256",
        "nonce",
        "ciphertext",
    }:
        raise BackupError("backup envelope shape is invalid")
    if (
        envelope.get("format") != _BACKUP_FORMAT
        or envelope.get("version") != BACKUP_FORMAT_VERSION
        or not isinstance(envelope.get("created_at"), str)
        or not isinstance(envelope.get("manifest_sha256"), str)
        or _HEX_64.fullmatch(str(envelope.get("manifest_sha256"))) is None
        or not isinstance(envelope.get("nonce"), str)
        or not isinstance(envelope.get("ciphertext"), str)
    ):
        raise BackupError("unsupported or malformed backup envelope")
    try:
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    except (ValueError, TypeError) as exc:
        raise BackupError("backup envelope encoding is invalid") from exc
    if len(nonce) != 12 or len(ciphertext) > _MAX_UNPACKED_BYTES + 16:
        raise BackupError("backup envelope cryptographic shape is invalid")
    try:
        payload = AESGCM(backup_key).decrypt(
            nonce,
            ciphertext,
            b"administrateme-backup-v1",
        )
    except Exception as exc:
        raise BackupError("backup decryption or authentication failed") from exc
    if len(payload) > _MAX_UNPACKED_BYTES:
        raise BackupError("backup plaintext exceeds the supported size boundary")
    return envelope, payload, artifact_sha256


def _validate_manifest(
    document: Any,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if not isinstance(document, dict):
        raise BackupError("backup manifest shape is invalid")
    required = {
        "format",
        "created_at",
        "file_count",
        "files",
        "projection_names",
        "manifest_sha256",
        "event_log_sha256",
    }
    if set(document) != required or document.get("format") != _MANIFEST_FORMAT:
        raise BackupError("backup manifest shape is invalid")
    canonical = dict(document)
    expected = canonical.pop("manifest_sha256", None)
    if not isinstance(expected, str) or _HEX_64.fullmatch(expected) is None:
        raise BackupError("backup manifest digest is invalid")
    actual = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if actual != expected:
        raise BackupError("backup manifest was tampered with")
    files = document.get("files")
    if (
        not isinstance(files, list)
        or not files
        or len(files) > _MAX_ARCHIVE_FILES
        or document.get("file_count") != len(files)
    ):
        raise BackupError("backup manifest file inventory is invalid")
    expected_projection_names = [
        str(projection.name) for projection in DEFAULT_PROJECTION_CLASSES
    ]
    if document.get("projection_names") != expected_projection_names:
        raise BackupError("backup projection registry is invalid")
    inventory: dict[str, dict[str, Any]] = {}
    casefolded: set[str] = set()
    unpacked_size = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise BackupError("backup manifest entry is invalid")
        name = item.get("path")
        size = item.get("size")
        digest = item.get("sha256")
        if (
            not isinstance(name, str)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or _HEX_64.fullmatch(digest) is None
        ):
            raise BackupError("backup manifest entry is invalid")
        pure = PurePosixPath(name)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or not pure.parts
            or pure.parts[0] != "state"
            or pure.as_posix() != name
            or name in inventory
            or name.casefold() in casefolded
        ):
            raise BackupError("backup manifest path inventory is unsafe")
        inventory[name] = item
        casefolded.add(name.casefold())
        unpacked_size += size
        if unpacked_size > _MAX_UNPACKED_BYTES:
            raise BackupError("backup manifest exceeds the unpacked size boundary")
    if "state/events/events.db" not in inventory:
        raise BackupError("backup manifest omits the encrypted event log")
    if (
        document.get("event_log_sha256")
        != inventory["state/events/events.db"]["sha256"]
    ):
        raise BackupError("backup event-log identity is invalid")
    return document, inventory


def _extract_and_validate_payload(
    payload: bytes,
    staging_root: Path,
) -> dict[str, Any]:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(payload), mode="r:")
    except tarfile.TarError as exc:
        raise BackupError("backup archive is invalid") from exc
    with archive:
        members = archive.getmembers()
        if not members or len(members) > _MAX_ARCHIVE_FILES + 1:
            raise BackupError("backup archive member count is invalid")
        names = [member.name for member in members]
        if len(names) != len(set(names)) or len(names) != len(
            {name.casefold() for name in names}
        ):
            raise BackupError("backup archive contains duplicate member names")
        member_by_name: dict[str, tarfile.TarInfo] = {}
        for member in members:
            pure = PurePosixPath(member.name)
            if (
                not member.isfile()
                or pure.is_absolute()
                or ".." in pure.parts
                or not pure.parts
                or pure.as_posix() != member.name
            ):
                raise BackupError("backup archive contains an unsafe member")
            member_by_name[member.name] = member
        manifest_member = member_by_name.get("manifest.json")
        if manifest_member is None:
            raise BackupError("backup archive omits its manifest")
        manifest_handle = archive.extractfile(manifest_member)
        if manifest_handle is None:
            raise BackupError("backup manifest cannot be read")
        try:
            manifest_document = json.loads(manifest_handle.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupError("backup manifest encoding is invalid") from exc
        manifest, inventory = _validate_manifest(manifest_document)
        if set(member_by_name) != {*inventory, "manifest.json"}:
            raise BackupError("backup archive and manifest inventories differ")
        for name, item in inventory.items():
            member = member_by_name[name]
            if member.size != item["size"]:
                raise BackupError("backup archive member size differs from manifest")
        root_descriptor = os.open(
            staging_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        directory_descriptors: dict[tuple[str, ...], int] = {(): root_descriptor}
        directory_identities: dict[tuple[str, ...], tuple[int, int]] = {}
        file_identities: dict[tuple[tuple[str, ...], str], tuple[int, int, int]] = {}

        def _validate_private_directory(
            descriptor: int,
            *,
            label: str,
        ) -> os.stat_result:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
            ):
                raise BackupError(f"backup extraction {label} is unsafe")
            return metadata

        def _directory(parts: tuple[str, ...]) -> int:
            existing = directory_descriptors.get(parts)
            if existing is not None:
                return existing
            parent_parts = parts[:-1]
            parent_descriptor = _directory(parent_parts)
            component = parts[-1]
            try:
                os.mkdir(component, mode=0o700, dir_fd=parent_descriptor)
            except FileExistsError:
                pass
            descriptor = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_descriptor,
            )
            try:
                metadata = _validate_private_directory(
                    descriptor,
                    label="directory",
                )
                os.fchmod(descriptor, 0o700)
            except BaseException:
                os.close(descriptor)
                raise
            directory_descriptors[parts] = descriptor
            directory_identities[parts] = (metadata.st_dev, metadata.st_ino)
            return descriptor

        def _record_private_file(
            parent_parts: tuple[str, ...],
            filename: str,
            source: Any,
            *,
            expected_size: int,
            expected_sha256: str,
        ) -> None:
            parent_descriptor = _directory(parent_parts)
            descriptor = os.open(
                filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_descriptor,
            )
            digest = hashlib.sha256()
            written = 0
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or metadata.st_mode & 0o077
                ):
                    raise BackupError("backup extraction target is unsafe")
                with os.fdopen(descriptor, "wb", closefd=False) as output:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        written += len(chunk)
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                os.fchmod(descriptor, 0o600)
                file_identities[(parent_parts, filename)] = (
                    metadata.st_dev,
                    metadata.st_ino,
                    written,
                )
            finally:
                os.close(descriptor)
            if written != expected_size or digest.hexdigest() != expected_sha256:
                raise BackupError("backup archive member failed integrity")

        try:
            root_metadata = _validate_private_directory(
                root_descriptor,
                label="root",
            )
            directory_identities[()] = (
                root_metadata.st_dev,
                root_metadata.st_ino,
            )
            required_bytes = sum(int(item["size"]) for item in inventory.values())
            filesystem = os.fstatvfs(root_descriptor)
            available_bytes = filesystem.f_bavail * filesystem.f_frsize
            if available_bytes < required_bytes + 512 * 1024 * 1024:
                raise BackupError("insufficient free disk for disposable rehearsal")
            for name, item in sorted(inventory.items()):
                pure = PurePosixPath(name)
                source = archive.extractfile(member_by_name[name])
                if source is None:
                    raise BackupError("backup archive member cannot be read")
                _record_private_file(
                    tuple(pure.parts[:-1]),
                    pure.name,
                    source,
                    expected_size=int(item["size"]),
                    expected_sha256=str(item["sha256"]),
                )
            encoded_manifest = json.dumps(manifest, sort_keys=True).encode("utf-8")
            _record_private_file(
                (),
                "manifest.json",
                io.BytesIO(encoded_manifest),
                expected_size=len(encoded_manifest),
                expected_sha256=hashlib.sha256(encoded_manifest).hexdigest(),
            )

            # Recheck every opened directory and published file against its
            # anchored parent descriptor. A concurrent intermediate-component
            # replacement therefore fails instead of redirecting extraction.
            for parts, expected_identity in directory_identities.items():
                if not parts:
                    current = os.fstat(root_descriptor)
                else:
                    current = os.stat(
                        parts[-1],
                        dir_fd=directory_descriptors[parts[:-1]],
                        follow_symlinks=False,
                    )
                if (
                    not stat.S_ISDIR(current.st_mode)
                    or (current.st_dev, current.st_ino) != expected_identity
                ):
                    raise BackupError(
                        "backup extraction directory changed during extraction"
                    )
            for (parent_parts, filename), expected in file_identities.items():
                current = os.stat(
                    filename,
                    dir_fd=directory_descriptors[parent_parts],
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(current.st_mode)
                    or (current.st_dev, current.st_ino, current.st_size) != expected
                ):
                    raise BackupError(
                        "backup extraction file changed during extraction"
                    )
            return manifest
        finally:
            for parts in sorted(
                directory_descriptors,
                key=len,
                reverse=True,
            ):
                os.close(directory_descriptors[parts])


def _encoded_value(value: Any) -> bytes:
    if value is None:
        return b"n"
    if isinstance(value, bytes):
        return b"b" + base64.b64encode(value)
    if isinstance(value, float):
        return f"f:{value.hex()}".encode("ascii")
    return b"j" + json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _logical_projection_digest(conn: Any) -> dict[str, Any]:
    digest = hashlib.sha256()
    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    row_count = 0
    for table in tables:
        digest.update(b"table\0" + table.encode("utf-8") + b"\0")
        quoted = '"' + table.replace('"', '""') + '"'
        order_columns = [
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({quoted})")
        ]
        ordering = ", ".join(
            '"' + column.replace('"', '""') + '"' for column in order_columns
        )
        cursor = conn.execute(
            f"SELECT * FROM {quoted}" + (f" ORDER BY {ordering}" if ordering else "")
        )
        result_columns = tuple(str(column[0]) for column in cursor.description or ())
        digest.update(json.dumps(result_columns).encode("utf-8"))
        for row in cursor:
            row_count += 1
            for value in row:
                encoded = _encoded_value(value)
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
    return {
        "sha256": digest.hexdigest(),
        "table_count": len(tables),
        "row_count": row_count,
    }


def _validate_workbook_artifacts(workbooks_dir: Path) -> dict[str, Any]:
    from openpyxl import load_workbook

    from adminme.projections.xlsx_workbooks import (
        _BIDIRECTIONAL_SHEETS,
        _READONLY_SHEETS,
    )
    from adminme.daemons.xlsx_sync.sheet_schemas import descriptor_for
    from adminme.projections.xlsx_workbooks.sidecar import hash_readonly_sheet

    expected_workbooks = {OPS_WORKBOOK_NAME, FINANCE_WORKBOOK_NAME}
    actual_workbooks = {
        path.name for path in workbooks_dir.glob("*.xlsx") if path.is_file()
    }
    if actual_workbooks != expected_workbooks:
        raise BackupError("rehearsal did not regenerate the canonical workbooks")
    sidecar_root = workbooks_dir.parent / ".xlsx-state"
    workbook_hashes: dict[str, str] = {}
    sidecar_hashes: list[str] = []
    bidirectional_count = 0
    readonly_count = 0
    expected_sidecars: set[Path] = set()

    def _require_private_regular_file(path: Path, kind: str) -> None:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise BackupError(f"rehearsal {kind} ownership or mode is unsafe")

    for workbook_name in sorted(expected_workbooks):
        workbook_path = workbooks_dir / workbook_name
        _require_private_regular_file(workbook_path, "workbook")
        workbook_hashes[workbook_name] = sha256_file(workbook_path)
        workbook = load_workbook(workbook_path, data_only=True, read_only=False)
        try:
            expected_sheets = tuple(
                _BIDIRECTIONAL_SHEETS[workbook_name] + _READONLY_SHEETS[workbook_name]
            )
            if set(workbook.sheetnames) != set(expected_sheets) or len(
                workbook.sheetnames
            ) != len(expected_sheets):
                raise BackupError("rehearsal workbook sheet contract differs")
            for sheet_name in _BIDIRECTIONAL_SHEETS[workbook_name]:
                sidecar = sidecar_root / workbook_name / f"{sheet_name}.json"
                expected_sidecars.add(sidecar)
                _require_private_regular_file(sidecar, "sidecar")
                document = json.loads(sidecar.read_text("utf-8"))
                sheet_rows = list(workbook[sheet_name].iter_rows(values_only=True))
                descriptor = descriptor_for(workbook_name, sheet_name)
                header_row = descriptor.header_row if descriptor is not None else 1
                headers = [
                    str(value) if value is not None else ""
                    for value in sheet_rows[header_row - 1]
                ]
                expected_rows = [
                    {headers[index]: row[index] for index in range(len(headers))}
                    for row in sheet_rows[header_row:]
                    if any(value is not None for value in row)
                ]
                expected_document = json.loads(
                    json.dumps({"rows": expected_rows}, default=str)
                )
                if document != expected_document:
                    raise BackupError("rehearsal bidirectional sidecar is invalid")
                sidecar_hashes.append(sha256_file(sidecar))
                bidirectional_count += 1
            for sheet_name in _READONLY_SHEETS[workbook_name]:
                sidecar = sidecar_root / workbook_name / f"{sheet_name}.json"
                expected_sidecars.add(sidecar)
                _require_private_regular_file(sidecar, "sidecar")
                document = json.loads(sidecar.read_text("utf-8"))
                rows = [
                    list(row)
                    for row in workbook[sheet_name].iter_rows(values_only=True)
                ]
                if document != {"content_hash": hash_readonly_sheet(rows)}:
                    raise BackupError("rehearsal read-only sidecar is invalid")
                sidecar_hashes.append(sha256_file(sidecar))
                readonly_count += 1
        finally:
            workbook.close()
    actual_sidecars = {path for path in sidecar_root.rglob("*.json") if path.is_file()}
    if actual_sidecars != expected_sidecars:
        raise BackupError("rehearsal sidecar inventory differs")
    aggregate = hashlib.sha256("".join(sorted(sidecar_hashes)).encode()).hexdigest()
    return {
        "expected_count": 2,
        "rebuilt_count": 2,
        "sheet_inventory_valid_count": 2,
        "bidirectional_sidecar_count": bidirectional_count,
        "readonly_sidecar_count": readonly_count,
        "sidecar_sha256": aggregate,
        "workbook_sha256": workbook_hashes,
    }


class _RejectingOfflineEventSink:
    async def write(self, *args: Any, **kwargs: Any) -> str:
        del args, kwargs
        raise BackupError("offline rehearsal attempted to append an event")


async def _offline_replay(staged_state: Path, event_key: bytes) -> dict[str, Any]:
    event_db = staged_state / "events" / "events.db"
    if not event_db.is_file():
        raise BackupError("staged event log is missing")
    event_digest_before = sha256_file(event_db)
    projection_root = staged_state / "projections"
    if projection_root.exists():
        shutil.rmtree(projection_root)
    projection_root.mkdir(parents=True, mode=0o700)
    config = load_instance_config(staged_state)
    event_log: EventLog | None = None
    bus: EventBus | None = None
    runner: ProjectionRunner | None = None
    try:
        event_log = EventLog(config, event_key, read_only=True)
        bus = EventBus(event_log, config.bus_checkpoint_path)
        runner = ProjectionRunner(bus, event_log, config, encryption_key=event_key)
        xlsx_projection = register_default_projections(
            runner,
            config,
            event_log,
            _RejectingOfflineEventSink(),
        )
        await runner.start(start_bus=False)
        names = runner.projection_names()
        if names != sorted(PROJECTION_NAMES) or names != sorted(
            projection.name for projection in DEFAULT_PROJECTION_CLASSES
        ):
            raise BackupError("restored projection registry is incomplete")
        event_count = await event_log.count()
        watermark = await event_log.latest_event_id()
        for name in PROJECTION_NAMES:
            await runner.rebuild(name)
        await xlsx_projection.stop()
        await xlsx_projection.regenerate_all(emit_event=False)
        projection_digests: dict[str, Any] = {}
        for name in PROJECTION_NAMES:
            projection_digests[name] = await runner._read_native(  # noqa: SLF001
                name,
                _logical_projection_digest,
            )
        status = await runner.status()
        degraded = [
            name
            for name, value in status.items()
            if value.get("degraded") or value.get("lag_count") not in {0, None}
        ]
        if degraded:
            raise BackupError("offline projection replay did not settle")
        if (
            await event_log.count() != event_count
            or await event_log.latest_event_id() != watermark
        ):
            raise BackupError("offline replay changed the source event watermark")
        workbooks = _validate_workbook_artifacts(config.xlsx_workbooks_dir)
    finally:
        try:
            if runner is not None:
                await runner.stop(stop_bus=False)
        finally:
            try:
                if bus is not None:
                    await bus.stop()
            finally:
                if event_log is not None:
                    await event_log.close()
    if sha256_file(event_db) != event_digest_before:
        raise BackupError("offline replay changed the encrypted event log")
    return {
        "watermark": {
            "kind": "latest-event-id-sha256",
            "event_count": event_count,
            "head_sha256": hashlib.sha256((watermark or "").encode()).hexdigest(),
        },
        "replay": {
            "projection_registry_count": len(PROJECTION_NAMES),
            "projections_rebuilt": len(PROJECTION_NAMES),
            "projection_divergence": "not_evaluated",
            "degraded_or_poisoned_count": 0,
            "projection_digests": projection_digests,
        },
        "workbooks": workbooks,
        "event_log_sha256": event_digest_before,
        "source_events_appended": 0,
    }


def _child_failure_code(exc: BaseException) -> str:
    if isinstance(exc, BackupError):
        return "backup_rehearsal_validation_failed"
    return "backup_rehearsal_child_failed"


def _extract_child(
    source: str,
    staging_root: str,
    expected_artifact_sha256: str,
    key_pipe: Any,
    result_pipe: Any,
) -> None:
    try:
        key = key_pipe.recv_bytes()
        envelope, payload, artifact_sha256 = _authenticate_backup(
            Path(source),
            key,
            expected_artifact_sha256=expected_artifact_sha256,
        )
        manifest = _extract_and_validate_payload(payload, Path(staging_root))
        if manifest["manifest_sha256"] != envelope["manifest_sha256"]:
            raise BackupError("backup manifest identity does not match envelope")
        result_pipe.send(
            {
                "status": "healthy",
                "artifact_sha256": artifact_sha256,
                "created_at": envelope["created_at"],
                "manifest_sha256": manifest["manifest_sha256"],
                "event_log_sha256": manifest["event_log_sha256"],
                "file_count": manifest["file_count"],
            }
        )
    except BaseException as exc:
        result_pipe.send({"status": "failed", "error_code": _child_failure_code(exc)})
    finally:
        key_pipe.close()
        result_pipe.close()


def _replay_child(
    staged_state: str,
    key_pipe: Any,
    result_pipe: Any,
) -> None:
    try:
        key = key_pipe.recv_bytes()
        result = asyncio.run(_offline_replay(Path(staged_state), key))
        result_pipe.send({"status": "healthy", **result})
    except BaseException as exc:
        result_pipe.send({"status": "failed", "error_code": _child_failure_code(exc)})
    finally:
        key_pipe.close()
        result_pipe.close()


class _ControllerDisconnected(RuntimeError):
    """The rehearsal controller disappeared while its guardian was active."""


class _WorkerNotReaped(RuntimeError):
    """A phase worker remains alive; its plaintext lease must be preserved."""


def _controller_disconnected(liveness_pipe: Any) -> bool:
    if not liveness_pipe.poll():
        return False
    try:
        liveness_pipe.recv_bytes()
    except EOFError:
        return True
    raise BackupError("rehearsal controller liveness channel is invalid")


def _upgrade_guardian_cleanup_lease(descriptor: int) -> None:
    """Require exclusive proof that no phase worker can still use payload."""

    fcntl.flock(descriptor, fcntl.LOCK_UN)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        # Preserve guardian visibility while the worker's independent shared
        # lease continues to block every stale-cleanup controller.
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        raise _WorkerNotReaped(
            "backup rehearsal worker still owns the plaintext lease"
        ) from exc


def _leased_phase_worker(
    target: Any,
    target_args: tuple[Any, ...],
    stage_path: str,
    key_pipe: Any,
    result_pipe: Any,
) -> None:
    """Hold a shared cleanup lease for the complete native worker lifetime."""

    stage = Path(stage_path)
    lock_path = stage / _REHEARSAL_ACTIVITY_LOCK
    descriptor: int | None = None
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        )
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        metadata = os.fstat(descriptor)
        current = lock_path.lstat()
        marker = stage / _REHEARSAL_MARKER
        marker_metadata = marker.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or current.st_dev != metadata.st_dev
            or current.st_ino != metadata.st_ino
            or not stat.S_ISREG(marker_metadata.st_mode)
            or marker_metadata.st_uid != os.getuid()
            or marker_metadata.st_mode & 0o077
        ):
            raise BackupError("rehearsal worker stage ownership is invalid")
        target(*target_args, key_pipe, result_pipe)
    except BaseException as exc:
        try:
            result_pipe.send(
                {"status": "failed", "error_code": _child_failure_code(exc)}
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        for connection in (key_pipe, result_pipe):
            try:
                connection.close()
            except OSError:
                pass
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _run_guardian_phase(
    target: Any,
    args: tuple[Any, ...],
    key: bytes,
    *,
    stage: Path,
    timeout_s: float,
    liveness_pipe: Any,
) -> tuple[dict[str, Any], int]:
    """Run one worker while its guardian also watches controller liveness."""

    context = mp.get_context("spawn")
    key_reader, key_writer = context.Pipe(duplex=False)
    result_reader, result_writer = context.Pipe(duplex=False)
    process = context.Process(
        target=_leased_phase_worker,
        args=(target, args, str(stage), key_reader, result_writer),
        daemon=False,
    )
    deadline = time.monotonic() + timeout_s
    result: dict[str, Any] | None = None
    try:
        process.start()
        key_reader.close()
        result_writer.close()
        key_writer.send_bytes(key)
        key_writer.close()
        while result is None:
            if _controller_disconnected(liveness_pipe):
                raise _ControllerDisconnected
            if result_reader.poll(0.05):
                try:
                    received = result_reader.recv()
                except EOFError:
                    received = None
                result = received if isinstance(received, dict) else None
                break
            if not process.is_alive() or time.monotonic() >= deadline:
                raise BackupError("backup rehearsal child failed or timed out")
        join_deadline = time.monotonic() + 10
        while process.is_alive():
            if _controller_disconnected(liveness_pipe):
                raise _ControllerDisconnected
            process.join(timeout=0.05)
            if time.monotonic() >= join_deadline:
                raise BackupError("backup rehearsal child failed to exit")
        if process.exitcode != 0 or result is None:
            raise BackupError("backup rehearsal child failed or timed out")
        return result, int(process.pid or 0)
    finally:
        for connection in (
            key_reader,
            key_writer,
            result_reader,
            result_writer,
        ):
            try:
                connection.close()
            except OSError:
                pass
        if process.pid is not None:
            try:
                alive = process.is_alive()
            except BaseException as exc:
                raise _WorkerNotReaped(
                    "backup rehearsal child terminality is unknown"
                ) from exc
            if alive:
                try:
                    process.terminate()
                except BaseException:
                    pass
                try:
                    process.join(timeout=5)
                except BaseException:
                    pass
            try:
                alive = process.is_alive()
            except BaseException as exc:
                raise _WorkerNotReaped(
                    "backup rehearsal child terminality is unknown"
                ) from exc
            if alive:
                try:
                    process.kill()
                except BaseException:
                    pass
                try:
                    process.join(timeout=5)
                except BaseException:
                    pass
            try:
                alive = process.is_alive()
            except BaseException as exc:
                raise _WorkerNotReaped(
                    "backup rehearsal child terminality is unknown"
                ) from exc
            if alive:
                raise _WorkerNotReaped("backup rehearsal child could not be reaped")
            process.close()


def _remove_rehearsal_payload(stage: Path) -> None:
    """Destroy plaintext while leaving owner evidence recoverable on a crash."""

    payload = stage / "payload"
    if payload.exists() or payload.is_symlink():
        metadata = payload.lstat()
        if (
            payload.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise BackupError("rehearsal plaintext payload ownership is unsafe")
        shutil.rmtree(payload)
    if payload.exists() or payload.is_symlink():
        raise BackupError("rehearsal plaintext payload cleanup failed")
    _fsync_private_directory(stage, label="rehearsal control stage")


def _remove_rehearsal_control_stage(stage: Path, parent: Path) -> None:
    """Remove payload-free control state in a deterministic durable order."""

    payload = stage / "payload"
    if payload.exists() or payload.is_symlink():
        raise BackupError("rehearsal control cleanup still contains payload")
    allowed = {
        _REHEARSAL_MARKER,
        _REHEARSAL_ACTIVITY_LOCK,
        _REHEARSAL_EXTRACTION_COMPLETE,
        _REHEARSAL_GUARDIAN_PID,
    }
    for entry in stage.iterdir():
        metadata = entry.lstat()
        if (
            entry.name not in allowed
            or entry.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise BackupError("rehearsal control residue is unsafe")

    # The marker disappears first. Every recoverable markerless state is
    # therefore already payload-free and contains only the exact controls
    # admitted above. The activity path disappears last while its descriptor
    # remains exclusively locked by the caller.
    marker = stage / _REHEARSAL_MARKER
    if marker.exists():
        marker.unlink()
        _fsync_private_directory(stage, label="rehearsal control stage")
    for name in (_REHEARSAL_EXTRACTION_COMPLETE, _REHEARSAL_GUARDIAN_PID):
        control = stage / name
        if control.exists():
            control.unlink()
    _fsync_private_directory(stage, label="rehearsal control stage")
    activity = stage / _REHEARSAL_ACTIVITY_LOCK
    if activity.exists():
        activity.unlink()
        _fsync_private_directory(stage, label="rehearsal control stage")
    stage.rmdir()
    _fsync_private_directory(parent, label="rehearsal parent")


def _rehearsal_guardian(
    source: str,
    stage_path: str,
    expected_artifact_sha256: str,
    phase_timeout_s: float,
    extraction_target: Any,
    replay_target: Any,
    key_pipe: Any,
    result_pipe: Any,
    liveness_pipe: Any,
) -> None:
    """Own plaintext, both distinct workers, and cleanup across parent death."""

    stage = Path(stage_path)
    payload = stage / "payload"
    lock_path = stage / _REHEARSAL_ACTIVITY_LOCK
    descriptor: int | None = None
    controller_connected = True
    cleanup_lease_exclusive = False

    def _remove_owned_payload() -> None:
        nonlocal cleanup_lease_exclusive
        if descriptor is None:
            raise BackupError("rehearsal guardian activity lease is unavailable")
        if not cleanup_lease_exclusive:
            _upgrade_guardian_cleanup_lease(descriptor)
            cleanup_lease_exclusive = True
        _remove_rehearsal_payload(stage)

    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        )
        # Workers acquire the same shared lease. Stale cleanup requires an
        # exclusive lease, so guardian death cannot expose their payload to a
        # second controller while a native worker is still alive.
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        metadata = os.fstat(descriptor)
        current = lock_path.lstat()
        marker = stage / _REHEARSAL_MARKER
        marker_metadata = marker.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or current.st_dev != metadata.st_dev
            or current.st_ino != metadata.st_ino
            or not stat.S_ISREG(marker_metadata.st_mode)
            or marker_metadata.st_uid != os.getuid()
            or marker_metadata.st_mode & 0o077
        ):
            raise BackupError("rehearsal guardian stage ownership is invalid")
        atomic_write(
            stage / _REHEARSAL_GUARDIAN_PID,
            str(os.getpid()).encode("ascii"),
            mode=0o600,
        )
        deadline = time.monotonic() + phase_timeout_s
        while not key_pipe.poll(0.05):
            if _controller_disconnected(liveness_pipe):
                raise _ControllerDisconnected
            if time.monotonic() >= deadline:
                raise BackupError("rehearsal guardian did not receive key material")
        try:
            key_material = key_pipe.recv_bytes()
        except EOFError as exc:
            raise _ControllerDisconnected from exc
        if len(key_material) != 64:
            raise BackupError("backup rehearsal key material is invalid")
        backup_key = key_material[:32]
        event_key = key_material[32:]
        extraction, extraction_pid = _run_guardian_phase(
            extraction_target,
            (source, str(payload), expected_artifact_sha256),
            backup_key,
            stage=stage,
            timeout_s=phase_timeout_s,
            liveness_pipe=liveness_pipe,
        )
        if extraction.get("status") != "healthy":
            raise BackupError(str(extraction.get("error_code", "extraction_failed")))
        atomic_write(
            stage / _REHEARSAL_EXTRACTION_COMPLETE,
            b"healthy",
            mode=0o600,
        )
        # Hold a short liveness handoff after extraction has exited and before
        # replay is allowed to start. Controller EOF at this exact boundary is
        # therefore owned by the same guardian, never by an unowned stage.
        liveness_pipe.poll(0.25)
        if _controller_disconnected(liveness_pipe):
            raise _ControllerDisconnected
        replay, replay_pid = _run_guardian_phase(
            replay_target,
            (str(payload / "state"),),
            event_key,
            stage=stage,
            timeout_s=phase_timeout_s,
            liveness_pipe=liveness_pipe,
        )
        if replay.get("status") != "healthy":
            raise BackupError(str(replay.get("error_code", "replay_failed")))
        if replay.get("event_log_sha256") != extraction.get("event_log_sha256"):
            raise BackupError("rehearsal event-log identity differs from manifest")
        _remove_owned_payload()
        if _controller_disconnected(liveness_pipe):
            raise _ControllerDisconnected
        result_pipe.send(
            {
                "status": "healthy",
                "extraction": extraction,
                "extraction_pid": extraction_pid,
                "replay": replay,
                "replay_pid": replay_pid,
            }
        )
    except _ControllerDisconnected:
        controller_connected = False
        try:
            _remove_owned_payload()
        except Exception:
            # The marker remains outside the payload, so a later controller can
            # safely recognize and finish cleanup once this guardian exits.
            pass
    except _WorkerNotReaped as exc:
        # Never remove payload without terminal proof. The unreaped worker
        # retains its shared activity lease, so every later cleanup refuses.
        try:
            if controller_connected:
                result_pipe.send(
                    {"status": "failed", "error_code": _child_failure_code(exc)}
                )
        except (BrokenPipeError, EOFError, OSError):
            pass
    except BaseException as exc:
        try:
            _remove_owned_payload()
        except Exception:
            pass
        try:
            if controller_connected:
                result_pipe.send(
                    {"status": "failed", "error_code": _child_failure_code(exc)}
                )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        try:
            key_pipe.close()
        except OSError:
            pass
        try:
            result_pipe.close()
        except OSError:
            pass
        try:
            liveness_pipe.close()
        except OSError:
            pass
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


class BackupManager:
    def __init__(
        self,
        paths: DeploymentPaths,
        *,
        root: Path | None = None,
        test_mode: bool = False,
    ) -> None:
        self.paths = paths
        self.root = (root or repository_root()).resolve()
        self.test_mode = test_mode
        self.store = secret_store(paths, test_mode=test_mode)
        self._services: ServiceManager | None = None

    @property
    def services(self) -> ServiceManager:
        if self._services is None:
            self._services = ServiceManager(self.paths, test_mode=self.test_mode)
        return self._services

    def create(self, target: Path | None = None) -> dict[str, Any]:
        if not self.paths.instance_dir.is_dir():
            raise BackupError("active state directory is missing")
        destination = target or (
            self.paths.backup_dir / f"administrateme-{_utc_stamp()}.ambackup"
        )
        destination = ensure_within(destination, self.paths.backup_dir)
        references = self._references()
        backup_key = self.store.get(references["backup"])
        before = self.services.status()
        should_restart = before.status in {"healthy", "degraded", "failed"}
        if should_restart:
            self.services.stop()
        try:
            payload, manifest = self._build_archive()
            nonce = os.urandom(12)
            ciphertext = AESGCM(backup_key).encrypt(
                nonce,
                payload,
                b"administrateme-backup-v1",
            )
            envelope = {
                "format": "administrateme-backup-v1",
                "version": BACKUP_FORMAT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "manifest_sha256": manifest["manifest_sha256"],
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            }
            atomic_write(
                destination,
                json.dumps(envelope, sort_keys=True).encode("utf-8"),
                mode=0o600,
            )
        finally:
            if should_restart:
                self.services.start()
        return {
            "status": "created",
            "path": str(destination),
            "file_count": manifest["file_count"],
            "event_log_sha256": manifest.get("event_log_sha256"),
            "manifest_sha256": manifest["manifest_sha256"],
            "encrypted": True,
            "services_restarted": should_restart,
        }

    def rehearse(
        self,
        backup: Path,
        *,
        candidate: Path | None = None,
        receipt: Path | None = None,
        timeout_s: float = 1800.0,
    ) -> dict[str, Any]:
        """Authenticate and replay a backup without targeting live state."""

        source = ensure_within(backup, self.paths.backup_dir)
        if source.is_symlink() or not source.is_file():
            raise BackupError("backup file is missing or unsafe")
        candidate_root = (candidate or self.root).resolve()
        candidate_commit = self._candidate_commit(candidate_root)
        references = self._references()
        backup_key = self.store.get(references["backup"])
        event_key = self.store.get(references["event_log"])
        artifact_sha256 = _sha256_private_regular_file(
            source,
            maximum_size=_MAX_BACKUP_BYTES,
        )
        isolation_before = self._isolation_snapshot()
        predecessor = self._predecessor_evidence()
        if isolation_before["pointer"] != predecessor["pointer_sha256"]:
            raise BackupError("active predecessor changed during evidence capture")
        instance_fingerprint = hashlib.sha256(
            str(self.paths.root).encode("utf-8")
        ).hexdigest()
        parent = Path(tempfile.gettempdir()) / (
            f"administrateme-backup-rehearsal-{instance_fingerprint[:16]}"
        )
        if parent.is_symlink():
            raise BackupError("rehearsal parent is a symlink")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent_metadata = parent.lstat()
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.getuid()
        ):
            raise BackupError("rehearsal parent ownership is unsafe")
        parent.chmod(0o700)
        lock_descriptor = os.open(
            parent / ".lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        lock_metadata = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != os.getuid()
            or lock_metadata.st_mode & 0o077
        ):
            os.close(lock_descriptor)
            raise BackupError("rehearsal lock ownership or mode is unsafe")
        stage: Path | None = None
        pending_stage: Path | None = None
        nonce: str | None = None
        staging_outside_deployment_root = False
        extraction_pid: int | None = None
        replay_pid: int | None = None
        try:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise BackupError(
                    "another backup rehearsal is already running"
                ) from exc
            self._remove_validated_stale_stages(parent, instance_fingerprint)
            nonce = os.urandom(16).hex()
            stage = parent / f"run-{nonce}"
            pending_stage = parent / f".pending-{nonce}"
            pending_stage.mkdir(mode=0o700)
            stage_resolved = stage.resolve()
            deployment_root = self.paths.root.resolve()
            staging_outside_deployment_root = (
                stage_resolved != deployment_root
                and deployment_root not in stage_resolved.parents
            )
            if not staging_outside_deployment_root:
                raise BackupError("rehearsal staging is inside the deployment root")
            atomic_write(
                pending_stage / _REHEARSAL_MARKER,
                json.dumps(
                    {
                        "contract": _REHEARSAL_CONTRACT,
                        "instance_fingerprint": instance_fingerprint,
                        "nonce": nonce,
                    },
                    sort_keys=True,
                ).encode("utf-8"),
                mode=0o600,
            )
            activity_descriptor = os.open(
                pending_stage / _REHEARSAL_ACTIVITY_LOCK,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(activity_descriptor)
            (pending_stage / "payload").mkdir(mode=0o700)
            os.replace(pending_stage, stage)
            pending_stage = None
            pipeline, _guardian_pid = self._run_rehearsal_guardian(
                str(source),
                str(stage),
                artifact_sha256,
                timeout_s,
                bytes(backup_key) + bytes(event_key),
                timeout_s=(timeout_s * 2) + 30,
            )
            if pipeline.get("status") != "healthy":
                raise BackupError(
                    str(pipeline.get("error_code", "rehearsal_pipeline_failed"))
                )
            extraction = pipeline.get("extraction")
            replay = pipeline.get("replay")
            extraction_pid = pipeline.get("extraction_pid")
            replay_pid = pipeline.get("replay_pid")
            if (
                not isinstance(extraction, dict)
                or not isinstance(replay, dict)
                or not isinstance(extraction_pid, int)
                or not isinstance(replay_pid, int)
                or extraction_pid <= 0
                or replay_pid <= 0
                or extraction_pid == replay_pid
            ):
                raise BackupError("rehearsal pipeline evidence is invalid")
            self._remove_validated_stale_stages(parent, instance_fingerprint)
            if stage.exists():
                raise BackupError("rehearsal staging cleanup failed")
            stage = None
            if self._isolation_snapshot() != isolation_before:
                raise BackupError(
                    "live deployment coordinates changed during rehearsal"
                )
            ending_candidate_commit = self._candidate_commit(candidate_root)
            if ending_candidate_commit != candidate_commit:
                raise BackupError("candidate identity changed during rehearsal")
            ending_predecessor = self._predecessor_evidence()
            if ending_predecessor != predecessor:
                raise BackupError("active predecessor changed during rehearsal")
            tested_at = datetime.now(timezone.utc).isoformat()
            result: dict[str, Any] = {
                "contract": _REHEARSAL_CONTRACT,
                "schema_version": 1,
                "status": "healthy",
                "tested_at": tested_at,
                "candidate": {
                    "commit": candidate_commit,
                    "clean_checkout_at_start": True,
                    "clean_checkout_at_end": True,
                    "controller_coordinate_matches": (
                        Path(__file__).resolve().parents[1] == candidate_root
                    ),
                    "execution_immutability": "not_evaluated",
                },
                "predecessor": predecessor,
                "artifact": {
                    "format": _BACKUP_FORMAT,
                    "version": BACKUP_FORMAT_VERSION,
                    "sha256": extraction["artifact_sha256"],
                    "manifest_sha256": extraction["manifest_sha256"],
                    "event_log_sha256": extraction["event_log_sha256"],
                    "created_at": extraction["created_at"],
                    "file_count": extraction["file_count"],
                    "authenticated": True,
                    "exact_manifest_members": True,
                },
                "watermark": replay["watermark"],
                "replay": replay["replay"],
                "workbooks": replay["workbooks"],
                "offline_replay_checks": {
                    "contract": "backup-rehearsal-offline-checks-v1",
                    "status": "healthy",
                    "failed_applicable_check_count": 0,
                    "live_checks": "not_evaluated",
                    "activation_eligible": False,
                    "provider_components_instantiated": 0,
                    "event_sink_mode": "rejecting",
                    "external_effects": "not_evaluated",
                },
                "isolation": {
                    "staging_outside_deployment_root": (
                        staging_outside_deployment_root
                    ),
                    "active_state_replaced": False,
                    "active_release_pointer_changed": False,
                    "rollback_tree_changed": False,
                    "service_manager_constructed": False,
                    "extraction_and_replay_processes_distinct": (
                        extraction_pid is not None
                        and replay_pid is not None
                        and extraction_pid != replay_pid
                    ),
                    "staging_destroyed": True,
                    "plaintext_staged": True,
                    "plaintext_stage_destroyed": True,
                },
                "source_events_appended": replay["source_events_appended"],
                "content_emitted_in_receipt": False,
            }
            result["receipt_sha256"] = hashlib.sha256(
                json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            target = receipt or (
                self.paths.backup_dir
                / (
                    f"backup-rehearsal-{candidate_commit}-{_utc_stamp()}-"
                    f"{nonce[:8]}.json"
                )
            )
            target = ensure_within(target, self.paths.backup_dir)
            if target == source:
                raise BackupError("rehearsal receipt cannot replace the backup")
            _write_new_private_file(
                target,
                json.dumps(result, indent=2, sort_keys=True).encode("utf-8"),
            )
            return {**result, "receipt_written": True}
        finally:
            if pending_stage is not None and pending_stage.exists():
                if (
                    nonce is None
                    or pending_stage.is_symlink()
                    or pending_stage.parent != parent
                    or pending_stage.name != f".pending-{nonce}"
                ):
                    raise BackupError("pending rehearsal cleanup target is unsafe")
                shutil.rmtree(pending_stage)
            if stage is not None and stage.exists():
                if (
                    nonce is None
                    or stage.is_symlink()
                    or stage.parent != parent
                    or stage.name != f"run-{nonce}"
                ):
                    raise BackupError("rehearsal staging cleanup target is unsafe")
                self._remove_validated_stale_stages(
                    parent,
                    instance_fingerprint,
                )
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(lock_descriptor)

    def restore(self, backup: Path) -> dict[str, Any]:
        source = ensure_within(backup, self.paths.backup_dir)
        if not source.is_file():
            raise BackupError("backup file is missing")
        references = self._references()
        backup_key = self.store.get(references["backup"])
        event_key = self.store.get(references["event_log"])
        envelope, payload, _artifact_sha256 = _authenticate_backup(source, backup_key)
        staging_root = Path(
            tempfile.mkdtemp(prefix="restore-staging-", dir=self.paths.root)
        )
        try:
            manifest = self._extract_and_validate(payload, staging_root)
            if manifest["manifest_sha256"] != envelope["manifest_sha256"]:
                raise BackupError("backup manifest identity does not match envelope")
            staged_state = staging_root / "state"
            replay = asyncio.run(self._prove_rebuild(staged_state, event_key))
            previous = self.services.status()
            should_restart = previous.status in {"healthy", "degraded", "failed"}
            if should_restart:
                self.services.stop()
            rollback = self.paths.rollback_dir / f"state-before-{_utc_stamp()}"
            ensure_within(rollback, self.paths.rollback_dir)
            try:
                os.replace(self.paths.instance_dir, rollback)
                os.replace(staged_state, self.paths.instance_dir)
                if should_restart:
                    self.services.start()
                diagnostics = Doctor(
                    self.paths,
                    root=self.root,
                    test_mode=self.test_mode,
                ).run(include_synthetic_proof=False)
                if diagnostics["status"] != "healthy":
                    failed_checks = ", ".join(
                        check["name"]
                        for check in diagnostics["checks"]
                        if not check["ok"]
                    )
                    raise BackupError(
                        f"restored state failed Doctor validation: {failed_checks}"
                    )
            except Exception:
                if self.paths.instance_dir.exists():
                    failed = self.paths.rollback_dir / f"failed-restore-{_utc_stamp()}"
                    os.replace(self.paths.instance_dir, failed)
                if rollback.exists():
                    os.replace(rollback, self.paths.instance_dir)
                if should_restart:
                    self.services.start()
                raise
            return {
                "status": "restored",
                "backup": str(source),
                "rollback": str(rollback),
                "manifest_sha256": manifest["manifest_sha256"],
                "projections_rebuilt": replay["replay"]["projections_rebuilt"],
                "workbooks_rebuilt": replay["workbooks"]["rebuilt_count"] == 2,
                "workbook_rebuild_count": replay["workbooks"]["rebuilt_count"],
                "sidecars_rebuilt": (
                    replay["workbooks"]["bidirectional_sidecar_count"]
                    + replay["workbooks"]["readonly_sidecar_count"]
                ),
                "services_restarted": should_restart,
            }
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root)

    def _references(self) -> dict[str, str]:
        path = self.paths.instance_dir / "config" / "secret-references.json"
        if not path.is_file():
            raise BackupError("secret-reference metadata is missing")
        return json.loads(path.read_text("utf-8"))["references"]

    def _build_archive(self) -> tuple[bytes, dict[str, Any]]:
        files: list[dict[str, Any]] = []
        payloads: dict[str, bytes] = {}
        for path in sorted(self.paths.instance_dir.rglob("*")):
            if path.is_symlink():
                # OpenClaw materializes plugin skill discovery as generated
                # links into reviewed release content.  They are runtime cache,
                # not household state, and are recreated on service startup.
                plugin_skills = (
                    self.paths.instance_dir / "openclaw" / "runtime" / "plugin-skills"
                )
                if path.parent == plugin_skills:
                    continue
                raise BackupError(f"backup refuses symlink: {path.name}")
            if not path.is_file():
                continue
            relative = Path("state") / path.relative_to(self.paths.instance_dir)
            try:
                payload = path.read_bytes()
            except FileNotFoundError:
                # SQLite removes quiesced shared-memory and WAL sidecars after
                # the directory walk can already have observed them. They are
                # never durable state; any other disappearing file fails closed.
                if path.name.endswith(("-shm", "-wal")):
                    continue
                raise
            relative_name = relative.as_posix()
            payloads[relative_name] = payload
            files.append(
                {
                    "path": relative_name,
                    "size": len(payload),
                    "sha256": __import__("hashlib").sha256(payload).hexdigest(),
                }
            )
        manifest: dict[str, Any] = {
            "format": "administrateme-backup-manifest-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "file_count": len(files),
            "files": files,
            "projection_names": list(PROJECTION_NAMES),
        }
        event_entry = next(
            (item for item in files if item["path"] == "state/events/events.db"),
            None,
        )
        if event_entry is None:
            raise BackupError("backup source omits the encrypted event log")
        manifest["event_log_sha256"] = event_entry["sha256"]
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        manifest["manifest_sha256"] = (
            __import__("hashlib").sha256(canonical).hexdigest()
        )
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for item in files:
                info = tarfile.TarInfo(item["path"])
                info.size = item["size"]
                info.mode = 0o600
                archive.addfile(info, io.BytesIO(payloads[item["path"]]))
            encoded = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(encoded)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(encoded))
        return buffer.getvalue(), manifest

    def _extract_and_validate(
        self,
        payload: bytes,
        staging_root: Path,
    ) -> dict[str, Any]:
        return _extract_and_validate_payload(payload, staging_root)

    async def _prove_rebuild(
        self,
        staged_state: Path,
        event_key: bytes,
    ) -> dict[str, Any]:
        return await _offline_replay(staged_state, event_key)

    def _candidate_commit(self, candidate: Path) -> str:
        if not candidate.is_dir() or candidate.is_symlink():
            raise BackupError("candidate checkout is missing or unsafe")
        if not self.test_mode and Path(__file__).resolve().parents[1] != candidate:
            raise BackupError("rehearsal controller is not the reviewed candidate")
        try:
            commit = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
            dirty = subprocess.run(
                ["git", "-C", str(candidate), "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackupError("candidate Git identity cannot be established") from exc
        if _HEX_40.fullmatch(commit) is None or dirty:
            raise BackupError("candidate checkout is dirty or has an invalid commit")
        return commit

    def _predecessor_evidence(self) -> dict[str, Any]:
        pointer = self.paths.app_dir / "current-release.json"
        if pointer.is_symlink() or not pointer.is_file():
            raise BackupError("active predecessor pointer is missing or unsafe")
        encoded, _metadata = _read_regular_file_once(
            pointer,
            maximum_size=1024 * 1024,
        )
        try:
            document = json.loads(encoded.decode("utf-8"))
            commit = str(document["commit"])
            release = ensure_within(
                Path(document["path"]),
                self.paths.app_dir / "releases",
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BackupError("active predecessor pointer is invalid") from exc
        if _HEX_40.fullmatch(commit) is None or not release.is_dir():
            raise BackupError("active predecessor release is invalid")
        if release.is_symlink() or release.name != commit:
            raise BackupError("active predecessor release identity is invalid")
        marker = release / ".administrateme-source-commit"
        marker_matches = False
        if marker.is_file() and not marker.is_symlink():
            marker_metadata = marker.lstat()
            marker_matches = (
                stat.S_ISREG(marker_metadata.st_mode)
                and marker_metadata.st_uid == os.getuid()
                and marker.read_text("utf-8").strip() == commit
            )
        if not marker_matches:
            raise BackupError("active predecessor source identity is invalid")
        return {
            "commit": commit,
            "pointer_sha256": hashlib.sha256(encoded).hexdigest(),
            "retained": True,
            "path_commit_coordinate_matches": True,
            "source_marker_matches": True,
            "immutable_release_valid": "not_evaluated",
            "rollback_compatible": "not_evaluated",
            "automatic_rollback_eligible": "not_evaluated",
            "automatic_rollback_executed": False,
        }

    def _isolation_snapshot(self) -> dict[str, str | None]:
        def _digest(path: Path) -> str | None:
            if not path.exists():
                return None
            if path.is_symlink() or not path.is_file():
                raise BackupError("live deployment coordinate is unsafe")
            return sha256_file(path)

        rollback_digest = hashlib.sha256()
        if self.paths.rollback_dir.exists():
            for path in sorted(self.paths.rollback_dir.rglob("*")):
                relative = path.relative_to(self.paths.rollback_dir).as_posix()
                metadata = path.lstat()
                rollback_digest.update(relative.encode("utf-8"))
                rollback_digest.update(
                    f":{metadata.st_mode}:{metadata.st_size}:{metadata.st_mtime_ns}".encode()
                )
        return {
            "pointer": _digest(self.paths.app_dir / "current-release.json"),
            "service_state": _digest(self.paths.service_state_path),
            "rollback_inventory": rollback_digest.hexdigest(),
        }

    @staticmethod
    def _valid_stage_marker(
        marker: Path,
        *,
        instance_fingerprint: str,
    ) -> bool:
        if marker.is_symlink() or not marker.is_file():
            return False
        try:
            document = json.loads(marker.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            document.get("contract") == _REHEARSAL_CONTRACT
            and document.get("instance_fingerprint") == instance_fingerprint
            and isinstance(document.get("nonce"), str)
            and re.fullmatch(r"[0-9a-f]{32}", document["nonce"]) is not None
            and marker.parent.name == f"run-{document['nonce']}"
        )

    def _remove_validated_stale_stages(
        self,
        parent: Path,
        instance_fingerprint: str,
    ) -> None:
        for pending in parent.glob(".pending-*"):
            metadata = pending.lstat()
            if (
                pending.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
                or re.fullmatch(r"\.pending-[0-9a-f]{32}", pending.name) is None
            ):
                raise BackupError("rehearsal parent contains an unsafe pending entry")
            # Children receive only the atomically published run-* path, so a
            # pending stage can never have an active plaintext writer.
            shutil.rmtree(pending)
            _fsync_private_directory(parent, label="rehearsal parent")
        for stage in parent.glob("run-*"):
            stage_metadata = stage.lstat()
            if (
                stage.is_symlink()
                or not stat.S_ISDIR(stage_metadata.st_mode)
                or stage_metadata.st_uid != os.getuid()
                or stage_metadata.st_mode & 0o077
                or re.fullmatch(r"run-[0-9a-f]{32}", stage.name) is None
            ):
                raise BackupError("rehearsal parent contains an unsafe stale entry")
            marker = stage / _REHEARSAL_MARKER
            marker_valid = self._valid_stage_marker(
                marker,
                instance_fingerprint=instance_fingerprint,
            )
            if not marker_valid:
                # Ordered cleanup removes payload before control evidence. A
                # crash after marker removal may therefore leave only private,
                # known control files. Such residue is safe to finish; any
                # markerless payload or unknown entry remains fail-closed.
                if marker.exists() or marker.is_symlink():
                    raise BackupError(
                        "rehearsal parent contains an unowned stale entry"
                    )
                payload = stage / "payload"
                if payload.exists() or payload.is_symlink():
                    raise BackupError(
                        "markerless rehearsal residue still contains payload"
                    )
                allowed = {
                    _REHEARSAL_ACTIVITY_LOCK,
                    _REHEARSAL_EXTRACTION_COMPLETE,
                    _REHEARSAL_GUARDIAN_PID,
                }
                for entry in stage.iterdir():
                    metadata = entry.lstat()
                    if (
                        entry.name not in allowed
                        or entry.is_symlink()
                        or not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_uid != os.getuid()
                        or metadata.st_mode & 0o077
                    ):
                        raise BackupError(
                            "markerless rehearsal control residue is unsafe"
                        )
            activity = stage / _REHEARSAL_ACTIVITY_LOCK
            if not activity.exists():
                if marker_valid:
                    raise BackupError("rehearsal activity lock is missing")
                _remove_rehearsal_control_stage(stage, parent)
                continue
            descriptor = os.open(
                activity,
                os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or metadata.st_mode & 0o077
                ):
                    raise BackupError("rehearsal activity lock is unsafe")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise BackupError(
                        "an orphan rehearsal child still owns its stage"
                    ) from exc
                current = activity.lstat()
                if (
                    current.st_dev != metadata.st_dev
                    or current.st_ino != metadata.st_ino
                ):
                    raise BackupError("rehearsal activity lock changed")
                # Plaintext is always removed and fsynced before ordered
                # control cleanup unlinks the ownership marker. If control
                # cleanup is interrupted, the markerless-residue branch above
                # can safely finish because payload is already absent.
                _remove_rehearsal_payload(stage)
                _remove_rehearsal_control_stage(stage, parent)
            finally:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    @staticmethod
    def _run_rehearsal_guardian(
        source: str,
        stage: str,
        artifact_sha256: str,
        phase_timeout_s: float,
        key_material: bytes,
        *,
        timeout_s: float,
        extraction_target: Any = _extract_child,
        replay_target: Any = _replay_child,
    ) -> tuple[dict[str, Any], int]:
        context = mp.get_context("spawn")
        key_reader, key_writer = context.Pipe(duplex=False)
        result_reader, result_writer = context.Pipe(duplex=False)
        liveness_reader, liveness_writer = context.Pipe(duplex=False)
        process = context.Process(
            target=_rehearsal_guardian,
            args=(
                source,
                stage,
                artifact_sha256,
                phase_timeout_s,
                extraction_target,
                replay_target,
                key_reader,
                result_writer,
                liveness_reader,
            ),
            daemon=False,
        )
        result: dict[str, Any] | None = None
        try:
            process.start()
            key_reader.close()
            result_writer.close()
            liveness_reader.close()
            key_writer.send_bytes(key_material)
            key_writer.close()
            if not result_reader.poll(timeout_s):
                raise BackupError("backup rehearsal guardian failed or timed out")
            try:
                received = result_reader.recv()
            except EOFError:
                received = None
            result = received if isinstance(received, dict) else None
            process.join(timeout=10)
            if process.is_alive():
                raise BackupError("backup rehearsal guardian failed to exit")
            if process.exitcode != 0 or result is None:
                raise BackupError("backup rehearsal guardian failed or timed out")
            return result, int(process.pid or 0)
        finally:
            # EOF is the guardian's authoritative controller-death signal. It
            # owns and reaps any active phase before removing plaintext.
            for connection in (
                liveness_writer,
                key_reader,
                key_writer,
                result_reader,
                result_writer,
                liveness_reader,
            ):
                try:
                    connection.close()
                except OSError:
                    pass
            if process.pid is not None and process.is_alive():
                process.join(timeout=10)
            if process.pid is not None and process.is_alive():
                try:
                    process.terminate()
                finally:
                    process.join(timeout=5)
            if process.pid is not None and process.is_alive():
                try:
                    process.kill()
                finally:
                    process.join(timeout=5)
            if process.pid is not None and process.is_alive():
                raise BackupError("backup rehearsal guardian could not be reaped")
            if process.pid is not None:
                process.close()


__all__ = ["BackupError", "BackupManager"]
