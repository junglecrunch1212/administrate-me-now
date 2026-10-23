"""Publish the reviewed Mac helper for authenticated Settings downloads.

Run from the clean immutable protected-main checkout after merge. This creates
only a versioned program artifact; it changes no bridge, grant, or live state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from adminme.bridge_helper_distribution import (
    ARCHIVE,
    CONTRACT,
    MAX_BYTES,
    read_distribution,
    distribution_root,
)
from bootstrap.member_bridge_helper_app import build_member_bridge_helper_app
from bootstrap.source_identity import (
    reviewed_candidate_commit,
    sanitized_git_environment,
)


def publish_helper(
    repository: Path, instance_dir: Path, commit: str
) -> dict[str, object]:
    if reviewed_candidate_commit(repository) != commit:
        raise RuntimeError("Publication requires the clean reviewed source commit")
    main = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repository),
            "rev-parse",
            "refs/remotes/origin/main",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=sanitized_git_environment(),
        timeout=10,
    ).stdout.strip()
    if main != commit:
        raise RuntimeError("Publication requires the fetched protected-main commit")
    root = distribution_root(instance_dir)
    # A caller cannot use the publication command to overwrite an installed release.
    if root.is_relative_to(repository) or root.is_symlink() or root.parent.is_symlink():
        raise RuntimeError("Choose the instance's separate helper download directory")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    destination = root / commit
    if destination.exists():
        existing, _ = read_distribution(instance_dir, commit)
        return existing
    stage = Path(tempfile.mkdtemp(prefix=".prepare-", dir=root))
    try:
        app = stage / "AdministrateMe Bridge Helper.app"
        build_member_bridge_helper_app(app, root=repository, expected_commit=commit)
        archive = stage / ARCHIVE
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as target:
            for source in sorted(app.rglob("*")):
                if source.is_symlink():
                    raise RuntimeError("Helper archive contains a symbolic link")
                if source.is_file():
                    target.write(source, source.relative_to(stage).as_posix())
        archive.chmod(0o600)
        payload = archive.read_bytes()
        if not 0 < len(payload) <= MAX_BYTES:
            raise RuntimeError("Helper archive exceeds the download bound")
        manifest: dict[str, object] = {
            "contract": CONTRACT,
            "filename": ARCHIVE,
            "release_commit": commit,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "architecture": "arm64",
            "minimum_macos": "15.0",
            "signature": "ad-hoc; macOS may require Open Anyway on first launch",
            "provider_writes": False,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n"
        )
        (stage / "manifest.json").chmod(0o600)
        shutil.rmtree(app)
        os.rename(stage, destination)
        verified, _ = read_distribution(instance_dir, commit)
        return verified
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--instance-dir", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            publish_helper(
                args.repository.resolve(),
                args.instance_dir.resolve(),
                args.expected_commit,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
