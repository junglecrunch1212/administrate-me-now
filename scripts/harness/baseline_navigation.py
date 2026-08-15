"""Run the navigation production journey against the immutable candidate base."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .core import HarnessError, candidate_base_sha, clean_environment, repository_root

BASELINE_TEST = (
    "tests/acceptance/test_gate7_household_experience.py::"
    "test_ten_surfaces_three_modes_and_independent_route_denial"
)


def run() -> None:
    """Archive the candidate merge base and execute its production-composition case."""

    root = repository_root()
    base = candidate_base_sha(root)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", base],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="administrateme-base-baseline-") as value:
        baseline = Path(value)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            bundle.extractall(baseline, filter="data")
        environment = clean_environment()
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
    print(f"baseline_base={base}")
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        raise HarnessError(
            f"immutable base navigation baseline failed with exit {result.returncode}"
        )


if __name__ == "__main__":
    run()
