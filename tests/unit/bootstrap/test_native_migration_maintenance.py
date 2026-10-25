"""Exercise native config classification through the real maintenance checks.

The unit boundary supplies native output; the separate hosted pinned-source
fixture must prove that output and database/session/cron behavior for real.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from adminme.openclaw_roster import agent_rows
from bootstrap import openclaw_migration
from bootstrap.contracts import atomic_write
from bootstrap.cos_channel_activation import CoSActivationError
from bootstrap.openclaw_activation import reconcile_service_topology
from tests.unit.bootstrap.test_cos_channel_activation import (
    ROOT,
    _maintenance_controller,
    _prepare_maintenance,
    _write,
)


@pytest.mark.parametrize(
    "mutation",
    [None, "new_tool", "new_recipient", "unrelated_field", "changed_receipt"],
)
def test_native_transition_restores_only_exact_original_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str | None
) -> None:
    controller, request = _maintenance_controller(tmp_path, monkeypatch)
    paths = controller.paths
    config_path = paths.instance_dir / "openclaw/runtime/openclaw.json"
    source = json.loads(config_path.read_text())
    rows = agent_rows(source)
    owner = rows[0]["id"]
    source["agents"] = {
        "list": [{**row, "default": row["id"] == owner} for row in rows]
    }
    _write(config_path, source)
    plan = _prepare_maintenance(controller, request)
    controller.pause_maintenance(plan)
    paused = json.loads(config_path.read_text())
    (paths.app_dir / "activation").mkdir(mode=0o700, exist_ok=True)
    journal: dict[str, Any] = {
        "transaction_id": "9" * 32,
        "predecessor_commit": "a" * 40,
        "target_commit": "b" * 40,
        "predecessor_path": str(ROOT),
        "target_path": str(ROOT),
    }
    receipt = openclaw_migration.capture(paths, journal)
    journal["openclaw_migration"] = receipt
    selected = copy.deepcopy(paused)
    selected["agents"] = {
        "ownership": "explicit",
        "entries": {
            row["id"]: {
                key: value for key, value in row.items() if key not in {"id", "default"}
            }
            for row in rows
        },
        "defaults": {"systemAgent": {"agentId": owner}},
    }
    selected["talk"] = {"agentId": owner}
    selected.setdefault("bindings", []).append(
        {"agentId": owner, "match": {"channel": "imessage", "accountId": "*"}}
    )
    atomic_write(config_path, json.dumps(selected).encode())
    doctor_payload = b'{"status":"ok"}'
    atomic_write(
        openclaw_migration._directory(paths, journal) / "doctor-result.json",
        doctor_payload,
    )  # noqa: SLF001
    receipt.update(
        state="doctor_settled",
        doctor={
            "settled": True,
            "result_path": "/tmp/openclaw/openclaw-update-doctor-1-00000000-0000-4000-8000-000000000001.json",
            "nonce": "00000000-0000-4000-8000-000000000002",
            "pid": 12345,
            "returncode": 0,
            "classification": "ok",
            "result_sha256": hashlib.sha256(doctor_payload).hexdigest(),
        },
    )
    receipt["configuration_sha256"] = openclaw_migration.record_configuration(
        paths, ROOT, journal
    )
    reconcile_service_topology(paths, ROOT)
    # Existing test setup stubs only the updater's completed-history boundary.
    from bootstrap.activation_transaction import ActivationJournal

    prior = ActivationJournal.committed_activation(
        controller, target_commit="b" * 40, predecessor_commit="a" * 40
    )
    deployment = {**prior, **journal}
    monkeypatch.setattr(
        ActivationJournal, "committed_activation", lambda *_args, **_kwargs: deployment
    )
    monkeypatch.setattr(controller, "_selection", lambda: ("b" * 40, ROOT))
    gateway = json.loads(config_path.read_text())
    if mutation == "new_tool":
        gateway["agents"]["entries"][owner]["tools"]["allow"].append(
            "unrequested-raw-write"
        )
    elif mutation == "new_recipient":
        account = next(iter(gateway["channels"]["imessage"]["accounts"].values()))
        account["allowFrom"].append("unrequested-recipient")
    elif mutation == "unrelated_field":
        gateway["models"]["retained"] = "changed"
    elif mutation == "changed_receipt":
        receipt["configuration_sha256"] = "f" * 64
    _write(config_path, gateway)
    if mutation is not None:
        with pytest.raises(
            (CoSActivationError, openclaw_migration.NativeMigrationError, ValueError)
        ):
            controller.activate(plan, enable_live=True)
        return
    result = controller.activate(plan, enable_live=True)
    assert result["observation_active"] is False
    assert result["provider_test_messages_sent"] == 0
    restored = json.loads(config_path.read_text())
    assert restored["agents"]["defaults"]["systemAgent"]["agentId"] == owner
    assert {
        "agentId": owner,
        "match": {"channel": "imessage", "accountId": "*"},
    } in restored["bindings"]
    plugin = restored["plugins"]["entries"]["administrateme"]["config"]
    assert plugin["standingSelfReplyBindings"] == plan.self_reply_bindings
    assert plugin["scheduledSelfReminderBindings"] == plan.self_reminder_bindings
