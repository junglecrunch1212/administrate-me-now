"""Hosted old-native → public Doctor → target-native retained-state qualification.

All state is synthetic and privately owned by the existing source-install probe.
Only the executable-path locator is adapted to setup-node's hosted location;
Doctor, its IPC/custody parser, generation checks and config classifier are real.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
from typing import Any
from unittest.mock import patch
import uuid

from bootstrap import openclaw_migration as migration
from bootstrap.contracts import DeploymentPaths, atomic_write, load_versions

PREDECESSOR_RELEASE = "04a9805e4c91698017dd10b38aac51c5e1d0e5d9"


def run(command: list[str], *, cwd: Path, timeout: int = 600) -> str:
    child = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=5)
        raise RuntimeError(
            "Owned qualification process exceeded its deadline"
        ) from None
    finally:
        # A failed/abandoned fixture must not leave its Gateway running while
        # the source-install caller removes the disposable state directory.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        else:
            raise RuntimeError("Qualification command left an unsettled process group")
    if child.returncode:
        # Fixture-only children have no credentials. Native RPC redacts its token.
        raise RuntimeError(
            f"Qualification command failed ({child.returncode}): "
            f"{stdout[-20000:]}\n{stderr[-20000:]}"
        )
    return stdout.strip()


def database_versions(runtime: Path) -> dict[str, int]:
    shared = runtime / "state/openclaw.sqlite"
    assert shared.is_file(), "predecessor did not create its native shared database"
    result: dict[str, int] = {}
    with sqlite3.connect(shared.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        result["shared"] = db.execute("PRAGMA user_version").fetchone()[0]
        stores = [
            Path(row[0]) for row in db.execute("SELECT path FROM agent_databases")
        ]
    assert len(stores) >= 2, "both actual native agent databases must be retained"
    for store in stores:
        assert store.is_absolute() and store.is_relative_to(runtime)
        with sqlite3.connect(store.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            result[str(store.relative_to(runtime))] = db.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
    return result


def main() -> None:
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    os.umask(0o077)
    repository = Path(__file__).resolve().parents[2]
    installed = Path(sys.argv[1]).resolve(strict=True)
    # The source-install caller owns and cleans this one disposable namespace.
    assert installed.name.startswith("adminme-openclaw-source.")
    assert installed.is_relative_to(Path(os.environ.get("TMPDIR", "/tmp")).resolve())
    target = load_versions(repository)
    node = Path(shutil.which("node") or "").resolve(strict=True)
    actual_node = run([str(node), "--version"], cwd=repository)
    assert actual_node == "v" + target["node"]["preferred"]
    old = json.loads(
        run(
            ["git", "show", f"{PREDECESSOR_RELEASE}:bootstrap/versions.json"],
            cwd=repository,
        )
    )
    predecessor = target["openclaw"]["reviewed_predecessor"]
    assert all(old["openclaw"][key] == predecessor[key] for key in predecessor)
    assert old["openclaw"]["source_archive_sha256"] == (
        "3fffb07090f63492bff89daac6ae0879e66e205c743911598bc79e3bb7e2b577"
    )
    # Keep the production installer unchanged: its manifest is scoped to a
    # copied fixture layout, with exactly the reviewed predecessor's pins.
    evidence_dir = Path(os.environ["RUNNER_TEMP"]) / "adminme-native-upgrade-evidence"
    evidence_dir.mkdir(mode=0o700, exist_ok=True)
    fixture = installed / "native-upgrade"
    copied = fixture / "old-installer/bootstrap"
    copied.mkdir(mode=0o700, parents=True)
    shutil.copy2(repository / "bootstrap/install-openclaw-source.sh", copied)
    atomic_write(copied / "versions.json", json.dumps(old).encode(), mode=0o600)
    old_install = fixture / "old-install"
    print(
        "Building exact reviewed predecessor source for retained-state proof",
        flush=True,
    )
    print(
        run(
            [
                "bash",
                str(copied / "install-openclaw-source.sh"),
                "--instance-root",
                str(old_install),
                "--python",
                sys.executable,
            ],
            cwd=repository,
            timeout=1800,
        ),
        flush=True,
    )
    old_source = old_install / "app/openclaw" / predecessor["commit"]
    new_source = installed / "app/openclaw" / target["openclaw"]["commit"]
    for source, manifest in ((old_source, old), (new_source, target)):
        package = json.loads((source / "package.json").read_text())
        assert package["version"] == manifest["openclaw"]["version"]
        assert package["packageManager"] == manifest["openclaw"]["package_manager"]
        archive = source.parents[1] / "downloads" / f"openclaw-{source.name}.tar.gz"
        with archive.open("rb") as stream:
            assert (
                hashlib.file_digest(stream, "sha256").hexdigest()
                == (manifest["openclaw"]["source_archive_sha256"])
            )
    paths = DeploymentPaths.from_root(fixture / "retained")
    paths.create()
    (paths.app_dir / "activation").mkdir(mode=0o700, exist_ok=True)
    native = paths.instance_dir / "openclaw"
    runtime = native / "runtime"
    runtime.mkdir(mode=0o700, parents=True)
    rows = []
    for index, name in enumerate(("upgrade-alpha", "upgrade-beta")):
        workspace = native / "workspaces" / name
        workspace.mkdir(mode=0o700, parents=True)
        atomic_write(
            workspace / "retained-note.md", b"Synthetic private retained note\n"
        )
        rows.append(
            {
                "id": name,
                "default": index == 0,
                "workspace": str(workspace),
                "tools": {"deny": ["*"]},
            }
        )
    config = {
        "gateway": {
            "mode": "local",
            "bind": "loopback",
            "auth": {"mode": "token", "token": secrets.token_hex(32)},
            "controlUi": {"enabled": False},
            "reload": {"mode": "off"},
        },
        "agents": {
            "defaults": {
                "workspace": rows[0]["workspace"],
                "heartbeat": {"every": "0m"},
                "skipBootstrap": True,
            },
            "list": rows,
        },
        "plugins": {"enabled": False, "slots": {"memory": "none"}},
        "channels": {},
        "cron": {"enabled": False},
        "browser": {"enabled": False},
        "discovery": {"mdns": {"mode": "off"}},
        "update": {"checkOnStart": False, "auto": {"enabled": False}},
        "logging": {"file": str(runtime / "gateway.log")},
    }
    atomic_write(runtime / "openclaw.json", json.dumps(config).encode(), mode=0o600)
    sentinel = paths.instance_dir / "domain-history-sentinel"
    atomic_write(
        sentinel, b"Append-only domain history remains outside native recovery\n"
    )
    domain_before = sentinel.read_bytes()
    proof = fixture / "retained-native.json"
    rpc_script = (
        repository / "openclaw/administrateme/scripts/check-openclaw-retained-state.mjs"
    )

    def rpc(source: Path, mode: str) -> None:
        print(
            run(
                [
                    str(node),
                    str(rpc_script),
                    str(source),
                    str(runtime),
                    str(proof),
                    mode,
                ],
                cwd=repository,
            ),
            flush=True,
        )

    rpc(old_source, "seed")
    rpc(old_source, "read")  # Actual predecessor restart, before any migration.
    before = database_versions(runtime)
    assert before.pop("shared") == 6 and set(before.values()) == {16}
    journal: dict[str, Any] = {
        "transaction_id": uuid.uuid4().hex,
        "predecessor_commit": PREDECESSOR_RELEASE,
        "target_commit": run(["git", "rev-parse", "HEAD"], cwd=repository),
        "target_path": str(repository),
    }
    journal["openclaw_migration"] = migration.capture(paths, journal)
    assert migration.may_restore_predecessor(paths, journal)

    def publish(receipt: dict[str, Any]) -> None:
        journal["openclaw_migration"]["doctor"] = dict(receipt)
        atomic_write(fixture / "doctor-journal.json", json.dumps(journal).encode())

    # setup-node's exact executable is outside Homebrew. This fixture adapts
    # only that locator after checking package/commit/archive/Node identities.
    try:
        with patch.object(
            migration,
            "_target_command",
            return_value=(node, new_source / "openclaw.mjs"),
        ):
            result = migration.run_doctor(paths, repository, journal, publish=publish)
    finally:
        output = (
            paths.app_dir
            / "activation"
            / f"native-{journal['transaction_id']}"
            / "doctor-output.log"
        )
        if output.is_file():
            redacted = output.read_text(errors="replace").replace(
                config["gateway"]["auth"]["token"], "[redacted]"
            )
            atomic_write(evidence_dir / "doctor-output.log", redacted.encode())
    assert result["settled"] is True and result["classification"] in {"ok", "advisory"}
    journal["openclaw_migration"]["state"] = "doctor_settled"
    assert not migration.may_restore_predecessor(paths, journal)
    journal["openclaw_migration"]["configuration_sha256"] = (
        migration.record_configuration(paths, repository, journal)
    )
    transition = migration.configuration_transition(paths, journal)
    assert set(transition["result"]["agents"]["entries"]) == {
        "upgrade-alpha",
        "upgrade-beta",
    }
    after = database_versions(runtime)
    assert after.pop("shared") == 20 and set(after.values()) == {25}
    assert set(before) == set(after), "native database coordinates must stay retained"
    journal["openclaw_migration"].update(
        state="candidate_admitted", candidate_admitted=True
    )
    rpc(new_source, "read")
    rpc(new_source, "read")  # Actual target restart, same identities/history.
    assert not migration.may_restore_predecessor(paths, journal)
    assert sentinel.read_bytes() == domain_before
    for row in rows:
        assert (
            Path(row["workspace"]) / "retained-note.md"
        ).read_bytes() == b"Synthetic private retained note\n"
    evidence = {
        "predecessor": old["openclaw"],
        "target": target["openclaw"],
        "actual_node": actual_node,
        "predecessor_declared_node": old["node"],
        "toolchain_scope": "Both exact sources built/executed with target Node; predecessor-compatible range, not an execution of its former preferred patch.",
        "doctor": result,
        "shared_schema": [6, 20],
        "agent_schema": [16, 25],
        "retained_agent_stores": len(after),
        "old_binary_restore_after_migration": False,
        "domain_history_unchanged": True,
        "model_or_provider_calls": 0,
        "gateway_restart_readbacks": {"predecessor": 1, "target": 2},
    }
    atomic_write(evidence_dir / "result.json", json.dumps(evidence, indent=2).encode())
    print(
        "NATIVE RETAINED UPGRADE: PASS (no downgrade after migration; forward recovery boundary retained)"
    )


if __name__ == "__main__":
    main()
