"""Deterministic fictional workload generator for the canonical sandbox."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.sandbox.contracts import build_target_envelope

ADULT_PRIMARY_ID = "member_synthetic_principal"
ADULT_SECONDARY_ID = "member_synthetic_secondary"
DEPENDENT_ID = "member_synthetic_dependent"
SECONDARY_DEPENDENT_ID = "member_synthetic_dependent_secondary"
COS_SERVICE_ID = "service-principal:chief-of-staff"
SYNTHETIC_TENANT_ID = "tenant-synthetic-live-fidelity-v1"
SCENARIO_ID = "live-fidelity-v1"
SCENARIO_VERSION = 4

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_MAX_EVENTS = 5_000_000
_MAX_TEXT_CHARS = 2_000_000
_HISTORY_ENRICHED_COUNT = 30
_HISTORY_CONFIRMED_COUNT = 30
_HISTORY_SUPERSEDED_COUNT = 12


class GeneratorError(RuntimeError):
    """A requested envelope is unsafe or cannot be generated faithfully."""


def _timestamp(index: int) -> str:
    return (_BASE + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")


def _event(
    tenant_id: str,
    event_type: str,
    schema_version: int,
    key: str,
    payload: dict[str, Any],
    *,
    index: int,
    member_id: str = ADULT_PRIMARY_ID,
    source_adapter: str,
    source_account_id: str,
    shared: bool = False,
    sensitivity: str = "normal",
    idempotency_prefix: str = "live-fidelity:v2",
) -> EventEnvelope:
    scope = "shared:household" if shared else f"private:{member_id}"
    return EventEnvelope(
        event_at_ms=1_767_225_600_000 + index,
        tenant_id=tenant_id,
        type=event_type,
        schema_version=schema_version,
        occurred_at=_timestamp(index),
        source_adapter=source_adapter,
        source_account_id=source_account_id,
        owner_scope=scope,
        visibility_scope=scope,
        sensitivity=sensitivity,  # type: ignore[arg-type]
        actor_identity=member_id,
        idempotency_key=f"{idempotency_prefix}:{key}",
        payload=payload,
    )


def _lengths(count: int, metrics: Mapping[str, int]) -> list[int]:
    if count <= 0:
        return []
    boundaries = (
        (0.50, int(metrics["p50"])),
        (0.90, int(metrics["p90"])),
        (0.95, int(metrics["p95"])),
        (0.99, int(metrics["p99"])),
        (1.00, int(metrics["maximum"])),
    )
    values: list[int] = []
    for fraction, length in boundaries:
        required = math.ceil(count * fraction)
        values.extend([length] * max(0, required - len(values)))
    return values[:count]


def _history_pressure(target: Mapping[str, Any]) -> tuple[int, int]:
    feedback_count = int(target["cardinalities"]["feedback_facts"])
    terminal_count = (
        _HISTORY_CONFIRMED_COUNT + feedback_count + _HISTORY_SUPERSEDED_COUNT
    )
    return terminal_count, terminal_count + _HISTORY_ENRICHED_COUNT


def _matter_depths(target: Mapping[str, Any]) -> list[int]:
    terminal_count, _route_change_count = _history_pressure(target)
    matter_count = int(target["cardinalities"]["triage_matters"]) + terminal_count
    depths = [
        max(1, value)
        for value in _lengths(matter_count, target["tails"]["matter_group_depth"])
    ]
    minimum_candidates = (
        int(target["cardinalities"]["triage_candidates"]) + terminal_count
    )
    cursor = 0
    while sum(depths) < minimum_candidates:
        depths[cursor % len(depths)] += 1
        cursor += 1
    return depths


def _fictional_text(seed: str, length: int, *, actionable: bool) -> str:
    prefix = (
        f"Please review fictional case {seed} by Tuesday and record the next step. "
        if actionable
        else f"Fictional archive note {seed}. "
    )
    vocabulary = (
        "Copper Finch Harbor Juniper Lantern Meadow Orbit Pebble Quartz "
        "Rivulet Saffron Thimble Umber Violet Willow Xylem Yarrow Zephyr. "
    )
    if length <= len(prefix):
        return prefix[:length]
    repetitions = math.ceil((length - len(prefix)) / len(vocabulary))
    return (prefix + vocabulary * repetitions)[:length]


def _persona_for(index: int) -> str:
    values = (
        ADULT_PRIMARY_ID,
        ADULT_SECONDARY_ID,
        DEPENDENT_ID,
        SECONDARY_DEPENDENT_ID,
    )
    return values[index % len(values)]


def _persona_id(persona: str) -> str:
    return {
        "adult_primary": ADULT_PRIMARY_ID,
        "adult_secondary": ADULT_SECONDARY_ID,
        "dependents": DEPENDENT_ID,
        "cos_service": COS_SERVICE_ID,
    }[persona]


def _persona_name(owner_id: str) -> str:
    if owner_id in {DEPENDENT_ID, SECONDARY_DEPENDENT_ID}:
        return "dependents"
    return {
        ADULT_PRIMARY_ID: "adult_primary",
        ADULT_SECONDARY_ID: "adult_secondary",
        COS_SERVICE_ID: "cos_service",
    }[owner_id]


def _persona_owner(persona: str, position: int) -> str:
    if persona == "dependents":
        return (DEPENDENT_ID, SECONDARY_DEPENDENT_ID)[position % 2]
    return _persona_id(persona)


def _merged_tail(
    aggregate: Mapping[str, int], persona: Mapping[str, int]
) -> dict[str, int]:
    return {
        key: max(int(aggregate.get(key, 0)), int(persona.get(key, 0)))
        for key in ("p50", "p90", "p95", "p99", "maximum")
    }


def _round_robin_threads(persona: str, depths: list[int]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for layer in range(max(depths, default=0)):
        for thread_position, depth in enumerate(depths):
            if layer < depth:
                values.append(
                    (
                        _persona_owner(persona, thread_position),
                        f"{persona}-thread-{thread_position:06d}",
                    )
                )
    return values


def _message_plan(target: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Create isolated per-persona threads whose actual tails dominate targets."""

    personas = list(target["personas"])
    thread_target = int(target["cardinalities"]["message_threads"])
    if thread_target < len(personas):
        raise GeneratorError("message thread target cannot represent every persona")
    thread_counts = {persona: thread_target // len(personas) for persona in personas}
    for persona in personas[: thread_target % len(personas)]:
        thread_counts[persona] += 1

    per_persona: dict[str, list[tuple[str, str, str]]] = {}
    for persona in personas:
        profile = target["personas"][persona]
        thread_metrics = _merged_tail(
            target["tails"]["thread_depth"],
            profile["tails"].get("thread_depth", {}),
        )
        depths = [
            max(1, value) for value in _lengths(thread_counts[persona], thread_metrics)
        ]
        required_messages = int(profile["cardinalities"].get("messages", 0))
        cursor = 0
        while sum(depths) < required_messages:
            depths[cursor % len(depths)] += 1
            cursor += 1
        owner_threads = _round_robin_threads(persona, depths)
        outbound = min(
            len(owner_threads) - 1,
            max(5, math.ceil(len(owner_threads) * 0.12)),
        )
        directions = ["inbound"] * (len(owner_threads) - outbound) + [
            "outbound"
        ] * outbound
        per_persona[persona] = [
            (owner, thread_id, directions[index])
            for index, (owner, thread_id) in enumerate(owner_threads)
        ]

    required_inbound = int(target["cardinalities"]["triage_candidates"]) + 20
    required_outbound = max(50, math.ceil(required_inbound * 0.12))
    required_total = max(
        int(target["cardinalities"]["recent_messages"]),
        required_inbound + required_outbound,
    )
    persona_cursor = 0
    while sum(len(values) for values in per_persona.values()) < required_total:
        persona = personas[persona_cursor % len(personas)]
        values = per_persona[persona]
        owner, thread_id, _direction = values[persona_cursor % len(values)]
        values.append((owner, thread_id, "inbound"))
        persona_cursor += 1

    plan: list[tuple[str, str, str]] = []
    offsets = {persona: 0 for persona in personas}
    while any(offsets[persona] < len(per_persona[persona]) for persona in personas):
        for persona in personas:
            position = offsets[persona]
            if position < len(per_persona[persona]):
                plan.append(per_persona[persona][position])
                offsets[persona] += 1
    return plan


def _message_events(
    tenant_id: str,
    target: Mapping[str, Any],
    *,
    start_index: int,
) -> Iterator[EventEnvelope]:
    plan = _message_plan(target)
    total = len(plan)
    subject_lengths = _lengths(total, target["tails"]["subject_chars"])
    body_lengths = _lengths(total, target["tails"]["body_chars"])
    recipient_lengths = _lengths(total, target["tails"]["recipient_count"])
    attachment_lengths = _lengths(total, target["tails"]["attachment_count"])
    participant_count = max(1, int(target["cardinalities"]["participants"]))
    cc_total = int(target["cardinalities"]["cc_recipients"])
    bcc_total = int(target["cardinalities"]["bcc_recipients"])
    category_labels: tuple[list[str], ...] = (
        ["INBOX", "CATEGORY_PRIMARY"],
        ["INBOX", "CATEGORY_UPDATES"],
        ["CATEGORY_PROMOTIONS"],
        ["SPAM"],
        [],
    )
    inbound_positions = [
        position
        for position, (_owner, _thread, direction) in enumerate(plan)
        if direction == "inbound"
    ]
    actual_attachments = [0] * total
    actual_recipients = [1] * total
    for destination, count in zip(
        inbound_positions, sorted(attachment_lengths, reverse=True)
    ):
        actual_attachments[destination] = count
    for destination, count in zip(
        inbound_positions, sorted(recipient_lengths, reverse=True)
    ):
        actual_recipients[destination] = max(1, count)

    persona_body_lengths: dict[str, list[int]] = {}
    persona_body_offsets: dict[str, int] = {}
    for persona, profile in target["personas"].items():
        count = sum(
            1 for owner, _thread, _direction in plan if _persona_name(owner) == persona
        )
        persona_body_lengths[persona] = _lengths(
            count,
            _merged_tail(
                target["tails"]["body_chars"],
                profile["tails"].get("body_chars", {}),
            ),
        )
        persona_body_offsets[persona] = 0

    providers = (
        "gmail",
        "microsoft_graph",
        "bluebubbles",
        "twilio_sms",
        "twilio_whatsapp",
    )
    for position, (member_id, thread_id, direction) in enumerate(plan):
        index = start_index + position
        persona = _persona_name(member_id)
        sender = f"participant-{position % participant_count:06d}@fictional.invalid"
        recipient = f"{member_id}@fictional.invalid"
        subject = _fictional_text(
            f"subject-{position:06d}", subject_lengths[position], actionable=False
        )
        if position % 9 == 0 and len(subject) >= 5:
            subject = "Fwd: " + subject[5:]
        persona_offset = persona_body_offsets[persona]
        persona_length = persona_body_lengths[persona][persona_offset]
        persona_body_offsets[persona] += 1
        body = _fictional_text(
            f"body-{position:06d}",
            max(body_lengths[position], persona_length),
            actionable=direction == "inbound",
        )
        references = (
            [f"<fictional-reference-{position - 1}@fictional.invalid>"]
            if position and position % 3 == 0
            else []
        )
        provider = (
            "openclaw_channel"
            if member_id == COS_SERVICE_ID
            else providers[position % len(providers)]
        )
        workspace = (
            "personal"
            if member_id == COS_SERVICE_ID
            else ("work" if position % 4 == 0 else "personal")
        )
        account_id = f"{provider}-{position % 9}-fixture"
        if direction == "inbound":
            attachments = [
                {
                    "filename": f"fictional-{position:06d}-{item:02d}.pdf",
                    "mime_type": "application/pdf",
                    "size": 1_024 + position + item,
                }
                for item in range(actual_attachments[position])
            ]
            cc = [f"cc-{position:06d}@fictional.invalid"] if position < cc_total else []
            bcc = (
                [f"bcc-{position:06d}@fictional.invalid"]
                if position < bcc_total
                else []
            )
            extra_recipients = max(
                0, actual_recipients[position] - 1 - len(cc) - len(bcc)
            )
            to_identifiers = [recipient] + [
                f"recipient-{position:06d}-{item:02d}@fictional.invalid"
                for item in range(extra_recipients)
            ]
            provider_labels = list(category_labels[position % len(category_labels)])
            for dimension in ("attention_states", "message_natures"):
                values = target["categories"][dimension]
                provider_labels.append(
                    f"SANDBOX:{dimension}:{values[position % len(values)]}"
                )
            for dimension, values in target["personas"][persona]["categories"].items():
                if values:
                    provider_labels.append(
                        f"SANDBOX_PERSONA:{dimension}:{values[position % len(values)]}"
                    )
            yield _event(
                tenant_id,
                "messaging.received",
                5,
                f"message:received:{position:06d}",
                {
                    "source_channel": "fixture"
                    if member_id == COS_SERVICE_ID
                    else provider,
                    "from_identifier": sender,
                    "to_identifier": recipient,
                    "to_identifiers": to_identifiers,
                    "thread_id": thread_id,
                    "subject": subject,
                    "body_text": body,
                    "body_html": None,
                    "received_at": _timestamp(index),
                    "attachments": attachments,
                    "native_message_id": f"fictional-received-{position:06d}",
                    "member_id": None if member_id == COS_SERVICE_ID else member_id,
                    "service_principal_id": COS_SERVICE_ID
                    if member_id == COS_SERVICE_ID
                    else None,
                    "routing_scope": "household_shared"
                    if member_id == COS_SERVICE_ID
                    else "member_private",
                    "routing_target_id": None
                    if member_id == COS_SERVICE_ID
                    else member_id,
                    "provider": provider,
                    "provider_account_id": account_id,
                    "workspace": workspace,
                    "cc_identifiers": cc,
                    "bcc_identifiers": bcc,
                    "reply_to_identifier": None,
                    "rfc_message_id": f"<fictional-{position:06d}@fictional.invalid>",
                    "references": references,
                    "provider_labels": provider_labels,
                    "unread": position % 2 == 0,
                    "archived": position % 7 == 0,
                    "starred": position % 11 == 0,
                    "important": position % 5 == 0,
                    "in_reply_to": references[-1] if references else None,
                },
                index=index,
                member_id=member_id,
                source_adapter="sandbox-cos-channel"
                if member_id == COS_SERVICE_ID
                else f"messaging-{provider}",
                source_account_id=account_id,
                shared=member_id == COS_SERVICE_ID,
            )
        else:
            yield _event(
                tenant_id,
                "messaging.sent",
                5,
                f"message:sent:{position:06d}",
                {
                    "source_channel": "fixture"
                    if member_id == COS_SERVICE_ID
                    else provider,
                    "to_identifier": sender,
                    "thread_id": thread_id,
                    "subject": subject,
                    "body_text": body,
                    "body_html": None,
                    "sent_at": _timestamp(index),
                    "delivery_status": "sent",
                    "member_id": None if member_id == COS_SERVICE_ID else member_id,
                    "service_principal_id": COS_SERVICE_ID
                    if member_id == COS_SERVICE_ID
                    else None,
                    "provider": provider,
                    "provider_account_id": account_id,
                    "workspace": workspace,
                    "native_message_id": f"fictional-sent-{position:06d}",
                    "in_reply_to_interaction_id": None,
                    "rfc_message_id": f"<fictional-sent-{position:06d}@fictional.invalid>",
                    "references": references,
                    "in_reply_to": references[-1] if references else None,
                    "send_as": "service_principal"
                    if member_id == COS_SERVICE_ID
                    else "member",
                    "routing_scope": "household_shared"
                    if member_id == COS_SERVICE_ID
                    else "member_private",
                    "routing_target_id": None
                    if member_id == COS_SERVICE_ID
                    else member_id,
                },
                index=index,
                member_id=member_id,
                source_adapter="sandbox-cos-channel"
                if member_id == COS_SERVICE_ID
                else f"messaging-{provider}",
                source_account_id=account_id,
                shared=member_id == COS_SERVICE_ID,
            )


def _structured_source_events(
    tenant_id: str,
    target: Mapping[str, Any],
    *,
    start_index: int,
) -> Iterator[EventEnvelope]:
    counts = target["cardinalities"]
    index = start_index
    for position in range(int(counts["calendar_facts"])):
        member_id = _persona_for(position)
        yield _event(
            tenant_id,
            "calendar.event_added",
            2,
            f"calendar:{position:06d}",
            {
                "member_id": member_id,
                "provider": "google_calendar",
                "provider_account_id": "calendar-fixture",
                "calendar_external_id": f"calendar-{position % 4}",
                "calendar_name": "Fictional calendar",
                "external_event_id": f"fictional-event-{position:06d}",
                "status": "tentative" if position % 7 == 0 else "confirmed",
                "summary": f"Fictional appointment {position:06d}",
                "start": _timestamp(index + 3_600),
                "end": _timestamp(index + 5_400),
                "start_time_zone": "America/New_York",
                "end_time_zone": "America/New_York",
                "availability": "busy",
                "attendees": [],
                "alarms": [],
                "attachments": [],
                "updated_at": _timestamp(index),
            },
            index=index,
            member_id=member_id,
            source_adapter="calendaring-google-calendar",
            source_account_id="calendar-fixture",
        )
        index += 1
    for position in range(int(counts["task_facts"])):
        member_id = _persona_for(position)
        yield _event(
            tenant_id,
            "task.created",
            1,
            f"task:{position:06d}",
            {
                "task_id": f"fictional-task-{position:06d}",
                "title": f"Complete fictional task {position:06d}",
                "description": "A deterministic task used only in the isolated sandbox.",
                "owner_member_id": member_id,
                "due": _timestamp(index + 86_400),
                "energy": ("low", "medium", "high")[position % 3],
                "effort": ("tiny", "small", "medium", "large")[position % 4],
                "item_type": (
                    "task",
                    "purchase",
                    "appointment",
                    "research",
                    "decision",
                    "chore",
                    "maintenance",
                )[position % 7],
                "created_by": "sandbox-generator",
                "source_system": "fixture",
            },
            index=index,
            member_id=member_id,
            source_adapter="tasks-google-tasks",
            source_account_id="tasks-fixture",
        )
        index += 1
    for position in range(int(counts["reminder_facts"])):
        member_id = _persona_for(position)
        yield _event(
            tenant_id,
            "reminder.cue.configured",
            1,
            f"reminder:{position:06d}",
            {
                "cue_id": f"fictional-cue-{position:06d}",
                "member_id": member_id,
                "provider": "apple_reminders",
                "provider_account_id": "reminders-fixture",
                "list_external_id": "fictional-reminders",
                "item_external_id": f"fictional-reminder-{position:06d}",
                "signal_key": f"fictional.signal.{position:06d}",
                "source": "manual",
                "kind": "presence",
                "operator": "present",
                "enabled": True,
                "configured_at": _timestamp(index),
            },
            index=index,
            member_id=member_id,
            source_adapter="reminders-apple-eventkit",
            source_account_id="reminders-fixture",
        )
        index += 1
    for position in range(int(counts["list_facts"])):
        member_id = _persona_for(position)
        yield _event(
            tenant_id,
            "list_item.added",
            3,
            f"list:{position:06d}",
            {
                "member_id": member_id,
                "provider": "google_tasks",
                "provider_account_id": "tasks-fixture",
                "list_external_id": f"fictional-list-{position % 5}",
                "item_external_id": f"fictional-item-{position:06d}",
                "list_name": "Fictional list",
                "title": f"Fictional list item {position:06d}",
                "priority": (0, 1, 5, 9)[position % 4],
                "completed": position % 9 == 0,
                "alarms": [],
                "recurrence_rules": [],
                "completion_policy": "manual",
                "last_modified_at": _timestamp(index),
                "sync_cycle_id": "live-fidelity-v2",
                "provider_position": f"{position:08d}",
                "source_links": [],
                "provider_data": {},
            },
            index=index,
            member_id=member_id,
            source_adapter="tasks-google-tasks",
            source_account_id="tasks-fixture",
        )
        index += 1
    for position in range(int(counts["recurrence_facts"])):
        member_id = _persona_for(position)
        yield _event(
            tenant_id,
            "recurrence.added",
            1,
            f"recurrence:{position:06d}",
            {
                "recurrence_id": f"fictional-recurrence-{position:06d}",
                "linked_kind": "household",
                "linked_id": "fictional-household",
                "kind": ("maintenance", "renewal", "celebration")[position % 3],
                "rrule": ("FREQ=WEEKLY", "FREQ=MONTHLY", "FREQ=YEARLY")[position % 3],
                "next_occurrence": _timestamp(index + 604_800),
                "lead_time_days": 3,
                "trackable": position % 2 == 0,
                "notes": "Fictional recurrence for sandbox coverage.",
            },
            index=index,
            member_id=member_id,
            source_adapter="reminders-apple-eventkit",
            source_account_id="reminders-fixture",
        )
        index += 1
    for position in range(int(counts["finance_facts"])):
        member_id = _persona_for(position)
        yield _event(
            tenant_id,
            "money_flow.provider_observed",
            1,
            f"finance:{position:06d}",
            {
                "member_id": member_id,
                "plaid_item_id": "fictional-plaid-item",
                "account_id": f"fictional-account-{position % 4}",
                "transaction_id": f"fictional-transaction-{position:06d}",
                "amount_minor": 100 + (position * 37),
                "currency": "USD",
                "occurred_at": _timestamp(index),
                "kind": "paid" if position % 4 else "received",
                "merchant_name": f"Fictional merchant {position % 11}",
                "merchant_category": "Sandbox",
                "pending": position % 8 == 0,
                "provider_revision": f"fixture-{position:06d}",
                "observed_at": _timestamp(index),
            },
            index=index,
            member_id=member_id,
            source_adapter="financial-plaid",
            source_account_id="finance-fixture",
            sensitivity="privileged",
        )
        index += 1
    for position in range(int(counts["note_facts"])):
        member_id = _persona_for(position)
        body = _fictional_text(
            f"note-{position:06d}", 480 + (position % 7) * 80, actionable=False
        )
        yield _event(
            tenant_id,
            "member_knowledge.note_added",
            3,
            f"note:{position:06d}",
            {
                "knowledge_id": f"fictional-knowledge-{position:06d}",
                "member_id": member_id,
                "title": f"Fictional note {position:06d}",
                "body": body,
                "body_format": "plain",
                "source_kind": "fixture",
                "source_type": "note",
                "source_external_id": f"fictional-note-{position:06d}",
                "content_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "captured_at": _timestamp(index),
                "locked": False,
                "shared": False,
                "attachments": [],
                "metadata": {},
                "capability_warnings": [],
            },
            index=index,
            member_id=member_id,
            source_adapter="knowledge-apple-notes",
            source_account_id="notes-fixture",
        )
        index += 1
    for position in range(int(counts["chat_facts"])):
        member_id = _persona_for(position)
        yield _event(
            tenant_id,
            "openclaw.domain_event.observed",
            1,
            f"chat:{position:06d}",
            {
                "event_kind": "commitment_candidate",
                "content": (
                    f"Fictional household chat turn {position:06d}; "
                    "review one imaginary next step."
                ),
                "consent": True,
                "agent_id": "sandbox-household-cos",
                "session_key": f"fictional-session-{position % 7:02d}",
                "session_id": f"fictional-chat-{position % 7:02d}",
                "channel_id": "sandbox-member-chat",
                "account_id": "sandbox-openclaw-account",
                "message_id": f"fictional-chat-message-{position:06d}",
                "thread_id": f"fictional-chat-{position % 7:02d}",
                "correlation_id": f"fictional-chat-correlation-{position:06d}",
            },
            index=index,
            member_id=member_id,
            source_adapter="sandbox-member-chat",
            source_account_id="sandbox-openclaw-account",
        )
        index += 1
    for position in range(max(20, int(counts["chat_facts"]))):
        yield _event(
            tenant_id,
            "openclaw.model_call.recorded",
            1,
            f"cos-runtime:{position:06d}",
            {
                "phase": "started" if position % 2 == 0 else "ended",
                "run_id": f"fictional-cos-run-{position // 2:06d}",
                "provider": "sandbox-deterministic",
                "model": "fictional-model-contract",
                "agent_id": "sandbox-household-cos",
                "session_key": f"fictional-cos-session-{position % 5:02d}",
                "channel_id": "sandbox-cos-runtime",
            },
            index=index,
            member_id=COS_SERVICE_ID,
            source_adapter="sandbox-cos-runtime",
            source_account_id="sandbox-openclaw-account",
            shared=True,
        )
        index += 1


def _contact_event(
    tenant_id: str,
    *,
    position: int,
    revision: int,
    index: int,
) -> EventEnvelope:
    member_id = _persona_for(position + revision)
    external = f"fictional-contact-{position:06d}"
    email = f"{external}@fictional.invalid"
    return _event(
        tenant_id,
        "contact.record.observed",
        1,
        f"contact:{position:06d}:revision:{revision:06d}",
        {
            "contact_record_id": external,
            "member_id": member_id,
            "provider": "fixture",
            "provider_account_id": "contacts-fixture",
            "container_external_id": "fictional-address-book",
            "external_id": external,
            "provider_revision": f"revision-{revision:06d}",
            "kind": "organization" if position % 7 == 0 else "person",
            "display_name": f"Fictional Contact {position:06d}",
            "sort_name": f"Contact {position:06d} Fictional",
            "given_name": "Fictional",
            "family_name": f"Contact {position:06d}",
            "organization_name": f"Fictional Organization {position % 23}"
            if position % 7 == 0
            else None,
            "identifiers": [
                {
                    "kind": "email",
                    "value": email,
                    "value_normalized": email,
                    "label": "fictional",
                    "primary": True,
                    "verified": False,
                }
            ],
            "fields": {"fictional_revision_bucket": revision % 17},
            "group_external_ids": [f"fictional-group-{position % 13}"],
            "observed_at": _timestamp(index),
            "capability_warnings": [],
        },
        index=index,
        member_id=member_id,
        source_adapter="contacts-google-people",
        source_account_id="contacts-fixture",
    )


def _cursor_event(
    tenant_id: str,
    *,
    position: int,
    index: int,
    member_id: str | None = None,
) -> EventEnvelope:
    owner_id = member_id or _persona_for(position)
    families = (
        "communication",
        "external_state_mirror",
        "inbound_data",
        "personal_knowledge",
        "outbound_action",
    )
    family = families[position % len(families)]
    return _event(
        tenant_id,
        "adapter.cursor_advanced",
        1,
        f"historical-cursor:{position:08d}",
        {
            "adapter_name": f"synthetic-{family}",
            "adapter_family": family,
            "cursor_kind": "opaque_page",
            "cursor_value": f"fictional-cursor-{position:08d}",
            "events_since_last": 1 + (position % 19),
            "advanced_at": _timestamp(index),
        },
        index=index,
        member_id=owner_id,
        source_adapter="synthetic-adapter-history",
        source_account_id=f"synthetic-{family}-history",
        shared=owner_id == COS_SERVICE_ID,
    )


def generated_events(
    tenant_id: str,
    manifest: Mapping[str, Any],
) -> Iterator[EventEnvelope]:
    """Yield the complete deterministic source workload without materializing it."""

    target = build_target_envelope(manifest)
    source_target = int(target["cardinalities"]["source_facts"])
    if source_target > _MAX_EVENTS:
        raise GeneratorError(
            f"source-fact target exceeds safe generator ceiling: {source_target}"
        )
    for metrics in target["tails"].values():
        if int(metrics["maximum"]) > _MAX_TEXT_CHARS:
            raise GeneratorError("tail target exceeds the safe fictional-text ceiling")

    index = 1
    message_events = list(_message_events(tenant_id, target, start_index=index))
    yield from message_events
    index += len(message_events)
    structured = list(_structured_source_events(tenant_id, target, start_index=index))
    yield from structured
    index += len(structured)
    source_emitted = len(message_events) + len(structured)
    contact_count = max(
        1,
        int(target["cardinalities"]["contact_facts"]),
        int(target["cardinalities"]["participants"]),
    )
    contacts = [
        _contact_event(
            tenant_id,
            position=position,
            revision=0,
            index=index + position,
        )
        for position in range(contact_count)
    ]
    yield from contacts
    index += len(contacts)
    source_emitted += len(contacts)
    persona_by_id = {
        ADULT_PRIMARY_ID: "adult_primary",
        ADULT_SECONDARY_ID: "adult_secondary",
        DEPENDENT_ID: "dependents",
        SECONDARY_DEPENDENT_ID: "dependents",
        COS_SERVICE_ID: "cos_service",
    }
    actual_by_persona = {persona: 0 for persona in target["personas"]}
    for event in [*message_events, *structured, *contacts]:
        persona = persona_by_id.get(str(event.actor_identity or ""))
        if persona:
            actual_by_persona[persona] += 1
    required_owners: list[str] = []
    for persona, profile in target["personas"].items():
        deficit = max(
            0,
            int(profile["cardinalities"].get("source_facts", 0))
            - actual_by_persona[persona],
        )
        required_owners.extend([_persona_id(persona)] * deficit)
    filler_count = max(
        max(0, source_target - source_emitted),
        len(required_owners),
    )
    for position in range(filler_count):
        owner_id = (
            required_owners[position]
            if position < len(required_owners)
            else _persona_for(position)
        )
        yield _cursor_event(
            tenant_id,
            position=position,
            index=index,
            member_id=owner_id,
        )
        index += 1


def generated_domain_events(
    tenant_id: str,
    manifest: Mapping[str, Any],
    *,
    source_event_ids: list[str],
) -> Iterator[EventEnvelope]:
    """Yield typed Triage pressure after source facts have durable identities."""

    if not source_event_ids:
        raise GeneratorError(
            "Triage generation requires durable fictional source events"
        )
    target = build_target_envelope(manifest)
    matter_depths = _matter_depths(target)
    matter_ids: list[str] = []
    for layer in range(max(matter_depths, default=0)):
        for matter_position, depth in enumerate(matter_depths):
            if layer < depth:
                matter_ids.append(f"fictional-matter-{matter_position:06d}")
    candidate_count = len(matter_ids)
    kinds = sorted(
        value
        for value in target["categories"]["candidate_kinds"]
        if not value.startswith("adversarial:")
    )
    if not kinds:
        raise GeneratorError("candidate-kind target is empty")
    response_sizes = _lengths(candidate_count, target["tails"]["response_bytes"])
    _terminal_count, route_change_count = _history_pressure(target)
    index = 10_000_000
    for position in range(candidate_count):
        member_id = _persona_for(position)
        target_kind = kinds[position % len(kinds)]
        route = (
            {
                "member_id": member_id,
                "provider": "google_tasks",
                "provider_account_id": "tasks-fixture",
                "container_external_id": "fictional-destination",
            }
            if position
            >= int(target["cardinalities"]["enrichment_or_destination_required"])
            + route_change_count
            else None
        )
        yield _event(
            tenant_id,
            "triage.item.proposed",
            4,
            f"triage:proposal:{position:06d}",
            {
                "proposal_id": f"fictional-proposal-{position:06d}",
                "target_kind": target_kind,
                "owner_member_id": member_id,
                "title": f"Review fictional {target_kind.replace('_', ' ')} {position:06d}",
                "candidate": {
                    "fictional": True,
                    "destination_required": route is None,
                    "incomplete_fields": ["reviewed_destination"]
                    if route is None
                    else [],
                    "duplicate_evidence_ids": [
                        source_event_ids[(position - 1) % len(source_event_ids)]
                    ]
                    if position % 11 == 0
                    else [],
                    "existing_object_evidence": {
                        "kind": target_kind,
                        "fictional_id": f"existing-{position % 37:04d}",
                    }
                    if position % 13 == 0
                    else None,
                    "sandbox_coverage": {
                        dimension: values[position % len(values)]
                        for dimension, values in target["categories"].items()
                    },
                    "sandbox_response_bytes": response_sizes[position],
                    "sandbox_topology": target["topology"] if position == 0 else {},
                },
                "route": route,
                "source_event_id": source_event_ids[position % len(source_event_ids)],
                "confidence": 0.55 + ((position % 40) / 100),
                "reasons": ["Deterministic adversarial sandbox coverage"],
                "parent_intent_id": matter_ids[position],
                "candidate_key": f"fictional-candidate-{position:06d}",
                "eligibility_rule_ids": ["sandbox.all-kinds.v1"],
                "source_occurred_at": _timestamp(position + 1),
            },
            index=index + position,
            member_id=member_id,
            source_adapter="member-triage",
            source_account_id=member_id,
            idempotency_prefix="live-fidelity-domain:v2",
        )


def generated_history_events(
    tenant_id: str,
    manifest: Mapping[str, Any],
    *,
    source_event_ids: list[str],
) -> Iterator[EventEnvelope]:
    """Yield typed review, learning, rule, and reversal history."""

    if not source_event_ids:
        raise GeneratorError("history generation requires durable source events")
    index = 20_000_000

    for position in range(_HISTORY_ENRICHED_COUNT):
        member_id = _persona_for(position)
        yield _event(
            tenant_id,
            "triage.item.enriched",
            1,
            f"enriched:{position:06d}",
            {
                "proposal_id": f"fictional-proposal-{position:06d}",
                "enriched_at": _timestamp(index + position),
                "enriched_by_member_id": member_id,
                "candidate_updates": {
                    "reviewed_destination": "fictional-destination",
                    "sandbox_correction": position % 5 == 0,
                },
                "route": {
                    "member_id": member_id,
                    "provider": "google_tasks",
                    "provider_account_id": "tasks-fixture",
                    "container_external_id": "fictional-destination",
                },
            },
            index=index + position,
            member_id=member_id,
            source_adapter="member-triage",
            source_account_id=member_id,
            idempotency_prefix="live-fidelity-history:v2",
        )
    yield from _remaining_history_events(
        tenant_id,
        manifest,
        source_event_ids=source_event_ids,
    )


def _percentiles(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    if not ordered:
        return {key: 0 for key in ("p50", "p90", "p95", "p99", "maximum")}

    def at(fraction: float) -> int:
        position = max(
            0,
            min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1),
        )
        return int(ordered[position])

    return {
        "p50": at(0.50),
        "p90": at(0.90),
        "p95": at(0.95),
        "p99": at(0.99),
        "maximum": int(ordered[-1]),
    }


def _generated_source_shape(event_type: str) -> str | None:
    if event_type == "openclaw.domain_event.observed":
        return "chat"
    if event_type.startswith("openclaw."):
        return "cos"
    prefixes = (
        ("calendar.", "calendar"),
        ("task.", "task"),
        ("reminder.", "reminder"),
        ("list_item.", "list"),
        ("recurrence.", "recurrence"),
        ("money_flow.", "finance"),
        ("financial.", "finance"),
        ("member_knowledge.", "notes"),
        ("contact.", "contacts"),
    )
    return next(
        (shape for prefix, shape in prefixes if event_type.startswith(prefix)),
        None,
    )


def measure_generated_coverage(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Measure the emitted typed workload instead of trusting its requested plan."""

    target = build_target_envelope(manifest)
    counts = {name: 0 for name in target["cardinalities"]}
    categories: dict[str, set[str]] = {name: set() for name in target["categories"]}
    topology: dict[str, set[str]] = {name: set() for name in target["topology"]}
    source_accounts: set[str] = set()
    source_adapters: set[str] = set()
    adapter_categories: set[str] = set()
    workspaces: set[str] = set()
    member_ids: set[str] = set()
    service_ids: set[str] = set()
    parties: set[str] = set()
    endpoints: set[str] = set()
    threads: Counter[str] = Counter()
    subject_lengths: list[int] = []
    body_lengths: list[int] = []
    preview_lengths: list[int] = []
    recipient_counts: list[int] = []
    attachment_counts: list[int] = []
    persona_counts = {
        persona: Counter({"source_facts": 0, "messages": 0})
        for persona in target["personas"]
    }
    persona_bodies: dict[str, list[int]] = {
        persona: [] for persona in target["personas"]
    }
    persona_threads: dict[str, Counter[str]] = {
        persona: Counter() for persona in target["personas"]
    }
    persona_categories: dict[str, dict[str, set[str]]] = {
        persona: {
            dimension: set() for dimension in target["personas"][persona]["categories"]
        }
        for persona in target["personas"]
    }

    for event in generated_events(SYNTHETIC_TENANT_ID, manifest):
        counts["source_facts"] += 1
        source_accounts.add(event.source_account_id)
        source_adapters.add(event.source_adapter)
        owner = str(event.actor_identity or "")
        persona = _persona_name(owner)
        persona_counts[persona]["source_facts"] += 1
        if owner == COS_SERVICE_ID:
            service_ids.add(owner)
        else:
            member_ids.add(owner)
        payload = event.payload
        workspace = payload.get("workspace")
        if isinstance(workspace, str):
            workspaces.add(workspace)
        shape = _generated_source_shape(event.type)
        if shape:
            categories["source_shapes"].add(shape)
            if "source_shapes" in persona_categories[persona]:
                persona_categories[persona]["source_shapes"].add(shape)
        if event.type == "adapter.cursor_advanced":
            family = str(payload.get("adapter_family") or "")
            if family:
                adapter_categories.add(family)
        if event.type in {"messaging.received", "messaging.sent"}:
            direction = "outbound" if event.type == "messaging.sent" else "inbound"
            counts["recent_messages"] += 1
            categories["message_directions"].add(direction)
            persona_counts[persona]["messages"] += 1
            persona_categories[persona]["message_directions"].add(direction)
            subject = str(payload.get("subject") or "")
            body = str(payload.get("body_text") or "")
            thread = str(payload.get("thread_id") or event.idempotency_key or "")
            subject_lengths.append(len(subject))
            body_lengths.append(len(body))
            preview_lengths.append(min(1_500, len(body)))
            persona_bodies[persona].append(len(body))
            threads[thread] += 1
            persona_threads[persona][thread] += 1
            recipients = [
                *(payload.get("to_identifiers") or []),
                *(payload.get("cc_identifiers") or []),
                *(payload.get("bcc_identifiers") or []),
            ]
            if event.type == "messaging.sent":
                recipients = [payload.get("to_identifier")]
            recipient_counts.append(len([value for value in recipients if value]))
            attachments = payload.get("attachments") or []
            attachment_counts.append(len(attachments))
            counts["attachments"] += len(attachments)
            counts["cc_recipients"] += len(payload.get("cc_identifiers") or [])
            counts["bcc_recipients"] += len(payload.get("bcc_identifiers") or [])
            for value in (
                payload.get("from_identifier"),
                payload.get("to_identifier"),
                *recipients,
            ):
                if isinstance(value, str) and value:
                    endpoints.add(value.casefold())
            labels = [str(value) for value in payload.get("provider_labels") or []]
            upper_labels = {value.upper() for value in labels}
            if "SPAM" in upper_labels:
                categories["provider_categories"].add("spam")
            if "CATEGORY_PROMOTIONS" in upper_labels:
                categories["provider_categories"].add("promotions")
            if "CATEGORY_UPDATES" in upper_labels:
                categories["provider_categories"].add("updates")
            if "CATEGORY_PRIMARY" in upper_labels or "INBOX" in upper_labels:
                categories["provider_categories"].add("primary")
            if not upper_labels.intersection(
                {
                    "SPAM",
                    "CATEGORY_PROMOTIONS",
                    "CATEGORY_UPDATES",
                    "CATEGORY_PRIMARY",
                    "INBOX",
                }
            ):
                categories["provider_categories"].add("uncategorized")
            if payload.get("unread"):
                categories["attention_states"].add("unread")
            if payload.get("archived"):
                categories["attention_states"].add("archived")
            if payload.get("important"):
                categories["attention_states"].add("important")
            if payload.get("references"):
                categories["message_history_shapes"].add("quoted")
            if subject.casefold().startswith("fwd:"):
                categories["message_history_shapes"].add("forwarded")
            for label in labels:
                if label.startswith("SANDBOX:"):
                    _prefix, dimension, value = label.split(":", 2)
                    if dimension in categories:
                        categories[dimension].add(value)
                elif label.startswith("SANDBOX_PERSONA:"):
                    _prefix, dimension, value = label.split(":", 2)
                    if dimension in persona_categories[persona]:
                        persona_categories[persona][dimension].add(value)
        if event.type.startswith("calendar."):
            counts["calendar_facts"] += 1
        if event.type.startswith("task."):
            counts["task_facts"] += 1
        if event.type.startswith("reminder."):
            counts["reminder_facts"] += 1
        if event.type.startswith("list_item."):
            counts["list_facts"] += 1
        if event.type.startswith("recurrence."):
            counts["recurrence_facts"] += 1
        if event.type.startswith(("financial.", "money_flow.")):
            counts["finance_facts"] += 1
        if event.type.startswith("member_knowledge."):
            counts["note_facts"] += 1
        if event.type.startswith("contact."):
            counts["contact_facts"] += 1
            parties.add(str(payload.get("contact_record_id") or ""))
            for identifier in payload.get("identifiers") or []:
                value = (
                    identifier.get("value") if isinstance(identifier, dict) else None
                )
                if isinstance(value, str) and value:
                    endpoints.add(value.casefold())
        if event.type == "openclaw.domain_event.observed":
            counts["chat_facts"] += 1

    counts["message_threads"] = len(threads)
    counts["participants"] = len(endpoints)
    counts["endpoints"] = len(endpoints)
    counts["members"] = len(member_ids)
    counts["service_principals"] = len(service_ids)
    counts["parties"] = len(parties)
    counts["relationships"] = 8
    counts["accounts"] = len(source_accounts)
    counts["workspaces"] = len(workspaces)
    counts["provider_families"] = len(source_adapters)
    counts["adapter_categories"] = len(adapter_categories)
    categories["adapter_categories"].update(adapter_categories)
    categories["persona_kinds"].update(target["categories"]["persona_kinds"])
    if any(depth > 1 for depth in threads.values()):
        categories["message_history_shapes"].add("multi_message")

    dummy_sources = [f"fictional-source-{position:06d}" for position in range(600)]
    matters: Counter[str] = Counter()
    response_sizes: list[int] = []
    for event in generated_domain_events(
        SYNTHETIC_TENANT_ID,
        manifest,
        source_event_ids=dummy_sources,
    ):
        counts["triage_candidates"] += 1
        payload = event.payload
        matters[str(payload.get("parent_intent_id") or "")] += 1
        if not payload.get("route"):
            counts["enrichment_or_destination_required"] += 1
        candidate = payload.get("candidate") or {}
        response_sizes.append(int(candidate.get("sandbox_response_bytes") or 0))
        for dimension, value in (candidate.get("sandbox_coverage") or {}).items():
            if dimension in categories and isinstance(value, str):
                categories[dimension].add(value)
        for dimension, values in (candidate.get("sandbox_topology") or {}).items():
            if dimension in topology and isinstance(values, list):
                topology[dimension].update(
                    value for value in values if isinstance(value, str)
                )
    counts["triage_matters"] = len(matters)

    for event in generated_history_events(
        SYNTHETIC_TENANT_ID,
        manifest,
        source_event_ids=dummy_sources,
    ):
        if event.type == "triage.feedback.recorded":
            counts["feedback_facts"] += 1
        if event.type.startswith("triage.policy_rule."):
            counts["rule_history_facts"] += 1

    tails = {
        "subject_chars": _percentiles(subject_lengths),
        "body_chars": _percentiles(body_lengths),
        "preview_chars": _percentiles(preview_lengths),
        "thread_depth": _percentiles(list(threads.values())),
        "recipient_count": _percentiles(recipient_counts),
        "attachment_count": _percentiles(attachment_counts),
        "matter_group_depth": _percentiles(list(matters.values())),
        "response_bytes": _percentiles(response_sizes),
    }
    return {
        "contract": "administrateme-measured-generated-coverage-v1",
        "cardinalities": counts,
        "tails": tails,
        "categories": {
            dimension: sorted(values) for dimension, values in categories.items()
        },
        "topology": {
            dimension: sorted(values) for dimension, values in topology.items()
        },
        "personas": {
            persona: {
                "cardinalities": dict(persona_counts[persona]),
                "tails": {
                    "body_chars": _percentiles(persona_bodies[persona]),
                    "thread_depth": _percentiles(
                        list(persona_threads[persona].values())
                    ),
                },
                "categories": {
                    dimension: sorted(values)
                    for dimension, values in persona_categories[persona].items()
                },
            }
            for persona in target["personas"]
        },
        "content_origin": "measured_typed_fictional_events",
        "live_content_copied": False,
    }


def _remaining_history_events(
    tenant_id: str,
    manifest: Mapping[str, Any],
    *,
    source_event_ids: list[str],
) -> Iterator[EventEnvelope]:
    target = build_target_envelope(manifest)
    feedback_count = int(target["cardinalities"]["feedback_facts"])
    rule_count = int(target["cardinalities"]["rule_history_facts"])
    index = 20_000_030

    for position in range(_HISTORY_CONFIRMED_COUNT):
        proposal_position = _HISTORY_ENRICHED_COUNT + position
        member_id = _persona_for(proposal_position)
        yield _event(
            tenant_id,
            "triage.item.confirmed",
            1,
            f"confirmed:{position:06d}",
            {
                "proposal_id": f"fictional-proposal-{proposal_position:06d}",
                "confirmed_at": _timestamp(index + position),
                "confirmed_by_member_id": member_id,
                "materialized_event_ids": [
                    f"fictional-materialized-{proposal_position:06d}"
                ],
            },
            index=index + position,
            member_id=member_id,
            source_adapter="member-triage",
            source_account_id=member_id,
            idempotency_prefix="live-fidelity-history:v2",
        )
    index += _HISTORY_CONFIRMED_COUNT

    reasons = (
        "not_actionable",
        "already_done",
        "duplicate",
        "wrong_kind",
        "wrong_context",
        "wrong_owner",
        "not_mine",
        "recurring_normal",
        "low_signal",
        "provider_promotion",
        "spam",
        "other",
    )
    for position in range(feedback_count):
        proposal_position = (
            _HISTORY_ENRICHED_COUNT + _HISTORY_CONFIRMED_COUNT + position
        )
        member_id = _persona_for(proposal_position)
        reason = reasons[position % len(reasons)]
        yield _event(
            tenant_id,
            "triage.item.dismissed",
            1,
            f"dismissed:{position:06d}",
            {
                "proposal_id": f"fictional-proposal-{proposal_position:06d}",
                "dismissed_at": _timestamp(index + (position * 2)),
                "dismissed_by_member_id": member_id,
                "reason": reason,
            },
            index=index + (position * 2),
            member_id=member_id,
            source_adapter="member-triage",
            source_account_id=member_id,
            idempotency_prefix="live-fidelity-history:v2",
        )
        yield _event(
            tenant_id,
            "triage.feedback.recorded",
            1,
            f"feedback:{position:06d}",
            {
                "feedback_id": f"fictional-feedback-{position:06d}",
                "proposal_id": f"fictional-proposal-{proposal_position:06d}",
                "member_id": member_id,
                "reason": reason,
                "target_kind": "task",
                "source_adapter": "sandbox-message",
                "provider_account_id": "fictional-provider-account",
                "workspace": "personal",
                "recorded_at": _timestamp(index + (position * 2) + 1),
            },
            index=index + (position * 2) + 1,
            member_id=member_id,
            source_adapter="member-triage",
            source_account_id=member_id,
            idempotency_prefix="live-fidelity-history:v2",
        )
    index += feedback_count * 2

    for position in range(12):
        proposal_position = (
            _HISTORY_ENRICHED_COUNT
            + _HISTORY_CONFIRMED_COUNT
            + feedback_count
            + position
        )
        member_id = _persona_for(proposal_position)
        yield _event(
            tenant_id,
            "triage.item.superseded",
            1,
            f"superseded:{position:06d}",
            {
                "proposal_id": f"fictional-proposal-{proposal_position:06d}",
                "superseded_at": _timestamp(index + position),
                "replacement_source_event_id": source_event_ids[
                    position % len(source_event_ids)
                ],
                "reason": ("source_changed", "source_deleted", "source_privileged")[
                    position % 3
                ],
            },
            index=index + position,
            member_id=member_id,
            source_adapter="member-triage",
            source_account_id=member_id,
            idempotency_prefix="live-fidelity-history:v2",
        )
    index += 12

    approved_count = max(1, math.ceil(rule_count * 0.67))
    for position in range(approved_count):
        member_id = _persona_for(position)
        scope_value = f"fictional-scope-{position:06d}"
        yield _event(
            tenant_id,
            "triage.policy_rule.approved",
            1,
            f"rule-approved:{position:06d}",
            {
                "rule_id": f"fictional-rule-{position:06d}",
                "member_id": member_id,
                "scope_kind": ("candidate_kind", "context", "account", "workspace")[
                    position % 4
                ],
                "scope_value_digest": hashlib.sha256(
                    scope_value.encode("utf-8")
                ).hexdigest(),
                "disposition": ("quiet", "always_surface", "route_to_low_signal")[
                    position % 3
                ],
                "enforcement_mode": "shadow",
                "preview_affected_count": 1 + position,
                "approved_by_member_id": member_id,
                "approved_at": _timestamp(index + position),
            },
            index=index + position,
            member_id=member_id,
            source_adapter="member-triage",
            source_account_id=member_id,
            idempotency_prefix="live-fidelity-history:v2",
        )
    index += approved_count
    for position in range(max(0, rule_count - approved_count)):
        member_id = _persona_for(position)
        yield _event(
            tenant_id,
            "triage.policy_rule.revoked",
            1,
            f"rule-revoked:{position:06d}",
            {
                "rule_id": f"fictional-rule-{position % approved_count:06d}",
                "member_id": member_id,
                "revoked_by_member_id": member_id,
                "revoked_at": _timestamp(index + position),
                "reason": "Fictional reversal coverage",
            },
            index=index + position,
            member_id=member_id,
            source_adapter="member-triage",
            source_account_id=member_id,
            idempotency_prefix="live-fidelity-history:v2",
        )


def expected_coverage(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the deterministic generator envelope for pre/post verification."""

    target = build_target_envelope(manifest)
    message_plan = _message_plan(target)
    inbound = sum(direction == "inbound" for _owner, _thread, direction in message_plan)
    outbound = len(message_plan) - inbound
    total = len(message_plan)
    matter_depths = _matter_depths(target)
    cardinalities = dict(target["cardinalities"])
    cardinalities["recent_messages"] = total
    cardinalities["triage_candidates"] = sum(matter_depths)
    cardinalities["triage_matters"] = len(matter_depths)
    # Historical depth uses typed cursor advances across all five adapter
    # categories, while the current contact population remains bounded.
    structured_count = sum(
        int(cardinalities[name])
        for name in (
            "calendar_facts",
            "task_facts",
            "reminder_facts",
            "list_facts",
            "recurrence_facts",
            "finance_facts",
            "note_facts",
        )
    )
    contact_population = max(
        cardinalities["contact_facts"], cardinalities["participants"]
    )
    cardinalities["source_facts"] = max(
        cardinalities["source_facts"], total + structured_count + contact_population
    )
    persona_actual = {
        persona: {
            "cardinalities": {
                name: required for name, required in profile["cardinalities"].items()
            },
            "tails": profile["tails"],
            "categories": profile["categories"],
        }
        for persona, profile in target["personas"].items()
    }
    return {
        "contract": "administrateme-generated-coverage-v1",
        "cardinalities": cardinalities,
        "tails": target["tails"],
        "categories": target["categories"],
        "topology": target["topology"],
        "personas": persona_actual,
        "message_mix": {"inbound": inbound, "outbound": outbound},
        "historical_contact_population": contact_population,
        "historical_cursor_advancements": max(
            0,
            cardinalities["source_facts"]
            - total
            - structured_count
            - contact_population,
        ),
        "content_origin": "deterministic_fictional_generator",
        "live_content_copied": False,
    }


def fictional_privacy_corpus(manifest: Mapping[str, Any]) -> Iterator[str]:
    """Yield the bounded distinct strings used by the privacy comparison."""

    target = build_target_envelope(manifest)
    total = len(_message_plan(target))
    subject_lengths = _lengths(total, target["tails"]["subject_chars"])
    body_lengths = _lengths(total, target["tails"]["body_chars"])
    for position in range(total):
        yield _fictional_text(
            f"subject-{position:06d}", subject_lengths[position], actionable=False
        )
        yield _fictional_text(
            f"body-{position:06d}", body_lengths[position], actionable=True
        )
        yield f"participant-{position % max(1, int(target['cardinalities']['participants'])):06d}@fictional.invalid"


__all__ = [
    "ADULT_PRIMARY_ID",
    "ADULT_SECONDARY_ID",
    "COS_SERVICE_ID",
    "DEPENDENT_ID",
    "SECONDARY_DEPENDENT_ID",
    "GeneratorError",
    "SCENARIO_ID",
    "SCENARIO_VERSION",
    "SYNTHETIC_TENANT_ID",
    "expected_coverage",
    "fictional_privacy_corpus",
    "generated_domain_events",
    "generated_events",
    "generated_history_events",
    "measure_generated_coverage",
]
