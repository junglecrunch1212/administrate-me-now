"""Current identity map through production composition, never fabricated action authority."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from adminme.runtime.application import create_application
from bootstrap.workstation_inventory import provision
from tests.acceptance.test_cos_identity_delegation_kernel import (
    A,
    B,
    OPENCLAW_HEADERS,
    _grant_private,
    _provenance,
    _settings,
)
from tests.acceptance.test_cos_service_private_work import _service
from tests.unit.test_workstation_inventory import document

PATH = "/api/openclaw/v1/tools/workstation-inventory"


def test_current_inventory_owner_filtering_and_truthful_unknowns(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        config = runtime.settings.instance.config_dir
        config.chmod(0o700)
        value = document(datetime.now(timezone.utc))
        for member in (A, B):
            row = document(datetime.now(timezone.utc), owner=member)["bindings"][0]
            row["binding_id"] = f"browser-{member}"
            value["bindings"].append(row)
        provision(config, value, expected_revision=0)

        def read(provenance: dict, **query: object) -> dict:
            response = client.post(
                PATH, headers=OPENCLAW_HEADERS, json={"provenance": provenance, **query}
            )
            assert response.status_code == 200, response.text
            return response.json()

        own = read(_service("own-inventory"))
        assert {r[3] for r in own["items"] if r[0] == "identities"} == {"CoS"}
        _grant_private(client, A, "current-inventory-grant-a")
        current = read(_service("delegated-inventory"))
        assert {r[3] for r in current["items"] if r[0] == "identities"} == {"CoS", A}
        member = read(_provenance(A, "member-inventory"))
        assert {r[3] for r in member["items"] if r[0] == "identities"} == {"CoS", A}
        assert member["provider_effect_count"] == 0
        assert member["inventory_grants_authority"] is False
        assert all(
            row[6][5] == "?" for row in member["items"] if row[0] == "identities"
        )
        assert member["counts"]["identities"] == 2
        assert own["counts"]["identities"] == 1
        assert "browser-" + B not in json.dumps(member)
        denied_detail = client.post(
            PATH,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _provenance(A, "other-binding"),
                "view": "details",
                "section": "identities",
                "binding_id": "browser-" + B,
            },
        )
        assert denied_detail.status_code == 404
        detail = read(
            _provenance(A, "own-detail"),
            view="details",
            section="identities",
            binding_id="cos-browser",
        )
        assert any(
            row["path"] == ["expected_identity", "account"] for row in detail["items"]
        )
        invalid = _service("forged-inventory")
        invalid["session_key"] += "bad"
        assert (
            client.post(
                PATH, headers=OPENCLAW_HEADERS, json={"provenance": invalid}
            ).status_code
            == 403
        )
        # Runtime rereads the canonical file; a permission/account change never
        # remains hidden behind a compiled prompt or inventory cache.
        value["revision"] = 2
        value["bindings"][0]["observed_identity"]["account"] = "wrong@fixture.invalid"
        provision(config, value, expected_revision=1)
        stale = client.post(
            PATH,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service("stale-page"),
                "offset": 1,
                "snapshot": own["snapshot"],
            },
        )
        assert stale.status_code == 409
        assert (
            read(_service("fresh-inventory"), section="identities")["items"][0][5]
            == "identity_mismatch"
        )
