"""Consistent encrypted backups and validated rollback-safe restores."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from adminme.runtime.application import AdministrateMeRuntime, RuntimeSettings
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


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
        self.services = ServiceManager(paths, test_mode=test_mode)

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

    def restore(self, backup: Path) -> dict[str, Any]:
        source = ensure_within(backup, self.paths.backup_dir)
        if not source.is_file():
            raise BackupError("backup file is missing")
        references = self._references()
        backup_key = self.store.get(references["backup"])
        event_key = self.store.get(references["event_log"])
        envelope = json.loads(source.read_text("utf-8"))
        if (
            envelope.get("format") != "administrateme-backup-v1"
            or envelope.get("version") != BACKUP_FORMAT_VERSION
        ):
            raise BackupError("unsupported backup format")
        try:
            payload = AESGCM(backup_key).decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                b"administrateme-backup-v1",
            )
        except Exception as exc:
            raise BackupError("backup decryption or authentication failed") from exc
        staging_root = Path(
            tempfile.mkdtemp(prefix="restore-staging-", dir=self.paths.root)
        )
        try:
            manifest = self._extract_and_validate(payload, staging_root)
            if manifest["manifest_sha256"] != envelope["manifest_sha256"]:
                raise BackupError("backup manifest identity does not match envelope")
            staged_state = staging_root / "state"
            asyncio.run(self._prove_rebuild(staged_state, event_key))
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
                "projections_rebuilt": len(PROJECTION_NAMES),
                "workbooks_rebuilt": True,
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
        for path in sorted(self.paths.instance_dir.rglob("*")):
            if path.is_symlink():
                raise BackupError(f"backup refuses symlink: {path.name}")
            if not path.is_file():
                continue
            relative = Path("state") / path.relative_to(self.paths.instance_dir)
            files.append(
                {
                    "path": relative.as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
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
        if event_entry:
            manifest["event_log_sha256"] = event_entry["sha256"]
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        manifest["manifest_sha256"] = __import__("hashlib").sha256(canonical).hexdigest()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for item in files:
                source = self.paths.root / item["path"]
                archive.add(source, arcname=item["path"], recursive=False)
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
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
            members = archive.getmembers()
            for member in members:
                pure = PurePosixPath(member.name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise BackupError("backup contains an unsafe path")
                if member.issym() or member.islnk() or member.isdev():
                    raise BackupError("backup contains an unsupported link or device")
                target = (staging_root / Path(*pure.parts)).resolve()
                if staging_root.resolve() not in target.parents and target != staging_root:
                    raise BackupError("backup extraction escaped staging root")
            archive.extractall(staging_root)
        manifest_path = staging_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        canonical = dict(manifest)
        expected = canonical.pop("manifest_sha256")
        digest = __import__("hashlib").sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if digest != expected:
            raise BackupError("backup manifest was tampered with")
        for item in manifest["files"]:
            path = staging_root / item["path"]
            if not path.is_file() or sha256_file(path) != item["sha256"]:
                raise BackupError(f"backup file failed integrity: {item['path']}")
        return manifest

    async def _prove_rebuild(self, staged_state: Path, event_key: bytes) -> None:
        workbook_dir = staged_state / "projections" / "xlsx_workbooks"
        existing_workbooks = {
            path.name for path in workbook_dir.glob("*.xlsx") if path.is_file()
        }
        settings = RuntimeSettings.credential_free(
            staged_state,
            encryption_key=event_key,
            repository_root=self.root,
            member_roles={"member_restore_validator": "principal"},
            openclaw_enabled=True,
            openclaw_shared_secret="fixture-openclaw-shared-secret-32",
            openclaw_actor_identities={
                "fixture-channel|fixture-account|fixture-actor": "member_restore_validator"
            },
        )
        runtime = AdministrateMeRuntime(settings)
        await runtime.start()
        try:
            assert runtime.projections is not None
            if set(runtime.projections.projection_names()) != set(PROJECTION_NAMES):
                raise BackupError("restored projection registry is incomplete")
            for name in PROJECTION_NAMES:
                await runtime.projections.rebuild(name)
            missing_workbooks = [
                name for name in existing_workbooks if not (workbook_dir / name).is_file()
            ]
            if missing_workbooks:
                raise BackupError(
                    f"restored workbook rebuild omitted: {', '.join(missing_workbooks)}"
                )
            diagnostics = await runtime.diagnostics()
            if not diagnostics["ready"]:
                raise BackupError("restored runtime is not ready after replay")
        finally:
            await runtime.stop()


__all__ = ["BackupError", "BackupManager"]
