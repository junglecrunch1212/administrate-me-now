"""Bounded, read-only service settlement before deployment or restore proof."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from bootstrap.services import (
    ServiceManager,
    isolated_degraded_adapter_ids,
    source_assurance_terminal_evidence,
)


_TRANSIENT_POST_SWITCH_CHECKS = frozenset(
    {
        "fifteen_projections_and_checkpoints",
        "openclaw_compatibility_and_plugin_discovery",
        "openclaw_cron_reconciliation",
        "launchd_and_process_state",
        "ports_and_loopback_binding",
        "source_assurance_terminal_dispositions",
    }
)
_SOURCE_SETTLEMENT_DERIVED_DEGRADATIONS = frozenset(
    {
        "policy:source-assurance",
        "product:member",
        "surface:triage-processing",
    }
)
_OPENCLAW_HEARTBEAT_TRANSIENT_CAPABILITY_KINDS = frozenset(
    {
        "openclaw_standing_order",
        "openclaw_chat_transport",
    }
)
_TRANSIENT_DEPLOYMENT_READINESS_DIMENSIONS = frozenset(
    {
        # The source-assurance projection settles after the candidate starts.
        "observation_effects",
        # The pinned gateway reports its topology only after the runtime is live.
        "openclaw",
    }
)
# A full reviewed language-bound replay can traverse more than one thousand
# immutable message facts on a household instance.  Keep this bounded, but do
# not make a healthy, advancing replay race an unrealistically short rollback
# clock.
POST_SWITCH_REPLAY_SETTLEMENT_SECONDS = 60 * 60
POST_SWITCH_POLL_SECONDS = 2
# A live runtime can briefly make the full readiness document unavailable
# while the source-settlement audit contends for the encrypted event log. Each
# miss has already consumed the bounded diagnostics timeout. Retry only while
# the cheap liveness proof succeeds and Doctor reports no non-settlement gap;
# an unresolved blackout still fails closed after this small fixed allowance.
POST_SWITCH_LIVE_READINESS_RETRIES = 3


def failed_check_names(result: dict[str, Any]) -> set[str]:
    return {
        str(check.get("name"))
        for check in result.get("checks", [])
        if isinstance(check, dict) and not check.get("ok")
    }


def doctor_has_only_transient_deployment_readiness_gap(
    result: dict[str, Any],
) -> bool:
    """Recognize only bounded readiness dimensions that settle after startup."""

    for check in result.get("checks", []):
        if not isinstance(check, dict) or check.get("name") != (
            "deployment_topology_and_recovery_readiness"
        ):
            continue
        evidence = check.get("evidence")
        if not isinstance(evidence, dict):
            return False
        blocking = evidence.get("blocking_dimensions")
        return (
            evidence.get("contract") == "deployment-readiness-v1"
            and evidence.get("state") == "blocked"
            and isinstance(blocking, list)
            and bool(blocking)
            and all(isinstance(value, str) for value in blocking)
            and set(blocking).issubset(_TRANSIENT_DEPLOYMENT_READINESS_DIMENSIONS)
        )
    return False


def failed_checks_are_known_transients(result: dict[str, Any]) -> bool:
    failed = failed_check_names(result)
    if not failed:
        return False
    allowed = set(_TRANSIENT_POST_SWITCH_CHECKS)
    if doctor_has_only_transient_deployment_readiness_gap(result):
        allowed.add("deployment_topology_and_recovery_readiness")
    return failed.issubset(allowed)


def runtime_has_only_transient_deployment_readiness_gap(
    diagnostics: dict[str, Any],
) -> bool:
    readiness = diagnostics.get("deployment_readiness")
    if not isinstance(readiness, dict):
        return False
    blocking = readiness.get("blocking_dimensions")
    return (
        diagnostics.get("service_ready") is True
        and diagnostics.get("safe_reads") is True
        and diagnostics.get("event_ingest_ready") is True
        and readiness.get("contract") == "deployment-readiness-v1"
        and readiness.get("state") == "blocked"
        and isinstance(blocking, list)
        and bool(blocking)
        and all(isinstance(value, str) for value in blocking)
        and set(blocking).issubset(_TRANSIENT_DEPLOYMENT_READINESS_DIMENSIONS)
    )


def has_new_structural_degradation(
    diagnostics: dict[str, Any],
    *,
    allowed_degraded_adapter_ids: frozenset[str] = frozenset(),
) -> bool:
    """Reject a runtime capability failure that emerges during replay."""

    adapter_boundary = isolated_degraded_adapter_ids(diagnostics)
    if adapter_boundary and adapter_boundary.issubset(allowed_degraded_adapter_ids):
        return False

    if diagnostics.get(
        "ready"
    ) is not True and not runtime_has_only_transient_deployment_readiness_gap(
        diagnostics
    ):
        return True
    capabilities = diagnostics.get("capabilities")
    if not isinstance(capabilities, dict):
        return True
    readiness = diagnostics.get("deployment_readiness")
    blocking = (
        set(readiness.get("blocking_dimensions") or [])
        if isinstance(readiness, dict)
        else set()
    )
    openclaw_heartbeat_pending = "openclaw" in blocking
    for capability_id, capability in capabilities.items():
        if capability_id in _SOURCE_SETTLEMENT_DERIVED_DEGRADATIONS:
            continue
        if not isinstance(capability, dict):
            continue
        state = capability.get("state")
        if state not in {"degraded", "blocked"}:
            continue
        if (
            openclaw_heartbeat_pending
            and state == "degraded"
            and capability.get("kind") in _OPENCLAW_HEARTBEAT_TRANSIENT_CAPABILITY_KINDS
        ):
            continue
        return True
    return False


def wait_for_settled_doctor(
    *,
    run_doctor: Callable[..., dict[str, Any]],
    services: ServiceManager,
    test_mode: bool,
    timeout_seconds: float = POST_SWITCH_REPLAY_SETTLEMENT_SECONDS,
    poll_seconds: float = POST_SWITCH_POLL_SECONDS,
    live_readiness_retry_limit: int = POST_SWITCH_LIVE_READINESS_RETRIES,
    allowed_degraded_adapter_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Share the upgrade/restore proof without invoking either controller.

    The caller supplies the appropriate release Doctor. Only known launch and
    source-settlement transients may wait; every structural failure fails closed.
    The complete proof runs after readiness, within one fixed settlement budget.
    """

    # Historical source replay may walk tens of thousands of encrypted
    # facts before its candidate-bearing source prefix. Repeating full
    # Doctor during that work competes for the same log and proves nothing
    # new. Poll only the content-free readiness document until source
    # settlement, then rerun the complete proof. Structural failures still
    # return on their first observation.
    deadline = time.monotonic() + (
        0
        if test_mode
        else min(max(timeout_seconds, 0), POST_SWITCH_REPLAY_SETTLEMENT_SECONDS)
    )
    live_readiness_retries = 0
    last = run_doctor(include_synthetic_proof=False)
    while True:
        if last.get("status") == "healthy":
            last = run_doctor(include_synthetic_proof=True)
            if last.get("status") == "healthy":
                return last
        failed = failed_check_names(last)
        if not failed_checks_are_known_transients(last):
            return last
        if test_mode:
            return last

        diagnostics = services.runtime_diagnostics()
        if diagnostics is None:
            # Readiness is the only cheap proof that an otherwise hidden
            # capability has not degraded during an allowed wait.  Before
            # the runtime is live, launch/port failures may still be a
            # genuine cold-start transient.  Once it is live—or for every
            # other transient class—missing readiness fails closed.
            cold_start_only = failed.issubset(
                {"launchd_and_process_state", "ports_and_loopback_binding"}
            )
            deployment_check_failed = (
                "deployment_topology_and_recovery_readiness" in failed
            )
            source_settlement_only = (
                "source_assurance_terminal_dispositions" in failed
                and (
                    not deployment_check_failed
                    or doctor_has_only_transient_deployment_readiness_gap(last)
                )
            )
            if (
                cold_start_only
                and not services.runtime_is_live()
                and time.monotonic() < deadline
            ):
                time.sleep(poll_seconds)
                last = run_doctor(include_synthetic_proof=False)
                continue
            if (
                source_settlement_only
                and live_readiness_retries < live_readiness_retry_limit
                and services.runtime_is_live()
                and time.monotonic() < deadline
            ):
                live_readiness_retries += 1
                time.sleep(poll_seconds)
                last = run_doctor(include_synthetic_proof=False)
                continue
            return last
        live_readiness_retries = 0
        if has_new_structural_degradation(
            diagnostics,
            allowed_degraded_adapter_ids=allowed_degraded_adapter_ids,
        ):
            return run_doctor(include_synthetic_proof=False)

        if "source_assurance_terminal_dispositions" in failed:
            settled, _evidence = source_assurance_terminal_evidence(diagnostics)
            if not settled and time.monotonic() < deadline:
                time.sleep(poll_seconds)
                continue

        if time.monotonic() >= deadline:
            final = run_doctor(include_synthetic_proof=False)
            if final.get("status") == "healthy":
                return run_doctor(include_synthetic_proof=True)
            return final

        time.sleep(poll_seconds)
        last = run_doctor(include_synthetic_proof=False)
