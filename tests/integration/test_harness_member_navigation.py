"""Production-composition preservation cases for the navigation pilot."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from adminme.runtime.application import RuntimeSettings, create_application

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"h" * 32
PRINCIPAL = "member-harness-principal"
CHILD = "member-harness-child"
DEVICE = "member-harness-device"
AMBIENT = "member-harness-ambient"
HEADERS = {
    PRINCIPAL: {"Tailscale-User-Login": "harness-principal.ts.net"},
    CHILD: {"Tailscale-User-Login": "harness-child.ts.net"},
    DEVICE: {"Tailscale-User-Login": "harness-device.ts.net"},
    AMBIENT: {"Tailscale-User-Login": "harness-ambient.ts.net"},
}


def _settings(instance_dir: Path) -> RuntimeSettings:
    config_dir = instance_dir / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "extensions-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active": {
                    "persona": "quiet_assistant",
                    "profiles": {
                        PRINCIPAL: "adhd_executive",
                        CHILD: "kid_scoreboard",
                        DEVICE: "ambient_entity",
                        AMBIENT: "ambient_entity",
                    },
                },
                "previous": None,
                "staged": {},
            }
        ),
        encoding="utf-8",
    )
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={
            "harness-principal.ts.net": PRINCIPAL,
            "harness-child.ts.net": CHILD,
            "harness-device.ts.net": DEVICE,
            "harness-ambient.ts.net": AMBIENT,
        },
        member_roles={
            PRINCIPAL: "principal",
            CHILD: "child",
            DEVICE: "device",
            AMBIENT: "ambient",
        },
        deterministic_openclaw_fixture=True,
        fixture_member_id=PRINCIPAL,
    )


def test_bootstrap_aliases_authorization_modes_and_observation_are_exact(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    app = create_application(_settings(instance))

    with TestClient(app) as client:
        expected = {
            PRINCIPAL: (
                [
                    "today",
                    "triage",
                    "comms",
                    "reminders",
                    "calendar",
                    "people",
                    "notes",
                    "finance",
                    "scoreboard",
                    "settings",
                ],
                "carousel",
            ),
            CHILD: (["today", "scoreboard"], "child"),
            DEVICE: (["scoreboard"], "compressed"),
            AMBIENT: (["scoreboard"], "compressed"),
        }
        for member_id, (routes, mode) in expected.items():
            response = client.get(
                "/api/member/v1/bootstrap", headers=HEADERS[member_id]
            )
            assert response.status_code == 200
            payload = response.json()
            assert [item["id"] for item in payload["navigation"]] == routes
            assert payload["mode"] == mode
            assert payload["observation_mode"] is True
            assert payload["trust"] == {
                "operator_access": False,
                "actions_as": member_id,
                "reads_as": member_id,
            }

        for alias, canonical in (("inbox", "triage"), ("knowledge", "notes")):
            response = client.get(
                f"/member/{alias}", headers=HEADERS[PRINCIPAL], follow_redirects=False
            )
            assert response.status_code == 308
            assert response.headers["location"] == f"/member/{canonical}"

        for restricted in (CHILD, DEVICE, AMBIENT):
            denied = client.get("/member/settings", headers=HEADERS[restricted])
            assert denied.status_code == 404
            assert denied.json() == {"detail": "surface not found"}

        scoreboard = client.get(
            "/api/member/v1/surfaces/scoreboard", headers=HEADERS[PRINCIPAL]
        )
        assert scoreboard.status_code == 200
        surface = scoreboard.json()
        assert surface["mode"] == "carousel"
        assert surface["semantics"]["mode"] == "carousel"
        assert surface["semantics"]["profile_id"] == "adhd_executive"
        assert surface["observation"] == {
            "active": True,
            "writes_execute": False,
            "explanation": (
                "Observation mode records what would happen without changing providers."
            ),
            "evidence_href": "/api/core/v1/activity",
        }
