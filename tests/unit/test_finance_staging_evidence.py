"""Focused synthetic failure-reporting regression; no provider fixtures."""

import asyncio
from builtins import ExceptionGroup
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from tests.fixtures.finance_staging_evidence import (
    GuardRejection,
    TABLES,
    collect_owned_case,
    durable_receipt,
    failure,
    require_expiry,
    wire_failure,
)


class PrivateExceptionName(Exception):
    pass


@pytest.mark.parametrize(
    "exc,reason,stage",
    [
        (
            GuardRejection("synthetic_approval_expired", "prepare_after_upload"),
            "synthetic_approval_expired",
            "prepare_after_upload",
        ),
        (
            GuardRejection(
                "synthetic_durable_authority_changed", "prepare_before_upload"
            ),
            "synthetic_durable_authority_changed",
            "prepare_before_upload",
        ),
        (
            ValueError("synthetic_approval_expired private-payload"),
            "synthetic_ValueError",
            "executor",
        ),
        (
            PrivateExceptionName("private-payload"),
            "synthetic_unclassified_error",
            "executor",
        ),
    ],
)
def test_only_typed_allowlisted_guard_reason_and_stage_survive(exc, reason, stage):
    assert failure(exc, "executor") == {"reason": reason, "stage": stage}


def raw_state():
    return {
        "counts": dict.fromkeys(TABLES, 0),
        "finance_events": [],
        "audit": [
            {
                "event_id": "private-id",
                "payload": {
                    "outcome": "rejected",
                    "reason": "synthetic_approval_expired",
                    "provider_effects": False,
                    "owner_id": "private-owner",
                    "publication": "private-payload",
                    "key": "private-key",
                },
            }
        ],
    }


def test_wire_and_durable_allowlists_drop_unknown_codes_and_private_values():
    assert wire_failure(
        {"reason": "private-payload", "stage": "private-path", "key": "private-key"}
    ) == {
        "reason": "synthetic_unclassified_error",
        "stage": "executor",
    }
    assert failure(GuardRejection("private-payload", "private-path"), "executor") == {
        "reason": "synthetic_unclassified_error",
        "stage": "executor",
    }
    state = raw_state()
    report = durable_receipt(state)
    assert "private" not in json.dumps(report)
    assert report["counts"] == dict.fromkeys(TABLES, 0)
    state["audit"][0]["payload"].update(
        reason="private-payload", outcome="private-outcome"
    )
    assert durable_receipt(state)["audit"] == [
        {"outcome": "unknown", "reason": "unclassified", "provider_effects_false": True}
    ]
    state["counts"][TABLES[0]] = "private-payload"
    with pytest.raises(AssertionError):
        durable_receipt(state)


@pytest.mark.parametrize(
    "changed", [None, "status", "reason", "stage", "events", "publication", "audit"]
)
def test_expiry_requires_exact_guard_response_and_durable_zero_state(changed):
    response = {
        "status": 422,
        "state": "rejected",
        "reason": "synthetic_approval_expired",
    }
    errors = [{"reason": "synthetic_approval_expired", "stage": "prepare_after_upload"}]
    durable = durable_receipt(raw_state())
    if changed == "status":
        response["status"] = 403
    elif changed == "reason":
        response["reason"] = "synthetic_ValueError"
    elif changed == "stage":
        errors[0]["stage"] = "prepare_before_upload"
    elif changed == "events":
        durable["finance_event_count"] = 1
    elif changed == "publication":
        durable["counts"]["finance_publications"] = 1
    elif changed == "audit":
        durable["audit"][0]["outcome"] = "accepted"
    if changed is None:
        require_expiry(response, errors, durable)
    else:
        with pytest.raises(AssertionError):
            require_expiry(response, errors, durable)


@pytest.mark.parametrize(
    "primary,cleanup_error",
    [("receive", True), ("failed", True), (None, True), (None, False)],
)
def test_controller_retains_primary_and_cleanup_failures(
    tmp_path, primary, cleanup_error
):
    class Run:
        name, output = "receipt", tmp_path
        record = {}

        def receive(self):
            if primary == "receive":
                raise ValueError("private-payload")
            return {
                "state": "failed" if primary == "failed" else "passed",
                "final_durable_state": durable_receipt(raw_state()),
            }

        def close(self):
            self.record["cleanup"] = {"forced": cleanup_error}
            if cleanup_error:
                raise OSError("private-cleanup-text")

    run = Run()
    if primary or cleanup_error:
        with pytest.raises(ExceptionGroup) as caught:
            collect_owned_case(run)
        assert "private" not in str(caught.value.exceptions)
    else:
        collect_owned_case(run)
    saved = json.loads((tmp_path / "receipt.json").read_text())
    assert saved["state"] == (
        "failed" if primary or cleanup_error else "correctness_passed"
    )
    assert [item["stage"] for item in saved["failures"]] == (
        (
            ["controller_receive" if primary == "receive" else "controller_result"]
            if primary
            else []
        )
        + (["controller_cleanup"] if cleanup_error else [])
    )
    assert saved["cleanup"]["forced"] is cleanup_error
    if primary != "receive":
        assert saved["result"]["final_durable_state"]["state"] == "read"
    assert "private" not in json.dumps(saved)


@pytest.mark.parametrize("failed_stage", [None, "durable_readback", "runtime_shutdown"])
def test_failed_case_attempts_final_readback_and_preserves_shutdown_failure(
    tmp_path, monkeypatch, failed_stage
):
    from tests.fixtures import finance_staging_cases as cases

    calls = []
    store = cases.RecordedStorage.__new__(cases.RecordedStorage)
    store.__dict__.update(
        root=tmp_path,
        imports=0,
        executor_errors=[],
        receipt_failures=[],
        final_durable_state={"state": "unavailable"},
        runtime_shutdown_completed=False,
        response=None,
        staging_source="test-digest",
        process=None,
        quarantined=False,
        stage_descriptor=None,
        lifetimes=[],
        stage_lifetimes=[],
    )

    async def close():
        calls.append("close")

    async def inspect(*_args):
        calls.append("read")
        if failed_stage == "durable_readback":
            raise ValueError("private-read-error")
        return deepcopy(raw_state())

    async def stop():
        calls.append("stop")
        if failed_stage == "runtime_shutdown":
            raise OSError("private-stop-error")

    runtime = SimpleNamespace(
        stop=stop,
        settings_command_locks={"finance-closed-package-import": asyncio.Lock()},
    )
    monkeypatch.setattr(
        cases.StagedStorage,
        "install",
        lambda self, *_args: setattr(self, "runtime", runtime),
    )
    monkeypatch.setattr(store, "close", close)
    monkeypatch.setattr(cases.cases, "inspect", inspect)
    monkeypatch.setattr(cases.RecordedStorage, "instances", [store])
    store.install(None, monkeypatch)

    def failed_case(*_args):
        try:
            raise AssertionError("private-original-error")
        finally:
            asyncio.run(runtime.stop())

    monkeypatch.setattr(cases, "small_staged_case", failed_case)
    report = cases.recorded_case("expiry_upload", tmp_path)
    assert calls == ["close", "read", "stop"]
    assert report["state"] == "failed"
    assert [item["stage"] for item in report["failures"]] == ["case"] + (
        [failed_stage] if failed_stage else []
    )
    assert report["final_durable_state"]["state"] == (
        "unavailable" if failed_stage == "durable_readback" else "read"
    )
    assert report["cleanup"]["runtime_shutdown_completed"] is (
        failed_stage != "runtime_shutdown"
    )
    assert report["cleanup"]["executors_reaped"] is False
    assert report["cleanup"]["stages_reaped_removed"] is False
    assert "private" not in json.dumps(report)
