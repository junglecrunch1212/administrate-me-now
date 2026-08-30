"""Durable, production-composed synthetic member scenario.

The scenario contains no copied household facts. It uses fictional provider
coordinates and source content, but every event enters the same encrypted log,
bus, projections, pipelines, Triage gates, and OpenClaw skill seam as the
central runtime.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import yaml

from adminme.context_fabric import policy_scope_digest
from adminme.events.envelope import EventEnvelope


SYNTHETIC_SCENARIO_ID = "member-life-v1"
SYNTHETIC_SCENARIO_VERSION = 1
SYNTHETIC_MEMBER_ID = "member_synthetic_principal"
SYNTHETIC_DEPENDENT_ID = "member_synthetic_dependent"
SYNTHETIC_IDENTITY = "synthetic-principal.local.invalid"
SYNTHETIC_MEMBER_EMAIL = "principal@member-fixture.invalid"
SYNTHETIC_SEEDED_AT = "2026-08-30T12:00:00Z"
_SYNTHETIC_KEY = hashlib.sha256(
    b"AdministrateMe credential-free synthetic member scenario v1"
).digest()


def prepare_synthetic_instance(instance_dir: Path) -> Path:
    """Create or validate a directory that can never be mistaken for live state."""

    resolved = instance_dir.expanduser().resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("synthetic instance directory cannot be a filesystem root")
    config_path = resolved / "config" / "instance.yaml"
    if config_path.exists():
        value = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if value.get("synthetic_scenario_id") != SYNTHETIC_SCENARIO_ID:
            raise ValueError(
                "refusing to use an instance directory without the matching "
                "synthetic scenario marker"
            )
        if value.get("synthetic_scenario_version") != SYNTHETIC_SCENARIO_VERSION:
            raise ValueError(
                "refusing to use an instance directory with an incompatible "
                "synthetic scenario version"
            )
        if not str(value.get("tenant_id") or "").startswith("tenant-synthetic-"):
            raise ValueError("synthetic instance tenant is not isolated")
        return resolved
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path.write_text(
        yaml.safe_dump(
            {
                "tenant_id": "tenant-synthetic-member-life-v1",
                "timezone": "America/New_York",
                "synthetic_scenario_id": SYNTHETIC_SCENARIO_ID,
                "synthetic_scenario_version": SYNTHETIC_SCENARIO_VERSION,
                "contains_real_household_data": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return resolved


def _connector(
    connection_id: str,
    kind: str,
    *,
    account_id: str,
    label: str,
    workspace: str = "personal",
    enable_writes: bool = False,
) -> dict[str, Any]:
    return {
        "id": connection_id,
        "kind": kind,
        "enabled": True,
        "config": {
            "member_id": SYNTHETIC_MEMBER_ID,
            "provider_account_id": account_id,
            "user_email": SYNTHETIC_MEMBER_EMAIL,
            "account_label": label,
            "workspace": workspace,
            "enable_writes": enable_writes,
            "ownership_attested": True,
            "synthetic": True,
        },
    }


def synthetic_adapter_config() -> dict[str, Any]:
    """Secret-free connector inventory used only by the synthetic runtime."""

    return {
        "adapter_supervisor_port": 0,
        "adapters": [
            _connector(
                "synthetic-gmail-personal",
                "messaging-gmail-api",
                account_id="gmail-personal-fixture",
                label="Personal Gmail (synthetic)",
                enable_writes=True,
            ),
            _connector(
                "synthetic-gmail-work",
                "messaging-gmail-api",
                account_id="gmail-work-fixture",
                label="Work Gmail (synthetic)",
                workspace="work",
                enable_writes=True,
            ),
            _connector(
                "synthetic-imessage",
                "messaging-bluebubbles",
                account_id="imessage-fixture",
                label="Messages (synthetic)",
                enable_writes=True,
            ),
            _connector(
                "synthetic-google-calendar",
                "calendaring-google-calendar",
                account_id="calendar-fixture",
                label="Google Calendar (synthetic)",
            ),
            _connector(
                "synthetic-apple-reminders",
                "reminders-apple-eventkit",
                account_id="reminders-fixture",
                label="Apple Reminders (synthetic)",
            ),
            _connector(
                "synthetic-google-tasks",
                "tasks-google-tasks",
                account_id="tasks-fixture",
                label="Google Tasks (synthetic)",
            ),
            _connector(
                "synthetic-apple-notes",
                "knowledge-apple-notes",
                account_id="notes-fixture",
                label="Apple Notes (synthetic)",
            ),
            _connector(
                "synthetic-plaid",
                "financial-plaid",
                account_id="finance-fixture",
                label="Household finances (synthetic)",
            ),
            _connector(
                "synthetic-contacts",
                "contacts-google-people",
                account_id="contacts-fixture",
                label="Google Contacts (synthetic)",
            ),
        ],
    }


def synthetic_household_mapping() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "household_label": "Synthetic household",
        "members": [
            {
                "member_id": SYNTHETIC_MEMBER_ID,
                "label": "Synthetic Principal",
                "role": "principal",
            },
            {
                "member_id": SYNTHETIC_DEPENDENT_ID,
                "label": "Synthetic Dependent",
                "role": "child",
            },
        ],
        "parties": [
            {
                "party_id": "party_synthetic_contact",
                "label": "Synthetic close contact",
                "kind": "person",
            },
            {
                "party_id": "party_synthetic_school",
                "label": "Synthetic school office",
                "kind": "organization",
            },
            {
                "party_id": "party_synthetic_utility",
                "label": "Synthetic utility provider",
                "kind": "organization",
            },
        ],
        "memberships": [
            {
                "membership_id": "membership_synthetic_principal",
                "party_id": SYNTHETIC_MEMBER_ID,
                "role": "principal",
            },
            {
                "membership_id": "membership_synthetic_dependent",
                "party_id": SYNTHETIC_DEPENDENT_ID,
                "role": "child",
            },
        ],
        "relationships": [
            {
                "relationship_id": "relationship_synthetic_parent",
                "from_party_id": SYNTHETIC_MEMBER_ID,
                "to_party_id": SYNTHETIC_DEPENDENT_ID,
                "kind": "parent",
                "visibility_scope": "shared:household",
            },
            {
                "relationship_id": "relationship_synthetic_contact",
                "from_party_id": SYNTHETIC_MEMBER_ID,
                "to_party_id": "party_synthetic_contact",
                "kind": "friend",
                "visibility_scope": f"private:{SYNTHETIC_MEMBER_ID}",
            },
        ],
    }


def build_synthetic_member_settings(
    instance_dir: Path,
    repository_root: Path,
    *,
    openclaw_gateway_token: str | None = None,
    openclaw_shared_secret: str | None = None,
) -> Any:
    """Build an isolated settings object; production env parsing cannot opt in."""

    from adminme.runtime.application import RuntimeSettings

    prepared = prepare_synthetic_instance(instance_dir)
    use_gateway = bool(openclaw_gateway_token)
    if use_gateway and not openclaw_shared_secret:
        raise ValueError("OpenClaw Gateway mode requires its shared secret")
    return RuntimeSettings.credential_free(
        prepared,
        encryption_key=_SYNTHETIC_KEY,
        repository_root=repository_root,
        member_identities={SYNTHETIC_IDENTITY: SYNTHETIC_MEMBER_ID},
        household_mapping=synthetic_household_mapping(),
        member_roles={
            SYNTHETIC_MEMBER_ID: "principal",
            SYNTHETIC_DEPENDENT_ID: "child",
        },
        deterministic_openclaw_fixture=not use_gateway,
        fixture_member_id=SYNTHETIC_MEMBER_ID,
        openclaw_enabled=True,
        openclaw_shared_secret=(
            openclaw_shared_secret
            if use_gateway
            else "synthetic-openclaw-fixture-secret-v1"
        ),
        openclaw_gateway_token=openclaw_gateway_token,
        member_chat_enabled_members=frozenset({SYNTHETIC_MEMBER_ID}),
        deterministic_member_chat_fixture=not use_gateway,
        openclaw_model_configured=True,
        adapter_config=synthetic_adapter_config(),
        synthetic_member_mode=True,
        synthetic_scenario_id=SYNTHETIC_SCENARIO_ID,
        synthetic_member_identity=SYNTHETIC_IDENTITY,
        synthetic_scenario_version=SYNTHETIC_SCENARIO_VERSION,
    )


def _event(
    tenant_id: str,
    event_type: str,
    schema_version: int,
    key: str,
    payload: dict[str, Any],
    *,
    source_adapter: str,
    source_account_id: str,
    occurred_at: str = SYNTHETIC_SEEDED_AT,
    sensitivity: str = "normal",
    shared: bool = False,
    offset_ms: int = 0,
) -> EventEnvelope:
    scope = "shared:household" if shared else f"private:{SYNTHETIC_MEMBER_ID}"
    return EventEnvelope(
        event_at_ms=1_777_550_400_000 + offset_ms,
        tenant_id=tenant_id,
        type=event_type,
        schema_version=schema_version,
        occurred_at=occurred_at,
        source_adapter=source_adapter,
        source_account_id=source_account_id,
        owner_scope=scope,
        visibility_scope=scope,
        sensitivity=sensitivity,  # type: ignore[arg-type]
        actor_identity=SYNTHETIC_MEMBER_ID,
        idempotency_key=f"synthetic-member:v1:{key}",
        payload=payload,
    )


def _message(
    tenant_id: str,
    *,
    key: str,
    account_id: str,
    sender: str,
    subject: str,
    body: str,
    labels: list[str],
    received_at: str,
    workspace: str = "personal",
    important: bool = False,
    thread_id: str | None = None,
    offset_ms: int,
) -> EventEnvelope:
    return _event(
        tenant_id,
        "messaging.received",
        4,
        f"message:{key}",
        {
            "source_channel": "gmail",
            "from_identifier": sender,
            "to_identifier": SYNTHETIC_MEMBER_EMAIL,
            "to_identifiers": [SYNTHETIC_MEMBER_EMAIL],
            "thread_id": thread_id or f"synthetic-thread-{key}",
            "subject": subject,
            "body_text": body,
            "body_html": None,
            "received_at": received_at,
            "attachments": [],
            "native_message_id": f"synthetic-message-{key}",
            "member_id": SYNTHETIC_MEMBER_ID,
            "provider": "gmail",
            "provider_account_id": account_id,
            "workspace": workspace,
            "cc_identifiers": [],
            "bcc_identifiers": [],
            "reply_to_identifier": None,
            "rfc_message_id": f"<synthetic-{key}@member-fixture.invalid>",
            "references": [],
            "provider_labels": labels,
            "unread": True,
            "archived": False,
            "starred": important,
            "important": important,
            "in_reply_to": None,
        },
        source_adapter="gmail",
        source_account_id=account_id,
        occurred_at=received_at,
        offset_ms=offset_ms,
    )


def synthetic_seed_events(tenant_id: str) -> list[EventEnvelope]:
    messages = [
        _message(
            tenant_id,
            key="real-contact-request",
            account_id="gmail-personal-fixture",
            sender="close-contact@member-fixture.invalid",
            subject="Signed form needed",
            body=(
                "Please send the signed household form by Tuesday. "
                "The receiving office needs one PDF copy."
            ),
            labels=["INBOX", "IMPORTANT"],
            received_at="2026-08-30T11:42:00Z",
            important=True,
            offset_ms=100,
        ),
        _message(
            tenant_id,
            key="school-request",
            account_id="gmail-personal-fixture",
            sender="office@school-fixture.invalid",
            subject="Permission response due",
            body=(
                "Please answer the school permission request by Friday. "
                "A response is required even if the dependent will not attend."
            ),
            labels=["INBOX", "IMPORTANT"],
            received_at="2026-08-30T10:15:00Z",
            important=True,
            offset_ms=200,
        ),
        _message(
            tenant_id,
            key="work-request",
            account_id="gmail-work-fixture",
            sender="project-lead@work-fixture.invalid",
            subject="Review requested",
            body=(
                "Review the project brief before the 3 PM meeting. "
                "Please leave comments on the open questions."
            ),
            labels=["INBOX", "IMPORTANT"],
            received_at="2026-08-30T09:05:00Z",
            workspace="work",
            important=True,
            offset_ms=300,
        ),
        _message(
            tenant_id,
            key="signin-one",
            account_id="gmail-personal-fixture",
            sender="security@provider-one-fixture.invalid",
            subject="New sign-in detected",
            body=(
                "Review the new sign-in detected on your account. "
                "If this was you, no provider change is required."
            ),
            labels=["INBOX", "CATEGORY_UPDATES"],
            received_at="2026-08-30T08:34:00Z",
            thread_id="synthetic-thread-signin-notices",
            offset_ms=400,
        ),
        _message(
            tenant_id,
            key="signin-two",
            account_id="gmail-personal-fixture",
            sender="security@provider-two-fixture.invalid",
            subject="New sign-in detected",
            body=(
                "Review the new sign-in detected on a second account. "
                "The activity came from a new browser."
            ),
            labels=["INBOX", "CATEGORY_UPDATES"],
            received_at="2026-08-30T08:31:00Z",
            thread_id="synthetic-thread-signin-notices",
            offset_ms=500,
        ),
        _message(
            tenant_id,
            key="utility-due",
            account_id="gmail-personal-fixture",
            sender="billing@utility-fixture.invalid",
            subject="Monthly statement ready",
            body=(
                "Pay the synthetic utility statement by September 5. "
                "This bill repeats monthly."
            ),
            labels=["INBOX", "CATEGORY_UPDATES"],
            received_at="2026-08-30T07:45:00Z",
            offset_ms=600,
        ),
        _message(
            tenant_id,
            key="promotion",
            account_id="gmail-personal-fixture",
            sender="offers@retail-fixture.invalid",
            subject="Weekend promotion",
            body="Save on fictional household supplies through Sunday.",
            labels=["CATEGORY_PROMOTIONS"],
            received_at="2026-08-30T06:10:00Z",
            offset_ms=700,
        ),
        _message(
            tenant_id,
            key="social",
            account_id="gmail-personal-fixture",
            sender="digest@social-fixture.invalid",
            subject="Social activity digest",
            body="Three fictional contacts posted updates this week.",
            labels=["CATEGORY_SOCIAL"],
            received_at="2026-08-29T22:05:00Z",
            offset_ms=800,
        ),
        _message(
            tenant_id,
            key="spam",
            account_id="gmail-personal-fixture",
            sender="noise@bulk-fixture.invalid",
            subject="Bulk notice",
            body="This synthetic bulk message should not look like an obligation.",
            labels=["SPAM"],
            received_at="2026-08-29T20:05:00Z",
            offset_ms=900,
        ),
    ]
    events: list[EventEnvelope] = [*messages]
    for index, (external_id, summary, start, end) in enumerate(
        [
            (
                "calendar-focus",
                "Focused work block",
                "2026-08-31T13:00:00-04:00",
                "2026-08-31T14:30:00-04:00",
            ),
            (
                "calendar-school",
                "School office appointment",
                "2026-09-01T09:00:00-04:00",
                "2026-09-01T09:30:00-04:00",
            ),
            (
                "calendar-household",
                "Household planning",
                "2026-09-02T18:00:00-04:00",
                "2026-09-02T18:30:00-04:00",
            ),
        ],
        start=1,
    ):
        events.append(
            _event(
                tenant_id,
                "calendar.event_added",
                2,
                f"calendar:{external_id}",
                {
                    "member_id": SYNTHETIC_MEMBER_ID,
                    "provider": "google_calendar",
                    "provider_account_id": "calendar-fixture",
                    "calendar_external_id": "primary-fixture-calendar",
                    "calendar_name": "Primary calendar (synthetic)",
                    "external_event_id": external_id,
                    "status": "confirmed",
                    "summary": summary,
                    "start": start,
                    "end": end,
                    "start_time_zone": "America/New_York",
                    "end_time_zone": "America/New_York",
                    "availability": "busy",
                    "updated_at": SYNTHETIC_SEEDED_AT,
                },
                source_adapter="calendaring-google-calendar",
                source_account_id="calendar-fixture",
                offset_ms=1_000 + index,
            )
        )
    for index, (external_id, title, due, priority) in enumerate(
        [
            ("task-call", "Return the service call", (2026, 8, 31), 1),
            ("task-groceries", "Add weekly staples", (2026, 9, 1), 5),
            ("task-renewal", "Review the annual renewal", (2026, 9, 4), 5),
        ],
        start=1,
    ):
        year, month, day = due
        events.append(
            _event(
                tenant_id,
                "list_item.added",
                3,
                f"list-item:{external_id}",
                {
                    "member_id": SYNTHETIC_MEMBER_ID,
                    "provider": "google_tasks",
                    "provider_account_id": "tasks-fixture",
                    "list_external_id": "default-fixture-list",
                    "item_external_id": external_id,
                    "list_name": "Personal tasks (synthetic)",
                    "title": title,
                    "priority": priority,
                    "completed": False,
                    "due": {
                        "year": year,
                        "month": month,
                        "day": day,
                        "all_day": True,
                        "floating": True,
                    },
                    "alarms": [],
                    "recurrence_rules": [],
                    "completion_policy": "manual",
                    "last_modified_at": SYNTHETIC_SEEDED_AT,
                    "sync_cycle_id": "synthetic-seed-v1",
                    "provider_position": f"{index:04d}",
                    "source_links": [],
                    "provider_data": {},
                },
                source_adapter="tasks-google-tasks",
                source_account_id="tasks-fixture",
                offset_ms=1_100 + index,
            )
        )
    events.extend(
        [
            _event(
                tenant_id,
                "financial.account_observed",
                1,
                "finance:account",
                {
                    "member_id": SYNTHETIC_MEMBER_ID,
                    "plaid_item_id": "plaid-item-fixture",
                    "account_id": "financial-account-fixture",
                    "institution_id": "institution-fixture",
                    "institution_name": "Synthetic financial institution",
                    "display_name": "Household checking (synthetic)",
                    "account_type": "depository",
                    "account_subtype": "checking",
                    "last4": "0000",
                    "current_balance_minor": 425_000,
                    "available_balance_minor": 410_000,
                    "currency": "USD",
                    "as_of": SYNTHETIC_SEEDED_AT,
                    "link_healthy": True,
                },
                source_adapter="financial-plaid",
                source_account_id="finance-fixture",
                sensitivity="privileged",
                offset_ms=1_200,
            ),
            _event(
                tenant_id,
                "money_flow.provider_observed",
                1,
                "finance:flow",
                {
                    "member_id": SYNTHETIC_MEMBER_ID,
                    "plaid_item_id": "plaid-item-fixture",
                    "account_id": "financial-account-fixture",
                    "transaction_id": "transaction-fixture-001",
                    "amount_minor": 8_450,
                    "currency": "USD",
                    "occurred_at": "2026-08-29T15:00:00Z",
                    "kind": "paid",
                    "merchant_name": "Synthetic utility provider",
                    "merchant_category": "Utilities",
                    "pending": False,
                    "provider_revision": "synthetic-revision-1",
                    "observed_at": SYNTHETIC_SEEDED_AT,
                },
                source_adapter="financial-plaid",
                source_account_id="finance-fixture",
                sensitivity="privileged",
                offset_ms=1_201,
            ),
            _event(
                tenant_id,
                "triage.feedback.recorded",
                1,
                "feedback:recurrence:one",
                {
                    "feedback_id": "synthetic-feedback-recurrence-one",
                    "proposal_id": "synthetic-resolved-recurrence-one",
                    "member_id": SYNTHETIC_MEMBER_ID,
                    "reason": "recurring_normal",
                    "target_kind": "recurrence",
                    "source_adapter": "gmail",
                    "provider_account_id": "gmail-personal-fixture",
                    "workspace": "personal",
                    "recorded_at": "2026-08-20T12:00:00Z",
                },
                source_adapter="member-triage",
                source_account_id=SYNTHETIC_MEMBER_ID,
                offset_ms=1_300,
            ),
            _event(
                tenant_id,
                "triage.feedback.recorded",
                1,
                "feedback:recurrence:two",
                {
                    "feedback_id": "synthetic-feedback-recurrence-two",
                    "proposal_id": "synthetic-resolved-recurrence-two",
                    "member_id": SYNTHETIC_MEMBER_ID,
                    "reason": "recurring_normal",
                    "target_kind": "recurrence",
                    "source_adapter": "gmail",
                    "provider_account_id": "gmail-personal-fixture",
                    "workspace": "personal",
                    "recorded_at": "2026-08-24T12:00:00Z",
                },
                source_adapter="member-triage",
                source_account_id=SYNTHETIC_MEMBER_ID,
                offset_ms=1_301,
            ),
            _event(
                tenant_id,
                "triage.policy_rule.approved",
                1,
                "rule:recurrence-low-signal",
                {
                    "rule_id": "synthetic-rule-recurring-normal",
                    "member_id": SYNTHETIC_MEMBER_ID,
                    "scope_kind": "candidate_kind",
                    "scope_value_digest": policy_scope_digest(
                        "candidate_kind", "recurrence"
                    ),
                    "disposition": "route_to_low_signal",
                    "enforcement_mode": "shadow",
                    "preview_affected_count": 2,
                    "approved_by_member_id": SYNTHETIC_MEMBER_ID,
                    "approved_at": "2026-08-25T12:00:00Z",
                },
                source_adapter="member-triage",
                source_account_id=SYNTHETIC_MEMBER_ID,
                offset_ms=1_302,
            ),
        ]
    )
    return events


async def reconcile_synthetic_member(runtime: Any) -> None:
    """Idempotently seed source facts, then settle the production consumers."""

    if runtime.events is None or runtime.event_log is None:
        raise RuntimeError("synthetic scenario requires a started runtime")
    marker_key = "synthetic-member:v1:message:real-contact-request"
    existing = await runtime.event_log.get_by_idempotency(
        runtime.settings.instance.tenant_id, marker_key
    )
    if existing is None:
        await runtime.events.write_batch(
            synthetic_seed_events(runtime.settings.instance.tenant_id)
        )

    seeded_events: list[dict[str, Any]] = []
    async for event in runtime.event_log.read_since():
        if str(event.get("idempotency_key") or "").startswith("synthetic-member:v1:"):
            seeded_events.append(event)
    latest_message_id = max(
        (
            str(event["event_id"])
            for event in seeded_events
            if event["type"] == "messaging.received"
        ),
        default=None,
    )
    if latest_message_id is not None:
        await runtime.wait_for_subscriber(
            "pipeline:pipeline:noise_filtering", latest_message_id, timeout_s=20.0
        )

    proposal_ids: list[str] = []
    async for event in runtime.event_log.read_since(types=["triage.item.proposed"]):
        if (event.get("payload") or {}).get("owner_member_id") == SYNTHETIC_MEMBER_ID:
            proposal_ids.append(str(event["event_id"]))
    waits = []
    if proposal_ids:
        waits.append(runtime.wait_for_projection("triage", max(proposal_ids)))
    projection_types = {
        "interactions": {"messaging.received"},
        "calendars": {"calendar.event_added"},
        "lists": {"list_item.added"},
        "money": {"financial.account_observed", "money_flow.provider_observed"},
        "context_fabric": {
            "triage.feedback.recorded",
            "triage.policy_rule.approved",
        },
    }
    for projection, event_types in projection_types.items():
        checkpoint_event_id = max(
            (
                str(event["event_id"])
                for event in seeded_events
                if event["type"] in event_types
            ),
            default=None,
        )
        if (
            checkpoint_event_id is not None
            and projection in runtime.projections.projection_names()
        ):
            waits.append(runtime.wait_for_projection(projection, checkpoint_event_id))
    if waits:
        await asyncio.gather(*waits)


__all__ = [
    "SYNTHETIC_DEPENDENT_ID",
    "SYNTHETIC_IDENTITY",
    "SYNTHETIC_MEMBER_ID",
    "SYNTHETIC_SCENARIO_ID",
    "SYNTHETIC_SCENARIO_VERSION",
    "build_synthetic_member_settings",
    "prepare_synthetic_instance",
    "reconcile_synthetic_member",
    "synthetic_seed_events",
]
