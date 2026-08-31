"""Active-runtime, read-only generation of content-free fidelity manifests."""

from __future__ import annotations

import json
import platform
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from adminme.events.registry import ensure_autoloaded, registry
from adminme.sandbox.contracts import (
    FIDELITY_CONTRACT,
    MINIMUM_CARDINALITIES,
    MINIMUM_TAILS,
    REQUIRED_CATEGORIES,
    SMALL_CELL_THRESHOLD,
)

_INTERNAL_SOURCE_ADAPTERS = frozenset(
    {
        "adminme-runtime",
        "member-triage",
        "openclaw",
        "pipeline",
        "system",
    }
)
_WORD_RE = re.compile(r"[a-z0-9]+")


def _percentiles(values: Sequence[int]) -> dict[str, int]:
    ordered = sorted(values)
    if not ordered:
        return {key: 0 for key in ("p50", "p90", "p95", "p99", "maximum")}

    def at(fraction: float) -> int:
        # Nearest-rank percentile is deterministic and conservative for tails.
        index = max(
            0, min(len(ordered) - 1, int((len(ordered) * fraction) + 0.999999) - 1)
        )
        return int(ordered[index])

    return {
        "p50": at(0.50),
        "p90": at(0.90),
        "p95": at(0.95),
        "p99": at(0.99),
        "maximum": int(ordered[-1]),
    }


def _category_values(counts: Counter[str]) -> tuple[list[str], int]:
    visible = sorted(
        name for name, count in counts.items() if count >= SMALL_CELL_THRESHOLD
    )
    suppressed = sum(1 for count in counts.values() if 0 < count < SMALL_CELL_THRESHOLD)
    return visible, suppressed


def _persona_aliases(member_roles: Mapping[str, str]) -> dict[str, str]:
    adults = sorted(
        member_id for member_id, role in member_roles.items() if role != "child"
    )
    dependents = sorted(
        member_id for member_id, role in member_roles.items() if role == "child"
    )
    aliases: dict[str, str] = {}
    if adults:
        aliases[adults[0]] = "adult_primary"
    if len(adults) > 1:
        aliases[adults[1]] = "adult_secondary"
    for member_id in adults[2:]:
        aliases[member_id] = "adult_secondary"
    for member_id in dependents:
        aliases[member_id] = "dependents"
    return aliases


def _message_category(payload: Mapping[str, Any]) -> set[str]:
    labels = {str(value).upper() for value in payload.get("provider_labels") or []}
    states: set[str] = set()
    if "SPAM" in labels:
        states.add("spam")
    if "CATEGORY_PROMOTIONS" in labels:
        states.add("promotions")
    if "CATEGORY_UPDATES" in labels:
        states.add("updates")
    if "CATEGORY_PRIMARY" in labels or "INBOX" in labels:
        states.add("primary")
    if not states:
        states.add("uncategorized")
    return states


def _source_shape(event_type: str) -> str | None:
    if event_type == "openclaw.domain_event.observed":
        return "chat"
    prefixes = (
        ("calendar.", "calendar"),
        ("task.", "task"),
        ("reminder.", "reminder"),
        ("list_item.", "list"),
        ("recurrence.", "recurrence"),
        ("financial.", "finance"),
        ("money_flow.", "finance"),
        ("member_knowledge.", "notes"),
        ("contact.", "contacts"),
        ("endpoint.", "contacts"),
        ("chat.", "chat"),
        ("openclaw.", "cos"),
    )
    return next(
        (shape for prefix, shape in prefixes if event_type.startswith(prefix)), None
    )


def privacy_compare(
    synthetic_values: Iterable[str],
    live_values: Iterable[str],
) -> dict[str, Any]:
    """Compare only in memory and return counts, never matched content.

    Exact normalized values and eight-word phrases of at least 32 characters
    are treated as suspicious.  No hash or sample is returned or persisted.
    """

    live_exact: set[str] = set()
    live_phrases: set[tuple[str, ...]] = set()
    live_seen = 0
    for raw in live_values:
        if not isinstance(raw, str) or not raw.strip():
            continue
        live_seen += 1
        normalized = " ".join(_WORD_RE.findall(raw.casefold()))
        if normalized:
            live_exact.add(normalized)
        words = tuple(_WORD_RE.findall(raw.casefold()))
        for index in range(max(0, len(words) - 7)):
            phrase = words[index : index + 8]
            if len(" ".join(phrase)) >= 32:
                live_phrases.add(phrase)

    exact_matches = 0
    phrase_matches = 0
    synthetic_seen = 0
    for raw in synthetic_values:
        if not isinstance(raw, str) or not raw.strip():
            continue
        synthetic_seen += 1
        normalized = " ".join(_WORD_RE.findall(raw.casefold()))
        exact_matches += int(bool(normalized and normalized in live_exact))
        words = tuple(_WORD_RE.findall(raw.casefold()))
        suspicious = False
        for index in range(max(0, len(words) - 7)):
            phrase = words[index : index + 8]
            if len(" ".join(phrase)) >= 32 and phrase in live_phrases:
                suspicious = True
                break
        phrase_matches += int(suspicious)
    return {
        "contract": "administrateme-in-memory-privacy-comparison-v1",
        "live_values_examined": live_seen,
        "synthetic_values_examined": synthetic_seen,
        "exact_matches": exact_matches,
        "suspicious_phrase_matches": phrase_matches,
        "passes": exact_matches == 0 and phrase_matches == 0,
        "content_persisted": False,
        "reversible_hashes_persisted": False,
    }


async def build_live_fidelity_manifest(runtime: Any) -> dict[str, Any]:
    """Profile an already-running active release without opening its DB anew."""

    if (
        runtime.event_log is None
        or runtime.projections is None
        or runtime.pipelines is None
    ):
        raise RuntimeError("live fidelity profiling requires a started runtime")
    binding = (runtime.settings.host_topology or {}).get("deployment_binding") or {}
    commit = binding.get("release_commit")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise RuntimeError(
            "live fidelity profiling requires an immutable active-release binding"
        )

    member_roles = dict(runtime.settings.member_roles)
    aliases = _persona_aliases(member_roles)
    service_ids = {
        principal.service_principal_id
        for principal in runtime.settings.service_principals
    }
    persona_counts: dict[str, Counter[str]] = {
        key: Counter({"source_facts": 0, "messages": 0})
        for key in ("adult_primary", "adult_secondary", "dependents", "cos_service")
    }
    persona_body_lengths: dict[str, list[int]] = {key: [] for key in persona_counts}
    persona_threads: dict[str, Counter[str]] = {
        key: Counter() for key in persona_counts
    }
    persona_categories: dict[str, dict[str, Counter[str]]] = {
        key: {
            "message_directions": Counter(),
            "source_shapes": Counter(),
        }
        for key in persona_counts
    }
    counts: Counter[str] = Counter({name: 0 for name in MINIMUM_CARDINALITIES})
    counts["members"] = len(member_roles)
    counts["service_principals"] = len(runtime.settings.service_principals)
    household = runtime.settings.household_mapping or {}
    counts["relationships"] = len(household.get("relationships") or [])
    counts["parties"] = len(household.get("parties") or [])

    adapter_rows = [
        row
        for row in (runtime.settings.adapter_config or {}).get("adapters", [])
        if isinstance(row, dict) and row.get("enabled") is not False
    ]
    counts["accounts"] = len(adapter_rows)
    workspace_shapes = {
        str((row.get("config") or {}).get("workspace") or "personal")
        for row in adapter_rows
    }
    counts["workspaces"] = len(workspace_shapes)

    participants: set[str] = set()
    threads: Counter[str] = Counter()
    subject_lengths: list[int] = []
    body_lengths: list[int] = []
    preview_lengths: list[int] = []
    recipient_counts: list[int] = []
    attachment_counts: list[int] = []
    event_sizes: list[int] = []
    categories: dict[str, Counter[str]] = {
        name: Counter() for name in REQUIRED_CATEGORIES
    }
    candidate_kinds: set[str] = set()
    candidate_sources: set[str] = set()

    async for event in runtime.event_log.read_since():
        event_type = str(event.get("type") or "")
        payload = event.get("payload") or {}
        if not isinstance(payload, Mapping):
            payload = {}
        event_sizes.append(
            len(json.dumps(event, sort_keys=True, default=str).encode("utf-8"))
        )
        source_adapter = str(event.get("source_adapter") or "")
        member_id = str(
            payload.get("member_id") or payload.get("owner_member_id") or ""
        )
        service_id = str(payload.get("service_principal_id") or "")
        actor_id = str(event.get("actor_identity") or "")
        owner_scope = str(event.get("owner_scope") or "")
        scope_member_id = (
            owner_scope.removeprefix("private:")
            if owner_scope.startswith("private:")
            else ""
        )
        persona = (
            "cos_service"
            if service_id in service_ids or actor_id in service_ids
            else aliases.get(member_id)
            or aliases.get(actor_id)
            or aliases.get(scope_member_id)
        )
        external_source = bool(
            source_adapter and source_adapter not in _INTERNAL_SOURCE_ADAPTERS
        )
        if external_source:
            counts["source_facts"] += 1
        if persona and (external_source or persona == "cos_service"):
            persona_counts[persona]["source_facts"] += 1
            shape = _source_shape(event_type)
            if shape:
                persona_categories[persona]["source_shapes"][shape] += 1

        if event_type in {
            "messaging.received",
            "messaging.sent",
            "telephony.sms_received",
        }:
            counts["recent_messages"] += 1
            if persona:
                persona_counts[persona]["messages"] += 1
                persona_body_lengths[persona].append(
                    len(str(payload.get("body_text") or ""))
                )
                persona_categories[persona]["message_directions"][
                    "outbound" if event_type == "messaging.sent" else "inbound"
                ] += 1
            direction = "outbound" if event_type == "messaging.sent" else "inbound"
            categories["message_directions"][direction] += 1
            if direction == "outbound":
                categories["attention_states"]["waiting"] += 1
            subject = str(payload.get("subject") or "")
            body = str(payload.get("body_text") or "")
            subject_lengths.append(len(subject))
            body_lengths.append(len(body))
            preview_lengths.append(min(len(body), 1_500))
            thread_id = str(
                payload.get("thread_id") or payload.get("native_message_id") or ""
            )
            if thread_id:
                threads[thread_id] += 1
                if persona:
                    persona_threads[persona][thread_id] += 1
            raw_recipients = [
                *(payload.get("to_identifiers") or []),
                *(payload.get("cc_identifiers") or []),
                *(payload.get("bcc_identifiers") or []),
            ]
            recipient_counts.append(len(raw_recipients))
            counts["cc_recipients"] += len(payload.get("cc_identifiers") or [])
            counts["bcc_recipients"] += len(payload.get("bcc_identifiers") or [])
            attachments = payload.get("attachments") or []
            attachment_counts.append(len(attachments))
            counts["attachments"] += len(attachments)
            for participant in [
                payload.get("from_identifier"),
                payload.get("to_identifier"),
                *raw_recipients,
            ]:
                if isinstance(participant, str) and participant:
                    participants.add(participant.casefold())
            for value in _message_category(payload):
                categories["provider_categories"][value] += 1
            if payload.get("unread"):
                categories["attention_states"]["unread"] += 1
            if payload.get("archived"):
                categories["attention_states"]["archived"] += 1
            if payload.get("important"):
                categories["attention_states"]["important"] += 1
            if payload.get("references"):
                categories["message_history_shapes"]["quoted"] += 1
            if "forward" in subject.casefold():
                categories["message_history_shapes"]["forwarded"] += 1

        if event_type.startswith("calendar."):
            counts["calendar_facts"] += 1
        if event_type.startswith("task."):
            counts["task_facts"] += 1
        if event_type.startswith("reminder."):
            counts["reminder_facts"] += 1
        if event_type.startswith("list_item."):
            counts["list_facts"] += 1
        if event_type.startswith("recurrence."):
            counts["recurrence_facts"] += 1
        if event_type.startswith(("financial.", "money_flow.")):
            counts["finance_facts"] += 1
        if event_type.startswith("member_knowledge."):
            counts["note_facts"] += 1
        if event_type.startswith(("contact.", "endpoint.")):
            counts["contact_facts"] += 1
            contact_id = str(
                payload.get("contact_record_id") or payload.get("external_id") or ""
            )
            if contact_id:
                participants.add(f"contact:{contact_id}")
            for identifier in payload.get("identifiers") or []:
                if isinstance(identifier, Mapping):
                    endpoint_value = identifier.get(
                        "value_normalized"
                    ) or identifier.get("value")
                    if isinstance(endpoint_value, str) and endpoint_value:
                        participants.add(endpoint_value.casefold())
        if (
            event_type.startswith("chat.")
            or event_type == "openclaw.domain_event.observed"
        ):
            counts["chat_facts"] += 1
        if event_type == "messaging.classified":
            classification = str(payload.get("classification") or "")
            if classification:
                categories["message_natures"][
                    "low_signal" if classification == "noise" else classification
                ] += 1
        if event_type == "messaging.semantic_features_extracted":
            if payload.get("reply_signal") == "required":
                categories["attention_states"]["reply_required"] += 1
            if payload.get("security_code"):
                categories["message_natures"]["security"] += 1
            if payload.get("transactional_due"):
                categories["message_natures"]["transactional"] += 1
        if event_type == "communication.state_changed":
            operation = str(payload.get("operation") or "")
            if operation == "quiet":
                categories["attention_states"]["quieted"] += 1
            if operation == "snooze":
                categories["attention_states"]["snoozed"] += 1
        if event_type == "triage.item.proposed":
            counts["triage_candidates"] += 1
            categories["review_states"]["pending_review"] += 1
            candidate_kinds.add(str(payload.get("target_kind") or "unknown"))
            candidate_sources.add(
                str(
                    payload.get("parent_intent_id")
                    or payload.get("source_event_id")
                    or ""
                )
            )
            if not payload.get("route"):
                counts["enrichment_or_destination_required"] += 1
                categories["destination_states"]["destination_required"] += 1
            else:
                categories["destination_states"]["complete"] += 1
        if event_type == "triage.item.enriched":
            categories["history_actions"]["correction"] += 1
            categories["destination_states"]["complete"] += 1
        if event_type == "triage.item.confirmed":
            categories["history_actions"]["confirmation"] += 1
            categories["review_states"]["confirmed"] += 1
        if event_type == "triage.item.dismissed":
            categories["history_actions"]["dismissal"] += 1
            categories["review_states"]["dismissed"] += 1
        if event_type == "triage.item.superseded":
            categories["history_actions"]["supersession"] += 1
            categories["review_states"]["superseded"] += 1
        if event_type == "triage.feedback.recorded":
            counts["feedback_facts"] += 1
            categories["history_actions"]["learning"] += 1
        if event_type.startswith("triage.policy_rule."):
            counts["rule_history_facts"] += 1
            categories["history_actions"][
                "rule_reversal" if event_type.endswith("revoked") else "rule_creation"
            ] += 1
        if event_type == "skill.call.recorded":
            categories["model_outcomes"]["success"] += 1
        if event_type == "skill.call.failed":
            failure_class = str(payload.get("failure_class") or "")
            categories["model_outcomes"][
                "timeout" if failure_class == "openclaw_timeout" else "unreachable"
            ] += 1
        if event_type.startswith("source.assessment.recovery_"):
            categories["model_outcomes"]["recovery"] += 1
            categories["history_actions"]["replay"] += 1

    counts["message_threads"] = len(threads)
    counts["participants"] = len(participants)
    counts["endpoints"] = len(participants)
    counts["triage_matters"] = len(candidate_sources)
    counts["parties"] = max(counts["parties"], counts["contact_facts"])
    counts["provider_families"] = len(
        {str(row.get("kind") or "") for row in adapter_rows}
    )
    counts["adapter_categories"] = 5
    categories["candidate_kinds"].update(candidate_kinds)
    if any(depth > 1 for depth in threads.values()):
        categories["message_history_shapes"]["multi_message"] += sum(
            depth > 1 for depth in threads.values()
        )

    tails = {name: {key: 0 for key in MINIMUM_TAILS[name]} for name in MINIMUM_TAILS}
    tails.update(
        {
            "subject_chars": _percentiles(subject_lengths),
            "body_chars": _percentiles(body_lengths),
            "preview_chars": _percentiles(preview_lengths),
            "thread_depth": _percentiles(list(threads.values())),
            "recipient_count": _percentiles(recipient_counts),
            "attachment_count": _percentiles(attachment_counts),
            "matter_group_depth": _percentiles(list(threads.values())),
            "response_bytes": _percentiles(event_sizes),
        }
    )

    # Configuration/state dimensions have no household strings.  Count their
    # structural presence so the same small-cell policy applies uniformly.
    categories["persona_kinds"].update(
        {key: SMALL_CELL_THRESHOLD for key in persona_counts}
    )
    categories["adapter_categories"].update(
        {
            "communication": SMALL_CELL_THRESHOLD,
            "external_state_mirror": SMALL_CELL_THRESHOLD,
            "inbound_data": SMALL_CELL_THRESHOLD,
            "personal_knowledge": SMALL_CELL_THRESHOLD,
            "outbound_action": SMALL_CELL_THRESHOLD,
        }
    )
    categories["source_shapes"].update(
        {
            name: max(SMALL_CELL_THRESHOLD, counts.get(f"{name}_facts", 0))
            for name in (
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
            )
        }
    )
    visible_categories: dict[str, list[str]] = {}
    suppressed = 0
    for dimension, dimension_counts in categories.items():
        visible_categories[dimension], count = _category_values(dimension_counts)
        suppressed += count

    ensure_autoloaded()
    event_schemas = {
        event_type: int(registry.latest_version(event_type) or 0)
        for event_type in registry.known_types()
    }
    projection_status = await runtime.projections.status()
    pipeline_status = await runtime.pipelines.status()
    versions_path = runtime.settings.repository_root / "bootstrap" / "versions.json"
    versions = json.loads(versions_path.read_text("utf-8"))
    openclaw = versions.get("openclaw") or {}
    plugin_versions = {
        "administrateme": str(
            (versions.get("administrateme") or {}).get("plugin_package_version")
            or "unknown"
        )
    }
    plugin_versions.update(
        {
            f"{pack.kind}:{pack.pack_id}": pack.version
            for pack in runtime.extensions.packs()
        }
    )
    provider_families = sorted(
        {str(row.get("kind") or "unknown") for row in adapter_rows}
    )
    permission_capabilities = sorted(
        {
            key
            for row in adapter_rows
            for key, enabled in ((row.get("config") or {}).items())
            if key.startswith(("read_", "write_", "enable_")) and bool(enabled)
        }
    )
    return {
        "contract": FIDELITY_CONTRACT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active_release": {"commit": commit, "immutable": True},
        "runtime": {
            "python": platform.python_version(),
            "node": str((versions.get("node") or {}).get("preferred") or "unknown"),
            "openclaw_gateway": f"{openclaw.get('version', 'unknown')}@{openclaw.get('commit', 'unknown')}",
            "plugins": plugin_versions,
        },
        "event_schemas": event_schemas,
        "projections": {
            name: int(status["version"]) for name, status in projection_status.items()
        },
        "pipelines": {
            name: str(status.get("version") or "unknown")
            for name, status in pipeline_status.items()
        },
        "topology": {
            "product_surfaces": [
                "calendar",
                "communications",
                "finance",
                "notes",
                "people",
                "reminders",
                "triage",
            ],
            "capabilities": sorted(runtime.capabilities),
            "adapter_categories": [
                "communication",
                "external_state_mirror",
                "inbound_data",
                "personal_knowledge",
                "outbound_action",
            ],
            "provider_families": provider_families,
            "account_shapes": sorted(
                {
                    f"{str(row.get('kind') or 'unknown')}:{str((row.get('config') or {}).get('workspace') or 'personal')}"
                    for row in adapter_rows
                }
            ),
            "workspace_shapes": sorted(workspace_shapes),
            "permission_capabilities": permission_capabilities,
        },
        "observation": {
            "active": bool(await runtime.observation.is_active()),
            "governance_state": "configured"
            if runtime.guarded_write is not None
            else "unavailable",
            "external_writes_enabled": False,
        },
        "cardinalities": dict(counts),
        "tails": tails,
        "categories": visible_categories,
        "personas": {
            key: {
                "cardinalities": dict(value),
                "tails": {
                    "body_chars": _percentiles(persona_body_lengths[key]),
                    "thread_depth": _percentiles(list(persona_threads[key].values())),
                },
                "categories": {
                    dimension: _category_values(counts_for_dimension)[0]
                    for dimension, counts_for_dimension in persona_categories[
                        key
                    ].items()
                },
            }
            for key, value in persona_counts.items()
        },
        "scale": {
            "event_log_events": len(event_sizes),
            "event_log_bytes": int(
                getattr(runtime.settings.instance.event_log_path.stat(), "st_size", 0)
            ),
            "replay_depth": len(event_sizes),
        },
        "privacy": {
            "small_cell_threshold": SMALL_CELL_THRESHOLD,
            "small_cells_suppressed": suppressed,
            "content_persisted": False,
            "reversible_hashes_persisted": False,
        },
    }


__all__ = ["build_live_fidelity_manifest", "privacy_compare"]
