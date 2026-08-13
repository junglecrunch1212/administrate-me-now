"""Run the navigation production journey against immutable pre-change HEAD."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .core import HarnessError, repository_root

BASELINE_TEST = (
    "tests/acceptance/test_gate7_household_experience.py::"
    "test_ten_surfaces_three_modes_and_independent_route_denial"
)


def run() -> None:
    """Archive HEAD to system temp and execute its production-composition case."""

    root = repository_root()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    archive = subprocess.run(
        ["git", "archive", "--format=tar", head],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="administrateme-head-baseline-") as value:
        baseline = Path(value)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            bundle.extractall(baseline, filter="data")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(baseline / "bridge"), str(baseline))
        )
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-q",
                BASELINE_TEST,
            ],
            cwd=baseline,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
    print(f"baseline_head={head}")
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        raise HarnessError(
            f"immutable HEAD navigation baseline failed with exit {result.returncode}"
        )


if __name__ == "__main__":
    run()
