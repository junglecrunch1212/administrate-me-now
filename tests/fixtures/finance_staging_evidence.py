"""Nonsecret, bounded failure receipts for the synthetic staging cases only."""

import asyncio
from builtins import ExceptionGroup
import json


GUARD_REASONS = frozenset(
    {"synthetic_approval_expired", "synthetic_durable_authority_changed"}
)
STAGES = frozenset(
    {
        "prepare_before_upload",
        "prepare_after_upload",
        "prepare_before_seal",
        "commit_guard",
        "executor",
        "case",
        "durable_readback",
        "runtime_shutdown",
        "controller_receive",
        "controller_result",
        "controller_cleanup",
        "controller_receipt",
    }
)
EXCEPTION_CODES = {
    AssertionError: "synthetic_assertion_failed",
    ValueError: "synthetic_ValueError",
    TimeoutError: "synthetic_timeout",
    OSError: "synthetic_os_error",
    asyncio.CancelledError: "synthetic_cancelled",
}
REASONS = (
    GUARD_REASONS
    | frozenset(EXCEPTION_CODES.values())
    | {"synthetic_unclassified_error"}
)
AUDIT_REASONS = REASONS | {
    "finance_import_request_cancelled",
    "finance_import_authorization_changed",
    "exact_approved_package",
}
TABLES = (
    "finance_provenance_blobs",
    "finance_publications",
    "finance_dated_projections",
    "money_flows",
)


class GuardRejection(ValueError):
    def __init__(self, reason, stage):
        self.reason = (
            reason
            if type(reason) is str and reason in GUARD_REASONS
            else "synthetic_unclassified_error"
        )
        self.stage = stage if type(stage) is str and stage in STAGES else "executor"
        super().__init__(self.reason)


def failure(exc, stage):
    # Never stringify an exception, payload or arbitrary class name. Even a
    # ValueError containing an allowlisted string is not a typed guard result.
    if type(exc) is GuardRejection:
        return wire_failure({"reason": exc.reason, "stage": exc.stage})
    return {
        "reason": EXCEPTION_CODES.get(type(exc), "synthetic_unclassified_error"),
        "stage": stage if stage in STAGES else "executor",
    }


def wire_failure(message):
    return {
        "reason": message.get("reason")
        if type(message.get("reason")) is str and message["reason"] in REASONS
        else "synthetic_unclassified_error",
        "stage": message.get("stage")
        if type(message.get("stage")) is str and message["stage"] in STAGES
        else "executor",
    }


def durable_receipt(state):
    counts = {table: state["counts"][table] for table in TABLES}
    assert all(type(value) is int and value >= 0 for value in counts.values())
    assert len(state["audit"]) <= 32
    audits = []
    for row in state["audit"]:
        payload = row["payload"]
        outcome, reason = payload.get("outcome"), payload.get("reason")
        audits.append(
            {
                "outcome": outcome
                if type(outcome) is str
                and outcome in {"accepted", "rejected", "incomplete"}
                else "unknown",
                "reason": reason
                if type(reason) is str and reason in AUDIT_REASONS
                else "unclassified",
                "provider_effects_false": payload.get("provider_effects") is False,
            }
        )
    return {
        "state": "read",
        "counts": counts,
        "finance_event_count": len(state["finance_events"]),
        "audit": audits,
        "money_checkpoint_verified": True,  # inspect checked durable equality.
    }


def require_expiry(response, errors, durable):
    # This is the API's existing pre-ingress PublicationError contract, not a
    # role-change HTTP assertion. A generic rejection cannot satisfy this case.
    assert response == {
        "status": 422,
        "state": "rejected",
        "reason": "synthetic_approval_expired",
    }
    assert errors == [
        {"reason": "synthetic_approval_expired", "stage": "prepare_after_upload"}
    ]
    assert durable["state"] == "read" and durable["money_checkpoint_verified"]
    assert durable["finance_event_count"] == 0 and all(
        value == 0 for value in durable["counts"].values()
    )
    assert durable["audit"] == [
        {
            "outcome": "rejected",
            "reason": "synthetic_approval_expired",
            "provider_effects_false": True,
        }
    ]


def collect_owned_case(run):
    """Keep primary and cleanup failures independently; both remain failures."""
    failures = []
    stage = "controller_receive"
    try:
        result = run.receive()
        run.record["result"] = result
        stage = "controller_result"
        assert result["state"] == "passed"
    except BaseException as exc:
        failures.append(failure(exc, stage))
    try:
        run.close()
    except BaseException as exc:
        failures.append(failure(exc, "controller_cleanup"))
    run.record["failures"] = failures
    run.record["state"] = "failed" if failures else "correctness_passed"
    try:
        (run.output / (run.name + ".json")).write_text(
            json.dumps(run.record, indent=2) + "\n"
        )
    except BaseException as exc:
        failures.append(failure(exc, "controller_receipt"))
    if failures:
        # Do not rethrow exception text or allow cleanup to replace the primary
        # failure. The retained record and safe group preserve their stages.
        raise ExceptionGroup(
            "synthetic staged case failed",
            [RuntimeError(item["stage"] + ":" + item["reason"]) for item in failures],
        ) from None
