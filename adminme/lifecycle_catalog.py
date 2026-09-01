"""Reviewed whole-life source, candidate, and lifecycle catalog.

This module is architecture metadata, not household truth.  It makes the
coverage boundary executable without introducing a universal work object or a
second source of truth.  Event payload properties continue to come from the
Pydantic schema registry; projections and providers retain their existing
authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, cast, get_args

from adminme.events.registry import ensure_autoloaded, registry
from adminme.events.schemas.triage import TriageTargetKind


CoverageState = Literal[
    "supported",
    "partial",
    "missing",
    "not_applicable",
    "optional_not_configured",
]
ImplementationState = Literal["exact", "ambiguous", "schema_only", "missing"]


@dataclass(frozen=True, slots=True)
class LifePattern:
    pattern_id: str
    label: str
    examples: tuple[str, ...]
    canonical_kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateChannel:
    channel_id: str
    source_family: str
    input_event_types: tuple[str, ...]
    first_surfaces: tuple[str, ...]
    assessment_owner: str
    candidate_targets: tuple[str, ...]
    coverage: CoverageState
    reason: str


@dataclass(frozen=True, slots=True)
class CandidateCoverageCell:
    channel_id: str
    target_kind: str
    coverage: CoverageState
    reason: str
    process_test: str | None = None


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    action: str
    from_states: tuple[str, ...]
    to_state: str
    event_type: str | None
    command_surface: str
    terminal: bool = False
    implementation: ImplementationState = "exact"
    defect_id: str | None = None
    process_test: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectLifecycle:
    object_kind: str
    authority: str
    projection: str
    first_surfaces: tuple[str, ...]
    management_surfaces: tuple[str, ...]
    history_surfaces: tuple[str, ...]
    transitions: tuple[LifecycleTransition, ...]


LIFE_PATTERNS: tuple[LifePattern, ...] = (
    LifePattern(
        "one_time_action",
        "One-time action",
        ("pay an invoice", "return a form", "make a decision", "buy supplies"),
        ("task",),
    ),
    LifePattern(
        "interpersonal_promise",
        "Interpersonal promise",
        ("send a document", "reply to a friend", "follow up with a teacher"),
        ("commitment",),
    ),
    LifePattern(
        "scheduled_presence",
        "Scheduled presence",
        ("doctor appointment", "school date", "meeting", "pickup window"),
        ("calendar_event",),
    ),
    LifePattern(
        "provider_reminder_or_list",
        "Provider reminder or list work",
        ("Apple Reminder", "Google Task", "grocery list item"),
        ("reminder", "list", "list_item"),
    ),
    LifePattern(
        "repeating_obligation",
        "Repeating obligation",
        ("HVAC filter", "recurring bill", "medication refill", "license renewal"),
        ("recurrence", "task"),
    ),
    LifePattern(
        "ordered_routine",
        "Ordered routine",
        ("morning routine", "bedtime", "leave-for-school checklist"),
        ("routine",),
    ),
    LifePattern(
        "relationship_memory",
        "Relationship memory",
        ("birthday", "anniversary", "employer", "preference", "address"),
        ("relationship_fact", "recurrence"),
    ),
    LifePattern(
        "place_asset_account",
        "Place, asset, or service account",
        ("house", "vehicle", "appliance", "warranty", "utility account"),
        ("place", "asset", "account"),
    ),
    LifePattern(
        "communication_obligation",
        "Communication obligation",
        ("reply requested", "date proposed", "bill received", "promise made"),
        ("interaction", "task", "commitment", "calendar_event", "recurrence"),
    ),
    LifePattern(
        "note_derived_obligation",
        "Note-derived obligation",
        ("meeting notes", "transcript", "shared note", "personal note"),
        ("note", "task", "calendar_event", "reminder", "recurrence"),
    ),
    LifePattern(
        "financial_fact_or_plan",
        "Financial fact or plan",
        ("transaction", "budget", "cash-flow scenario", "savings goal"),
        ("money_flow", "finance_plan"),
    ),
    LifePattern(
        "context_sensitive_cue",
        "Context-sensitive cue",
        ("at home", "at a store", "device state", "weather"),
        ("context_signal", "reminder_cue"),
    ),
    LifePattern(
        "automation_action",
        "Automation action",
        ("turn off a device", "change thermostat", "lock a door"),
        ("automation_action",),
    ),
    LifePattern(
        "notification",
        "Notification",
        ("surface a reminder", "deliver a nudge", "retry a failed delivery"),
        ("notification",),
    ),
    LifePattern(
        "artifact_or_evidence",
        "Artifact or evidence",
        ("receipt", "document", "attachment", "recording"),
        ("artifact", "note"),
    ),
)


_ALL_TRIAGE_TARGETS = tuple(get_args(TriageTargetKind))
_MESSAGE_TARGETS = _ALL_TRIAGE_TARGETS
_NOTE_TARGETS = _ALL_TRIAGE_TARGETS


CANDIDATE_CHANNELS: tuple[CandidateChannel, ...] = (
    CandidateChannel(
        "member_add_for_review",
        "member_intent",
        ("triage.intent.submitted",),
        ("triage",),
        "inline:member-intent",
        _ALL_TRIAGE_TARGETS,
        "supported",
        "Exact member text is durably held before deterministic rules and bounded exact-evidence language extraction; all results remain human-gated.",
    ),
    CandidateChannel(
        "openclaw_add_for_review",
        "member_intent",
        ("triage.intent.submitted",),
        ("triage",),
        "inline:member-intent",
        _ALL_TRIAGE_TARGETS,
        "supported",
        "The typed OpenClaw tool records the same exact member-intent fact; generic chat remains non-canonical.",
    ),
    CandidateChannel(
        "cos_direct_gmail",
        "service_direct_intake",
        ("messaging.received", "direct_intake.routed"),
        ("triage",),
        "inline:direct-intake-exact-route",
        _ALL_TRIAGE_TARGETS,
        "supported",
        "An exact reviewed authenticated-metadata route may create one member-private primary intent cue; its text remains incomplete semantics and every derived target remains human-gated.",
    ),
    CandidateChannel(
        "gmail_message",
        "communications",
        ("messaging.received", "messaging.sent"),
        ("comms", "triage"),
        "pipeline:noise_filtering",
        _MESSAGE_TARGETS,
        "supported",
        "The bounded message skill exposes all reviewed Triage target kinds; fixed code still requires exact evidence, allowlisted fields, private scope, and human confirmation.",
    ),
    CandidateChannel(
        "sms_message",
        "communications",
        ("telephony.sms_received",),
        ("comms", "triage"),
        "pipeline:noise_filtering",
        _MESSAGE_TARGETS,
        "optional_not_configured",
        "The shared pipeline exists, but the family bridge/provider is not live-proven.",
    ),
    CandidateChannel(
        "member_note",
        "notes",
        (
            "member_knowledge.note_added",
            "member_knowledge.note_updated",
            "member_knowledge.note_corrected",
        ),
        ("notes", "triage"),
        "pipeline:member_knowledge_indexing",
        _NOTE_TARGETS,
        "supported",
        "The bounded note skill exposes all reviewed Triage target kinds; fixed code still requires exact note evidence, allowlisted fields, private scope, and human confirmation.",
    ),
    CandidateChannel(
        "calendar_source_fact",
        "calendaring",
        ("calendar.event_added", "calendar.event_updated", "calendar.event_deleted"),
        ("calendar", "today"),
        "not_applicable:provider_truth",
        (),
        "not_applicable",
        "An already-official provider Calendar fact bypasses Triage; only an explicit source-linked derivative request may create a candidate.",
    ),
    CandidateChannel(
        "provider_reminder_source_fact",
        "reminders_tasks",
        (
            "list_item.added",
            "list_item.updated",
            "list_item.completed",
            "list_item.uncompleted",
            "list_item.deleted",
        ),
        ("reminders", "today"),
        "not_applicable:provider_truth",
        (),
        "not_applicable",
        "An already-official provider item remains provider truth and is not re-proposed.",
    ),
    CandidateChannel(
        "contact_source_fact",
        "people",
        ("contact.record.observed", "contact.record.deleted", "contact.group.observed"),
        ("people",),
        "not_applicable:curated_provider_truth",
        (),
        "not_applicable",
        "Curated contacts may deterministically qualify Parties; ambiguous identifiers use typed review evidence rather than language routing.",
    ),
    CandidateChannel(
        "plaid_source_fact",
        "financial",
        (
            "financial.account_observed",
            "money_flow.provider_observed",
            "money_flow.provider_removed",
        ),
        ("finance",),
        "pipeline:deterministic_source_candidates",
        ("recurrence",),
        "supported",
        "Settled exact-payee observations may propose one reviewed weekly, monthly, or annual recurrence after a closed cadence rule matches; privileged data never enters a model.",
    ),
    CandidateChannel(
        "home_assistant_raw_fact",
        "automation_context",
        ("ha.state_changed",),
        ("reminders", "today"),
        "not_applicable:unbound_provider_state",
        (),
        "not_applicable",
        "Raw Home Assistant state has no member or work semantics and cannot create a candidate.",
    ),
    CandidateChannel(
        "reviewed_context_rule",
        "automation_context",
        ("context.signal.observed",),
        ("reminders", "today", "triage"),
        "pipeline:deterministic_source_candidates",
        ("task", "reminder", "recurrence"),
        "supported",
        "Only a member-bound context fact carrying an exact reviewed rule snapshot may produce a private human-gated proposal; unmatched signals receive a terminal no-candidate assessment.",
    ),
)


_SUPPORTED_CHANNEL_TESTS = {
    "member_add_for_review": (
        "tests/acceptance/test_source_assurance_vertical_slice.py::"
        "test_every_source_reaches_an_audited_disposition_in_production_composition"
    ),
    "openclaw_add_for_review": (
        "tests/acceptance/test_event_lifecycle_matrix.py::"
        "test_openclaw_add_for_review_reaches_every_triage_target"
    ),
    "cos_direct_gmail": (
        "tests/acceptance/test_service_owned_cos_sources_direct_intake.py::"
        "test_g05_direct_routes_isolate_members_fail_closed_and_replay"
    ),
    "gmail_message": (
        "tests/acceptance/test_event_lifecycle_matrix.py::"
        "test_message_and_note_sources_reach_every_triage_target"
    ),
    "member_note": (
        "tests/acceptance/test_event_lifecycle_matrix.py::"
        "test_message_and_note_sources_reach_every_triage_target"
    ),
    "plaid_source_fact": (
        "tests/acceptance/test_event_lifecycle_matrix.py::"
        "test_structured_finance_and_reviewed_context_rules_only_propose_to_triage"
    ),
    "reviewed_context_rule": (
        "tests/acceptance/test_event_lifecycle_matrix.py::"
        "test_structured_finance_and_reviewed_context_rules_only_propose_to_triage"
    ),
}


def candidate_coverage_matrix() -> list[CandidateCoverageCell]:
    """Return every reviewed channel × Triage-target decision.

    Empty cells are forbidden. Canonical provider facts are deliberately
    not re-proposed, while the two known derivation gaps remain explicit.
    """

    rows: list[CandidateCoverageCell] = []
    for channel in CANDIDATE_CHANNELS:
        for target in _ALL_TRIAGE_TARGETS:
            if target in channel.candidate_targets:
                rows.append(
                    CandidateCoverageCell(
                        channel.channel_id,
                        target,
                        channel.coverage,
                        channel.reason,
                        _SUPPORTED_CHANNEL_TESTS.get(channel.channel_id)
                        if channel.coverage == "supported"
                        else None,
                    )
                )
                continue
            if channel.channel_id == "plaid_source_fact" and target in {
                "task",
                "reminder",
            }:
                coverage: CoverageState = "not_applicable"
                reason = "A settled transaction proves money movement, not unpaid work; Task or Reminder creation requires another grounded source or an explicit member request."
            else:
                coverage = "not_applicable"
                reason = "This channel records canonical provider/context truth; automatic derivation of this target would duplicate authority or lacks grounded semantics."
            rows.append(
                CandidateCoverageCell(
                    channel.channel_id,
                    target,
                    coverage,
                    reason,
                )
            )
    return rows


OBJECT_LIFECYCLES: tuple[ObjectLifecycle, ...] = (
    ObjectLifecycle(
        "task",
        "administrateme_event_log",
        "tasks",
        ("triage", "reminders"),
        ("reminders", "today", "scoreboard"),
        ("reminders", "scoreboard", "activity"),
        (
            LifecycleTransition(
                "create", (), "open", "task.created", "triage/reminders"
            ),
            LifecycleTransition(
                "edit",
                ("open", "deferred", "blocked"),
                "unchanged",
                "task.updated",
                "reminders",
            ),
            LifecycleTransition(
                "defer",
                ("open", "blocked"),
                "deferred",
                "task.deferred",
                "reminders/today",
            ),
            LifecycleTransition(
                "snooze",
                ("open", "blocked"),
                "deferred",
                "task.snoozed",
                "reminders/today",
            ),
            LifecycleTransition(
                "block",
                ("open",),
                "blocked",
                "task.blocked",
                "reminders/today",
            ),
            LifecycleTransition(
                "unblock",
                ("blocked",),
                "open",
                "task.unblocked",
                "reminders/today",
            ),
            LifecycleTransition(
                "complete",
                ("open", "deferred", "blocked"),
                "completed",
                "task.completed",
                "reminders/today",
                terminal=True,
            ),
            LifecycleTransition(
                "abandon",
                ("open", "deferred", "blocked"),
                "abandoned",
                "task.cancelled",
                "reminders",
                terminal=True,
            ),
            LifecycleTransition(
                "reopen",
                ("completed", "abandoned"),
                "open",
                "task.reopened",
                "reminders",
            ),
        ),
    ),
    ObjectLifecycle(
        "commitment",
        "administrateme_event_log",
        "commitments",
        ("triage", "comms"),
        ("today", "comms"),
        ("activity", "scoreboard"),
        tuple(
            LifecycleTransition(
                action,
                states,
                target,
                event_type,
                surface,
                terminal,
                cast(ImplementationState, implementation),
                defect,
            )
            for action, states, target, event_type, surface, terminal, implementation, defect in (
                (
                    "propose",
                    (),
                    "proposed",
                    "commitment.proposed",
                    "triage",
                    False,
                    "exact",
                    None,
                ),
                (
                    "confirm",
                    ("proposed",),
                    "open",
                    "commitment.confirmed",
                    "triage",
                    False,
                    "exact",
                    None,
                ),
                (
                    "dismiss",
                    ("proposed",),
                    "dismissed",
                    "commitment.dismissed",
                    "triage",
                    True,
                    "exact",
                    None,
                ),
                (
                    "edit",
                    ("proposed", "open", "deferred", "blocked"),
                    "unchanged",
                    "commitment.edited",
                    "triage/comms",
                    False,
                    "exact",
                    None,
                ),
                (
                    "snooze",
                    ("open",),
                    "snoozed",
                    "commitment.snoozed",
                    "today",
                    False,
                    "exact",
                    None,
                ),
                (
                    "defer",
                    ("open", "snoozed"),
                    "deferred",
                    "commitment.deferred",
                    "today",
                    False,
                    "exact",
                    None,
                ),
                (
                    "block",
                    ("open",),
                    "blocked",
                    "commitment.blocked",
                    "today",
                    False,
                    "exact",
                    None,
                ),
                (
                    "unblock",
                    ("blocked",),
                    "open",
                    "commitment.unblocked",
                    "today",
                    False,
                    "exact",
                    None,
                ),
                (
                    "complete",
                    ("open", "deferred", "blocked"),
                    "completed",
                    "commitment.completed",
                    "today",
                    True,
                    "exact",
                    None,
                ),
                (
                    "cancel",
                    ("open", "deferred", "blocked"),
                    "cancelled",
                    "commitment.cancelled",
                    "today",
                    True,
                    "exact",
                    None,
                ),
                (
                    "delegate",
                    ("open",),
                    "delegated",
                    "commitment.delegated",
                    "today",
                    True,
                    "exact",
                    None,
                ),
                (
                    "expire",
                    ("proposed",),
                    "expired",
                    "commitment.expired",
                    "system",
                    True,
                    "exact",
                    None,
                ),
            )
        ),
    ),
    ObjectLifecycle(
        "recurrence",
        "administrateme_event_log",
        "recurrences",
        ("triage", "reminders"),
        ("reminders", "today", "scoreboard"),
        ("activity", "scoreboard"),
        (
            LifecycleTransition(
                "add",
                (),
                "active",
                "recurrence.added",
                "triage/reminders/workbook",
            ),
            LifecycleTransition(
                "edit",
                ("active", "paused"),
                "unchanged",
                "recurrence.updated",
                "reminders/workbook",
            ),
            LifecycleTransition(
                "complete_occurrence",
                ("active",),
                "active_next_occurrence",
                "recurrence.completed",
                "today",
            ),
            LifecycleTransition(
                "pause",
                ("active",),
                "paused",
                "recurrence.paused",
                "today",
            ),
            LifecycleTransition(
                "resume",
                ("paused",),
                "active",
                "recurrence.resumed",
                "today",
            ),
            LifecycleTransition(
                "end",
                ("active", "paused"),
                "ended",
                "recurrence.ended",
                "today",
                terminal=True,
            ),
        ),
    ),
    ObjectLifecycle(
        "routine",
        "administrateme_event_log",
        "recurrences",
        ("reminders", "today"),
        ("reminders", "today", "scoreboard"),
        ("activity", "scoreboard"),
        (
            LifecycleTransition(
                "define",
                (),
                "active",
                "routine.defined",
                "reminders",
            ),
            LifecycleTransition(
                "complete_step",
                ("active_occurrence",),
                "next_step",
                "routine.step_outcome_recorded",
                "today",
            ),
            LifecycleTransition(
                "skip_step",
                ("active_occurrence",),
                "next_step",
                "routine.step_outcome_recorded",
                "today",
            ),
            LifecycleTransition(
                "pause",
                ("active",),
                "paused",
                "routine.paused",
                "today",
            ),
            LifecycleTransition(
                "resume",
                ("paused",),
                "active",
                "routine.resumed",
                "today",
            ),
            LifecycleTransition(
                "end",
                ("active", "paused"),
                "ended",
                "routine.ended",
                "today",
                terminal=True,
            ),
        ),
    ),
    ObjectLifecycle(
        "provider_calendar_event",
        "external_provider",
        "calendars",
        ("calendar", "triage"),
        ("calendar", "today"),
        ("calendar", "activity"),
        (
            LifecycleTransition(
                "observe_add", (), "scheduled", "calendar.event_added", "provider"
            ),
            LifecycleTransition(
                "observe_update",
                ("scheduled",),
                "scheduled",
                "calendar.event_updated",
                "provider",
            ),
            LifecycleTransition(
                "observe_delete",
                ("scheduled",),
                "deleted",
                "calendar.event_deleted",
                "provider",
                terminal=True,
            ),
            LifecycleTransition(
                "attend_or_miss",
                ("scheduled",),
                "outcome_recorded",
                "calendar.event_outcome_recorded",
                "calendar/today",
                terminal=True,
            ),
            LifecycleTransition(
                "request_provider_change",
                ("scheduled",),
                "pending_provider",
                "external_sync.write_requested",
                "triage/calendar",
            ),
            LifecycleTransition(
                "provider_result",
                ("pending_provider",),
                "reconcile_or_fail",
                "external_sync.write_completed",
                "calendar",
            ),
        ),
    ),
    ObjectLifecycle(
        "provider_list_item",
        "external_provider",
        "lists",
        ("reminders", "triage"),
        ("reminders", "today"),
        ("reminders", "activity"),
        (
            LifecycleTransition(
                "observe_add", (), "open", "list_item.added", "provider"
            ),
            LifecycleTransition(
                "observe_update",
                ("open", "completed"),
                "unchanged",
                "list_item.updated",
                "provider",
            ),
            LifecycleTransition(
                "observe_complete",
                ("open",),
                "completed",
                "list_item.completed",
                "provider",
                terminal=True,
            ),
            LifecycleTransition(
                "observe_uncomplete",
                ("completed",),
                "open",
                "list_item.uncompleted",
                "provider",
            ),
            LifecycleTransition(
                "observe_delete",
                ("open", "completed"),
                "deleted",
                "list_item.deleted",
                "provider",
                terminal=True,
            ),
            LifecycleTransition(
                "request_provider_change",
                ("open", "completed"),
                "pending_provider",
                "external_sync.write_requested",
                "triage/reminders",
            ),
            LifecycleTransition(
                "provider_result",
                ("pending_provider",),
                "reconcile_or_fail",
                "external_sync.write_completed",
                "reminders",
            ),
        ),
    ),
    ObjectLifecycle(
        "provider_list",
        "external_provider",
        "lists",
        ("reminders", "triage"),
        ("reminders",),
        ("reminders", "activity"),
        (
            LifecycleTransition(
                "observe_create", (), "active", "list.created", "provider"
            ),
            LifecycleTransition(
                "observe_update", ("active",), "active", "list.updated", "provider"
            ),
            LifecycleTransition(
                "observe_delete",
                ("active",),
                "deleted",
                "list.deleted",
                "provider",
                terminal=True,
            ),
            LifecycleTransition(
                "request_provider_change",
                ("active",),
                "pending_provider",
                "external_sync.write_requested",
                "triage/reminders",
            ),
        ),
    ),
    ObjectLifecycle(
        "party",
        "administrateme_event_log",
        "parties",
        ("people", "triage"),
        ("people",),
        ("people", "activity"),
        (
            LifecycleTransition(
                "create", (), "active", "party.created", "triage/people"
            ),
            LifecycleTransition(
                "edit", ("active",), "active", "party.updated", "people"
            ),
            LifecycleTransition(
                "qualify", ("active",), "active", "party.qualification_set", "people"
            ),
            LifecycleTransition(
                "archive",
                ("active",),
                "archived",
                "party.archived",
                "people",
                terminal=True,
            ),
            LifecycleTransition(
                "restore", ("archived",), "active", "party.restored", "people"
            ),
            LifecycleTransition(
                "merge", ("active",), "merged", "party.merged", "people", terminal=True
            ),
            LifecycleTransition(
                "split", ("merged",), "active", "party.split", "people"
            ),
        ),
    ),
    ObjectLifecycle(
        "relationship",
        "administrateme_event_log",
        "parties",
        ("people", "triage"),
        ("people",),
        ("people", "activity"),
        (
            LifecycleTransition(
                "add", (), "active", "relationship.added", "triage/people"
            ),
            LifecycleTransition(
                "edit", ("active",), "active", "relationship.updated", "people"
            ),
            LifecycleTransition(
                "end",
                ("active",),
                "ended",
                "relationship.ended",
                "people",
                terminal=True,
            ),
        ),
    ),
    ObjectLifecycle(
        "relationship_fact",
        "administrateme_event_log",
        "parties",
        ("people", "triage"),
        ("people",),
        ("people", "activity"),
        (
            LifecycleTransition(
                "confirm", (), "active", "relationship_fact.confirmed", "triage/people"
            ),
            LifecycleTransition(
                "revoke",
                ("active",),
                "revoked",
                "relationship_fact.revoked",
                "people",
                terminal=True,
            ),
        ),
    ),
    ObjectLifecycle(
        "note",
        "external_provider_or_member_source",
        "member_knowledge",
        ("notes",),
        ("notes",),
        ("notes", "triage", "activity"),
        (
            LifecycleTransition(
                "observe_add",
                (),
                "active",
                "member_knowledge.note_added",
                "provider/bridge",
            ),
            LifecycleTransition(
                "observe_update",
                ("active",),
                "active",
                "member_knowledge.note_updated",
                "provider/bridge",
            ),
            LifecycleTransition(
                "correct",
                ("active",),
                "active",
                "member_knowledge.note_corrected",
                "notes",
            ),
            LifecycleTransition(
                "observe_delete",
                ("active",),
                "deleted",
                "member_knowledge.note_deleted",
                "provider/bridge",
                terminal=True,
            ),
            LifecycleTransition(
                "grant",
                ("active",),
                "active_shared",
                "member_knowledge.access_granted",
                "notes",
            ),
            LifecycleTransition(
                "revoke_grant",
                ("active_shared",),
                "active",
                "member_knowledge.access_revoked",
                "notes",
            ),
        ),
    ),
    ObjectLifecycle(
        "interaction",
        "external_provider",
        "interactions",
        ("comms",),
        ("comms", "today", "triage"),
        ("comms", "activity"),
        (
            LifecycleTransition(
                "receive", (), "received", "messaging.received", "provider"
            ),
            LifecycleTransition(
                "send_observed", (), "sent", "messaging.sent", "provider"
            ),
            LifecycleTransition(
                "snooze_or_quiet",
                ("received",),
                "locally_held",
                "communication.state_changed",
                "comms",
            ),
            LifecycleTransition(
                "request_provider_action",
                ("received", "sent"),
                "pending_provider",
                "external_sync.write_requested",
                "comms",
            ),
            LifecycleTransition(
                "provider_complete",
                ("pending_provider",),
                "reconciled",
                "external_sync.write_completed",
                "comms",
            ),
            LifecycleTransition(
                "provider_fail",
                ("pending_provider",),
                "failed",
                "external_sync.write_failed",
                "comms",
            ),
        ),
    ),
    ObjectLifecycle(
        "money_flow",
        "external_provider_or_reviewed_workbook",
        "money",
        ("finance",),
        ("finance",),
        ("finance", "activity"),
        (
            LifecycleTransition(
                "observe", (), "current", "money_flow.provider_observed", "provider"
            ),
            LifecycleTransition(
                "observe_remove",
                ("current",),
                "removed",
                "money_flow.provider_removed",
                "provider",
                terminal=True,
            ),
            LifecycleTransition(
                "add_manual",
                (),
                "current",
                "money_flow.manually_added",
                "finance_workbook",
            ),
            LifecycleTransition(
                "correct_manual",
                ("current",),
                "current",
                "money_flow.manually_updated",
                "finance_workbook",
            ),
            LifecycleTransition(
                "delete_manual",
                ("current",),
                "deleted",
                "money_flow.manually_deleted",
                "finance_workbook",
                terminal=True,
            ),
            LifecycleTransition(
                "classify",
                ("current",),
                "current",
                "money_flow.classification_set",
                "finance_workbook",
            ),
        ),
    ),
    ObjectLifecycle(
        "finance_plan",
        "reviewed_finance_workbook",
        "money",
        ("finance",),
        ("finance",),
        ("finance", "activity"),
        (
            LifecycleTransition(
                "set_assumption",
                (),
                "active",
                "finance.assumption_set",
                "finance_workbook",
            ),
            LifecycleTransition(
                "set_budget", (), "active", "finance.budget_set", "finance_workbook"
            ),
            LifecycleTransition(
                "set_scenario",
                (),
                "active_or_inactive",
                "finance.scenario_set",
                "finance_workbook",
            ),
        ),
    ),
    ObjectLifecycle(
        "finance_goal",
        "reviewed_finance_workbook_and_event_log",
        "money",
        ("finance",),
        ("finance", "finance_workbook"),
        ("finance", "activity"),
        (
            LifecycleTransition(
                "define_or_update",
                (),
                "active_or_preserved",
                "finance.goal_set",
                "finance/finance_workbook",
            ),
            LifecycleTransition(
                "pause",
                ("active",),
                "paused",
                "finance.goal_paused",
                "finance/finance_workbook",
            ),
            LifecycleTransition(
                "complete",
                ("active",),
                "complete",
                "finance.goal_completed",
                "finance/finance_workbook",
                terminal=True,
            ),
            LifecycleTransition(
                "reopen",
                ("paused", "complete"),
                "active",
                "finance.goal_reopened",
                "finance/finance_workbook",
            ),
        ),
    ),
    ObjectLifecycle(
        "place",
        "administrateme_event_log",
        "places_assets_accounts",
        ("workbook",),
        ("people", "settings"),
        ("activity",),
        (
            LifecycleTransition("add", (), "active", "place.added", "workbook"),
            LifecycleTransition(
                "edit", ("active",), "active", "place.updated", "workbook"
            ),
            LifecycleTransition(
                "archive",
                ("active",),
                "archived",
                "place.archived",
                "settings",
                terminal=True,
            ),
            LifecycleTransition(
                "restore", ("archived",), "active", "place.restored", "settings"
            ),
        ),
    ),
    ObjectLifecycle(
        "asset",
        "administrateme_event_log",
        "places_assets_accounts",
        ("workbook",),
        ("settings", "today"),
        ("activity",),
        (
            LifecycleTransition("add", (), "active", "asset.added", "workbook"),
            LifecycleTransition(
                "edit", ("active",), "active", "asset.updated", "workbook"
            ),
            LifecycleTransition(
                "archive",
                ("active",),
                "archived",
                "asset.archived",
                "settings",
                terminal=True,
            ),
            LifecycleTransition(
                "restore", ("archived",), "active", "asset.restored", "settings"
            ),
        ),
    ),
    ObjectLifecycle(
        "account",
        "administrateme_event_log_or_external_provider",
        "places_assets_accounts/money",
        ("workbook", "finance"),
        ("finance", "settings"),
        ("finance", "activity"),
        (
            LifecycleTransition("add", (), "active", "account.added", "workbook"),
            LifecycleTransition(
                "edit", ("active",), "active", "account.updated", "workbook"
            ),
            LifecycleTransition(
                "observe_financial",
                (),
                "current",
                "financial.account_observed",
                "provider",
            ),
            LifecycleTransition(
                "archive",
                ("active",),
                "archived",
                "account.archived",
                "settings",
                terminal=True,
            ),
            LifecycleTransition(
                "restore",
                ("archived",),
                "active",
                "account.restored",
                "settings",
            ),
        ),
    ),
    ObjectLifecycle(
        "context_signal",
        "source_observation",
        "lists",
        ("reminders",),
        ("reminders", "today"),
        ("evidence",),
        (
            LifecycleTransition(
                "observe",
                (),
                "active_until_expiry",
                "context.signal.observed",
                "adapter",
            ),
        ),
    ),
    ObjectLifecycle(
        "reminder_cue",
        "administrateme_event_log",
        "lists",
        ("reminders",),
        ("reminders",),
        ("evidence",),
        (
            LifecycleTransition(
                "configure", (), "active", "reminder.cue.configured", "reminders"
            ),
            LifecycleTransition(
                "clear",
                ("active",),
                "cleared",
                "reminder.cue.cleared",
                "reminders",
                terminal=True,
            ),
        ),
    ),
    ObjectLifecycle(
        "automation_action",
        "external_provider_with_governed_command",
        "event_evidence",
        ("automation",),
        ("automation", "settings"),
        ("activity", "evidence"),
        (
            LifecycleTransition(
                "execute",
                ("approved",),
                "completed",
                "action.executed",
                "automation",
                terminal=True,
            ),
            LifecycleTransition(
                "fail",
                ("approved",),
                "failed",
                "action.failed",
                "automation",
                terminal=True,
            ),
        ),
    ),
    ObjectLifecycle(
        "notification",
        "administrateme_event_log",
        "event_evidence",
        ("today", "system"),
        ("today", "settings"),
        ("activity", "evidence"),
        (
            LifecycleTransition(
                "raise_signal",
                (),
                "pending_decision",
                "notification.signal.raised",
                "pipeline",
            ),
            LifecycleTransition(
                "decide",
                ("pending_decision",),
                "selected_or_suppressed",
                "notification.decision.made",
                "deterministic_policy",
            ),
            LifecycleTransition(
                "record_delivery",
                ("selected_or_suppressed",),
                "terminal_delivery",
                "notification.delivery.result",
                "openclaw",
                terminal=True,
            ),
        ),
    ),
    ObjectLifecycle(
        "artifact",
        "external_or_member_source",
        "artifacts",
        ("notes", "comms"),
        ("notes", "comms"),
        ("evidence",),
        (
            LifecycleTransition(
                "receive", (), "retained", "artifact.received", "adapter"
            ),
        ),
    ),
)


OBJECT_KIND_ALIASES: dict[str, str] = {
    "task": "task",
    "commitment": "commitment",
    "calendar_event": "provider_calendar_event",
    "reminder": "provider_list_item",
    "list": "provider_list",
    "list_item": "provider_list_item",
    "recurrence": "recurrence",
    "routine": "routine",
    "party_identifier": "party",
    "party_candidate": "party",
    "party_relationship": "relationship",
    "relationship_fact": "relationship_fact",
    "party": "party",
    "relationship": "relationship",
    "note": "note",
    "interaction": "interaction",
    "money_flow": "money_flow",
    "finance_plan": "finance_plan",
    "finance_goal": "finance_goal",
    "place": "place",
    "asset": "asset",
    "account": "account",
    "context_signal": "context_signal",
    "reminder_cue": "reminder_cue",
    "automation_action": "automation_action",
    "notification": "notification",
    "artifact": "artifact",
}


_TRANSITION_PROCESS_TEST_BY_OBJECT: dict[str, str] = {
    "task": "tests/acceptance/test_gate7_household_experience.py::test_today_is_one_deterministic_brief_with_typed_member_actions",
    "commitment": "tests/acceptance/test_gate7_household_experience.py::test_confirmed_commitments_can_be_cancelled_or_delegated_without_reassigning",
    "recurrence": "tests/acceptance/test_event_lifecycle_matrix.py::test_member_defines_and_edits_recurrence_and_ordered_routine",
    "routine": "tests/acceptance/test_event_lifecycle_matrix.py::test_member_defines_and_edits_recurrence_and_ordered_routine",
    "provider_calendar_event": "tests/acceptance/test_gate7_household_experience.py::test_calendar_onboarding_drives_calendar_today_and_triage_routes",
    "provider_list_item": "tests/acceptance/test_gate7_household_experience.py::test_reminders_surface_replays_context_and_refuses_unconfigured_writes",
    "provider_list": "tests/acceptance/test_gate7_household_experience.py::test_reminders_surface_replays_context_and_refuses_unconfigured_writes",
    "party": "tests/acceptance/test_people_vertical_slice.py::test_ac30_people_is_private_qualified_replayable_and_human_gated",
    "relationship": "tests/acceptance/test_people_vertical_slice.py::test_ac30_people_is_private_qualified_replayable_and_human_gated",
    "relationship_fact": "tests/acceptance/test_people_vertical_slice.py::test_ac30_people_is_private_qualified_replayable_and_human_gated",
    "note": "tests/acceptance/test_member_bridge_knowledge_vertical_slice.py::test_member_bridge_knowledge_is_private_searchable_and_replayable",
    "interaction": "tests/acceptance/test_gate7_household_experience.py::test_comms_is_member_isolated_deterministic_and_locally_controllable",
    "money_flow": "tests/acceptance/test_gate7_household_experience.py::test_ac32_finance_workbook_to_cockpit_replay_and_member_isolation",
    "finance_plan": "tests/acceptance/test_gate7_household_experience.py::test_ac32_finance_workbook_to_cockpit_replay_and_member_isolation",
    "finance_goal": "tests/acceptance/test_gate7_household_experience.py::test_ac32_finance_workbook_to_cockpit_replay_and_member_isolation",
    "place": "tests/acceptance/test_gate7_household_experience.py::test_ac31_settings_is_a_truthful_member_control_plane",
    "asset": "tests/acceptance/test_gate7_household_experience.py::test_ac31_settings_is_a_truthful_member_control_plane",
    "account": "tests/acceptance/test_gate7_household_experience.py::test_ac31_settings_is_a_truthful_member_control_plane",
    "context_signal": "tests/acceptance/test_event_lifecycle_matrix.py::test_structured_finance_and_reviewed_context_rules_only_propose_to_triage",
    "reminder_cue": "tests/acceptance/test_gate7_household_experience.py::test_reminders_surface_replays_context_and_refuses_unconfigured_writes",
    "automation_action": "tests/acceptance/test_openclaw_native_seam.py::test_lifecycle_and_approval_are_durable_scope_filtered_and_exactly_once",
    "notification": "tests/acceptance/test_openclaw_native_seam.py::test_actor_boundary_observation_bridge_model_and_dashboard_scope",
    "artifact": "tests/acceptance/test_member_bridge_knowledge_vertical_slice.py::test_every_retained_member_source_reaches_the_real_sink",
}


_EVENT_FAMILY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("triage.", "triage"),
    ("task.", "task"),
    ("commitment.", "commitment"),
    ("recurrence.", "recurrence"),
    ("routine.", "routine"),
    ("calendar.", "calendar"),
    ("list_item.", "provider_list_item"),
    ("list.", "provider_list"),
    ("reminder.", "reminder_context_or_delivery"),
    ("messaging.", "communication"),
    ("communication.", "communication"),
    ("interaction.", "communication"),
    ("telephony.", "communication"),
    ("party.", "people"),
    ("contact.", "people"),
    ("endpoint.", "people"),
    ("identifier.", "people"),
    ("identity.", "people"),
    ("membership.", "people"),
    ("relationship.", "people"),
    ("relationship_fact.", "people"),
    ("member_relationship.", "people"),
    ("member_knowledge.", "notes"),
    ("artifact.", "artifact"),
    ("embedding.", "derived_index"),
    ("money_flow.", "finance"),
    ("financial.", "finance"),
    ("finance.", "finance"),
    ("place.", "operations"),
    ("asset.", "operations"),
    ("account.", "operations"),
    ("context.", "context"),
    ("ha.", "automation"),
    ("action.", "automation"),
    ("notification.", "notification"),
    ("external_sync.", "external_write"),
    ("member.provider_action.", "external_write"),
    ("external.", "external_write"),
    ("observation.", "governance"),
    ("write.", "governance"),
    ("review_request", "governance"),
    ("adapter.", "adapter_runtime"),
    ("skill.", "language_evidence"),
    ("openclaw.", "openclaw"),
    ("household.", "openclaw_compatibility"),
    ("settings.", "settings"),
    ("service.delegation.", "service_delegation"),
    ("direct_intake.", "source_routing"),
    ("temporary_surface.", "temporary_surface"),
    ("xlsx.", "workbook_runtime"),
    ("runtime.", "runtime"),
    ("source.assessment.", "source_assurance"),
    ("source.policy_v2.", "source_policy"),
    ("bridge.", "bridge"),
    ("reward.", "outcome_feedback"),
    ("digest.", "proactive_program"),
    ("paralysis.", "proactive_program"),
    ("crm.", "proactive_program"),
    ("custody_brief.", "proactive_program"),
)


def event_family(event_type: str) -> str | None:
    """Return the reviewed semantic family for a technical event type."""

    for prefix, family in _EVENT_FAMILY_PREFIXES:
        if event_type == prefix or event_type.startswith(prefix):
            return family
    return None


def registered_event_catalog() -> list[dict[str, Any]]:
    """Return every schema version and its JSON-schema property contract."""

    ensure_autoloaded()
    items: list[dict[str, Any]] = []
    for event_type in registry.known_types():
        versions = sorted(
            version
            for registered_type, version in registry._by_key
            if registered_type == event_type
        )
        for version in versions:
            model = registry.get(event_type, version)
            assert model is not None
            # Low-level tests register intentionally fake schemas on the
            # process-global registry. They are fixtures, not release event
            # contracts, and must not contaminate generated evidence when the
            # complete suite shares one interpreter.
            if model.__module__.startswith(("test_", "tests.")):
                continue
            family = event_family(event_type)
            schema = model.model_json_schema()
            items.append(
                {
                    "event_type": event_type,
                    "schema_version": version,
                    "family": family,
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                    "additional_properties": schema.get("additionalProperties"),
                }
            )
    return items


def _lifecycle_document(lifecycle: ObjectLifecycle) -> dict[str, Any]:
    document = asdict(lifecycle)
    default_process_test = _TRANSITION_PROCESS_TEST_BY_OBJECT.get(lifecycle.object_kind)
    document["transitions"] = [
        {
            **asdict(transition),
            "process_test": transition.process_test or default_process_test,
        }
        for transition in lifecycle.transitions
    ]
    return document


def catalog_document() -> dict[str, Any]:
    """Return the complete machine-readable catalog with current gaps visible."""

    events = registered_event_catalog()
    candidate_cells = candidate_coverage_matrix()
    return {
        "contract": "whole-life-event-lifecycle-catalog-v1",
        "life_patterns": [asdict(value) for value in LIFE_PATTERNS],
        "triage_target_kinds": list(_ALL_TRIAGE_TARGETS),
        "object_kind_aliases": OBJECT_KIND_ALIASES,
        "candidate_channels": [asdict(value) for value in CANDIDATE_CHANNELS],
        "source_candidate_coverage": [asdict(value) for value in candidate_cells],
        "object_lifecycles": [
            _lifecycle_document(value) for value in OBJECT_LIFECYCLES
        ],
        "technical_events": events,
        "summary": {
            "life_patterns": len(LIFE_PATTERNS),
            "candidate_channels": len(CANDIDATE_CHANNELS),
            "source_candidate_cells": len(candidate_cells),
            "object_lifecycles": len(OBJECT_LIFECYCLES),
            "technical_event_types": len({item["event_type"] for item in events}),
            "technical_event_schemas": len(events),
            "uncataloged_event_types": sorted(
                {str(item["event_type"]) for item in events if item["family"] is None}
            ),
            "missing_source_candidate_cells": [
                f"{cell.channel_id}:{cell.target_kind}"
                for cell in candidate_cells
                if cell.coverage == "missing"
            ],
            "unproved_supported_source_candidate_cells": [
                f"{cell.channel_id}:{cell.target_kind}"
                for cell in candidate_cells
                if cell.coverage == "supported" and cell.process_test is None
            ],
            "missing_lifecycle_transitions": [
                f"{lifecycle.object_kind}:{transition.action}"
                for lifecycle in OBJECT_LIFECYCLES
                for transition in lifecycle.transitions
                if transition.implementation == "missing"
            ],
            "ambiguous_lifecycle_transitions": [
                f"{lifecycle.object_kind}:{transition.action}"
                for lifecycle in OBJECT_LIFECYCLES
                for transition in lifecycle.transitions
                if transition.implementation == "ambiguous"
            ],
            "unproved_exact_lifecycle_transitions": [
                f"{lifecycle['object_kind']}:{transition['action']}"
                for lifecycle in [
                    _lifecycle_document(value) for value in OBJECT_LIFECYCLES
                ]
                for transition in lifecycle["transitions"]
                if transition["implementation"] == "exact"
                and transition["process_test"] is None
            ],
        },
    }


__all__ = [
    "CANDIDATE_CHANNELS",
    "LIFE_PATTERNS",
    "OBJECT_LIFECYCLES",
    "OBJECT_KIND_ALIASES",
    "CandidateChannel",
    "CandidateCoverageCell",
    "LifePattern",
    "LifecycleTransition",
    "ObjectLifecycle",
    "catalog_document",
    "candidate_coverage_matrix",
    "event_family",
    "registered_event_catalog",
]
