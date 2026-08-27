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
        bridge_enrollments={
            "a.ts.net": {
                "member_id": "member-a",
                "enrollment_id": "enrollment-gate6-activity",
                "status": "active",
                "configured_packs": ["knowledge-obsidian"],
                "enabled_packs": ["knowledge-obsidian"],
                "authorized_packs": ["knowledge-obsidian"],
                "adapters_active": ["knowledge-obsidian"],
                "pack_config": {"knowledge-obsidian": {"member_id": "member-a"}},
                "pack_capability_states": {"knowledge-obsidian": "not_required"},
                "provider_capability_state": "not_required",
                "capability_attestations": {},
            }
        },
        member_roles={"member-a": "principal", "member-b": "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id="member-a",
    )
    app = create_application(settings)
    with TestClient(app) as client:
        private = EventEnvelope(
            event_at_ms=1_850_000_000_000,
            tenant_id=settings.instance.tenant_id,
            type="member_knowledge.note_added",
            schema_version=1,
            occurred_at="2028-08-16T00:53:20Z",
            source_adapter="knowledge-obsidian",
            source_account_id="obsidian",
            owner_scope="private:member-a",
            visibility_scope="private:member-a",
            sensitivity="sensitive",
            payload={
                "knowledge_id": "gate6-private-note",
                "member_id": "member-a",
                "title": "Gate 6 private activity",
                "body": "Synthetic private activity fixture.",
                "source_kind": "obsidian",
                "source_external_id": "gate6-private-note.md",
                "content_sha256": "0" * 64,
                "captured_at": "2028-08-16T00:53:20Z",
            },
        )
        response = client.post(
            "/api/bridge/ingest",
            headers={
                "Tailscale-User-Login": "a.ts.net",
                "X-AdministrateMe-Bridge-Enrollment": "enrollment-gate6-activity",
            },
            json=private.model_dump(mode="json"),
        )
        assert response.status_code == 202, response.text
        event_id = response.json()["event_id"]

        activity = client.get(
            "/api/core/v1/activity", headers={"Tailscale-User-Login": "a.ts.net"}
        ).json()
        item = next(item for item in activity["items"] if item["event_id"] == event_id)
        assert item["source"] == {
            "adapter": "knowledge-obsidian",
            "account_id": "obsidian",
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
