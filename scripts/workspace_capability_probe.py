#!/usr/bin/env python3
"""Offline evaluator for sanitized operator capability receipts.

Input is a closed JSON object on stdin containing operator attestations derived
from actual tool results. This does not authenticate evidence artifacts or run
probes: evaluate_bytes only checks binding, freshness and completeness. Never
construct success receipts from agent claims, registration or settings.

Example: python scripts/workspace_capability_probe.py < sanitized-receipts.json
Exit 0 means all four bounded surfaces have qualifying attestations; 1 means
incomplete; 2 means invalid input. No network, subprocess, or filesystem I/O is
performed apart from stdin/stdout. The synthetic unit tests show the contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Sequence, TypedDict


CONTRACT = "workspace-capability-receipts-v1"
REPORT_CONTRACT = "workspace-capability-readiness-report-v1"
MAX_INPUT_BYTES = 64 * 1024
MAX_RECEIPTS = 32
MAX_OBSERVATION_AGE = timedelta(minutes=15)
MAX_PROOF_LIFETIME = timedelta(hours=24)


class Surface(str, Enum):
    SHELL_FILES = "shell_files"
    NATIVE_APP_CONTROL = "native_app_control"
    BROWSER = "browser"
    AUTHORIZED_MEMBER_ROUTE = "authorized_member_route"


SURFACES = tuple(Surface)


REQUIRED_CHECKS = {
    Surface.SHELL_FILES: {
        "native_tool_execution",
        "absolute_directory",
        "create_readback_match",
        "exact_scratch_cleanup",
    },
    Surface.NATIVE_APP_CONTROL: {
        "native_app_tool_execution",
        "app_identity_match",
        "interaction_readback_match",
    },
    Surface.BROWSER: {
        "native_browser_tool_execution",
        "destination_identity_match",
        "interaction_readback_match",
    },
    Surface.AUTHORIZED_MEMBER_ROUTE: {
        "authenticated_member_intake",
        "member_scope_match",
        "native_turn_completed",
        "same_channel_result_observed",
    },
}
ALL_CHECKS = set().union(*REQUIRED_CHECKS.values())


class Readiness(str, Enum):
    SUPPORTED_UNVERIFIED = "supported_but_unverified"
    VERIFIED = "verified"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    PENDING_UNKNOWN = "pending_unknown"
    UNVERIFIED = "unverified"
    UNSUPPORTED = "unsupported_integration"
    ROUTING_FAILURE = "routing_failure"


class Support(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class Outcome(str, Enum):
    ROUTING_FAILURE = "routing_failure"
    SUCCESS = "success"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    PENDING = "pending"
    UNKNOWN = "unknown"


class Disposition(str, Enum):
    COMPLETED = "completed"
    QUEUED = "queued"
    PROMPTED = "prompted"
    REGISTERED = "registered"
    CONFIGURED = "configured"
    UNKNOWN = "unknown"


class EvidenceKind(str, Enum):
    ACTUAL_TERMINAL_RESULT = "actual_terminal_result"
    CAPABILITY_DECLARATION = "capability_declaration"
    REGISTRATION = "registration"
    PROMPT = "prompt"
    ENABLED_SETTING = "enabled_setting"
    RELATIVE_PATH = "relative_path"
    QUEUED_RESULT = "queued_result"


class TrafficContext(TypedDict):
    existing_approved_connection_used: bool
    new_outbound_setup_requested: bool


class ContractError(ValueError):
    """The untrusted input did not satisfy the closed receipt contract."""


@dataclass(frozen=True)
class Intent:
    device_id: str
    principal_id: str
    resources: Mapping[Surface, str]


@dataclass(frozen=True)
class Receipt:
    surface: Surface
    claim: Readiness
    support: Support
    outcome: Outcome
    terminal: bool
    disposition: Disposition
    evidence_kind: EvidenceKind
    device_id: str
    principal_id: str
    resource_id: str
    observed_at: datetime
    valid_until: datetime
    evidence_id: str
    evidence_sha256: str
    checks: tuple[str, ...]


@dataclass(frozen=True)
class Request:
    intent: Intent
    receipts: Sequence[Receipt]
    traffic: TrafficContext


_METADATA = re.compile(r"^[^\x00-\x1f\x7f]{1,500}$")
_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
_SECRET_SHAPE = re.compile(
    r"(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"authorization|cookie|private[_-]?key)\s*[:=]",
    re.IGNORECASE,
)


def _object(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError("closed object mismatch")
    return value


def _text(value: Any, *, evidence_id: bool = False) -> str:
    pattern = _EVIDENCE_ID if evidence_id else _METADATA
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ContractError("invalid metadata")
    if _SECRET_SHAPE.search(value):
        raise ContractError("secret-shaped value")
    return value


def _literal(value: Any, enum_type: type[Enum]) -> Any:
    if not isinstance(value, str):
        raise ContractError("invalid literal")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ContractError("unknown literal") from exc


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ContractError("invalid timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("invalid timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ContractError("timestamp must be timezone-aware")
    return result.astimezone(timezone.utc)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate key")
        result[key] = value
    return result


def _bounded_shape(value: Any, *, depth: int = 0) -> int:
    if depth > 10:
        raise ContractError("input nesting exceeds limit")
    if isinstance(value, dict):
        count = 1 + sum(_bounded_shape(v, depth=depth + 1) for v in value.values())
    elif isinstance(value, list):
        count = 1 + sum(_bounded_shape(v, depth=depth + 1) for v in value)
    else:
        count = 1
    if count > 2_000:
        raise ContractError("input structure exceeds limit")
    return count


def _parse(raw: bytes) -> Request:
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise ContractError("input size outside boundary")
    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise ContractError("invalid JSON") from exc
    _bounded_shape(document)
    root = _object(
        document, {"contract", "schema_version", "intent", "receipts", "traffic"}
    )
    if root["contract"] != CONTRACT:
        raise ContractError("unknown contract")
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ContractError("unknown schema")

    intent_raw = _object(root["intent"], {"device_id", "principal_id", "resources"})
    resources_raw = _object(
        intent_raw["resources"], {surface.value for surface in SURFACES}
    )
    intent = Intent(
        device_id=_text(intent_raw["device_id"]),
        principal_id=_text(intent_raw["principal_id"]),
        resources={
            surface: _text(resources_raw[surface.value]) for surface in SURFACES
        },
    )

    rows = root["receipts"]
    if not isinstance(rows, list) or len(rows) > MAX_RECEIPTS:
        raise ContractError("receipt count outside boundary")
    receipt_keys = {
        "surface",
        "claim",
        "support",
        "outcome",
        "terminal",
        "disposition",
        "evidence_kind",
        "device_id",
        "principal_id",
        "resource_id",
        "observed_at",
        "valid_until",
        "evidence_id",
        "evidence_sha256",
        "checks",
    }
    receipts: list[Receipt] = []
    for untrusted in rows:
        row = _object(untrusted, receipt_keys)
        if type(row["terminal"]) is not bool:
            raise ContractError("terminal must be boolean")
        checks = row["checks"]
        if (
            not isinstance(checks, list)
            or any(not isinstance(check, str) for check in checks)
            or len(checks) != len(set(checks))
            or not set(checks) <= ALL_CHECKS
        ):
            raise ContractError("invalid acceptance checks")
        digest = row["evidence_sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ContractError("invalid evidence digest")
        observed_at = _time(row["observed_at"])
        valid_until = _time(row["valid_until"])
        if (
            not observed_at < valid_until
            or valid_until - observed_at > MAX_PROOF_LIFETIME
        ):
            raise ContractError("invalid observation window")
        receipts.append(
            Receipt(
                surface=_literal(row["surface"], Surface),
                claim=_literal(row["claim"], Readiness),
                support=_literal(row["support"], Support),
                outcome=_literal(row["outcome"], Outcome),
                terminal=row["terminal"],
                disposition=_literal(row["disposition"], Disposition),
                evidence_kind=_literal(row["evidence_kind"], EvidenceKind),
                device_id=_text(row["device_id"]),
                principal_id=_text(row["principal_id"]),
                resource_id=_text(row["resource_id"]),
                observed_at=observed_at,
                valid_until=valid_until,
                evidence_id=_text(row["evidence_id"], evidence_id=True),
                evidence_sha256=digest,
                checks=tuple(checks),
            )
        )

    traffic_raw = _object(
        root["traffic"],
        {"existing_approved_connection_used", "new_outbound_setup_requested"},
    )
    if any(type(traffic_raw[key]) is not bool for key in traffic_raw):
        raise ContractError("traffic flags must be boolean")
    traffic: TrafficContext = {
        "existing_approved_connection_used": traffic_raw[
            "existing_approved_connection_used"
        ],
        "new_outbound_setup_requested": traffic_raw["new_outbound_setup_requested"],
    }
    return Request(intent=intent, receipts=tuple(receipts), traffic=traffic)


def _binding_digest(intent: Intent, surface: Surface) -> str:
    canonical = json.dumps(
        [intent.device_id, intent.principal_id, intent.resources[surface]],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"workspace-capability-target-v1\0" + canonical).hexdigest()


def _fresh(receipt: Receipt, now: datetime) -> bool:
    return (
        receipt.observed_at <= now < receipt.valid_until
        and now - receipt.observed_at <= MAX_OBSERVATION_AGE
    )


def _terminal_tuple(receipt: Receipt, outcome: Outcome) -> bool:
    return (
        receipt.outcome is outcome
        and receipt.terminal is True
        and receipt.disposition is Disposition.COMPLETED
        and receipt.evidence_kind is EvidenceKind.ACTUAL_TERMINAL_RESULT
    )


_HANDOFFS = {
    Surface.SHELL_FILES: (
        "native local-worker acceptance on the exact approved directory, device, "
        "and service principal"
    ),
    Surface.NATIVE_APP_CONTROL: (
        "native-app acceptance through an upgraded supported tool with current "
        "device, principal, service, and resource identity"
    ),
    Surface.BROWSER: (
        "admit a browser worker, then verify the actual destination account, "
        "workspace, role, device, profile, and resource"
    ),
    Surface.AUTHORIZED_MEMBER_ROUTE: (
        "member-route acceptance with a current grant and exact operating principal, "
        "represented member, recipient, device, and resource"
    ),
}


def _surface_result(
    request: Request, surface: Surface, now: datetime
) -> dict[str, Any]:
    matches = [row for row in request.receipts if row.surface is surface]
    base = {
        "surface": surface.value,
        "target_binding_sha256": _binding_digest(request.intent, surface),
        "next_handoff": _HANDOFFS[surface],
    }
    if not matches:
        return {
            **base,
            "state": Readiness.PENDING_UNKNOWN.value,
            "basis": "receipt_missing",
        }
    if len(matches) != 1:
        return {
            **base,
            "state": Readiness.UNVERIFIED.value,
            "basis": "multiple_receipts_are_ambiguous",
        }
    receipt = matches[0]
    intended = request.intent
    if (
        receipt.device_id != intended.device_id
        or receipt.principal_id != intended.principal_id
        or receipt.resource_id != intended.resources[surface]
    ):
        return {
            **base,
            "state": Readiness.UNVERIFIED.value,
            "basis": "intended_device_principal_or_resource_mismatch",
        }
    if not _fresh(receipt, now):
        return {
            **base,
            "state": Readiness.UNVERIFIED.value,
            "basis": "observation_missing_current_validity",
        }
    if surface is Surface.SHELL_FILES and (
        not os.path.isabs(receipt.resource_id)
        or os.path.normpath(receipt.resource_id) != receipt.resource_id
    ):
        return {
            **base,
            "state": Readiness.UNVERIFIED.value,
            "basis": "shell_file_proof_requires_exact_absolute_resource",
        }

    if receipt.claim is Readiness.VERIFIED:
        if (
            receipt.support is Support.SUPPORTED
            and _terminal_tuple(receipt, Outcome.SUCCESS)
            and set(receipt.checks) == REQUIRED_CHECKS[surface]
        ):
            return {
                **base,
                "state": Readiness.VERIFIED.value,
                "basis": "current_matching_operator_attested_terminal_success",
                "evidence_sha256": receipt.evidence_sha256,
            }
    elif receipt.claim is Readiness.DENIED:
        if _terminal_tuple(receipt, Outcome.DENIED):
            return {
                **base,
                "state": Readiness.DENIED.value,
                "basis": "current_matching_actual_terminal_denial",
            }
    elif receipt.claim is Readiness.UNAVAILABLE:
        if _terminal_tuple(receipt, Outcome.UNAVAILABLE):
            return {
                **base,
                "state": Readiness.UNAVAILABLE.value,
                "basis": "current_matching_actual_terminal_unavailability",
            }
    elif receipt.claim is Readiness.ROUTING_FAILURE:
        if _terminal_tuple(receipt, Outcome.ROUTING_FAILURE):
            return {
                **base,
                "state": Readiness.ROUTING_FAILURE.value,
                "basis": "current_matching_terminal_routing_failure",
            }
    elif receipt.claim is Readiness.UNSUPPORTED:
        if receipt.support is Support.UNSUPPORTED and _terminal_tuple(
            receipt, Outcome.UNAVAILABLE
        ):
            return {
                **base,
                "state": Readiness.UNSUPPORTED.value,
                "basis": "current_matching_unsupported_integration",
            }
    elif receipt.claim is Readiness.PENDING_UNKNOWN:
        if (
            receipt.outcome is Outcome.PENDING
            and receipt.terminal is False
            and receipt.disposition in {Disposition.QUEUED, Disposition.UNKNOWN}
        ):
            return {
                **base,
                "state": Readiness.PENDING_UNKNOWN.value,
                "basis": "nonterminal_pending_receipt",
            }
    elif receipt.claim is Readiness.SUPPORTED_UNVERIFIED:
        if receipt.support is Support.SUPPORTED and not _terminal_tuple(
            receipt, Outcome.SUCCESS
        ):
            return {
                **base,
                "state": Readiness.SUPPORTED_UNVERIFIED.value,
                "basis": "support_metadata_without_terminal_success",
            }
    return {
        **base,
        "state": Readiness.UNVERIFIED.value,
        "basis": "contradictory_or_nonqualifying_receipt",
    }


def _control_coverage() -> list[dict[str, str | bool]]:
    # A working browser or shell is not evidence of network approval enforcement.
    return [
        {
            "path": path,
            "approval_enforcement_evidenced": False,
            "evidence": "not_evidenced_by_capability_receipts",
            "next_handoff": handoff,
            "coverage_gap": gap,
        }
        for path, handoff, gap in (
            (
                "shell_child_processes_and_curl",
                "Qualify native managed-network approvals for the actual exec harness.",
                "An HTTP proxy does not cover arbitrary sockets or escaped child processes.",
            ),
            (
                "browser",
                "Qualify browser network controls for the actual profile and process.",
                "Navigation review does not cover all subresources, workers or background traffic.",
            ),
            (
                "package_managers",
                "Review the exact install and include its child scripts in network coverage.",
                "An approved package download does not approve arbitrary lifecycle-script traffic.",
            ),
            (
                "plugins_and_mcp",
                "Identify each native or remote server and its enforced network boundary.",
                "Tool allowlists and gateway proxy settings do not constrain every server process.",
            ),
            (
                "delegated_agents",
                "Verify each worker inherits the approved tool and network boundary.",
                "A parent prompt or approval does not prove child-process enforcement.",
            ),
            (
                "background_processes",
                "Inventory the actual service processes and qualify their network boundary.",
                "Interactive approval hooks do not necessarily cover scheduled or background work.",
            ),
        )
    ]


def _refusal(now: datetime) -> dict[str, Any]:
    return {
        "contract": REPORT_CONTRACT,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "mode": "offline_metadata_only",
        "bounded_acceptance": "refused_invalid_input",
        "full_workspace_ready": False,
        "error": {
            "code": "invalid_input",
            "message": "input did not match the closed contract",
        },
        "external_effects": "none",
    }


def evaluate_bytes(raw: bytes, *, now: datetime | None = None) -> dict[str, Any]:
    """Return a bounded, non-echoing report; malformed input returns one refusal."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        request = _parse(raw)
    except (ContractError, RecursionError):
        return _refusal(current)
    rows = [_surface_result(request, surface, current) for surface in SURFACES]
    all_verified = all(row["state"] == Readiness.VERIFIED.value for row in rows)
    return {
        "contract": REPORT_CONTRACT,
        "generated_at": current.isoformat().replace("+00:00", "Z"),
        "mode": "offline_metadata_only",
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "bounded_acceptance": (
            "attestations_complete" if all_verified else "not_accepted"
        ),
        "full_workspace_ready": False,
        "surfaces": rows,
        "control_coverage": _control_coverage(),
        "traffic": {
            "existing_approved_connection": (
                "declared_context_only_not_reverified"
                if request.traffic["existing_approved_connection_used"]
                else "not_declared"
            ),
            "new_outbound_setup_request": (
                "present_not_authorized_or_executed"
                if request.traffic["new_outbound_setup_requested"]
                else "none"
            ),
            "runner_network_activity": "none",
        },
        "limitations": [
            "source artifacts are operator-attested, not independently authenticated here",
            "no universal egress mediation is claimed",
            "prompt guidance, tool allowlists, settings, and environment flags are not terminal proof",
            "no blanket grant or persisted security change is created",
        ],
        "external_effects": "none",
    }


def main() -> int:
    report = evaluate_bytes(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    acceptance = report["bounded_acceptance"]
    if acceptance == "refused_invalid_input":
        return 2
    return 0 if acceptance == "attestations_complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
