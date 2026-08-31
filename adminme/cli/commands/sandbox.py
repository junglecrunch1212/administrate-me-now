"""Mandatory live-fidelity sandbox command surface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Never

import typer
import uvicorn

from adminme.runtime.application import create_application
from adminme.sandbox.manager import (
    SandboxError,
    _network_contained_command,
    _network_contained_serve_command,
    capture_profile,
    create_disposable,
    default_state_root,
    discard_disposable,
    inspect_current,
    reconcile,
    run_test,
)
from adminme.synthetic_member import build_synthetic_member_settings

sandbox_app = typer.Typer(
    help="Reconcile and run the fail-closed, live-fidelity synthetic sandbox.",
    no_args_is_help=True,
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _deployment_pointer() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "AdministrateMe"
        / "app"
        / "current-release.json"
    )


def _artifact_path(state_dir: Path) -> Path:
    return state_dir / "profiles" / "active-fidelity.json"


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True))


def _fail(exc: Exception) -> Never:
    typer.echo(f"Sandbox refused to run: {exc}", err=True)
    raise typer.Exit(code=2) from exc


def _contained_environment(*, require_gateway: bool) -> dict[str, str]:
    gateway_token = os.environ.get("ADMINME_OPENCLAW_GATEWAY_TOKEN")
    gateway_secret = os.environ.get("ADMINME_OPENCLAW_SHARED_SECRET")
    if require_gateway and (not gateway_token or not gateway_secret):
        raise SandboxError("Gateway mode requires both injected OpenClaw credentials")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH"}
    }
    environment["ADMINME_SANDBOX_NETWORK_CONTAINED"] = "1"
    if require_gateway:
        environment["ADMINME_OPENCLAW_GATEWAY_TOKEN"] = str(gateway_token)
        environment["ADMINME_OPENCLAW_SHARED_SECRET"] = str(gateway_secret)
    return environment


def _contained_cli_command(arguments: list[str]) -> list[str]:
    return [sys.executable, "-m", "adminme.cli.main", "sandbox", *arguments]


def _run_contained_cli(
    arguments: list[str],
    *,
    require_gateway: bool,
    serve_port: int | None = None,
) -> int:
    environment = _contained_environment(require_gateway=require_gateway)
    command = _contained_cli_command(arguments)
    if serve_port is None:
        contained = _network_contained_command(command, require_gateway=require_gateway)
    else:
        contained = _network_contained_serve_command(
            command,
            port=serve_port,
            require_gateway=require_gateway,
        )
    completed = subprocess.run(
        contained,
        cwd=_repository_root(),
        env=environment,
        check=False,
    )
    return int(completed.returncode)


@sandbox_app.command("capture-profile")
def capture_profile_command(
    tailscale_identity: str = typer.Option(
        ...,
        "--tailscale-identity",
        help="Authorized adult identity presented to the active read-only endpoint.",
    ),
    active_url: str = typer.Option("http://127.0.0.1:3330", "--active-url"),
    active_release_pointer: Path = typer.Option(
        _deployment_pointer(), "--active-release-pointer"
    ),
    state_dir: Path = typer.Option(default_state_root(), "--state-dir"),
    output: Path | None = typer.Option(None, "--output"),
) -> None:
    """Capture fresh aggregates from the exact active release and sign them."""

    try:
        path = capture_profile(
            base_url=active_url,
            tailscale_identity=tailscale_identity,
            active_release_pointer=active_release_pointer,
            state_root=state_dir,
            repository_root=_repository_root(),
            output=output,
        )
    except (SandboxError, OSError, ValueError, json.JSONDecodeError) as exc:
        _fail(exc)
    _emit({"status": "captured", "artifact": str(path), "content_persisted": False})


@sandbox_app.command("reconcile")
def reconcile_command(
    artifact: Path | None = typer.Option(None, "--artifact"),
    active_release_pointer: Path = typer.Option(
        _deployment_pointer(), "--active-release-pointer"
    ),
    state_dir: Path = typer.Option(default_state_root(), "--state-dir"),
) -> None:
    """Rebuild the encrypted canonical baseline when any contract changes."""

    try:
        exit_code = _run_contained_cli(
            [
                "_reconcile-contained",
                "--artifact",
                str(artifact or _artifact_path(state_dir)),
                "--active-release-pointer",
                str(active_release_pointer),
                "--state-dir",
                str(state_dir),
            ],
            require_gateway=False,
        )
    except (SandboxError, OSError, ValueError, json.JSONDecodeError) as exc:
        _fail(exc)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@sandbox_app.command("_reconcile-contained", hidden=True)
def _reconcile_contained_command(
    artifact: Path = typer.Option(..., "--artifact"),
    active_release_pointer: Path = typer.Option(..., "--active-release-pointer"),
    state_dir: Path = typer.Option(..., "--state-dir"),
) -> None:
    """Internal reconciliation worker inherited by an OS network sandbox."""

    if os.environ.get("ADMINME_SANDBOX_NETWORK_CONTAINED") != "1":
        _fail(SandboxError("sandbox reconciliation requires network containment"))
    try:
        metadata = reconcile(
            artifact_path=artifact,
            active_release_pointer=active_release_pointer,
            state_root=state_dir,
            repository_root=_repository_root(),
        )
    except (SandboxError, OSError, ValueError, json.JSONDecodeError) as exc:
        _fail(exc)
    _emit(
        {
            "status": "current",
            "baseline_id": metadata["baseline_id"],
            "active_release_commit": metadata["identity"]["active_release_commit"],
            "dominance": metadata["dominance"],
            "privacy_proof": metadata["privacy_proof"],
            "production_composition": metadata["production_composition"],
        }
    )


@sandbox_app.command("inspect")
def inspect_command(
    state_dir: Path = typer.Option(default_state_root(), "--state-dir"),
) -> None:
    """Print a content-free explanation of the current baseline sufficiency."""

    try:
        metadata = inspect_current(
            state_root=state_dir,
            repository_root=_repository_root(),
        )
    except (SandboxError, OSError, ValueError, json.JSONDecodeError) as exc:
        _fail(exc)
    _emit(metadata)


@sandbox_app.command("serve")
def serve_command(
    fresh: bool = typer.Option(
        False, "--fresh", help="Required: serve a disposable clone."
    ),
    artifact: Path | None = typer.Option(None, "--artifact"),
    active_release_pointer: Path = typer.Option(
        _deployment_pointer(), "--active-release-pointer"
    ),
    state_dir: Path = typer.Option(default_state_root(), "--state-dir"),
    port: int = typer.Option(8321, min=1024, max=65535),
    require_gateway: bool = typer.Option(False, "--require-gateway"),
) -> None:
    """Reconcile, clone, and serve candidate code on loopback only."""

    if not fresh:
        _fail(
            SandboxError(
                "serve requires --fresh; canonical baselines are never served directly"
            )
        )
    try:
        arguments = [
            "_serve-contained",
            "--artifact",
            str(artifact or _artifact_path(state_dir)),
            "--active-release-pointer",
            str(active_release_pointer),
            "--state-dir",
            str(state_dir),
            "--port",
            str(port),
        ]
        if require_gateway:
            arguments.append("--require-gateway")
        exit_code = _run_contained_cli(
            arguments,
            require_gateway=require_gateway,
            serve_port=port,
        )
    except (SandboxError, OSError, ValueError, json.JSONDecodeError) as exc:
        _fail(exc)
    if exit_code not in {0, -2, 130}:
        raise typer.Exit(code=exit_code)


@sandbox_app.command("_serve-contained", hidden=True)
def _serve_contained_command(
    artifact: Path = typer.Option(..., "--artifact"),
    active_release_pointer: Path = typer.Option(..., "--active-release-pointer"),
    state_dir: Path = typer.Option(..., "--state-dir"),
    port: int = typer.Option(..., "--port", min=1024, max=65535),
    require_gateway: bool = typer.Option(False, "--require-gateway"),
) -> None:
    """Internal reconciler and server inherited by one OS network sandbox."""

    if os.environ.get("ADMINME_SANDBOX_NETWORK_CONTAINED") != "1":
        _fail(SandboxError("sandbox server requires network containment"))
    gateway_token = os.environ.get("ADMINME_OPENCLAW_GATEWAY_TOKEN")
    gateway_secret = os.environ.get("ADMINME_OPENCLAW_SHARED_SECRET")
    if require_gateway and (not gateway_token or not gateway_secret):
        _fail(SandboxError("Gateway mode requires both injected OpenClaw credentials"))
    disposable = None
    canonical_before = ""
    try:
        metadata = reconcile(
            artifact_path=artifact,
            active_release_pointer=active_release_pointer,
            state_root=state_dir,
            repository_root=_repository_root(),
        )
        canonical_before = str(metadata["instance_fingerprint"])
        disposable = create_disposable(
            state_root=state_dir,
            repository_root=_repository_root(),
        )
        settings = build_synthetic_member_settings(
            disposable.path,
            _repository_root(),
            encryption_key=disposable.encryption_key_path.read_bytes(),
            openclaw_gateway_token=gateway_token if require_gateway else None,
            openclaw_shared_secret=gateway_secret if require_gateway else None,
        )
        typer.echo(
            f"Disposable live-fidelity sandbox ready at http://127.0.0.1:{port}/member/triage"
        )
        uvicorn.run(
            create_application(settings),
            host="127.0.0.1",
            port=port,
            proxy_headers=False,
            server_header=False,
        )
    except (SandboxError, OSError, ValueError, json.JSONDecodeError) as exc:
        _fail(exc)
    finally:
        if disposable is not None:
            current = inspect_current(
                state_root=state_dir,
                repository_root=_repository_root(),
            )
            discard_disposable(disposable)
            if current["instance_fingerprint"] != canonical_before:
                raise SandboxError("canonical baseline changed while serving a clone")


@sandbox_app.command(
    "test",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def test_command(
    ctx: typer.Context,
    artifact: Path | None = typer.Option(None, "--artifact"),
    active_release_pointer: Path = typer.Option(
        _deployment_pointer(), "--active-release-pointer"
    ),
    state_dir: Path = typer.Option(default_state_root(), "--state-dir"),
    require_gateway: bool = typer.Option(False, "--require-gateway"),
    runtime: str = typer.Option(
        "candidate",
        "--runtime",
        help="Run candidate code or the exact active release for comparison.",
    ),
) -> None:
    """Mandatory preflight, disposable clone, test command, and clone discard."""

    command = list(ctx.args)
    if command and command[0] == "--":
        command = command[1:]
    try:
        arguments = [
            "_test-contained",
            "--artifact",
            str(artifact or _artifact_path(state_dir)),
            "--active-release-pointer",
            str(active_release_pointer),
            "--state-dir",
            str(state_dir),
            "--runtime",
            runtime,
        ]
        if require_gateway:
            arguments.append("--require-gateway")
        arguments.extend(["--", *command])
        exit_code = _run_contained_cli(
            arguments,
            require_gateway=require_gateway,
        )
    except (SandboxError, OSError, ValueError, json.JSONDecodeError) as exc:
        _fail(exc)
    raise typer.Exit(code=exit_code)


@sandbox_app.command(
    "_test-contained",
    hidden=True,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def _test_contained_command(
    ctx: typer.Context,
    artifact: Path = typer.Option(..., "--artifact"),
    active_release_pointer: Path = typer.Option(..., "--active-release-pointer"),
    state_dir: Path = typer.Option(..., "--state-dir"),
    require_gateway: bool = typer.Option(False, "--require-gateway"),
    runtime: str = typer.Option("candidate", "--runtime"),
) -> None:
    """Internal full-lifecycle worker inherited by an OS network sandbox."""

    if os.environ.get("ADMINME_SANDBOX_NETWORK_CONTAINED") != "1":
        _fail(SandboxError("sandbox test requires network containment"))
    command = list(ctx.args)
    if command and command[0] == "--":
        command = command[1:]
    try:
        exit_code = run_test(
            command,
            artifact_path=artifact,
            active_release_pointer=active_release_pointer,
            state_root=state_dir,
            repository_root=_repository_root(),
            require_gateway=require_gateway,
            runtime_mode=runtime,
        )
    except (SandboxError, OSError, ValueError, json.JSONDecodeError) as exc:
        _fail(exc)
    raise typer.Exit(code=exit_code)


__all__ = ["sandbox_app"]
