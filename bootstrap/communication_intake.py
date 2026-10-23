"""Read retained intake/write boundaries without constructing a household runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import select
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from adminme.apple_service_permissions import (
    CONTRACT as APPLE_SERVICE_WRITE_CONTRACT,
    EVENT_TYPE as APPLE_SERVICE_PERMISSION_EVENT,
    PROVIDERS as APPLE_SERVICE_PROVIDERS,
)
from adminme.communication_intake_policy import (
    POLICY_EVENT_TYPE,
    POLICY_ID,
    read_policy_epoch,
)
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from bootstrap.contracts import DeploymentPaths
from bootstrap.deployment_budget import deployment_clock
from bootstrap.secrets import secret_store


# Match the existing candidate Keychain probe's eight-second access boundary.
# Cleanup is separately reserved so a stuck prompt cannot consume recovery time.
_INSPECTION_SECONDS = 8.0
_CLEANUP_SECONDS = 2.0
INSPECTION_MAX_SECONDS = _INSPECTION_SECONDS + _CLEANUP_SECONDS
_NO_POLICY_EXIT = 10
_EXPLICIT_POLICY_EXIT = 11
_APPLE_WRITES_EXIT = 12
_EXPLICIT_AND_APPLE_WRITES_EXIT = 13
_EXIT_REQUIREMENTS = {
    _NO_POLICY_EXIT: frozenset[str](),
    _EXPLICIT_POLICY_EXIT: frozenset({POLICY_ID}),
    _APPLE_WRITES_EXIT: frozenset({APPLE_SERVICE_WRITE_CONTRACT}),
    _EXPLICIT_AND_APPLE_WRITES_EXIT: frozenset(
        {POLICY_ID, APPLE_SERVICE_WRITE_CONTRACT}
    ),
}
_APPLE_WRITE_FACT_TYPES = (
    APPLE_SERVICE_PERMISSION_EVENT,
    "member.provider_action.confirmed",
    "external_sync.write_requested",
    "external_sync.write_completed",
    "external_sync.write_failed",
)


class CommunicationIntakeInspectionError(RuntimeError):
    pass


def _require_file(path: Path, *, root: Path) -> None:
    """Reject redirects and writable source coordinates before resolving secrets."""
    path.relative_to(root)
    for coordinate in (*reversed(path.parents), path):
        if coordinate != root and root not in coordinate.parents:
            continue
        metadata = coordinate.lstat()
        expected = stat.S_ISREG if coordinate == path else stat.S_ISDIR
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not expected(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o022
        ):
            raise CommunicationIntakeInspectionError(
                "communication intake inspection coordinate is unsafe"
            )


def _group_exists(group_id: int) -> bool:
    try:
        os.killpg(group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap_inspection(process: subprocess.Popen[bytes]) -> None:
    """Bound cleanup, reap the child, and reject unknown descendant terminality."""
    deadline = deployment_clock() + _CLEANUP_SECONDS
    for signal_value, grace in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 1.5)):
        process.poll()  # Reap a terminated direct child before testing its group.
        if not _group_exists(process.pid):
            break
        try:
            os.killpg(process.pid, signal_value)
        except ProcessLookupError:
            break
        except OSError as exc:
            raise CommunicationIntakeInspectionError(
                "communication intake inspection process termination is unproven"
            ) from exc
        phase_deadline = min(deadline, deployment_clock() + grace)
        while deployment_clock() < phase_deadline:
            process.poll()
            if not _group_exists(process.pid):
                break
            time.sleep(min(0.01, max(0.0, phase_deadline - deployment_clock())))
    process.poll()
    if process.returncode is None or _group_exists(process.pid):
        raise CommunicationIntakeInspectionError(
            "communication intake inspection did not prove process termination; "
            "release mutation refused"
        )


def _bounded_instance_policies(paths: DeploymentPaths) -> frozenset[str]:
    """The controller never resolves Keychain material or accepts child output."""
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("ADMINME_TEST_MODE", None)
    environment["ADMINME_REPOSITORY_ROOT"] = str(root)
    deadline = deployment_clock() + _INSPECTION_SECONDS
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "bootstrap.communication_intake",
                "--instance-root",
                str(paths.root),
            ],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        code = process.wait(timeout=max(0.0, deadline - deployment_clock()))
        if _group_exists(process.pid):
            raise CommunicationIntakeInspectionError(
                "communication intake inspection left a background process"
            )
        if code in _EXIT_REQUIREMENTS:
            return _EXIT_REQUIREMENTS[code]
        raise CommunicationIntakeInspectionError(
            "durable communication intake policy cannot be verified; "
            "release mutation refused"
        )
    except BaseException as exc:
        if process is not None:
            _reap_inspection(process)
        if not isinstance(exc, Exception):
            raise
        raise CommunicationIntakeInspectionError(
            "bounded communication intake inspection failed; release mutation refused"
        ) from exc


def required_instance_policies(
    paths: DeploymentPaths, *, test_mode: bool = False
) -> frozenset[str]:
    """Inspect durable intake and Apple contracts in one bounded, read-only child."""
    if test_mode:
        # This provider requires ADMINME_TEST_MODE=1 and has no Keychain fallback.
        return _read_instance_policies(paths, test_mode=True)
    return _bounded_instance_policies(paths)


def _requires_apple_write_contract(fact: dict[str, Any]) -> bool:
    # Even a revoke retains the original authority and any unknown effect. A
    # missing/stale Settings cache must never erase this rollback boundary.
    if fact.get("type") == APPLE_SERVICE_PERMISSION_EVENT:
        return True
    payload = fact.get("payload")
    if not isinstance(payload, dict):
        raise CommunicationIntakeInspectionError("retained write fact is invalid")
    provider = payload.get("provider")
    if provider == "apple_notes":
        return True
    if provider not in APPLE_SERVICE_PROVIDERS:
        return False
    command = payload.get(
        "command_payload"
        if fact.get("type") == "member.provider_action.confirmed"
        else "payload"
    )
    # Old unmarked Calendar/Reminders actions retain their existing semantics.
    # Presence, not truthiness, matters: an invalid marker is not a downgrade.
    return isinstance(command, dict) and "apple_service_permission" in command


def _read_instance_policies(
    paths: DeploymentPaths, *, test_mode: bool = False
) -> frozenset[str]:
    """Fail closed if persisted authority cannot be read, authenticated, or validated.

    The source manifest describes what a release can *introduce*. This encrypted
    fact describes what every subsequent runtime must enforce, even when the
    current release is a precursor with no default policy. Nothing here creates
    a log, migrates its schema, rebuilds a projection, or starts a consumer.
    """
    try:
        config_path = paths.instance_dir / "config" / "instance.yaml"
        references_path = paths.instance_dir / "config" / "secret-references.json"
        _require_file(config_path, root=paths.instance_dir)
        _require_file(references_path, root=paths.instance_dir)
        config = load_instance_config(paths.instance_dir)
        if not isinstance(config.tenant_id, str) or not config.tenant_id:
            raise CommunicationIntakeInspectionError("intake tenant is invalid")
        _require_file(config.event_log_path, root=paths.instance_dir)
        document = json.loads(references_path.read_text("utf-8"))
        reference = document["references"]["event_log"]
        if not isinstance(reference, str) or not reference:
            raise CommunicationIntakeInspectionError("intake key reference is invalid")
        key = secret_store(paths, test_mode=test_mode).get(reference)

        async def inspect() -> frozenset[str]:
            log = EventLog(config, key, read_only=True)
            try:
                # A changed instance tenant must not hide an epoch in the same
                # physical household log and reauthorize an unaware rollback.
                async for fact in log.read_since(types=[POLICY_EVENT_TYPE]):
                    if fact.get("tenant_id") != config.tenant_id:
                        raise CommunicationIntakeInspectionError(
                            "communication intake policy tenant is inconsistent"
                        )
                epoch = await read_policy_epoch(log, config.tenant_id)
                required = {POLICY_ID} if epoch is not None else set()
                async for fact in log.read_since(types=list(_APPLE_WRITE_FACT_TYPES)):
                    if not _requires_apple_write_contract(fact):
                        continue
                    if fact.get("tenant_id") != config.tenant_id:
                        raise CommunicationIntakeInspectionError(
                            "retained Apple write history tenant is inconsistent"
                        )
                    required.add(APPLE_SERVICE_WRITE_CONTRACT)
                return frozenset(required)
            finally:
                await log.close()

        return asyncio.run(inspect())
    except Exception as exc:
        # Secret-provider and SQLCipher errors must not disclose coordinates,
        # key references, or malformed household event contents to public output.
        raise CommunicationIntakeInspectionError(
            "durable communication intake policy cannot be verified; "
            "release mutation refused"
        ) from exc


@contextmanager
def _worker_deadline() -> Iterator[None]:
    """A tiny independent watcher also bounds a worker whose controller dies.

    The watcher starts before Keychain/framework calls and shares only this
    worker's isolated process group. It cannot be delayed by a blocked Python
    signal handler inside a native Keychain call. Success closes the watcher
    explicitly; timeout or worker death kills the complete isolated group.
    """
    if os.getpgrp() != os.getpid():
        raise CommunicationIntakeInspectionError(
            "communication intake worker requires an isolated process group"
        )
    read_descriptor, write_descriptor = os.pipe()
    try:
        watcher = os.fork()
    except BaseException:
        os.close(read_descriptor)
        os.close(write_descriptor)
        raise
    if watcher == 0:
        os.close(write_descriptor)
        completed = False
        try:
            ready, _write, _errors = select.select(
                [read_descriptor], [], [], _INSPECTION_SECONDS
            )
            completed = bool(ready) and os.read(read_descriptor, 1) == b"1"
        finally:
            if not completed:
                try:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                finally:
                    os._exit(1)
            os._exit(0)
    os.close(read_descriptor)
    try:
        yield
    finally:
        try:
            os.write(write_descriptor, b"1")
        finally:
            os.close(write_descriptor)
            os.waitpid(watcher, 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with _worker_deadline():
            paths = DeploymentPaths.from_root(args.instance_root, production=True)
            policies = _read_instance_policies(paths)
    except Exception:
        return 1
    return next(
        (code for code, required in _EXIT_REQUIREMENTS.items() if policies == required),
        1,
    )


if __name__ == "__main__":
    raise SystemExit(main())
