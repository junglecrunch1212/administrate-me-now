"""Explicit owner intent through member APIs, encrypted history and replay."""

from dataclasses import replace
import json

from fastapi.testclient import TestClient

from adminme.lib.governance import ActionGateConfig
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_disclosure import enabled_settings, import_fixture
from tests.acceptance.test_openclaw_native_seam import DEMO_HEADERS, OTHER

BASE = "/api/member/v1/finance/choices"


def goal(publication, **overrides):
    return {
        "request_id": "synthetic-choice-create",
        "book_id": publication["book_id"],
        "publication_id": publication["publication_id"],
        "label": "Synthetic reserve goal",
        "kind": "reserve",
        "status": "selected",
        "target_minor": 123456,
        "currency": "USD",
        "target_date": "2030-06-01",
        "priority": "preferred",
        **overrides,
    }


def test_goal_preference_idempotency_conflict_replay_and_restart(tmp_path):
    configured = enabled_settings(tmp_path)
    with TestClient(create_application(configured)) as client:
        publication, _ = import_fixture(client)
        created = client.post(
            BASE + "/goals/goal-synthetic", headers=DEMO_HEADERS, json=goal(publication)
        )
        assert created.status_code == 200, created.text
        data = created.json()
        assert data["choice"]["record"]["revision"] == 1
        assert data["choice"]["record"]["external_action_authorized"] is False
        repeated = client.post(
            BASE + "/goals/goal-synthetic", headers=DEMO_HEADERS, json=goal(publication)
        )
        assert (
            repeated.json()["duplicate"]
            and repeated.json()["event_id"] == data["event_id"]
        )
        conflict = client.post(
            BASE + "/goals/goal-synthetic",
            headers=DEMO_HEADERS,
            json=goal(publication, target_minor=999),
        )
        assert conflict.status_code == 409
        stale = client.post(
            BASE + "/goals/goal-synthetic",
            headers=DEMO_HEADERS,
            json=goal(publication, request_id="stale-choice-update", status="paused"),
        )
        assert stale.status_code == 409
        updated = client.post(
            BASE + "/goals/goal-synthetic",
            headers=DEMO_HEADERS,
            json=goal(
                publication,
                request_id="pause-choice-update",
                status="paused",
                expected_event_id=data["event_id"],
            ),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["choice"]["record"]["revision"] == 2
        preference = client.post(
            BASE + "/preferences/grocery_substitution",
            headers=DEMO_HEADERS,
            json={
                "request_id": "synthetic-menu-choice",
                "book_id": publication["book_id"],
                "publication_id": publication["publication_id"],
                "selected_option_id": "keep_menu",
            },
        )
        assert preference.status_code == 200, preference.text
        assert (
            preference.json()["choice"]["record"]["source"] == "explicit_member_choice"
        )
        snapshot = client.get(BASE, headers=DEMO_HEADERS).json()
        assert len(snapshot["items"]) == 2
        assert snapshot["items"][0]["record"]["status"] == "paused"
        client.portal.call(client.app.state.runtime.projections.rebuild, "money")
        assert (
            client.get(BASE, headers=DEMO_HEADERS).json()["items"] == snapshot["items"]
        )

        async def inspect():
            row = (
                client.app.state.runtime.projections.connection("money")
                .execute(
                    "SELECT content_sha256 FROM finance_publications WHERE publication_id=?",
                    (publication["publication_id"],),
                )
                .fetchone()
            )
            return row[0]

        assert client.portal.call(inspect) == publication["content_sha256"]
    with TestClient(create_application(configured)) as client:
        assert (
            client.get(BASE, headers=DEMO_HEADERS).json()["items"] == snapshot["items"]
        )


def test_choices_require_owner_baseline_and_current_view(tmp_path):
    configured = enabled_settings(tmp_path)
    configured = replace(
        configured,
        member_identities={
            **configured.member_identities,
            "fixture-other.ts.net": OTHER,
        },
    )
    with TestClient(create_application(configured)) as client:
        assert (
            client.get(BASE, headers=DEMO_HEADERS).json()["state"]
            == "accepted_publication_required"
        )
        missing = client.post(
            BASE + "/goals/goal-synthetic",
            headers=DEMO_HEADERS,
            json=goal({"book_id": "absent", "publication_id": "absent"}),
        )
        assert missing.status_code == 422
        publication, _ = import_fixture(client)
        assert (
            client.post(
                BASE + "/goals/goal-synthetic",
                headers=DEMO_HEADERS,
                json=goal(publication),
            ).status_code
            == 200
        )
        other = {"Tailscale-User-Login": "fixture-other.ts.net"}
        hidden = client.get(
            BASE, headers=other, params={"book_id": publication["book_id"]}
        )
        assert hidden.status_code == 200, hidden.text
        assert hidden.json()["items"] == [] and "123456" not in hidden.text
        assert (
            client.post(
                BASE + "/goals/stolen", headers=other, json=goal(publication)
            ).status_code
            == 422
        )
        view = {**DEMO_HEADERS, "X-AdminMe-View-Member": OTHER}
        assert client.get(BASE, headers=view).status_code == 403
        assert (
            client.post(
                BASE + "/goals/forged",
                headers=DEMO_HEADERS,
                json={**goal(publication), "owner_member_id": OTHER},
            ).status_code
            == 422
        )
        runtime = client.app.state.runtime
        runtime.settings = replace(runtime.settings, finance_publications_enabled=False)
        assert client.get(BASE, headers=DEMO_HEADERS).status_code == 403


def test_denied_write_audit_contains_no_private_choice_fields(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        publication, _ = import_fixture(client)
        runtime = client.app.state.runtime
        runtime.guarded_write._config = ActionGateConfig(
            action_gates={"finance.*": "hard_refuse"}, rate_limits={}
        )
        denied = client.post(
            BASE + "/goals/private-choice", headers=DEMO_HEADERS, json=goal(publication)
        )
        assert denied.status_code in {403, 409}, denied.text

        async def audits():
            return [
                row
                async for row in runtime.event_log.read_since(
                    None, types=["write.denied"], limit=100
                )
            ]

        rows = client.portal.call(audits)
        assert rows
        text = json.dumps(rows)
        for private in (
            "123456",
            "Synthetic reserve goal",
            publication["book_id"],
            publication["publication_id"],
        ):
            assert private not in text
        assert client.get(BASE, headers=DEMO_HEADERS).json()["items"] == []
