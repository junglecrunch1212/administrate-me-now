"""Gate 6 truthful capabilities and scope-filtered activity read model."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.extensions import ExtensionCatalog
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_activity_and_capabilities_derive_from_real_wiring(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    extensions = ExtensionCatalog(REPO_ROOT, instance)
    token = extensions.stage("profile", "adhd_executive")
    extensions.test_stage(token)
    extensions.activate(token, member_id="member-a")
    settings = RuntimeSettings.credential_free(
        instance,
        encryption_key=b"g" * 32,
        repository_root=REPO_ROOT,
        member_identities={"a.ts.net": "member-a", "b.ts.net": "member-b"},
        bridge_enrollments={"a.ts.net": {"member_id": "member-a", "status": "active"}},
        member_roles={"member-a": "principal", "member-b": "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id="member-a",
    )
    app = create_application(settings)
    with TestClient(app) as client:
        private = EventEnvelope(
            event_at_ms=1_850_000_000_000,
            tenant_id=settings.instance.tenant_id,
            type="bridge.enrolled",
            schema_version=1,
            occurred_at="2028-08-16T00:53:20Z",
            source_adapter="bridge-fixture",
            source_account_id="member-mac",
            owner_scope="private:member-a",
            visibility_scope="private:member-a",
            sensitivity="normal",
            payload={
                "member_id": "member-a",
                "bridge_node_id": "member-mac",
                "tailscale_identity": "a.ts.net",
                "adapters_active": ["fixture"],
                "enrolled_at": "2028-08-16T00:53:20Z",
            },
        )
        response = client.post(
            "/api/bridge/ingest",
            headers={"Tailscale-User-Login": "a.ts.net"},
            json=private.model_dump(mode="json"),
        )
        assert response.status_code == 202
        event_id = response.json()["event_id"]

        activity = client.get(
            "/api/core/v1/activity", headers={"Tailscale-User-Login": "a.ts.net"}
        ).json()
        item = next(item for item in activity["items"] if item["event_id"] == event_id)
        assert item["source"] == {
            "adapter": "bridge-fixture",
            "account_id": "member-mac",
        }
        assert item["scope"]["owner"] == "private:member-a"
        assert "payload" not in item
        assert activity["payload_policy"] == "identifier-and-state-fields-only"
        other = client.get(
            "/api/core/v1/activity", headers={"Tailscale-User-Login": "b.ts.net"}
        ).json()
        assert event_id not in {item["event_id"] for item in other["items"]}

        what_now = client.get(
            "/api/core/v1/what-now", headers={"Tailscale-User-Login": "a.ts.net"}
        ).json()
        assert what_now["profile_id"] == "adhd_executive"
        diagnostics = client.get("/api/system/ready").json()
        capabilities = diagnostics["capabilities"]
        assert capabilities["adapter:fixture-messaging"]["state"] == "healthy"
        assert capabilities["policy:notifications"]["state"] == "healthy"
        assert capabilities["read_model:activity-feed"]["state"] == "healthy"
        assert (
            len(
                [
                    key
                    for key in capabilities
                    if key.startswith(("profile:", "persona:"))
                ]
            )
            == 9
        )
