"""Hosted disposable predecessor worker; executed with python -c from its archive."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import adminme.events.log as log_module
import adminme.projections.triage.handlers as triage_module
import adminme.runtime.application as application


async def _settle(runtime: Any) -> None:
    deadline = asyncio.get_running_loop().time() + 60.0
    prior: str | None = None
    stable = 0
    statuses: dict[str, dict[str, Any]] = {}
    while asyncio.get_running_loop().time() < deadline:
        statuses = {
            name: await runtime.bus.subscriber_status(name)
            for name in sorted(runtime.bus._subscribers)  # noqa: SLF001
        }
        assert not any(row["degraded"] for row in statuses.values()), statuses
        latest = await runtime.event_log.latest_event_id()
        stable = (
            stable + 1
            if latest == prior
            and all(row["lag_count"] == 0 for row in statuses.values())
            else 0
        )
        prior = latest
        if stable >= 2:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"protected predecessor did not settle: {statuses}")


async def _external_writes(runtime: Any) -> int:
    return await runtime.event_log.count_since(
        None, types=["external_sync.write_requested"]
    )


def main() -> None:
    root, instance, result_path = (Path(value).resolve() for value in sys.argv[1:4])
    event_id = sys.argv[4]
    assert root.parent == instance.parent == result_path.parent
    assert root.name == "protected-predecessor"
    assert instance.name == "synthetic-encrypted-state"
    for module in (application, log_module, triage_module):
        assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
    member = "fixture-rollback-adult"  # fixture:tenant_data:ok
    settings = application.RuntimeSettings.credential_free(
        instance,
        encryption_key=b"r" * 32,
        repository_root=root,
        member_identities={"rollback.ts.net": member},
        member_roles={member: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=member,
    )
    assert settings.instance.instance_dir.resolve() == instance
    assert settings.openclaw_gateway_token is None
    assert settings.openclaw_enabled is False
    with TestClient(application.create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime)
        retained = client.portal.call(runtime.event_log.get, event_id)
        assert retained is not None and retained["schema_version"] == 4
        assert retained["type"] == "triage.item.superseded"
        assert retained["payload"]["expected_last_event_id"]
        conn = runtime.projections.connection("triage")
        row = conn.execute(
            "SELECT * FROM triage_items WHERE proposal_id=?",
            ("fixture-rollback-member-dismissal",),
        ).fetchone()
        assert row is not None and row["state"] == "dismissed"
        assert row["resolved_by"] == member
        response = client.get(
            "/api/member/v1/surfaces/triage",
            headers={"Tailscale-User-Login": "rollback.ts.net"},
            params={"member_id": member},
        )
        assert response.status_code == 200, response.text
        assert response.json()["triage"]
        assert client.portal.call(_external_writes, runtime) == 0
        result = {
            "contract": "protected-predecessor-triage-rollback-v1",
            "imports_from_archive": True,
            "same_retained_v4_event": True,
            "all_consumers_settled": True,
            "member_api_status": response.status_code,
            "member_dismissal_preserved": True,
            "external_write_requests": 0,
        }
    result_path.write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
