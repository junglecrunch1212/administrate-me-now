"""Focused updater custody/generation tests; native CLI proof is a separate fixture."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from bootstrap import openclaw_migration as migration
from bootstrap.contracts import DeploymentPaths, atomic_write


def fixture(tmp_path: Path) -> tuple[DeploymentPaths, dict[str, Any]]:
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    (paths.app_dir / "activation").mkdir(mode=0o700, exist_ok=True)
    root = paths.instance_dir / "openclaw"
    (root / "runtime/state").mkdir(mode=0o700, parents=True)
    (root / "workspace").mkdir(mode=0o700)
    atomic_write(root / "workspace/notes.md", b"Retained synthetic note\n", mode=0o600)
    atomic_write(
        root / "runtime/openclaw.json",
        json.dumps(
            {
                "agents": {
                    "list": [
                        {
                            "id": "main",
                            "default": True,
                            "workspace": str(root / "workspace"),
                        }
                    ]
                }
            }
        ).encode(),
        mode=0o600,
    )
    atomic_write(root / "runtime/sessions.json", b'{"retained":"session"}', mode=0o600)
    atomic_write(root / "runtime/jobs.json", b'{"retained":"timer"}', mode=0o600)
    return paths, {
        "transaction_id": uuid.uuid4().hex,
        "predecessor_commit": "1" * 40,
        "target_commit": "2" * 40,
    }


def captured(tmp_path: Path) -> tuple[DeploymentPaths, dict[str, Any]]:
    paths, journal = fixture(tmp_path)
    journal["openclaw_migration"] = migration.capture(paths, journal)
    return paths, journal


def test_capture_is_native_only_and_unchanged_allows_pre_admission_recovery(
    tmp_path: Path,
) -> None:
    paths, journal = captured(tmp_path)
    root = paths.instance_dir / "openclaw"
    atomic_write(
        paths.instance_dir / "domain-history.sqlite",
        b"append-only domain history",
        mode=0o600,
    )
    atomic_write(
        root / "source-install.json", b'{"commit":"new-reviewed-source"}', mode=0o600
    )
    assert migration.may_restore_predecessor(paths, journal)
    capsule = migration._directory(paths, journal) / "state"  # noqa: SLF001
    assert (capsule / "workspace/notes.md").read_bytes() == (
        root / "workspace/notes.md"
    ).read_bytes()
    assert not (capsule / "domain-history.sqlite").exists()
    assert (root / "runtime/openclaw.json").read_text().count('"default": true') == 1


@pytest.mark.parametrize(
    "change",
    ["session", "cron", "database", "replaced_same_bytes", "new_file", "config"],
)
def test_any_later_native_generation_refuses_old_binary(
    tmp_path: Path, change: str
) -> None:
    paths, journal = captured(tmp_path)
    runtime = paths.instance_dir / "openclaw/runtime"
    if change == "replaced_same_bytes":
        atomic_write(
            runtime / "sessions.json",
            (runtime / "sessions.json").read_bytes(),
            mode=0o600,
        )
    else:
        relative = {
            "session": "sessions.json",
            "cron": "jobs.json",
            "database": "state/openclaw.sqlite",
            "new_file": "unexpected",
            "config": "openclaw.json",
        }[change]
        atomic_write(runtime / relative, b"new or foreign native write", mode=0o600)
    assert not migration.may_restore_predecessor(paths, journal)
    assert (runtime / "sessions.json").exists()  # No destructive restore was attempted.


def test_managed_links_are_recorded_without_following_external_artifacts(
    tmp_path: Path,
) -> None:
    paths, journal = fixture(tmp_path)
    release = paths.app_dir / "releases" / ("3" * 40)
    release.mkdir(parents=True)
    (release / "program.md").write_text("immutable program")
    root = paths.instance_dir / "openclaw"
    (root / "workspace/program.md").symlink_to(release / "program.md")
    journal["openclaw_migration"] = migration.capture(paths, journal)
    assert migration.unchanged(paths, journal)
    assert (
        migration._directory(paths, journal) / "state/workspace/program.md"
    ).is_symlink()  # noqa: SLF001


def test_external_state_link_is_not_misreported_as_captured(tmp_path: Path) -> None:
    paths, journal = fixture(tmp_path)
    external = tmp_path / "outside.sqlite"
    external.write_text("not managed")
    (paths.instance_dir / "openclaw/runtime/outside").symlink_to(external)
    with pytest.raises(migration.NativeMigrationError, match="escapes managed"):
        migration.capture(paths, journal)


def test_registry_outside_managed_state_blocks_before_doctor(tmp_path: Path) -> None:
    paths, journal = fixture(tmp_path)
    database = paths.instance_dir / "openclaw/runtime/state/openclaw.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE agent_databases(path TEXT)")
        connection.execute(
            "INSERT INTO agent_databases VALUES (?)",
            (str(tmp_path / "external.sqlite"),),
        )
    database.chmod(0o600)
    original = database.read_bytes()
    with pytest.raises(migration.NativeMigrationError, match="registry|registered"):
        migration.capture(paths, journal)
    assert database.read_bytes() == original


def test_private_snapshot_tampering_never_authorizes_rewind(tmp_path: Path) -> None:
    paths, journal = captured(tmp_path)
    atomic_write(
        migration._directory(paths, journal) / "snapshot.json", b"{}", mode=0o600
    )  # noqa: SLF001
    with pytest.raises(migration.NativeMigrationError, match="identity changed"):
        migration.may_restore_predecessor(paths, journal)


def test_doctor_environment_has_external_ownership_no_inherited_update_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = fixture(tmp_path)
    monkeypatch.setenv("OPENCLAW_UPDATE_RUN_ID", "foreign-run")
    monkeypatch.setenv("OPENCLAW_UPDATE_PHASE", "foreign-phase")
    monkeypatch.setenv("PROVIDER_API_SECRET", "must-not-inherit")
    monkeypatch.setenv("OPENCLAW_UPDATE_PARENT_ALLOWS_GATEWAY_ACTIVATION", "1")
    env = migration.doctor_environment(
        paths, Path("/tmp/openclaw/result.json"), Path("/exact/node")
    )
    assert "OPENCLAW_UPDATE_RUN_ID" not in env
    assert "OPENCLAW_UPDATE_PHASE" not in env
    assert "PROVIDER_API_SECRET" not in env
    assert env["OPENCLAW_UPDATE_PARENT_ALLOWS_GATEWAY_ACTIVATION"] == "0"
    assert env["OPENCLAW_UPDATE_PARENT_ALLOWS_GATEWAY_SERVICE_REPAIR"] == "0"
    assert env["OPENCLAW_SERVICE_REPAIR_POLICY"] == "external"
    assert env["OPENCLAW_SUPERVISOR_MODE"] == "external"
    assert env["OPENCLAW_CONFIG_PATH"] == str(
        paths.instance_dir / "openclaw/runtime/openclaw.json"
    )


def fake_doctor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    result: dict[str, Any] | None = None,
    custody_change: dict[str, Any] | None = None,
    returncode: int = 0,
    alive: bool = False,
) -> list[list[str]]:
    calls: list[list[str]] = []
    entry = tmp_path / "reviewed/openclaw.mjs"
    entry.parent.mkdir()
    entry.write_text("not executed")
    monkeypatch.setattr(
        migration, "_target_command", lambda *_: (Path("/exact/node"), entry)
    )

    class Child:
        pid = 87654321

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            self.returncode = returncode
            calls.append(argv)
            assert kwargs["start_new_session"] is True
            assert kwargs["stdin"] is subprocess.DEVNULL
            result_path = Path(
                kwargs["env"]["OPENCLAW_UPDATE_POST_INSTALL_DOCTOR_RESULT_PATH"]
            )
            custody = Path(str(result_path) + ".processes")
            value = json.loads(custody.read_text())
            value.update({"pid": self.pid, **(custody_change or {})})
            atomic_write(custody, json.dumps(value).encode(), mode=0o600)
            if result is not None:
                atomic_write(result_path, json.dumps(result).encode(), mode=0o600)

        def poll(self) -> int:
            return self.returncode

    def process_group(pid: int, sig: int) -> None:
        assert pid == Child.pid and sig == 0
        if not alive:
            raise ProcessLookupError

    monkeypatch.setattr(migration.subprocess, "Popen", Child)
    monkeypatch.setattr(migration.os, "killpg", process_group)
    return calls


def publish_into(journal: dict[str, Any]):
    def publish(record: dict[str, Any]) -> None:
        journal["openclaw_migration"].update(
            doctor=dict(record),
            state="doctor_settled" if record["settled"] else "doctor_starting",
        )
        migration.validate_receipt(journal["openclaw_migration"], journal)

    return publish


def test_classified_settled_doctor_preserves_original_generation_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, journal = captured(tmp_path)
    calls = fake_doctor(monkeypatch, tmp_path, result={"status": "ok"})
    result = migration.run_doctor(
        paths, tmp_path, journal, publish=publish_into(journal)
    )
    assert result["settled"] is True
    assert calls == [
        [
            "/exact/node",
            str(tmp_path / "reviewed/openclaw.mjs"),
            "doctor",
            "--repair",
            "--non-interactive",
            "--no-workspace-suggestions",
        ]
    ]
    assert migration.may_restore_predecessor(paths, journal)
    journal["openclaw_migration"]["state"] = "candidate_admitted"
    journal["openclaw_migration"]["candidate_admitted"] = True
    assert not migration.may_restore_predecessor(paths, journal)


@pytest.mark.parametrize(
    "problem",
    [
        "missing_result",
        "unresolved_slot",
        "wrong_nonce",
        "alive",
        "missing_classification",
    ],
)
def test_exit_zero_without_exact_settlement_never_allows_old_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    paths, journal = captured(tmp_path)
    fake_doctor(
        monkeypatch,
        tmp_path,
        result=None
        if problem == "missing_result"
        else {}
        if problem == "missing_classification"
        else {"status": "ok"},
        custody_change={"slots": [{"id": 1}]}
        if problem == "unresolved_slot"
        else {"nonce": "another-invocation"}
        if problem == "wrong_nonce"
        else None,
        alive=problem == "alive",
    )
    with pytest.raises(migration.NativeMigrationError):
        migration.run_doctor(paths, tmp_path, journal, publish=publish_into(journal))
    assert not migration.may_restore_predecessor(paths, journal)


def test_classified_failure_is_recoverable_only_if_nothing_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, journal = captured(tmp_path)
    fake_doctor(monkeypatch, tmp_path, result={"status": "error"}, returncode=1)
    with pytest.raises(migration.NativeMigrationError, match="classified migration"):
        migration.run_doctor(paths, tmp_path, journal, publish=publish_into(journal))
    assert migration.may_restore_predecessor(paths, journal)
    atomic_write(
        paths.instance_dir / "openclaw/runtime/jobs.json", b"new state", mode=0o600
    )
    assert not migration.may_restore_predecessor(paths, journal)


def test_timeout_keeps_unsettled_custody_even_when_process_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, journal = captured(tmp_path)
    entry = tmp_path / "reviewed/openclaw.mjs"
    entry.parent.mkdir()
    entry.write_text("not executed")
    monkeypatch.setattr(
        migration, "_target_command", lambda *_: (Path("/exact/node"), entry)
    )
    killed: list[tuple[int, int]] = []

    class Child:
        pid = 87654322
        returncode: int | None = None

        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def poll(self) -> None:
            return None

        def wait(self, timeout: float) -> int:
            self.returncode = -15
            return self.returncode

    monkeypatch.setattr(migration.subprocess, "Popen", Child)
    monkeypatch.setattr(
        migration.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )
    with pytest.raises(
        migration.NativeMigrationError, match="deadline; custody remains unproven"
    ):
        migration.run_doctor(
            paths, tmp_path, journal, publish=publish_into(journal), timeout=0
        )
    assert killed == [(Child.pid, migration.signal.SIGTERM)]
    assert journal["openclaw_migration"]["doctor"]["settled"] is False
    assert not migration.may_restore_predecessor(paths, journal)


def test_foreign_pid_custody_cannot_be_used_as_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, journal = captured(tmp_path)
    fake_doctor(
        monkeypatch, tmp_path, result={"status": "ok"}, custody_change={"pid": 12345}
    )
    with pytest.raises(migration.NativeMigrationError, match="custody did not settle"):
        migration.run_doctor(paths, tmp_path, journal, publish=publish_into(journal))
    assert not migration.may_restore_predecessor(paths, journal)


def test_contained_generation_failure_does_not_invent_a_doctor_process(
    tmp_path: Path,
) -> None:
    paths, journal = captured(tmp_path)
    journal["openclaw_migration"]["state"] = "contained"
    migration.validate_receipt(journal["openclaw_migration"], journal)
    assert not migration.may_restore_predecessor(paths, journal)


@pytest.mark.parametrize("refusal", ["maintenanceRefusal", "configWriteRefusal"])
def test_classified_ok_with_refusal_is_not_migration_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: str
) -> None:
    paths, journal = captured(tmp_path)
    fake_doctor(
        monkeypatch, tmp_path, result={"status": "ok", refusal: {"kind": "deferred"}}
    )
    with pytest.raises(migration.NativeMigrationError, match="classified migration"):
        migration.run_doctor(paths, tmp_path, journal, publish=publish_into(journal))
    # Only genuinely unchanged state may recover the old source, despite exit 0.
    assert migration.may_restore_predecessor(paths, journal)


@pytest.mark.parametrize("authored_entries", [False, True])
def test_public_roster_expansion_uses_exact_native_cas_and_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authored_entries: bool
) -> None:
    paths, _ = fixture(tmp_path)
    runtime = paths.instance_dir / "openclaw/runtime"
    original: dict[str, Any] = {
        "agents": {
            "defaults": {"workspace": str(paths.instance_dir / "openclaw/workspace")}
        }
    }
    if authored_entries:
        original["agents"]["entries"] = {"main": {}}
    atomic_write(runtime / "openclaw.json", json.dumps(original).encode())
    requested = {
        "main": {},
        "staff": {
            "workspace": str(paths.instance_dir / "openclaw/staff"),
            "tools": {"allow": ["typed"]},
        },
    }
    selected = json.loads(json.dumps(original))
    selected["agents"]["entries"] = json.loads(json.dumps(requested))
    selected["agents"]["entries"]["main"]["workspace"] = original["agents"]["defaults"][
        "workspace"
    ]
    selected["agents"]["ownership"] = "explicit"
    selected["agents"]["defaults"]["systemAgent"] = {"agentId": "main"}
    selected["talk"] = {"agentId": "main"}
    monkeypatch.setattr(
        migration,
        "_target_command",
        lambda *_: (Path("/exact/node"), Path("/exact/openclaw.mjs")),
    )
    monkeypatch.setattr(
        migration, "load_versions", lambda *_: {"openclaw": {"commit": "1" * 40}}
    )
    calls = []

    def write(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(argv)
        assert argv[:5] == [
            "/exact/node",
            "/exact/openclaw.mjs",
            "config",
            "set",
            "agents.entries",
        ]
        assert json.loads(argv[5]) == requested
        assert argv[6:8] == ["--strict-json", "--replace"]
        assert kwargs["env"]["OPENCLAW_SUPERVISOR_MODE"] == "external"
        assert not any(key.startswith("OPENCLAW_UPDATE_") for key in kwargs["env"])
        atomic_write(runtime / "openclaw.json", json.dumps(selected).encode())
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(migration.subprocess, "run", write)
    assert migration.expand_sole_roster(paths, tmp_path, requested) == selected
    assert calls[0][8:] == (
        ["--expect-current-json", '{"main":{}}']
        if authored_entries
        else ["--expect-current-absent"]
    )
    receipt = json.loads((runtime.parent / "native-roster-expansion.json").read_text())
    assert receipt["source"] == original
    assert receipt["result"] == selected
    assert receipt["kind"] == "sole-agent-expansion"
