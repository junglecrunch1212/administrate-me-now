"""Versioned, content-free contracts for live-fidelity reconciliation.

The live side exports aggregates only.  Candidate code consumes the signed
artifact and must prove every dimension before it may create a disposable
instance.  No default in this module permits an absent live measurement to be
treated as evidence of fidelity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

FIDELITY_CONTRACT = "administrateme-live-fidelity-v1"
GENERATOR_VERSION = 4
MANIFEST_MAX_AGE_SECONDS = 15 * 60
SMALL_CELL_THRESHOLD = 5


class ManifestError(RuntimeError):
    """A fidelity artifact is incomplete, stale, or internally inconsistent."""


# Every value is a structural count.  Values that had no measured initial
# floor still require an explicit live value; the small non-zero floor ensures
# their production path exists in every canonical baseline.
MINIMUM_CARDINALITIES: dict[str, int] = {
    "source_facts": 67_930,
    "recent_messages": 500,
    "message_threads": 343,
    "participants": 184,
    "members": 4,
    "service_principals": 1,
    "parties": 184,
    "relationships": 8,
    "endpoints": 184,
    "accounts": 9,
    "workspaces": 2,
    "provider_families": 9,
    "adapter_categories": 5,
    "attachments": 40,
    "cc_recipients": 80,
    "bcc_recipients": 12,
    "calendar_facts": 30,
    "task_facts": 40,
    "reminder_facts": 30,
    "list_facts": 30,
    "recurrence_facts": 20,
    "finance_facts": 30,
    "note_facts": 30,
    "contact_facts": 184,
    "chat_facts": 20,
    "triage_candidates": 951,
    "triage_matters": 522,
    "enrichment_or_destination_required": 591,
    "feedback_facts": 30,
    "rule_history_facts": 12,
}

MINIMUM_PERSONA_CARDINALITIES: dict[str, dict[str, int]] = {
    "adult_primary": {"source_facts": 5_000, "messages": 200},
    "adult_secondary": {"source_facts": 5_000, "messages": 200},
    "dependents": {"source_facts": 1_000, "messages": 50},
    "cos_service": {"source_facts": 1_000, "messages": 50},
}

MINIMUM_PERSONA_CATEGORIES: dict[str, dict[str, frozenset[str]]] = {
    "adult_primary": {
        "message_directions": frozenset({"inbound", "outbound"}),
        "source_shapes": frozenset(
            {
                "calendar",
                "task",
                "reminder",
                "list",
                "recurrence",
                "finance",
                "notes",
                "contacts",
                "chat",
            }
        ),
    },
    "adult_secondary": {
        "message_directions": frozenset({"inbound", "outbound"}),
        "source_shapes": frozenset(
            {
                "calendar",
                "task",
                "reminder",
                "list",
                "recurrence",
                "finance",
                "notes",
                "contacts",
                "chat",
            }
        ),
    },
    "dependents": {
        "message_directions": frozenset({"inbound", "outbound"}),
        "source_shapes": frozenset(
            {"calendar", "task", "reminder", "list", "notes", "chat"}
        ),
    },
    "cos_service": {
        "message_directions": frozenset({"inbound", "outbound"}),
        "source_shapes": frozenset({"chat", "cos"}),
    },
}

TAIL_KEYS = ("p50", "p90", "p95", "p99", "maximum")
MINIMUM_TAILS: dict[str, dict[str, int]] = {
    "subject_chars": {"p50": 24, "p90": 80, "p95": 120, "p99": 220, "maximum": 500},
    "body_chars": {
        "p50": 240,
        "p90": 6_000,
        "p95": 6_000,
        "p99": 9_000,
        "maximum": 12_000,
    },
    "preview_chars": {"p50": 120, "p90": 300, "p95": 500, "p99": 900, "maximum": 1_500},
    "thread_depth": {"p50": 1, "p90": 3, "p95": 5, "p99": 10, "maximum": 14},
    "recipient_count": {"p50": 1, "p90": 3, "p95": 5, "p99": 8, "maximum": 12},
    "attachment_count": {"p50": 0, "p90": 1, "p95": 2, "p99": 4, "maximum": 8},
    "matter_group_depth": {"p50": 1, "p90": 3, "p95": 5, "p99": 9, "maximum": 13},
    "response_bytes": {
        "p50": 1_024,
        "p90": 16_384,
        "p95": 32_768,
        "p99": 65_536,
        "maximum": 131_072,
    },
}

REQUIRED_CATEGORIES: dict[str, frozenset[str]] = {
    "persona_kinds": frozenset(
        {"adult_primary", "adult_secondary", "dependent", "cos_service"}
    ),
    "adapter_categories": frozenset(
        {
            "communication",
            "external_state_mirror",
            "inbound_data",
            "personal_knowledge",
            "outbound_action",
        }
    ),
    "message_directions": frozenset({"inbound", "outbound"}),
    "message_history_shapes": frozenset({"quoted", "forwarded", "multi_message"}),
    "attention_states": frozenset(
        {
            "unread",
            "archived",
            "quieted",
            "waiting",
            "snoozed",
            "important",
            "reply_required",
        }
    ),
    "message_natures": frozenset(
        {"transactional", "security", "promotional", "spam", "low_signal", "personal"}
    ),
    "provider_categories": frozenset(
        {"primary", "updates", "promotions", "spam", "uncategorized"}
    ),
    "source_shapes": frozenset(
        {
            "calendar",
            "task",
            "reminder",
            "list",
            "recurrence",
            "finance",
            "notes",
            "contacts",
            "chat",
            "cos",
        }
    ),
    "candidate_kinds": frozenset(
        {
            "task",
            "commitment",
            "calendar_event",
            "reminder",
            "list",
            "list_item",
            "recurrence",
            "party_identifier",
            "party_candidate",
            "party_relationship",
            "relationship_fact",
        }
    ),
    "queue_lanes": frozenset({"focus", "later", "waiting", "low_signal"}),
    "review_states": frozenset(
        {
            "pending_interpretation",
            "pending_review",
            "confirmed",
            "dismissed",
            "superseded",
            "failed",
        }
    ),
    "destination_states": frozenset(
        {"complete", "enrichment_required", "destination_required"}
    ),
    "history_actions": frozenset(
        {
            "dismissal",
            "confirmation",
            "supersession",
            "learning",
            "rule_creation",
            "rule_reversal",
            "correction",
            "replay",
        }
    ),
    "model_outcomes": frozenset(
        {
            "success",
            "timeout",
            "unreachable",
            "retry",
            "idempotency_race",
            "recovery",
            "replay",
        }
    ),
    "safety_paths": frozenset(
        {
            "governance",
            "approval",
            "observation",
            "privacy",
            "tenant_isolation",
            "zero_provider_effect",
        }
    ),
    "query_pressure": frozenset(
        {
            "sorting",
            "filtering",
            "pagination",
            "stable_cursor",
            "large_payload",
            "rendering",
        }
    ),
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError(f"{field} must be an object")
    return value


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ManifestError(f"{field} must be a list of non-empty strings")
    return sorted(set(value))


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ManifestError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ManifestError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_manifest(
    value: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = MANIFEST_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Validate and normalize an active-runtime fidelity manifest."""

    manifest = dict(value)
    if manifest.get("contract") != FIDELITY_CONTRACT:
        raise ManifestError(
            f"unsupported fidelity contract: {manifest.get('contract')!r}"
        )
    created_at = _utc(manifest.get("created_at"), "created_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - created_at).total_seconds()
    if age < -60 or age > max_age_seconds:
        raise ManifestError(
            f"fidelity manifest is not current (age_seconds={int(age)})"
        )

    active = _mapping(manifest.get("active_release"), "active_release")
    commit = active.get("commit")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ManifestError(
            "active_release.commit must be an immutable 40-character Git SHA"
        )

    runtime = _mapping(manifest.get("runtime"), "runtime")
    for field in ("python", "node", "openclaw_gateway"):
        if not isinstance(runtime.get(field), str) or not runtime[field]:
            raise ManifestError(f"runtime.{field} is required")
    plugins = _mapping(runtime.get("plugins"), "runtime.plugins")
    if any(
        not isinstance(key, str) or not isinstance(version, str)
        for key, version in plugins.items()
    ):
        raise ManifestError("runtime.plugins must map plugin IDs to versions")

    for field in ("event_schemas", "projections", "pipelines"):
        versions = _mapping(manifest.get(field), field)
        if not versions or any(not isinstance(key, str) for key in versions):
            raise ManifestError(f"{field} must contain version boundaries")

    topology = _mapping(manifest.get("topology"), "topology")
    for field in (
        "product_surfaces",
        "capabilities",
        "adapter_categories",
        "provider_families",
        "account_shapes",
        "workspace_shapes",
        "permission_capabilities",
    ):
        _strings(topology.get(field), f"topology.{field}")

    observation = _mapping(manifest.get("observation"), "observation")
    if observation.get("active") is not True:
        raise ManifestError("observation mode must be active")
    if observation.get("governance_state") != "configured":
        raise ManifestError("governance must be configured")
    if observation.get("external_writes_enabled") is not False:
        raise ManifestError("external writes must be disabled")

    cardinalities = _mapping(manifest.get("cardinalities"), "cardinalities")
    missing_counts = sorted(set(MINIMUM_CARDINALITIES) - set(cardinalities))
    if missing_counts:
        raise ManifestError(
            "manifest is missing cardinalities: " + ", ".join(missing_counts)
        )
    for name, count in cardinalities.items():
        if (
            not isinstance(name, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise ManifestError(f"cardinalities.{name} must be a non-negative integer")

    tails = _mapping(manifest.get("tails"), "tails")
    missing_tails = sorted(set(MINIMUM_TAILS) - set(tails))
    if missing_tails:
        raise ManifestError(
            "manifest is missing tail metrics: " + ", ".join(missing_tails)
        )
    for dimension, raw_metrics in tails.items():
        metrics = _mapping(raw_metrics, f"tails.{dimension}")
        prior = -1
        for quantile in TAIL_KEYS:
            value_at_quantile = metrics.get(quantile)
            if (
                not isinstance(value_at_quantile, int)
                or isinstance(value_at_quantile, bool)
                or value_at_quantile < prior
            ):
                raise ManifestError(
                    f"tails.{dimension} must be non-negative and monotonic"
                )
            prior = value_at_quantile

    categories = _mapping(manifest.get("categories"), "categories")
    missing_categories = sorted(set(REQUIRED_CATEGORIES) - set(categories))
    if missing_categories:
        raise ManifestError(
            "manifest is missing category dimensions: " + ", ".join(missing_categories)
        )
    for dimension, values in categories.items():
        _strings(values, f"categories.{dimension}")

    personas = _mapping(manifest.get("personas"), "personas")
    required_personas = {
        "adult_primary",
        "adult_secondary",
        "dependents",
        "cos_service",
    }
    if set(personas) != required_personas:
        raise ManifestError(
            "personas must contain adult_primary, adult_secondary, dependents, and cos_service"
        )
    for persona, raw_profile in personas.items():
        profile = _mapping(raw_profile, f"personas.{persona}")
        counts = _mapping(
            profile.get("cardinalities"), f"personas.{persona}.cardinalities"
        )
        if "source_facts" not in counts or "messages" not in counts:
            raise ManifestError(
                f"personas.{persona} must measure source_facts and messages"
            )
        if any(not isinstance(count, int) or count < 0 for count in counts.values()):
            raise ManifestError(f"personas.{persona} cardinalities are invalid")
        persona_tails = _mapping(profile.get("tails"), f"personas.{persona}.tails")
        for dimension, raw_metrics in persona_tails.items():
            metrics = _mapping(raw_metrics, f"personas.{persona}.tails.{dimension}")
            prior = -1
            for quantile in TAIL_KEYS:
                metric = metrics.get(quantile)
                if not isinstance(metric, int) or metric < prior:
                    raise ManifestError(
                        f"personas.{persona}.tails.{dimension} must be monotonic"
                    )
                prior = metric
        persona_categories = _mapping(
            profile.get("categories"), f"personas.{persona}.categories"
        )
        for dimension, values in persona_categories.items():
            _strings(values, f"personas.{persona}.categories.{dimension}")

    privacy = _mapping(manifest.get("privacy"), "privacy")
    if (
        privacy.get("content_persisted") is not False
        or privacy.get("reversible_hashes_persisted") is not False
    ):
        raise ManifestError(
            "fidelity manifests must persist no content or reversible hashes"
        )
    if privacy.get("small_cell_threshold") != SMALL_CELL_THRESHOLD:
        raise ManifestError("fidelity manifest small-cell threshold is incompatible")
    if not isinstance(privacy.get("small_cells_suppressed"), int):
        raise ManifestError("privacy.small_cells_suppressed is required")

    scale = _mapping(manifest.get("scale"), "scale")
    for field in ("event_log_events", "event_log_bytes", "replay_depth"):
        if not isinstance(scale.get(field), int) or scale[field] < 0:
            raise ManifestError(f"scale.{field} must be a non-negative integer")
    return manifest


def build_target_envelope(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Compute the deterministic minimum that must dominate this manifest."""

    validated = validate_manifest(manifest)
    live_counts = validated["cardinalities"]
    cardinalities = {
        name: max(MINIMUM_CARDINALITIES.get(name, 0), math.ceil(int(count) * 1.10))
        for name, count in live_counts.items()
    }
    for name, count_floor in MINIMUM_CARDINALITIES.items():
        cardinalities[name] = max(cardinalities.get(name, 0), count_floor)

    tails: dict[str, dict[str, int]] = {}
    for dimension in set(validated["tails"]) | set(MINIMUM_TAILS):
        live = validated["tails"].get(dimension, {})
        tail_floor = MINIMUM_TAILS.get(dimension, {})
        tails[dimension] = {
            key: max(int(live.get(key, 0)), int(tail_floor.get(key, 0)))
            for key in TAIL_KEYS
        }

    categories: dict[str, list[str]] = {}
    for dimension in set(validated["categories"]) | set(REQUIRED_CATEGORIES):
        live = set(validated["categories"].get(dimension, []))
        required = set(REQUIRED_CATEGORIES.get(dimension, ()))
        # The extra adversarial value makes categorical dominance a strict
        # superset even when the live system already exhibits every ordinary
        # state in the reviewed taxonomy.
        required.add(f"adversarial:{dimension}:boundary")
        categories[dimension] = sorted(live | required)

    topology: dict[str, list[str]] = {}
    for dimension, values in validated["topology"].items():
        required_values = set(values)
        required_values.add(f"adversarial:topology:{dimension}:boundary")
        topology[dimension] = sorted(required_values)

    persona_targets: dict[str, Any] = {}
    for persona, profile in validated["personas"].items():
        persona_floor = MINIMUM_PERSONA_CARDINALITIES[persona]
        persona_tails = {
            dimension: {
                key: max(
                    int(metrics[key]),
                    int(MINIMUM_TAILS.get(dimension, {}).get(key, 0)),
                )
                for key in TAIL_KEYS
            }
            for dimension, metrics in profile["tails"].items()
        }
        persona_categories = {}
        for dimension in set(profile["categories"]) | set(
            MINIMUM_PERSONA_CATEGORIES[persona]
        ):
            required_values = set(profile["categories"].get(dimension, []))
            required_values.update(
                MINIMUM_PERSONA_CATEGORIES[persona].get(dimension, ())
            )
            required_values.add(f"adversarial:{persona}:{dimension}:boundary")
            persona_categories[dimension] = sorted(required_values)
        persona_targets[persona] = {
            "cardinalities": {
                name: max(
                    math.ceil(int(count) * 1.10),
                    int(persona_floor.get(name, 0)),
                )
                for name, count in profile["cardinalities"].items()
            },
            "tails": persona_tails,
            "categories": persona_categories,
        }
    cardinalities["recent_messages"] = max(
        cardinalities["recent_messages"],
        sum(
            int(profile["cardinalities"].get("messages", 0))
            for profile in persona_targets.values()
        ),
    )
    return {
        "contract": FIDELITY_CONTRACT,
        "generator_version": GENERATOR_VERSION,
        "source_manifest_fingerprint": fingerprint(validated),
        "active_release_commit": validated["active_release"]["commit"],
        "cardinalities": cardinalities,
        "tails": tails,
        "categories": categories,
        "topology": topology,
        "personas": persona_targets,
    }


def dominance_report(
    manifest: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> dict[str, Any]:
    """Return content-free pass/fail evidence for every required dimension."""

    target = build_target_envelope(manifest)
    count_rows: dict[str, Any] = {}
    for name, required in target["cardinalities"].items():
        measured_count = int(
            _mapping(actual.get("cardinalities"), "actual.cardinalities").get(name, -1)
        )
        count_rows[name] = {
            "required": required,
            "actual": measured_count,
            "passes": measured_count >= required,
        }

    tail_rows: dict[str, Any] = {}
    actual_tails = _mapping(actual.get("tails"), "actual.tails")
    for dimension, required_metrics in target["tails"].items():
        measured_metrics = _mapping(
            actual_tails.get(dimension), f"actual.tails.{dimension}"
        )
        metrics = {
            key: {
                "required": required,
                "actual": int(measured_metrics.get(key, -1)),
                "passes": int(measured_metrics.get(key, -1)) >= required,
            }
            for key, required in required_metrics.items()
        }
        tail_rows[dimension] = {
            "metrics": metrics,
            "passes": all(row["passes"] for row in metrics.values()),
        }

    category_rows: dict[str, Any] = {}
    actual_categories = _mapping(actual.get("categories"), "actual.categories")
    live_categories = manifest["categories"]
    for dimension, required_values in target["categories"].items():
        measured_values = set(
            _strings(
                actual_categories.get(dimension),
                f"actual.categories.{dimension}",
            )
        )
        live_values = set(live_categories.get(dimension, []))
        required_set = set(required_values)
        category_rows[dimension] = {
            "missing": sorted(required_set - measured_values),
            "strict_superset": measured_values > live_values,
            "passes": required_set <= measured_values and measured_values > live_values,
        }

    topology_rows: dict[str, Any] = {}
    actual_topology = _mapping(actual.get("topology"), "actual.topology")
    for dimension, required_values in target["topology"].items():
        measured_values = set(
            _strings(
                actual_topology.get(dimension),
                f"actual.topology.{dimension}",
            )
        )
        live_values = set(manifest["topology"].get(dimension, []))
        required_set = set(required_values)
        topology_rows[dimension] = {
            "missing": sorted(required_set - measured_values),
            "strict_superset": measured_values > live_values,
            "passes": required_set <= measured_values and measured_values > live_values,
        }

    persona_rows: dict[str, Any] = {}
    actual_personas = _mapping(actual.get("personas"), "actual.personas")
    for persona, profile in target["personas"].items():
        measured_profile = _mapping(
            actual_personas.get(persona), f"actual.personas.{persona}"
        )
        measured_counts = _mapping(
            measured_profile.get("cardinalities"),
            f"actual.personas.{persona}.cardinalities",
        )
        rows = {
            name: {
                "required": required,
                "actual": int(measured_counts.get(name, -1)),
                "passes": int(measured_counts.get(name, -1)) >= required,
            }
            for name, required in profile["cardinalities"].items()
        }
        measured_tails = _mapping(
            measured_profile.get("tails"), f"actual.personas.{persona}.tails"
        )
        tail_results: dict[str, Any] = {}
        for dimension, required_metrics in profile["tails"].items():
            actual_metrics = _mapping(
                measured_tails.get(dimension),
                f"actual.personas.{persona}.tails.{dimension}",
            )
            result = {
                key: {
                    "required": required,
                    "actual": int(actual_metrics.get(key, -1)),
                    "passes": int(actual_metrics.get(key, -1)) >= required,
                }
                for key, required in required_metrics.items()
            }
            tail_results[dimension] = {
                "metrics": result,
                "passes": all(value["passes"] for value in result.values()),
            }
        measured_categories = _mapping(
            measured_profile.get("categories"),
            f"actual.personas.{persona}.categories",
        )
        category_results: dict[str, Any] = {}
        for dimension, required_values in profile["categories"].items():
            actual_values = set(
                _strings(
                    measured_categories.get(dimension),
                    f"actual.personas.{persona}.categories.{dimension}",
                )
            )
            live_values = set(
                manifest["personas"][persona]["categories"].get(dimension, [])
            )
            required_set = set(required_values)
            category_results[dimension] = {
                "missing": sorted(required_set - actual_values),
                "strict_superset": actual_values > live_values,
                "passes": required_set <= actual_values and actual_values > live_values,
            }
        persona_rows[persona] = {
            "cardinalities": rows,
            "tails": tail_results,
            "categories": category_results,
            "passes": all(row["passes"] for row in rows.values())
            and all(row["passes"] for row in tail_results.values())
            and all(row["passes"] for row in category_results.values()),
        }

    passes = (
        all(row["passes"] for row in count_rows.values())
        and all(row["passes"] for row in tail_rows.values())
        and all(row["passes"] for row in category_rows.values())
        and all(row["passes"] for row in topology_rows.values())
        and all(row["passes"] for row in persona_rows.values())
    )
    return {
        "contract": "administrateme-dominance-report-v1",
        "source_manifest_fingerprint": target["source_manifest_fingerprint"],
        "generator_version": GENERATOR_VERSION,
        "passes": passes,
        "cardinalities": count_rows,
        "tails": tail_rows,
        "categories": category_rows,
        "topology": topology_rows,
        "personas": persona_rows,
    }


__all__ = [
    "FIDELITY_CONTRACT",
    "GENERATOR_VERSION",
    "MANIFEST_MAX_AGE_SECONDS",
    "MINIMUM_CARDINALITIES",
    "MINIMUM_TAILS",
    "REQUIRED_CATEGORIES",
    "SMALL_CELL_THRESHOLD",
    "ManifestError",
    "build_target_envelope",
    "canonical_json",
    "dominance_report",
    "fingerprint",
    "validate_manifest",
]
