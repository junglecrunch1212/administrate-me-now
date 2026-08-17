"""Dependency-free exact committed-diff binding for hosted review jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Iterable


class HostedBindingError(RuntimeError):
    """The hosted checkout cannot produce exact clean committed evidence."""


def _git(root: Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "no diagnostic"
        raise HostedBindingError(f"git {' '.join(arguments)} failed: {detail}")
    return process.stdout


def committed_task_prefix(
    root: Path,
    paths: Iterable[str],
    *,
    base_sha: str,
    head_sha: str = "HEAD",
) -> bytes:
    """Return the exact task manifest and committed-diff prefix used by Harness."""

    normalized = sorted({str(Path(path)) for path in paths})
    if not normalized:
        return b""
    manifest = [b"TASK_PATHS_V2\0"]
    for relative in normalized:
        encoded = relative.encode("utf-8")
        manifest.extend((f"{len(encoded)}:".encode("ascii"), encoded, b"\0"))
    committed = _git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        base_sha,
        head_sha,
        "--",
        *normalized,
    ).encode()
    return b"".join([*manifest, b"\nCOMMITTED_DIFF\0", committed])


def clean_committed_binding(
    root: Path, *, base_sha: str, head_sha: str = "HEAD"
) -> dict[str, object]:
    """Bind every committed path in a clean hosted checkout."""

    if _git(root, "diff", "--name-only") or _git(
        root, "diff", "--cached", "--name-only", "HEAD"
    ):
        raise HostedBindingError("hosted checkout has tracked staged or unstaged work")
    paths = sorted(
        set(
            line
            for line in _git(
                root, "diff", "--name-only", base_sha, head_sha
            ).splitlines()
            if line
        )
    )
    if not paths:
        raise HostedBindingError("hosted candidate has no committed changed paths")
    prefix = committed_task_prefix(
        root, paths, base_sha=base_sha, head_sha=head_sha
    )
    digest = hashlib.sha256(
        b"".join(
            [
                prefix,
                b"\nSTAGED_DIFF\0",
                b"",
                b"\nUNSTAGED_DIFF\0",
                b"",
            ]
        )
    ).hexdigest()
    return {"changed_paths": paths, "diff_sha256": digest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        result = clean_committed_binding(
            Path.cwd(), base_sha=args.base, head_sha=args.head
        )
    except HostedBindingError as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
