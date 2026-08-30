"""Credential-free canonical household demo command."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import typer
import uvicorn
from fastapi.testclient import TestClient

from adminme.demo import FIXTURE_MEMBER_ID
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.synthetic_member import build_synthetic_member_settings

demo_app = typer.Typer(help="Run deterministic, credential-free product demos.")


@demo_app.command("canonical-message")
def canonical_message(
    body: str = typer.Option(
        "Could you reply today with the fixture household update?",
        help="Synthetic message body.",
    ),
) -> None:
    """Run the same versioned HTTP route used by the black-box acceptance."""
    repository_root = Path(__file__).resolve().parents[3]
    with tempfile.TemporaryDirectory(prefix="adminme-demo-") as temp_dir:
        settings = RuntimeSettings.credential_free(
            Path(temp_dir),
            encryption_key=b"credential-free-demo-key-32byte!",
            repository_root=repository_root,
            member_identities={"fixture-principal.ts.net": FIXTURE_MEMBER_ID},
            member_roles={FIXTURE_MEMBER_ID: "principal"},
            deterministic_openclaw_fixture=True,
            fixture_member_id=FIXTURE_MEMBER_ID,
        )
        with TestClient(create_application(settings)) as client:
            response = client.post(
                "/api/comms/v1/demo/canonical-message",
                headers={
                    "Tailscale-User-Login": "fixture-principal.ts.net",
                    "X-Correlation-Id": "demo:cli",
                },
                json={
                    "request_id": "cli-canonical-message",
                    "native_message_id": "fixture-cli-001",
                    "body": body,
                    "received_at": "2026-07-30T12:05:00Z",
                },
            )
            if response.status_code != 200:
                raise typer.Exit(code=1)
            typer.echo(json.dumps(response.json(), indent=2, sort_keys=True))


@demo_app.command("synthetic-member")
def synthetic_member(
    instance_dir: Path = typer.Option(
        ...,
        "--instance-dir",
        help=(
            "Dedicated persistent directory for fictional synthetic state. "
            "An existing non-synthetic instance is rejected."
        ),
    ),
    port: int = typer.Option(8321, min=1024, max=65535),
    model_transport: str = typer.Option(
        "deterministic",
        "--model-transport",
        help="Use 'deterministic' or the locally pinned 'gateway'.",
    ),
) -> None:
    """Serve the production-composed synthetic member on loopback only."""

    repository_root = Path(__file__).resolve().parents[3]
    if model_transport not in {"deterministic", "gateway"}:
        raise typer.BadParameter("must be 'deterministic' or 'gateway'")
    gateway = model_transport == "gateway"
    gateway_token = os.environ.get("ADMINME_OPENCLAW_GATEWAY_TOKEN")
    shared_secret = os.environ.get("ADMINME_OPENCLAW_SHARED_SECRET")
    if gateway and (not gateway_token or not shared_secret):
        raise typer.BadParameter(
            "gateway mode requires ADMINME_OPENCLAW_GATEWAY_TOKEN and "
            "ADMINME_OPENCLAW_SHARED_SECRET"
        )
    settings = build_synthetic_member_settings(
        instance_dir,
        repository_root,
        openclaw_gateway_token=gateway_token if gateway else None,
        openclaw_shared_secret=shared_secret if gateway else None,
    )
    typer.echo(
        f"Synthetic member ready at http://127.0.0.1:{port}/member/triage "
        f"using {settings.instance.instance_dir}"
    )
    uvicorn.run(
        create_application(settings),
        host="127.0.0.1",
        port=port,
        proxy_headers=False,
        server_header=False,
    )


__all__ = ["demo_app"]
