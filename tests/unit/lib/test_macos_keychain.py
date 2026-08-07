from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from adminme.lib import macos_keychain
from adminme.lib.macos_keychain import (
    RuntimeKeychainError,
    delete_runtime_password,
    get_runtime_password,
    put_runtime_password,
    resolve_runtime_alias,
)


def test_runtime_keychain_import_survives_bootstrap_module_shadow() -> None:
    repository = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import adminme.lib.macos_keychain",
            str(repository / "bootstrap"),
        ],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_runtime_password_uses_apple_cli_and_anonymous_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "oauth-payload-" + ("x" * 624)
    calls: list[tuple[list[str], dict[str, object]]] = []
    stored: dict[str, str] = {}

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        service = command[command.index("-s") + 1]
        if command[1] == "add-generic-password":
            value = str(kwargs["input"]).splitlines()[0]
            stored[service] = value
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[1] == "find-generic-password":
            if service not in stored:
                return SimpleNamespace(returncode=44, stdout="", stderr="")
            return SimpleNamespace(
                returncode=0, stdout=f"{stored[service]}\n", stderr=""
            )
        stored.pop(service, None)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(macos_keychain.subprocess, "run", run)
    monkeypatch.setattr(macos_keychain, "_new_generation", lambda: "a" * 16)

    put_runtime_password("com.administrateme.instance.event-log", "instance", secret)
    assert (
        get_runtime_password("com.administrateme.instance.event-log", "instance")
        == secret
    )
    delete_runtime_password("com.administrateme.instance.event-log", "instance")

    assert not stored
    writes = [
        (command, kwargs)
        for command, kwargs in calls
        if command[1] == "add-generic-password"
    ]
    assert len(writes) > 2
    for command, kwargs in writes:
        assert command[0] == "/usr/bin/security"
        assert command[-1] == "-w"
        assert secret not in command
        first, second = str(kwargs["input"]).splitlines()
        assert first == second
        assert len(first) <= 120
    reads = [
        (command, kwargs)
        for command, kwargs in calls
        if command[1] == "find-generic-password"
    ]
    assert reads
    assert all("input" not in kwargs for _command, kwargs in reads)
    assert any(command[1] == "delete-generic-password" for command, _kwargs in calls)


def test_runtime_password_rejects_fragment_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, str] = {}

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        service = command[command.index("-s") + 1]
        if command[1] == "add-generic-password":
            stored[service] = str(kwargs["input"]).splitlines()[0]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[1] == "find-generic-password":
            if service not in stored:
                return SimpleNamespace(returncode=44, stdout="", stderr="")
            return SimpleNamespace(
                returncode=0, stdout=f"{stored[service]}\n", stderr=""
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(macos_keychain.subprocess, "run", run)
    monkeypatch.setattr(macos_keychain, "_new_generation", lambda: "b" * 16)
    service = "com.administrateme.instance.event-log"
    put_runtime_password(service, "instance", "large-" + ("value" * 80))
    part = next(name for name in stored if ".amkc1-" in name)
    stored[part] = "A" + stored[part][1:]

    with pytest.raises(RuntimeKeychainError, match="envelope"):
        get_runtime_password(service, "instance")


def test_runtime_password_cleans_unpublished_generation_on_root_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, str] = {}
    root = "com.administrateme.instance.event-log"

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        service = command[command.index("-s") + 1]
        if command[1] == "find-generic-password":
            return SimpleNamespace(returncode=44, stdout="", stderr="")
        if command[1] == "add-generic-password":
            if service == root:
                return SimpleNamespace(returncode=1, stdout="", stderr="hidden")
            stored[service] = str(kwargs["input"]).splitlines()[0]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        stored.pop(service, None)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(macos_keychain.subprocess, "run", run)
    monkeypatch.setattr(macos_keychain, "_new_generation", lambda: "c" * 16)

    with pytest.raises(RuntimeKeychainError, match="write failed"):
        put_runtime_password(root, "instance", "long-" + ("value" * 80))
    assert not stored


def test_runtime_password_fails_closed_without_returning_cli_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        macos_keychain.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="secret-output", stderr="secret-error"
        ),
    )

    with pytest.raises(RuntimeKeychainError, match="unavailable") as captured:
        get_runtime_password("com.administrateme.instance.event-log", "instance")
    assert "secret" not in str(captured.value)

    def timeout(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(["security"], 10)

    monkeypatch.setattr(macos_keychain.subprocess, "run", timeout)
    with pytest.raises(RuntimeKeychainError, match="did not complete"):
        put_runtime_password(
            "com.administrateme.instance.event-log", "instance", "encoded"
        )


def test_runtime_alias_resolution_is_instance_scoped_and_cycle_safe(
    tmp_path: Path,
) -> None:
    logical = "keychain://instance/event-log"
    first = "keychain://instance/event-log-runtime-py314-first"
    second = "keychain://instance/event-log-runtime-py314-second"
    alias_path = tmp_path / "aliases.json"
    alias_path.write_text(
        json.dumps({"schema_version": 1, "aliases": {logical: first, first: second}}),
        encoding="utf-8",
    )
    assert resolve_runtime_alias(logical, alias_path) == second

    alias_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "aliases": {logical: "keychain://another-instance/event-log"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeKeychainError, match="instance boundary"):
        resolve_runtime_alias(logical, alias_path)

    alias_path.write_text(
        json.dumps({"schema_version": 1, "aliases": {logical: first, first: logical}}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeKeychainError, match="cycle"):
        resolve_runtime_alias(logical, alias_path)
