from __future__ import annotations

import json
from pathlib import Path
import subprocess
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


def test_runtime_password_uses_apple_cli_and_anonymous_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "encoded-secret"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        if command[1] == "add-generic-password":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[1] == "find-generic-password":
            return SimpleNamespace(returncode=0, stdout=f"{secret}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(macos_keychain.subprocess, "run", run)

    put_runtime_password("com.administrateme.instance.event-log", "instance", secret)
    assert (
        get_runtime_password("com.administrateme.instance.event-log", "instance")
        == secret
    )
    delete_runtime_password("com.administrateme.instance.event-log", "instance")

    put_command, put_kwargs = calls[0]
    assert put_command[0] == "/usr/bin/security"
    assert put_command[-1] == "-w"
    assert secret not in put_command
    assert put_kwargs["input"] == f"{secret}\n{secret}\n"
    get_command, get_kwargs = calls[1]
    assert get_command[1] == "find-generic-password"
    assert secret not in get_command
    assert "input" not in get_kwargs
    assert calls[2][0][1] == "delete-generic-password"


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
