"""Gate 2 process acceptance for the production runtime spine."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
import sqlcipher3

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
    "triage",
}


def _seed_v2_triage_projection(settings: RuntimeSettings) -> None:
    """Create the exact deployed pre-PR #287 Triage table shape."""

    path = settings.instance.projection_db_path("triage")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlcipher3.connect(str(path))
    try:
        conn.execute(f"PRAGMA key = \"x'{TEST_KEY.hex()}'\"")
        conn.executescript(
            """
            CREATE TABLE triage_items (
                proposal_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                target_kind TEXT NOT NULL,
                owner_member_id TEXT NOT NULL,
                title TEXT NOT NULL,
                candidate_json TEXT NOT NULL DEFAULT '{}',
                route_json TEXT,
                source_event_id TEXT NOT NULL,
                confidence REAL,
                reasons_json TEXT NOT NULL DEFAULT '[]',
                state TEXT NOT NULL CHECK (
                    state IN ('pending_review','confirmed','dismissed')
                ),
                proposed_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT,
                resolution_reason TEXT,
                materialized_event_ids_json TEXT NOT NULL DEFAULT '[]',
                owner_scope TEXT NOT NULL,
                visibility_scope TEXT NOT NULL,
                sensitivity TEXT NOT NULL DEFAULT 'normal' CHECK (
                    sensitivity IN ('normal','sensitive','privileged')
                ),
                last_event_id TEXT NOT NULL,
                PRIMARY KEY (tenant_id, proposal_id)
            );
            CREATE INDEX idx_triage_tenant_owner_state
                ON triage_items(tenant_id, owner_member_id, state, proposed_at);
            CREATE INDEX idx_triage_tenant_source
                ON triage_items(tenant_id, source_event_id);
            """
        )
        conn.execute(
            """INSERT INTO triage_items (
                   proposal_id, tenant_id, source_kind, target_kind,
                   owner_member_id, title, source_event_id, state, proposed_at,
                   owner_scope, visibility_scope, last_event_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy-proposal",
                settings.instance.tenant_id,
                "message",
                "task",
                "acceptance-member",
                "Preserved candidate",
                "legacy-source-event",
                "pending_review",
                "2026-08-08T12:00:00Z",
                "private:acceptance-member",
                "private:acceptance-member",
                "legacy-source-event",
            ),
        )
        conn.commit()
    finally:
        conn.close()


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


def test_real_process_upgrades_existing_triage_projection_before_indexing(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    settings = RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"acceptance-member.ts.net": "acceptance-member"},
    )
    _seed_v2_triage_projection(settings)

    with TestClient(create_application(settings)) as client:
        assert client.get("/api/system/live").json()["runtime_started"] is True
        response = client.get(
            "/api/core/v1/triage",
            headers={"Tailscale-User-Login": "acceptance-member.ts.net"},
        )
        assert response.status_code == 200, response.text
        [item] = response.json()["items"]
        assert item["proposal_id"] == "legacy-proposal"
        assert item["title"] == "Preserved candidate"

        conn = client.app.state.runtime.projections.connection("triage")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(triage_items)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(triage_items)")}
        assert {"parent_intent_id", "domain_approval_event_id"} <= columns
        assert "idx_triage_tenant_parent_intent" in indexes
