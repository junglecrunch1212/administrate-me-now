"""Native isolated current-to-predecessor-to-current metadata history proof."""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys


def test_exact_metadata_predecessor_compatibility(tmp_path):
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    current = Path(os.environ["FINANCE_METADATA_CURRENT_SOURCE"]).resolve(strict=True)
    prior = Path(os.environ["FINANCE_METADATA_PRIOR_SOURCE"]).resolve(strict=True)
    output = Path(os.environ["FINANCE_METADATA_RECOVERY_OUTPUT"])
    output.mkdir(parents=True, exist_ok=True)
    scratch = tmp_path / "synthetic-recovery"
    scratch.mkdir(mode=0o700)
    key = scratch / "synthetic.key"
    key.write_bytes(secrets.token_bytes(32))
    key.chmod(0o600)
    state = scratch / "instance"
    probe = Path(__file__).with_name("finance_metadata_rollback_probe.py")
    receipt = {"state": "incomplete", "phases": [], "synthetic_only": True}
    try:
        for phase, source in (
            ("seed", current),
            ("predecessor", prior),
            ("roundtrip", current),
        ):
            report = output / (phase + ".json")
            command = [
                sys.executable,
                "-I",
                "-B",
                str(probe),
                phase,
                str(source),
                str(state),
                str(key),
                str(report),
                str(output / "seed.json"),
            ]
            process = subprocess.run(
                command, text=True, capture_output=True, timeout=120, check=False
            )
            (output / (phase + ".stdout.txt")).write_text(process.stdout)
            (output / (phase + ".stderr.txt")).write_text(process.stderr)
            receipt["phases"].append({"name": phase, "exit_code": process.returncode})
            assert process.returncode == 0, {
                "phase": phase,
                "stderr_tail": process.stderr[-2000:],
            }
            assert json.loads(report.read_text())["state"] == "passed"
        receipt["state"] = "passed"
    finally:
        shutil.rmtree(scratch)
        receipt["synthetic_storage_and_key_removed"] = not scratch.exists()
        (output / "lifecycle.json").write_text(json.dumps(receipt, indent=2) + "\n")
