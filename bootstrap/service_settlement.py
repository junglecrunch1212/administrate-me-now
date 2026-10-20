"""Bounded, read-only service settlement before deployment or restore proof."""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bootstrap.contracts import DeploymentPaths
from bootstrap.deployment_budget import deployment_clock
from bootstrap.deployment_failure import settlement_diagnostic_evidence
from bootstrap.doctor import Doctor
from bootstrap.services import (
    RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS,
    ServiceManager,
    isolated_degraded_adapter_ids,
    source_assurance_terminal_evidence,
)
from bootstrap.wizard import service_environment_path

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
# Fixed reviewed ceilings, not an estimate of any household's remaining work.
# Advancing checkpoints can satisfy the watchdog, never extend the deadline.
POST_SWITCH_REPLAY_SETTLEMENT_SECONDS = 6 * 60 * 60
POST_SWITCH_CONSUMER_PROGRESS_SECONDS = 10 * 60
POST_SWITCH_POLL_SECONDS = 2
RELEASE_DOCTOR_TIMEOUT_SECONDS = 120
_RUNTIME_LIVENESS_TIMEOUT_SECONDS = 5
_REPLAY_CONSUMER_KINDS = frozenset({"projection", "reactive_pipeline"})
# A live runtime can briefly make the full readiness document unavailable
# while the source-settlement audit contends for the encrypted event log. Each
# miss has already consumed the bounded diagnostics timeout. Retry only while
# the cheap liveness proof succeeds and Doctor reports no non-settlement gap;
# an unresolved blackout still fails closed after this small fixed allowance.
POST_SWITCH_LIVE_READINESS_RETRIES = 3


class _SettlementStopped(RuntimeError):
    def __init__(
        self, reason: str, *, consumer_id: str | None = None, phase: str | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.consumer_id = consumer_id
        self.phase = phase


@dataclass
class _ConsumerProgress:
    kind: str
    version: int | str
    checkpoint: str | None
    last_progress: float
    lag: int


class _ReplayProgress:
    """Freeze each observed lagging consumer's identity for this settlement."""

    def __init__(self) -> None:
        self.consumers: dict[str, _ConsumerProgress] = {}

    def check_stalls(self, now: float) -> None:
        for consumer_id, consumer in self.consumers.items():
            if (
                consumer.lag > 0
                and now - consumer.last_progress
                >= POST_SWITCH_CONSUMER_PROGRESS_SECONDS
            ):
                raise _SettlementStopped("consumer_stalled", consumer_id=consumer_id)

    def next_deadline(self, deadline: float) -> float:
        return min(
            [deadline]
            + [
                consumer.last_progress + POST_SWITCH_CONSUMER_PROGRESS_SECONDS
                for consumer in self.consumers.values()
                if consumer.lag > 0
            ]
        )

    def observe(self, diagnostics: dict[str, Any], now: float) -> None:
        capabilities = diagnostics["capabilities"]
        # A disappearance, renamed consumer or changed version cannot create a
        # fresh progress timer. Keep the original identity even after lag is zero.
        for consumer_id, consumer in self.consumers.items():
            capability = capabilities.get(consumer_id)
            if (
                not isinstance(capability, dict)
                or capability.get("kind") != consumer.kind
            ):
                raise _SettlementStopped(
                    "consumer_identity_changed", consumer_id=consumer_id
                )

        for consumer_id, capability in capabilities.items():
            if not isinstance(capability, dict):
                continue
            kind = capability.get("kind")
            if kind not in _REPLAY_CONSUMER_KINDS:
                continue
            details = capability.get("details")
            prior = self.consumers.get(consumer_id)
            if not isinstance(details, dict):
                if prior is not None:
                    raise _SettlementStopped(
                        "consumer_evidence_invalid", consumer_id=consumer_id
                    )
                continue
            failures = details.get("consecutive_failures", 0)
            if details.get("poison_event_id") or (
                isinstance(failures, int) and failures > 0
            ):
                raise _SettlementStopped("consumer_failed", consumer_id=consumer_id)
            lag = details.get("lag_count")
            if prior is None and (type(lag) is not int or lag <= 0):
                continue
            version = details.get("version")
            checkpoint = details.get("checkpoint_event_id")
            if (
                type(lag) is not int
                or lag < 0
                or not (
                    (type(version) is int and version > 0)
                    or (isinstance(version, str) and bool(version.strip()))
                )
                or not (
                    checkpoint is None
                    or (isinstance(checkpoint, str) and bool(checkpoint.strip()))
                )
            ):
                raise _SettlementStopped(
                    "consumer_evidence_invalid", consumer_id=consumer_id
                )
            if prior is None:
                self.consumers[consumer_id] = _ConsumerProgress(
                    kind=kind,
                    version=version,
                    checkpoint=checkpoint,
                    last_progress=now,
                    lag=lag,
                )
                continue
            if type(version) is not type(prior.version) or version != prior.version:
                raise _SettlementStopped(
                    "consumer_identity_changed", consumer_id=consumer_id
                )
            if prior.checkpoint is not None and (
                checkpoint is None or checkpoint < prior.checkpoint
            ):
                raise _SettlementStopped(
                    "consumer_checkpoint_regressed", consumer_id=consumer_id
                )
            if checkpoint is not None and (
                prior.checkpoint is None or checkpoint > prior.checkpoint
            ):
                prior.checkpoint = checkpoint
                prior.last_progress = now
            elif prior.lag == 0 and lag > 0:
                # An observed empty backlog ends the pending episode. New
                # derived work gets its own timer, retaining the same frozen
                # identity/version and the unchanged operation deadline.
                prior.last_progress = now
            # Lag, last_success_at and source-assurance counts are deliberately
            # absent from the condition that refreshes last_progress.
            prior.lag = lag


def _stopped_report(
    last: dict[str, Any],
    stopped: _SettlementStopped,
    *,
    triggering_readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "contract": "bounded-service-settlement-v1",
        "reason": stopped.reason,
    }
    if stopped.consumer_id is not None:
        evidence["consumer_id"] = stopped.consumer_id
    if stopped.phase is not None:
        evidence["phase"] = stopped.phase
    if triggering_readiness is not None:
        evidence["triggering_readiness"] = triggering_readiness
    return {
        **last,
        "status": "failed",
        "checks": [
            *last.get("checks", []),
            {"name": "bounded_service_settlement", "ok": False, "evidence": evidence},
        ],
    }


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


def _doctor_has_absent_readiness(result: dict[str, Any]) -> bool:
    """Recognize the exact evidence produced when Doctor receives no readiness.

    This permits a bounded fresh observation, never an inferred passing check.
    Present but unrecognized contracts and independent failures remain terminal.
    """

    checks = result.get("checks")
    if not isinstance(checks, list) or not checks:
        return False
    by_name: dict[str, dict[str, Any]] = {}
    for check in checks:
        if (
            not isinstance(check, dict)
            or not isinstance(check.get("name"), str)
            or type(check.get("ok")) is not bool
            or check["name"] in by_name
        ):
            return False
        by_name[check["name"]] = check
    failed = {name for name, check in by_name.items() if not check["ok"]}
    required = {
        "source_assurance_terminal_dispositions",
        "deployment_topology_and_recovery_readiness",
    }
    if not required <= failed <= required | {"launchd_and_process_state"}:
        return False
    if by_name["deployment_topology_and_recovery_readiness"].get("evidence") != {}:
        return False
    if (
        by_name["source_assurance_terminal_dispositions"].get("evidence")
        != (source_assurance_terminal_evidence(None)[1])
    ):
        return False
    if "launchd_and_process_state" in failed:
        evidence = by_name["launchd_and_process_state"].get("evidence")
        definitions = (
            evidence.get("definitions") if isinstance(evidence, dict) else None
        )
        if (
            not isinstance(definitions, dict)
            or not definitions
            or not all(value is True for value in definitions.values())
        ):
            return False
    return True


def _fresh_readiness_contracts_are_known(diagnostics: dict[str, Any]) -> bool:
    deployment = diagnostics.get("deployment_readiness")
    capabilities = diagnostics.get("capabilities")
    source = (
        capabilities.get("policy:source-assurance")
        if isinstance(capabilities, dict)
        else None
    )
    details = source.get("details") if isinstance(source, dict) else None
    return (
        isinstance(deployment, dict)
        and deployment.get("contract") == "deployment-readiness-v1"
        and isinstance(details, dict)
        and details.get("contract") == "source-assurance-v1"
    )


def _doctor_has_present_readiness(result: dict[str, Any]) -> bool:
    by_name = {
        check.get("name"): check
        for check in result.get("checks", [])
        if isinstance(check, dict)
    }
    return all(
        isinstance(evidence := by_name.get(name, {}).get("evidence"), dict)
        and evidence.get("contract") == contract
        for name, contract in (
            ("source_assurance_terminal_dispositions", "source-assurance-v1"),
            ("deployment_topology_and_recovery_readiness", "deployment-readiness-v1"),
        )
    )


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


def wait_for_recovery_baseline(
    *,
    services: ServiceManager,
    baseline: dict[str, Any],
    absolute_deadline: float,
    reserve_seconds: float,
    validate_selection: Callable[[], None],
) -> dict[str, Any] | None:
    """Settle restored consumers and their audit without refreshing owner proof.

    This separate path preserves the exact saved attestation gaps. Only replay
    and audit lag may wait; the existing operation deadline and checkpoint
    watchdog still apply, with room reserved for the remaining full proof.
    """
    from bootstrap.recovery_readiness import (
        recovery_consumers_settled,
        recovery_settlement_pending,
        runtime_matches_baseline,
    )

    if (
        not math.isfinite(absolute_deadline)
        or not math.isfinite(reserve_seconds)
        or reserve_seconds < 0
    ):
        raise ValueError("recovery settlement requires a finite proof budget")
    progress = _ReplayProgress()
    audit_pending_since: float | None = None
    try:
        while True:
            validate_selection()
            now = deployment_clock()
            if now + RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS + reserve_seconds > (
                absolute_deadline
            ):
                return None
            diagnostics = services.runtime_diagnostics()
            validate_selection()
            now = deployment_clock()
            if now + reserve_seconds >= absolute_deadline:
                return None
            # Malformed/missing evidence and new structural failures do not
            # enter a wait. This predicate validates every consumer's details.
            if not recovery_settlement_pending(baseline, diagnostics):
                return None
            assert isinstance(diagnostics, dict)
            progress.observe(diagnostics, now)
            progress.check_stalls(now)
            if runtime_matches_baseline(baseline, diagnostics):
                return diagnostics
            if recovery_consumers_settled(diagnostics):
                if audit_pending_since is None:
                    audit_pending_since = now
                if now - audit_pending_since >= POST_SWITCH_CONSUMER_PROGRESS_SECONDS:
                    return None
            else:
                audit_pending_since = None
            until = progress.next_deadline(absolute_deadline - reserve_seconds)
            if audit_pending_since is not None:
                until = min(
                    until, audit_pending_since + POST_SWITCH_CONSUMER_PROGRESS_SECONDS
                )
            time.sleep(min(POST_SWITCH_POLL_SECONDS, max(until - now, 0)))
    except _SettlementStopped:
        return None


def wait_for_settled_doctor(
    *,
    run_doctor: Callable[..., dict[str, Any]],
    services: ServiceManager,
    test_mode: bool,
    timeout_seconds: float = POST_SWITCH_REPLAY_SETTLEMENT_SECONDS,
    poll_seconds: float = POST_SWITCH_POLL_SECONDS,
    live_readiness_retry_limit: int = POST_SWITCH_LIVE_READINESS_RETRIES,
    allowed_degraded_adapter_ids: frozenset[str] = frozenset(),
    validate_selection: Callable[[], None] | None = None,
    absolute_deadline: float | None = None,
) -> dict[str, Any]:
    """Share the upgrade/restore proof without invoking either controller.

    The caller supplies the appropriate release Doctor. Only known launch and
    source-settlement transients may wait; every structural failure fails closed.
    The complete proof runs after readiness, within one fixed settlement budget.
    """

    if not math.isfinite(timeout_seconds) or (
        absolute_deadline is not None and not math.isfinite(absolute_deadline)
    ):
        raise ValueError("settlement requires a finite deadline")
    deadline = deployment_clock() + min(
        max(timeout_seconds, 0), POST_SWITCH_REPLAY_SETTLEMENT_SECONDS
    )
    if absolute_deadline is not None:
        deadline = min(deadline, absolute_deadline)
    progress = _ReplayProgress()
    live_readiness_retries = 0
    doctor_blackout_pending = False
    last: dict[str, Any] = {"status": "failed", "checks": []}
    diagnostics: dict[str, Any] | None = None

    def check_budget(
        *,
        reserve_seconds: float = 0,
        phase: str | None = None,
        check_progress: bool = True,
    ) -> None:
        if validate_selection is not None:
            validate_selection()
        now = deployment_clock()
        if now >= deadline:
            raise _SettlementStopped("deadline_exhausted", phase=phase)
        if check_progress:
            progress.check_stalls(now)
        if now + reserve_seconds > deadline:
            raise _SettlementStopped("proof_budget_exhausted", phase=phase)

    def checked_call(
        callback: Callable[[], Any],
        *,
        reserve_seconds: float,
        phase: str,
        check_progress: bool = True,
    ) -> Any:
        # Preserve the existing callbacks and their subprocess/HTTP limits.
        # Do not start a bounded operation unless its whole limit fits. Check
        # again after returning: a late healthy report is never an acceptance.
        check_budget(
            reserve_seconds=reserve_seconds,
            phase=phase,
            check_progress=check_progress,
        )
        result = callback()
        check_budget(phase=phase, check_progress=check_progress)
        return result

    def doctor(*, include_synthetic_proof: bool) -> dict[str, Any]:
        return checked_call(
            lambda: run_doctor(include_synthetic_proof=include_synthetic_proof),
            reserve_seconds=RELEASE_DOCTOR_TIMEOUT_SECONDS,
            phase="full_doctor" if include_synthetic_proof else "doctor",
        )

    def pause() -> None:
        check_budget(check_progress=False)
        until = progress.next_deadline(deadline)
        time.sleep(min(max(poll_seconds, 0), max(until - deployment_clock(), 0)))
        check_budget(check_progress=False)

    try:
        last = doctor(include_synthetic_proof=False)
        while True:
            check_budget(check_progress=False)
            if doctor_blackout_pending and _doctor_has_present_readiness(last):
                # The actual Doctor ended this blackout. A later cheap-read or
                # full-proof blackout begins a new bounded episode, even when
                # the very next readiness observation is unavailable.
                live_readiness_retries = 0
                doctor_blackout_pending = False
            if last.get("status") == "healthy":
                last = doctor(include_synthetic_proof=True)
                if last.get("status") == "healthy":
                    return last
            failed = failed_check_names(last)
            absent_readiness = _doctor_has_absent_readiness(last)
            doctor_blackout_pending = doctor_blackout_pending or absent_readiness
            if not failed_checks_are_known_transients(last) and not absent_readiness:
                return last
            if test_mode:
                return last

            diagnostics = checked_call(
                services.runtime_diagnostics,
                reserve_seconds=RUNTIME_DIAGNOSTICS_TIMEOUT_SECONDS,
                phase="readiness",
                check_progress=False,
            )
            if diagnostics is None:
                # Only the same three live-readiness misses are tolerated.
                # Missing reports do not refresh any consumer progress timer.
                progress.check_stalls(deployment_clock())
                cold_start_only = failed.issubset(
                    {"launchd_and_process_state", "ports_and_loopback_binding"}
                )
                deployment_check_failed = (
                    "deployment_topology_and_recovery_readiness" in failed
                )
                source_settlement_only = absent_readiness or (
                    "source_assurance_terminal_dispositions" in failed
                    and (
                        not deployment_check_failed
                        or doctor_has_only_transient_deployment_readiness_gap(last)
                    )
                )
                can_retry_source = (
                    source_settlement_only
                    and live_readiness_retries < live_readiness_retry_limit
                )
                if not cold_start_only and not can_retry_source:
                    return last
                live = checked_call(
                    services.runtime_is_live,
                    reserve_seconds=_RUNTIME_LIVENESS_TIMEOUT_SECONDS,
                    phase="liveness",
                )
                if (cold_start_only and not live) or (can_retry_source and live):
                    if live:
                        live_readiness_retries += 1
                    pause()
                    if deployment_clock() >= progress.next_deadline(deadline):
                        # Recheck readiness at a due watchdog before repeating
                        # Doctor; stale missing evidence cannot decide progress.
                        continue
                    last = doctor(include_synthetic_proof=False)
                    continue
                return last
            if absent_readiness and not _fresh_readiness_contracts_are_known(
                diagnostics
            ):
                raise _SettlementStopped("readiness_contract_invalid")
            if has_new_structural_degradation(
                diagnostics,
                allowed_degraded_adapter_ids=allowed_degraded_adapter_ids,
            ):
                last = doctor(include_synthetic_proof=False)
                if last.get("status") == "healthy":
                    raise _SettlementStopped("structural_degradation")
                return last

            progress.observe(diagnostics, deployment_clock())
            # Observe once at the watchdog boundary before judging the last
            # checkpoint. Work may have advanced during the final poll or HTTP
            # call. The absolute deadline still bounds that entire observation.
            progress.check_stalls(deployment_clock())
            source = diagnostics["capabilities"].get("policy:source-assurance", {})
            details = source.get("details", {}) if isinstance(source, dict) else {}
            counts = details.get("counts", {}) if isinstance(details, dict) else {}
            if isinstance(counts, dict) and isinstance(counts.get("failed"), int):
                if counts["failed"] > 0:
                    raise _SettlementStopped("source_assurance_failed")

            if absent_readiness:
                # A fresh document permits another actual Doctor; it cannot
                # replace the missing evidence inside the retained report.
                if live_readiness_retries >= live_readiness_retry_limit:
                    return last
                live = checked_call(
                    services.runtime_is_live,
                    reserve_seconds=_RUNTIME_LIVENESS_TIMEOUT_SECONDS,
                    phase="liveness",
                )
                if not live:
                    return last
                live_readiness_retries += 1
                pause()
                if deployment_clock() >= progress.next_deadline(deadline):
                    continue
                last = doctor(include_synthetic_proof=False)
                continue

            if not doctor_blackout_pending:
                live_readiness_retries = 0
            if "source_assurance_terminal_dispositions" in failed:
                settled, _evidence = source_assurance_terminal_evidence(diagnostics)
                if not settled:
                    pause()
                    continue

            pause()
            if deployment_clock() >= progress.next_deadline(deadline):
                continue
            last = doctor(include_synthetic_proof=False)
    except _SettlementStopped as stopped:
        # The last Doctor and the cheap observation that stopped settlement
        # can differ. Preserve both without relabelling an older Doctor's
        # source counts as the cause of this failure. Deadline failures do
        # not claim that an earlier cheap observation triggered expiration.
        trigger = (
            settlement_diagnostic_evidence(diagnostics, consumer_id=stopped.consumer_id)
            if diagnostics is not None
            and stopped.reason not in {"deadline_exhausted", "proof_budget_exhausted"}
            else None
        )
        return _stopped_report(last, stopped, triggering_readiness=trigger)


def run_release_doctor(
    paths: DeploymentPaths,
    release: Path,
    *,
    include_synthetic_proof: bool,
    test_mode: bool = False,
    require_runtime: bool = False,
    doctor_factory: Callable[..., Any] = Doctor,
    error_type: type[RuntimeError] = RuntimeError,
    timeout_seconds: float = RELEASE_DOCTOR_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run the selected release's coherent Python, registry, packs and Doctor.

    Restore requires an installed executable. The upgrade caller retains its
    established in-process fallback for credential-free preparation fixtures.
    """

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise error_type("selected release Doctor has no remaining proof budget")
    timeout_seconds = min(timeout_seconds, RELEASE_DOCTOR_TIMEOUT_SECONDS)
    release_python = release / ".venv" / "bin" / "python"
    if require_runtime and (
        not release_python.is_file() or not os.access(release_python, os.X_OK)
    ):
        raise error_type("selected release Doctor runtime is unavailable")
    if test_mode or not release_python.is_file():
        return doctor_factory(
            paths,
            root=release,
            test_mode=test_mode,
        ).run(include_synthetic_proof=include_synthetic_proof)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment.update(
        {
            "ADMINME_INSTANCE_ROOT": str(paths.root),
            "ADMINME_INSTANCE_DIR": str(paths.instance_dir),
            "ADMINME_REPOSITORY_ROOT": str(release),
            "OPENCLAW_STATE_DIR": str(paths.instance_dir / "openclaw" / "runtime"),
            "OPENCLAW_CONFIG_PATH": str(
                paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
            ),
            "PATH": service_environment_path(paths, release),
        }
    )
    command = [
        str(release_python),
        "-m",
        "bootstrap.cli",
        "--instance-root",
        str(paths.root),
        "doctor",
    ]
    if not include_synthetic_proof:
        command.append("--no-synthetic-proof")
    try:
        completed = subprocess.run(
            command,
            cwd=release,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise error_type(
            f"candidate Doctor exceeded {timeout_seconds:g} seconds; "
            "verify candidate-runtime "
            "Keychain access and inspect the redacted service logs"
        ) from exc
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise error_type(
            "candidate Doctor did not return a valid diagnostic report"
        ) from exc
    if not isinstance(result, dict):
        raise error_type("candidate Doctor returned an invalid diagnostic report")
    return result
