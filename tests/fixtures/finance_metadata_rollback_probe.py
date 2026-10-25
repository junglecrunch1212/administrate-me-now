"""Exact-source synthetic runtime rollback probe; no household state or credentials."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

CURRENT = "7751c462b0788eccf8435ac4dd6b1da9bbc26b30"
PREDECESSOR = "04a9805e4c91698017dd10b38aac51c5e1d0e5d9"
KINDS = {"settings.finance_metadata_changed", "finance.metadata_inspected"}


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda item: {"bytes_hex": item.hex()},
        ).encode()
    ).hexdigest()


def main():
    phase, source_arg, state_arg, key_arg, report_arg, seed_arg = sys.argv[1:]
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert phase in {"seed", "predecessor", "roundtrip"}
    source, state, key, report, seed = map(
        Path, (source_arg, state_arg, key_arg, report_arg, seed_arg)
    )
    source = source.resolve(strict=True)
    expected = PREDECESSOR if phase == "predecessor" else CURRENT
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    assert revision == expected
    sys.path[:0] = [str(source), str(source / "bridge")]
    os.chdir(source)
    from fastapi.testclient import TestClient
    from adminme.events.registry import registry
    from adminme.runtime import application
    from tests.acceptance.test_openclaw_native_seam import _settings, DEMO_HEADERS
    import asyncio

    assert Path(application.__file__).resolve().is_relative_to(source)
    assert len(key.read_bytes()) == 32
    settings = replace(_settings(state), encryption_key=key.read_bytes())
    result = {
        "phase": phase,
        "source": revision,
        "state": "incomplete",
        "provider_writes": 0,
        "metadata_enablement": False,
        "scope": "synthetic runtime/state compatibility, not updater or live-device qualification",
    }

    async def inventory(runtime):
        rows = [row async for row in runtime.event_log.read_since()]
        return {
            "events": {row["event_id"]: digest(row) for row in rows},
            "metadata": {
                row["event_id"]: {"type": row["type"], "hash": digest(row)}
                for row in rows
                if row["type"] in KINDS
            },
            "authority_events": {
                row["event_id"]: digest(row)
                for row in rows
                if row["type"].startswith(("settings.", "service.delegation."))
            },
            "member_roles": dict(runtime.settings.member_roles),
            "member_identities": dict(runtime.settings.member_identities),
        }

    async def consumers(runtime):
        values = {}
        for name, subscriber in runtime.bus._subscribers.items():
            status = await runtime.bus.subscriber_status(name)
            values[name] = {
                "lag": status["lag_count"],
                "draining": subscriber.draining,
                "triggered": subscriber.trigger.is_set(),
                "degraded": subscriber.degraded,
                "poison": subscriber.poison_event_id,
            }
        return values

    def settle(client):
        end = time.monotonic() + 10
        while True:
            response = client.get("/api/system/ready")
            body = response.json()
            freshness = body["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]
            states = client.portal.call(consumers, client.app.state.runtime)
            assert states, "consumer set is empty"
            assert freshness["refresh_error_type"] is None, body
            settled = all(
                v
                == {
                    "lag": 0,
                    "draining": False,
                    "triggered": False,
                    "degraded": False,
                    "poison": None,
                }
                for v in states.values()
            )
            if (
                freshness["revision_current"]
                and not freshness["refresh_in_progress"]
                and settled
            ):
                assert (
                    response.status_code == 200
                    and body["service_ready"] is True
                    and body["event_ingest_ready"] is True
                ), body
                assert (
                    body["capabilities"]["policy:source-assurance"]["state"]
                    == "healthy"
                ), body
                live = client.get("/api/system/live")
                assert live.status_code == 200
                return {
                    "ready_status": response.status_code,
                    "service_ready": body["service_ready"],
                    "event_ingest_ready": body["event_ingest_ready"],
                    "source_assurance": body["capabilities"]["policy:source-assurance"][
                        "state"
                    ],
                    "consumers": states,
                }
            assert time.monotonic() < end, {"freshness": freshness, "consumers": states}
            client.portal.call(asyncio.sleep, 0.01)

    def projection_digest(connection):
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_%' ESCAPE '\\'"
            )
        ]
        values = {}
        for table in sorted(tables):
            quoted = '"' + table.replace('"', '""') + '"'
            rows = [
                tuple(row)
                for row in connection.execute("SELECT * FROM " + quoted).fetchall()
            ]
            values[table] = {
                "rows": len(rows),
                "sha256": digest(sorted(rows, key=repr)),
            }
        return values

    def assert_preserved(before, after):
        assert all(
            after["events"].get(key) == value for key, value in before["events"].items()
        ), "event loss or mutation"
        for field in (
            "metadata",
            "authority_events",
            "member_roles",
            "member_identities",
        ):
            assert before[field] == after[field], field

    try:
        with TestClient(application.create_application(settings)) as client:
            runtime = client.app.state.runtime
            result["startup"] = settle(client)
            assert all(
                (registry.get(kind, 1) is None) == (phase == "predecessor")
                for kind in KINDS
            )
            if phase == "seed":
                initial = client.get(
                    "/api/member/v1/settings/finance-metadata", headers=DEMO_HEADERS
                ).json()
                assert (
                    initial["revision"] == 0
                    and not initial["owner_read_enabled"]
                    and not initial["cos_read_enabled"]
                )
                changed = client.put(
                    "/api/member/v1/settings/finance-metadata",
                    headers=DEMO_HEADERS,
                    json={
                        "request_id": "synthetic-off-recovery-proof",
                        "expected_revision": 0,
                        "owner_read_enabled": False,
                        "cos_read_enabled": False,
                    },
                )
                assert changed.status_code == 200 and changed.json()["revision"] == 1
                denied = client.post(
                    "/api/member/v1/finance/bindings", headers=DEMO_HEADERS, json={}
                )
                assert (
                    denied.status_code == 200
                    and denied.json()["state"] == "access_unavailable"
                    and denied.json()["bindings"] == []
                )
                result["after_events"] = settle(client)
                result["inventory"] = client.portal.call(inventory, runtime)
                assert sorted(
                    item["type"] for item in result["inventory"]["metadata"].values()
                ) == sorted(KINDS)
            else:
                original = json.loads(seed.read_text())["inventory"]
                before = client.portal.call(inventory, runtime)
                assert_preserved(original, before)
                if phase == "predecessor":
                    names = runtime.projections.projection_names()
                    assert names, "projection set is empty"
                    before_projections = {
                        name: client.portal.call(
                            runtime.projections.read, name, projection_digest
                        )
                        for name in names
                    }
                    for name in names:
                        client.portal.call(runtime.projections.rebuild, name)
                    result["after_replay"] = settle(client)
                    after_projections = {
                        name: client.portal.call(
                            runtime.projections.read, name, projection_digest
                        )
                        for name in names
                    }
                    assert before_projections == after_projections, (
                        "projection replay changed state"
                    )
                    result["replayed_projections"] = names
                    result["projection_digest"] = digest(after_projections)
                else:
                    control = client.get(
                        "/api/member/v1/settings/finance-metadata", headers=DEMO_HEADERS
                    )
                    assert control.status_code == 200
                    value = control.json()
                    assert (
                        value["revision"] == 1
                        and not value["owner_read_enabled"]
                        and not value["cos_read_enabled"]
                    )
                    result["metadata_control"] = value
                result["inventory"] = client.portal.call(inventory, runtime)
                assert_preserved(original, result["inventory"])
        result["clean_shutdown"] = True
        if phase == "predecessor":
            with TestClient(application.create_application(settings)) as client:
                result["second_startup"] = settle(client)
                assert_preserved(
                    original, client.portal.call(inventory, client.app.state.runtime)
                )
            result["second_clean_shutdown"] = True
        result["state"] = "passed"
    finally:
        report.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
