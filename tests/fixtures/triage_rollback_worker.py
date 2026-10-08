"""Hosted disposable predecessor worker; executed with python -c from its archive."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import adminme.events.log as log_module
import adminme.projections.triage.handlers as triage_module
import adminme.runtime.application as application
from adminme.events.envelope import EventEnvelope


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


def _prepare_upgrade(client: TestClient, member: str) -> dict[str, Any]:
    """Use the archived predecessor's actual API and CAS projection behavior."""
    runtime = client.app.state.runtime
    conn = runtime.projections.connection("triage")
    version = conn.execute(
        "SELECT value FROM _projection_meta WHERE key='version'"
    ).fetchone()[0]
    assert version == "11"
    assert "content_revision_event_id" not in {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(triage_items)").fetchall()
    }
    headers = {"Tailscale-User-Login": "rollback.ts.net"}
    expected: dict[str, dict[str, Any]] = {}
    refused_action_id = "fixture-predecessor-refused-correction"
    for kind in ("untouched", "enriched", "managed", "refused", "dismissed"):
        proposal_id = f"fixture-predecessor-{kind}"
        source_id = f"fixture-predecessor-source-{kind}"
        now = datetime.now(timezone.utc).isoformat()
        proposal_event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=time.time_ns() // 1_000_000,
                tenant_id=runtime.settings.instance.tenant_id,
                type="triage.item.proposed",
                schema_version=1,
                occurred_at=now,
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=f"private:{member}",
                visibility_scope=f"private:{member}",
                idempotency_key=f"fixture-predecessor-proposal:{kind}",
                payload={
                    "proposal_id": proposal_id,
                    "target_kind": "task",
                    "owner_member_id": member,
                    "title": f"Review fixture {kind}",
                    "candidate": {},
                    "route": {"member_id": member},
                    "source_event_id": source_id,
                },
            ),
        )
        client.portal.call(_settle, runtime)
        content_revision = proposal_event_id
        if kind in {"enriched", "managed", "dismissed"}:
            path = (
                "enrich"
                if kind == "enriched"
                else "manage"
                if kind == "managed"
                else "dismiss"
            )
            body: dict[str, Any] = {
                "request_id": f"fixture-predecessor-member-{kind}",
                "expected_last_event_id": proposal_event_id,
            }
            if kind == "enriched":
                body["title"] = "Member enriched the fixture obligation"
            elif kind == "managed":
                body.update(
                    action="correct_extraction",
                    corrected_title="Member corrected the fixture obligation",
                )
            else:
                body["reason"] = "Member dismissed the fixture obligation"
            response = client.post(
                f"/api/member/v1/triage/{proposal_id}/{path}",
                headers=headers,
                json=body,
            )
            assert response.status_code == 200, response.text
            if kind != "dismissed":
                content_revision = response.json()["event_id"]
        elif kind == "refused":
            # Model the durable race that the predecessor CAS rejected: the
            # attempted command exists, but never entered the applied-action
            # table and must not become a content revision on upgrade.
            client.portal.call(
                runtime.events.write,
                EventEnvelope(
                    event_at_ms=time.time_ns() // 1_000_000,
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="triage.item.managed",
                    schema_version=1,
                    occurred_at=now,
                    source_adapter="member-triage",
                    source_account_id=member,
                    actor_identity=member,
                    owner_scope=f"private:{member}",
                    visibility_scope=f"private:{member}",
                    idempotency_key=refused_action_id,
                    payload={
                        "action_id": refused_action_id,
                        "proposal_id": proposal_id,
                        "member_id": member,
                        "action": "correct_extraction",
                        "action_revision": 1,
                        "expected_last_event_id": "fixture-unmatched-stale-revision",
                        "corrected_title": "This refused correction must never apply",
                        "managed_at": now,
                    },
                ),
            )
        client.portal.call(_settle, runtime)
        row = dict(
            conn.execute(
                "SELECT * FROM triage_items WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        )
        if kind == "refused":
            assert row["title"] == "Review fixture refused"
            assert row["last_event_id"] == proposal_event_id
            assert (
                conn.execute(
                    "SELECT 1 FROM triage_management_actions WHERE action_id=?",
                    (refused_action_id,),
                ).fetchone()
                is None
            )
        expected[proposal_id] = {
            "title": row["title"],
            "state": row["state"],
            "proposal_event_id": proposal_event_id,
            "content_revision_event_id": content_revision,
            "source_event_id": source_id,
        }
    assert client.portal.call(_external_writes, runtime) == 0
    return {
        "contract": "protected-predecessor-edited-triage-v1",
        "projection_version": version,
        "rows": expected,
        "refused_action_id": refused_action_id,
        "refused_attempt_applied": False,
        "external_write_requests": 0,
    }


def main() -> None:
    root, instance, result_path = (Path(value).resolve() for value in sys.argv[1:4])
    event_id = sys.argv[4]
    mode = sys.argv[5] if len(sys.argv) > 5 else "verify-rollback"
    assert mode in {"verify-rollback", "prepare-upgrade"}
    assert root.parent == instance.parent == result_path.parent
    assert root.name == "protected-predecessor"
    assert instance.name == "synthetic-encrypted-state"
    for module in (application, log_module, triage_module):
        assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
    member = "fixture-rollback-adult"  # fixture:tenant_data:ok
    settings = application.RuntimeSettings.credential_free(
        instance,
        # Explicit legacy-only mode when this additive fixture seam exists.
        **(
            {"legacy_personal_obligation_compatibility": True}
            if "legacy_personal_obligation_compatibility"
            in application.RuntimeSettings.__dataclass_fields__
            else {}
        ),
        encryption_key=b"r" * 32,
        repository_root=root,
        member_identities={"rollback.ts.net": member},
        member_roles={member: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=member,
        human_triage_management_enabled=mode == "prepare-upgrade",
    )
    assert settings.instance.instance_dir.resolve() == instance
    assert settings.openclaw_gateway_token is None
    assert settings.openclaw_enabled is False
    with TestClient(application.create_application(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime)
        if mode == "prepare-upgrade":
            result = _prepare_upgrade(client, member)
            result_path.write_text(json.dumps(result), encoding="utf-8")
            return
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
