"""AC-30: production-composed People and relationship-memory process proof."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
KEY = b"p" * 32
OWNER = "member-people-owner"
OTHER = "member-people-other"
CHILD = "member-people-child"
HEADERS = {
    OWNER: {"Tailscale-User-Login": "people-owner.fixture.ts.net"},
    OTHER: {"Tailscale-User-Login": "people-other.fixture.ts.net"},
    CHILD: {"Tailscale-User-Login": "people-child.fixture.ts.net"},
}


def _settings(instance_dir: Path) -> RuntimeSettings:
    instance_dir.mkdir()
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=KEY,
        repository_root=REPO_ROOT,
        member_identities={
            "people-owner.fixture.ts.net": OWNER,
            "people-other.fixture.ts.net": OTHER,
            "people-child.fixture.ts.net": CHILD,
        },
        member_roles={OWNER: "principal", OTHER: "principal", CHILD: "child"},
    )


def _event(
    runtime: Any,
    event_type: str,
    payload: dict[str, Any],
    *,
    owner: str = OWNER,
    source_adapter: str = "people-acceptance-fixture",
) -> EventEnvelope:
    now = EventEnvelope.now_utc_iso()
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=runtime.settings.instance.tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=now,
        source_adapter=source_adapter,
        source_account_id="credential-free",
        owner_scope=f"private:{owner}",
        visibility_scope=f"private:{owner}",
        sensitivity="normal",
        actor_identity=owner,
        payload=payload,
    )


def _append_and_wait(
    client: TestClient, runtime: Any, envelope: EventEnvelope, projection: str
) -> str:
    event_id = client.portal.call(runtime.events.write, envelope)
    client.portal.call(runtime.wait_for_projection, projection, event_id)
    return event_id


def test_ac30_people_is_private_qualified_replayable_and_human_gated(
    tmp_path: Path,
) -> None:
    app = create_application(_settings(tmp_path / "instance"))
    with TestClient(app) as client:
        runtime = app.state.runtime
        _append_and_wait(
            client,
            runtime,
            _event(
                runtime,
                "endpoint.observed",
                {
                    "endpoint_id": "endpoint-automated",
                    "member_id": OWNER,
                    "kind": "email",
                    "value": "offers@example.test",
                    "value_normalized": "offers@example.test",
                    "classification": "automated",
                    "observed_at": EventEnvelope.now_utc_iso(),
                    "evidence": ["inbound sender"],
                },
                source_adapter="identity_resolution",
            ),
            "parties",
        )
        source_event_id = _append_and_wait(
            client,
            runtime,
            _event(
                runtime,
                "contact.record.observed",
                {
                    "contact_record_id": "contact-acceptance-1",
                    "member_id": OWNER,
                    "provider": "fixture",
                    "provider_account_id": "fixture-contacts",
                    "container_external_id": "people",
                    "external_id": "provider-person-1",
                    "provider_revision": "etag-1",
                    "kind": "person",
                    "display_name": "Example Neighbor",
                    "organization_name": "Neighborhood Group",
                    "identifiers": [
                        {
                            "kind": "email",
                            "value": "neighbor@example.test",
                            "value_normalized": "neighbor@example.test",
                            "primary": True,
                        }
                    ],
                    "observed_at": EventEnvelope.now_utc_iso(),
                },
            ),
            "parties",
        )

        people = client.get("/api/member/v1/surfaces/people", headers=HEADERS[OWNER])
        assert people.status_code == 200, people.text
        cockpit = people.json()["cockpit"]
        assert cockpit["contract"] == "deterministic-people-cockpit-v1"
        assert [row["display_name"] for row in cockpit["directory"]] == [
            "Example Neighbor"
        ]
        assert cockpit["unresolved_endpoints"] == {
            "count": 0,
            "actionable_count": 0,
            "copy": (
                "Observed senders stay out of People. Only a deterministic, "
                "evidence-backed identity candidate may enter Triage for confirmation."
            ),
        }
        assert "offers@example.test" not in people.text
        party_id = cockpit["directory"][0]["party_id"]

        search = client.get(
            "/api/member/v1/people/search?q=Neighborhood", headers=HEADERS[OWNER]
        )
        assert search.status_code == 200, search.text
        assert [row["party_id"] for row in search.json()["results"]] == [party_id]
        detail = client.get(f"/api/member/v1/people/{party_id}", headers=HEADERS[OWNER])
        assert detail.status_code == 200, detail.text
        assert detail.json()["contact_records"][0]["provider_revision"] == "etag-1"
        assert detail.json()["identifiers"][0]["value"] == "neighbor@example.test"
        profile = client.post(
            f"/api/member/v1/people/{party_id}/profile",
            headers=HEADERS[OWNER],
            json={
                "request_id": "people-profile-acceptance-1",
                "expected_last_event_id": None,
                "circle": "Neighbor",
                "desired_cadence_days": 90,
                "cues_enabled": True,
                "favorite": True,
                "suppressed": False,
                "suppression_reason": None,
                "tags": ["community"],
            },
        )
        assert profile.status_code == 202, profile.text
        profiled = client.get(
            "/api/member/v1/surfaces/people", headers=HEADERS[OWNER]
        ).json()["cockpit"]["directory"][0]
        assert profiled["favorite"] is True
        assert profiled["relationship_label"] == "Neighbor"

        assert (
            client.get(
                f"/api/member/v1/people/{party_id}", headers=HEADERS[OTHER]
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/api/member/v1/surfaces/people", headers=HEADERS[CHILD]
            ).status_code
            == 404
        )

        _append_and_wait(
            client,
            runtime,
            _event(
                runtime,
                "relationship_fact.confirmed",
                {
                    "fact_id": "fact-acceptance-1",
                    "member_id": OWNER,
                    "party_id": party_id,
                    "kind": "preference",
                    "label": "Coffee",
                    "value": "Decaf",
                    "source_event_id": source_event_id,
                    "confirmed_at": EventEnvelope.now_utc_iso(),
                    "confirmed_by_member_id": OWNER,
                },
            ),
            "parties",
        )
        detail = client.get(
            f"/api/member/v1/people/{party_id}", headers=HEADERS[OWNER]
        ).json()
        assert detail["relationship_facts"][0]["value"] == "Decaf"

        grant_event_id = _append_and_wait(
            client,
            runtime,
            _event(
                runtime,
                "party.access_granted",
                {
                    "grant_id": "grant-acceptance-1",
                    "party_id": party_id,
                    "owner_member_id": OWNER,
                    "grantee_member_id": OTHER,
                    "field_groups": ["identifiers", "facts"],
                    "granted_at": EventEnvelope.now_utc_iso(),
                },
            ),
            "parties",
        )
        granted_detail = client.get(
            f"/api/member/v1/people/{party_id}", headers=HEADERS[OTHER]
        )
        assert granted_detail.status_code == 200, granted_detail.text
        assert granted_detail.json()["identifiers"][0]["value"] == (
            "neighbor@example.test"
        )
        assert granted_detail.json()["relationship_facts"][0]["value"] == "Decaf"
        assert granted_detail.json()["contact_records"] == []
        assert granted_detail.json()["member_profile"] is None
        assert [
            row["party_id"]
            for row in client.get(
                "/api/member/v1/people/search?q=Decaf", headers=HEADERS[OTHER]
            ).json()["results"]
        ] == [party_id]
        _append_and_wait(
            client,
            runtime,
            _event(
                runtime,
                "party.access_revoked",
                {
                    "grant_id": "grant-acceptance-1",
                    "revoked_at": EventEnvelope.now_utc_iso(),
                    "revoked_by_member_id": OWNER,
                },
            ),
            "parties",
        )
        assert grant_event_id
        assert (
            client.get(
                f"/api/member/v1/people/{party_id}", headers=HEADERS[OTHER]
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/api/member/v1/people/search?q=Decaf", headers=HEADERS[OTHER]
            ).json()["results"]
            == []
        )

        owner_detail = client.get(
            f"/api/member/v1/people/{party_id}", headers=HEADERS[OWNER]
        ).json()
        archived = client.post(
            f"/api/member/v1/people/{party_id}/lifecycle",
            headers=HEADERS[OWNER],
            json={
                "request_id": "people-archive-acceptance-1",
                "expected_last_event_id": owner_detail["party"]["last_event_id"],
                "action": "archive",
                "reason": "acceptance lifecycle proof",
            },
        )
        assert archived.status_code == 202, archived.text
        assert (
            client.get(
                f"/api/member/v1/people/{party_id}", headers=HEADERS[OWNER]
            ).status_code
            == 404
        )
        restored = client.post(
            f"/api/member/v1/people/{party_id}/lifecycle",
            headers=HEADERS[OWNER],
            json={
                "request_id": "people-restore-acceptance-1",
                "expected_last_event_id": archived.json()["event_id"],
                "action": "restore",
                "reason": None,
            },
        )
        assert restored.status_code == 202, restored.text
        assert (
            client.get(
                f"/api/member/v1/people/{party_id}", headers=HEADERS[OWNER]
            ).status_code
            == 200
        )

        proposal_id = "people-candidate-acceptance"
        triage_event = _append_and_wait(
            client,
            runtime,
            _event(
                runtime,
                "triage.item.proposed",
                {
                    "proposal_id": proposal_id,
                    "target_kind": "party_candidate",
                    "owner_member_id": OWNER,
                    "title": "Example Coach",
                    "candidate": {
                        "kind": "person",
                        "display_name": "Example Coach",
                    },
                    "route": {"member_id": OWNER},
                    "source_event_id": source_event_id,
                    "confidence": 0.8,
                    "reasons": ["fixture candidate; human confirmation required"],
                },
            ),
            "triage",
        )
        before = client.get(
            "/api/member/v1/surfaces/people", headers=HEADERS[OWNER]
        ).json()["cockpit"]["directory"]
        assert all(row["display_name"] != "Example Coach" for row in before)
        confirmed = client.post(
            f"/api/member/v1/triage/{proposal_id}/approve",
            headers=HEADERS[OWNER],
            json={
                "request_id": "people-acceptance-confirm-1",
                "expected_last_event_id": triage_event,
                "reason": None,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        after = client.get(
            "/api/member/v1/surfaces/people", headers=HEADERS[OWNER]
        ).json()["cockpit"]["directory"]
        assert {row["display_name"] for row in after} == {
            "Example Coach",
            "Example Neighbor",
        }

        client.portal.call(runtime.projections.rebuild, "parties")
        replayed = client.get(
            "/api/member/v1/surfaces/people", headers=HEADERS[OWNER]
        ).json()["cockpit"]["directory"]
        assert {row["display_name"] for row in replayed} == {
            "Example Coach",
            "Example Neighbor",
        }

        page = client.get("/member/people", headers=HEADERS[OWNER])
        assert page.status_code == 200
        js = client.get("/member-assets/member.js", headers=HEADERS[OWNER])
        css = client.get("/member-assets/member.css", headers=HEADERS[OWNER])
        assert "function renderPeople(data)" in js.text
        assert "PRIVATE RELATIONSHIP MEMORY" in js.text
        assert ".people-workspace" in css.text
        assert 'body[data-surface="people"] .chat-panel' in css.text
