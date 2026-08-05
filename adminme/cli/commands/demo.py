"""Credential-free canonical household demo command."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import typer
from fastapi.testclient import TestClient

from adminme.demo import FIXTURE_MEMBER_ID
from adminme.runtime.application import RuntimeSettings, create_application

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


__all__ = ["demo_app"]
