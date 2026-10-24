"""Supported revocation/recovery for authenticated-request actions only.

Run disable on the schema-aware runtime after a regression. Existing source
acquisition, private context, self replies and manual actions keep their grants.
Enable preserves the cutoff: historical pending actions never auto-resume.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from adminme.lib.instance_config import load_instance_config
from adminme.request_actions_control import FILENAME, read_control


def set_disabled(config_dir: Path, disabled: bool) -> dict:
    previous = read_control(config_dir)
    value = {
        "contract": "request-actions-control-v1",
        "disabled": disabled,
        "revoked_before": datetime.now(timezone.utc).isoformat()
        if disabled
        else previous["revoked_before"],
    }
    path = config_dir / FILENAME
    if path.is_symlink():
        raise ValueError("recovery control must not be a symlink")
    config_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".request-actions-", dir=config_dir)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-dir", required=True, type=Path)
    parser.add_argument("action", choices=("disable", "enable", "status"))
    args = parser.parse_args()
    instance = load_instance_config(args.instance_dir)
    result = (
        read_control(instance.config_dir)
        if args.action == "status"
        else set_disabled(instance.config_dir, args.action == "disable")
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
