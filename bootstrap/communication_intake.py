"""Read the irreversible intake boundary without constructing a household runtime."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

from adminme.communication_intake_policy import POLICY_ID, read_policy_epoch
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store


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


def required_instance_policies(
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
                epoch = await read_policy_epoch(log, config.tenant_id)
                return frozenset({POLICY_ID}) if epoch is not None else frozenset()
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
