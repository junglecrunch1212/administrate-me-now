"""Gate 2 process acceptance for the production runtime spine."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"a" * 32
EXPECTED_PROJECTIONS = {
    "parties",
    "interactions",
    "artifacts",
    "commitments",
    "tasks",
    "recurrences",
    "calendars",
    "places_assets_accounts",
    "money",
    "vector_search",
    "xlsx_workbooks",
    "member_knowledge",
    "lists",
}


def test_real_process_boot_ingest_diagnostics_and_shutdown(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    settings = RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"acceptance-member.ts.net": "acceptance-member"},
    )
    app = create_application(settings)

    with TestClient(app) as client:
        assert client.get("/api/system/live").json()["runtime_started"] is True
        readiness = client.get("/api/system/ready")
        assert readiness.status_code == 503
        diagnostics = readiness.json()
        assert diagnostics["status"] == "blocked"
        assert diagnostics["observation_active"] is True
        registered = {
            key.removeprefix("projection:")
            for key in diagnostics["capabilities"]
            if key.startswith("projection:")
        }
        assert registered == EXPECTED_PROJECTIONS

        envelope = EventEnvelope(
            event_at_ms=1_780_000_000_000,
            tenant_id=settings.instance.tenant_id,
            type="bridge.enrolled",
            schema_version=1,
            occurred_at="2026-05-28T12:00:00Z",
            source_adapter="acceptance-bridge",
            source_account_id="acceptance-node",
            owner_scope="private:acceptance-member",
            visibility_scope="private:acceptance-member",
            sensitivity="normal",
            payload={
                "member_id": "acceptance-member",
                "bridge_node_id": "acceptance-node",
                "tailscale_identity": "acceptance-member.ts.net",
                "adapters_active": ["acceptance-fixture"],
                "enrolled_at": "2026-05-28T12:00:00Z",
            },
        )
        result = client.post(
            "/api/bridge/ingest",
            json=envelope.model_dump(mode="json"),
            headers={"Tailscale-User-Login": "acceptance-member.ts.net"},
        )
        assert result.status_code == 202, result.text
        event_id = result.json()["event_id"]

    async def read_back() -> dict[str, object] | None:
        reopened = EventLog(settings.instance, TEST_KEY)
        try:
            return await reopened.get(event_id)
        finally:
            await reopened.close()

    stored = asyncio.run(read_back())
    assert stored is not None
    assert stored["owner_scope"] == "private:acceptance-member"
    assert stored["source_adapter"] == "acceptance-bridge"
