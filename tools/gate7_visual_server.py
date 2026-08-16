"""Deterministic local-only portal server for Gate 7 rendered evidence."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import HTTPException

from adminme.runtime.application import RuntimeSettings, create_application
from adminme.events.envelope import EventEnvelope
from adminme.events.log import _EventIdGenerator
from adminme.lib.governance import GuardedWriteResult


ROOT = Path(__file__).resolve().parents[1]
INSTANCE = Path(os.environ["ADMINME_GATE7_VISUAL_INSTANCE"]).resolve()
PORT = int(os.environ.get("ADMINME_GATE7_VISUAL_PORT", "3347"))
MODEL_CONFIGURED = os.environ.get("ADMINME_GATE7_VISUAL_MODEL", "1") == "1"
FIXED_NOW = datetime.fromisoformat(
    os.environ.get("ADMINME_GATE7_VISUAL_NOW", "2026-08-13T14:00:00Z").replace(
        "Z", "+00:00"
    )
).astimezone(timezone.utc)
FIXTURE_CLOCK = [FIXED_NOW]

PRINCIPAL = "visual_principal"
ADULT = "visual_adult"
CHILD = "visual_child"
DEVICE = "visual_shared_screen"
_ORIGINAL_EVENT_ID_MINT = _EventIdGenerator.mint


class _Gate7CalendarRequest:
    def __init__(self, service: "_Gate7CalendarService") -> None:
        self._service = service

    def execute(self) -> dict[str, Any]:
        self._service.list_calls += 1
        if self._service.mode == "transient":
            raise ConnectionResetError("credential-free Calendar transient")
        if self._service.mode == "malformed":
            return {
                "items": [
                    {
                        "summary": "Malformed fixture record",
                        "start": {"dateTime": "2026-08-13T15:00:00Z"},
                        "end": {"dateTime": "2026-08-13T16:00:00Z"},
                    }
                ],
                "nextSyncToken": "must-not-advance",
            }
        return {
            "items": [
                {
                    "id": "gate7-calendar-recovered-event",
                    "etag": "gate7-calendar-revision-1",
                    "summary": "Credential-free recovered event",
                    "start": {"dateTime": "2026-08-13T15:00:00Z"},
                    "end": {"dateTime": "2026-08-13T16:00:00Z"},
                    "updated": "2026-08-13T14:00:00Z",
                },
                {
                    "id": "gate7-calendar-recovered-deletion",
                    "etag": "gate7-calendar-revision-2",
                    "status": "cancelled",
                    "updated": "2026-08-13T14:01:00Z",
                },
            ],
            "nextSyncToken": "gate7-calendar-sync-token",
        }


class _Gate7CalendarEvents:
    def __init__(self, service: "_Gate7CalendarService") -> None:
        self._service = service

    def list(self, **_kwargs: Any) -> _Gate7CalendarRequest:
        return _Gate7CalendarRequest(self._service)

    def insert(self, **_kwargs: Any) -> Any:
        self._service.write_calls += 1
        raise AssertionError("Gate 7 Calendar fixture forbids provider writes")


class _Gate7CalendarService:
    def __init__(self) -> None:
        self.mode = "transient"
        self.list_calls = 0
        self.write_calls = 0

    def events(self) -> _Gate7CalendarEvents:
        return _Gate7CalendarEvents(self)


class _FixtureDateTime(datetime):
    """Clock used only by this credential-free subprocess fixture."""

    @classmethod
    def now(cls, tz=None):
        value = FIXTURE_CLOCK[0]
        return value if tz is None else value.astimezone(tz)


class _FixtureTime:
    """Wall clock proxy that preserves monotonic and sleep behavior."""

    @staticmethod
    def time() -> float:
        return FIXTURE_CLOCK[0].timestamp()

    def __getattr__(self, name):
        return getattr(time, name)


def _install_determinism() -> None:
    """Freeze already-imported AdministrateMe clocks and generated UUIDs."""

    original_datetime = datetime
    for name, module in tuple(sys.modules.items()):
        if (
            name.startswith("adminme")
            and getattr(module, "datetime", None) is original_datetime
        ):
            setattr(module, "datetime", _FixtureDateTime)
    import adminme.temporary_surfaces as temporary_surfaces

    temporary_surfaces._now = fixture_now
    sequence = count(1)
    uuid.uuid4 = lambda: uuid.UUID(int=next(sequence) << 96)
    _EventIdGenerator.mint = lambda generator, _now_ms: _ORIGINAL_EVENT_ID_MINT(
        generator, int(FIXTURE_CLOCK[0].timestamp() * 1000)
    )
    for name in (
        "adminme.lib.governance",
        "adminme.lib.observation",
        "adminme.products.commitment_service",
        "adminme.products.member.main",
        "adminme.runtime.application",
    ):
        module = sys.modules.get(name)
        if module is not None and getattr(module, "time", None) is time:
            setattr(module, "time", _FixtureTime())


def fixture_now() -> datetime:
    return FIXTURE_CLOCK[0]


def build_app():
    _install_determinism()
    INSTANCE.mkdir(parents=True, exist_ok=True)
    config = INSTANCE / "config"
    config.mkdir(exist_ok=True)
    (config / "instance.yaml").write_text(
        "tenant_id: tenant-gate7-visual\ntimezone: Etc/UTC\n",
        encoding="utf-8",
    )
    (config / "extensions-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active": {
                    "persona": "quiet_assistant",
                    "profiles": {
                        PRINCIPAL: "adhd_executive",
                        ADULT: "minimalist_parent",
                        CHILD: "kid_scoreboard",
                    },
                },
                "previous": None,
                "staged": {},
            }
        ),
        encoding="utf-8",
    )
    calendar_service = _Gate7CalendarService()
    settings = RuntimeSettings.credential_free(
        INSTANCE,
        encryption_key=b"v" * 32,
        repository_root=ROOT,
        member_identities={
            "visual-principal.ts.net": PRINCIPAL,
            "visual-adult.ts.net": ADULT,
            "visual-child.ts.net": CHILD,
            "visual-screen.ts.net": DEVICE,
        },
        member_roles={
            PRINCIPAL: "principal",
            ADULT: "principal",
            CHILD: "child",
            DEVICE: "device",
        },
        deterministic_openclaw_fixture=True,
        fixture_member_id=PRINCIPAL,
        openclaw_enabled=True,
        openclaw_shared_secret="gate7-visual-secret-never-rendered",
        openclaw_model_configured=MODEL_CONFIGURED,
        member_chat_enabled_members=frozenset({PRINCIPAL, ADULT, CHILD}),
        child_chat_enabled_members=frozenset({CHILD}),
        deterministic_member_chat_fixture=True,
        adapter_config={
            "adapter_supervisor_port": 0,
            "adapters": [
                {
                    "id": "calendar-health-fixture",
                    "kind": "calendaring-google-calendar",
                    "config": {
                        "oauth_credentials_ref": "keychain://fixture/calendar",
                        "calendar_id": "primary",
                        "calendar_name": "Personal calendar",
                        "member_id": PRINCIPAL,
                        "provider_account_id": "visual-calendar-account",
                        "poll_interval_s": 60,
                    },
                }
            ],
        },
    )
    import adminme.runtime.application as runtime_application

    real_adapter_loader = runtime_application.load_builtin_adapters

    def gate7_adapter_loader(
        config: dict[str, Any], *, repository_root: Path
    ) -> list[Any]:
        adapters = real_adapter_loader(config, repository_root=repository_root)
        for adapter in adapters:
            if adapter.name != "calendaring-google-calendar":
                continue
            adapter._credentials_resolver = lambda _ref: {}
            adapter._service_factory = lambda *_args: calendar_service
            adapter._poll_interval_s = 0.1
        return adapters

    runtime_application.load_builtin_adapters = gate7_adapter_loader
    app = create_application(settings)

    @app.post("/__gate7/calendar-health/{mode}")
    async def set_calendar_health(mode: str):
        """Drive only synthetic Calendar protocol states for rendered QA."""

        if mode not in {"open", "malformed", "recovered"}:
            raise HTTPException(400, "unsupported Calendar fixture mode")
        runtime = app.state.runtime
        if runtime.adapters is None or runtime.adapter_cursor_store is None:
            raise HTTPException(503, "Calendar fixture runtime is unavailable")
        managed = runtime.adapters._managed.get("calendar-health-fixture")  # noqa: SLF001
        if managed is None:
            raise HTTPException(503, "Calendar fixture adapter is unavailable")
        store = runtime.adapter_cursor_store
        cursor_id = "calendar-health-fixture"
        if mode in {"open", "malformed"}:
            calendar_service.mode = "transient" if mode == "open" else "malformed"
            while store.get(cursor_id).error_count < 3:
                store.record_error(cursor_id)
            if mode == "malformed":
                store.begin_recovery(cursor_id)
        else:
            calendar_service.mode = "recovered"
            if store.get(cursor_id).circuit_state == "open":
                store.begin_recovery(cursor_id)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            cursor = store.get(cursor_id)
            state = managed.adapter.status().state
            expected = (
                cursor.circuit_state == "closed" and state == "running"
                if mode == "recovered"
                else cursor.circuit_state == "open" and state != "running"
            )
            if expected:
                return {
                    "mode": mode,
                    "state": state,
                    "circuit_state": cursor.circuit_state,
                    "cursor_present": cursor.provider_watermark is not None,
                    "provider_writes": calendar_service.write_calls,
                }
            await asyncio.sleep(0.02)
        raise HTTPException(503, "Calendar fixture state did not settle")

    def install_triage_failure_hooks() -> None:
        """Install opt-in, one-shot failures inside the production handlers."""

        runtime = app.state.runtime
        if getattr(app.state, "triage_failure_hooks_installed", False):
            return
        original_check = runtime.guarded_write.check
        original_write = runtime.events.write

        async def checked_write(session, action, payload):
            proposal_denials = getattr(app.state, "fail_next_intent_proposal_guards", 0)
            if proposal_denials and action == "triage.item.proposed":
                app.state.fail_next_intent_proposal_guards = proposal_denials - 1
                return GuardedWriteResult(
                    pass_=False,
                    layer_failed="governance",
                    reason="gate7_post_capture_proposal_fixture",
                    correlation_id=session.correlation_id,
                )
            if (
                getattr(app.state, "fail_next_task_materialization", False)
                and action == "task.created"
            ):
                app.state.fail_next_task_materialization = False
                return GuardedWriteResult(
                    pass_=False,
                    layer_failed="governance",
                    reason="gate7_post_confirmation_fixture",
                    correlation_id=session.correlation_id,
                )
            return await original_check(session, action, payload)

        async def event_write(envelope, **kwargs):
            if (
                getattr(app.state, "fail_next_intent_feature_append", False)
                and envelope.type == "triage.intent.features_extracted"
            ):
                app.state.fail_next_intent_feature_append = False
                raise HTTPException(
                    503,
                    "gate7 fixture interrupted intent processing after durable capture",
                )
            return await original_write(envelope, **kwargs)

        runtime.guarded_write.check = checked_write
        runtime.events.write = event_write
        app.state.triage_failure_hooks_installed = True

    @app.middleware("http")
    async def local_fixture_identity(request, call_next):
        """Let an ordinary localhost browser exercise the fixture safely.

        The production application still requires the Tailscale identity
        header. This helper exists only in this credential-free visual server,
        binds to loopback, and never overrides an explicitly supplied fixture
        identity used by the role/isolation harness.
        """

        if not request.headers.get("Tailscale-User-Login"):
            request.scope["headers"].append(
                (b"tailscale-user-login", b"visual-principal.ts.net")
            )
        return await call_next(request)

    @app.post("/__gate7/seed-calendar")
    async def seed_calendar():
        """Seed synthetic scoped facts for rendered Calendar evidence only."""

        runtime = app.state.runtime
        now = fixture_now()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        common = {
            "event_at_ms": int(now.timestamp() * 1000),
            "tenant_id": runtime.settings.instance.tenant_id,
            "occurred_at": now.isoformat(),
            "source_adapter": "gate7-calendar-fixture",
            "source_account_id": "credential-free",
            "owner_scope": f"private:{PRINCIPAL}",
            "visibility_scope": f"private:{PRINCIPAL}",
            "sensitivity": "normal",
            "actor_identity": PRINCIPAL,
        }
        events = [
            EventEnvelope(
                **common,
                type="calendar.source_observed",
                schema_version=1,
                idempotency_key="gate7:visual:calendar-source:personal",
                payload={
                    "member_id": PRINCIPAL,
                    "provider": "google_calendar",
                    "provider_account_id": "visual-calendar-account",
                    "calendar_external_id": "visual-personal",
                    "calendar_name": "Personal",
                    "access_role": "owner",
                    "primary": True,
                    "selected": True,
                    "read_details": True,
                    "read_free_busy": True,
                    "write_events": True,
                    "observed_at": now.isoformat(),
                },
            ),
            EventEnvelope(
                **common,
                type="calendar.source_observed",
                schema_version=1,
                idempotency_key="gate7:visual:calendar-source:household",
                payload={
                    "member_id": PRINCIPAL,
                    "provider": "google_calendar",
                    "provider_account_id": "visual-calendar-account",
                    "calendar_external_id": "visual-household",
                    "calendar_name": "Household context",
                    "access_role": "reader",
                    "selected": True,
                    "read_details": True,
                    "read_free_busy": True,
                    "write_events": False,
                    "observed_at": now.isoformat(),
                },
            ),
            EventEnvelope(
                **common,
                type="calendar.rule.configured",
                schema_version=1,
                idempotency_key="gate7:visual:calendar-rule:personal",
                payload={
                    "rule_id": "gate7-visual-calendar-personal",
                    "subject_member_id": PRINCIPAL,
                    "source_member_id": PRINCIPAL,
                    "provider": "google_calendar",
                    "provider_account_id": "visual-calendar-account",
                    "calendar_external_id": "visual-personal",
                    "purpose": "personal",
                    "effect": "hard_block",
                    "tentative_effect": "soft_hold",
                    "caregiving_load": "ignore",
                    "caregiving_summary_values": [],
                    "buffer_kind": "appointment",
                    "write_target": True,
                    "destination_kind": "personal",
                    "enabled": True,
                    "configured_at": now.isoformat(),
                },
            ),
            EventEnvelope(
                **common,
                type="calendar.rule.configured",
                schema_version=1,
                idempotency_key="gate7:visual:calendar-rule:household",
                payload={
                    "rule_id": "gate7-visual-calendar-household",
                    "subject_member_id": PRINCIPAL,
                    "source_member_id": PRINCIPAL,
                    "provider": "google_calendar",
                    "provider_account_id": "visual-calendar-account",
                    "calendar_external_id": "visual-household",
                    "purpose": "family_context",
                    "effect": "context",
                    "tentative_effect": "soft_hold",
                    "caregiving_load": "ignore",
                    "caregiving_summary_values": [],
                    "buffer_kind": "none",
                    "write_target": False,
                    "enabled": True,
                    "configured_at": now.isoformat(),
                },
            ),
            EventEnvelope(
                **common,
                type="calendar.schedule_policy.configured",
                schema_version=1,
                idempotency_key="gate7:visual:calendar-policy",
                payload={
                    "member_id": PRINCIPAL,
                    "timezone": "Etc/UTC",
                    "windows": [
                        {
                            "days": [0, 1, 2, 3, 4],
                            "start_local": "00:00",
                            "end_local": "10:00",
                            "kind": "protected",
                            "effect": "hard_block",
                            "label": "Protected morning",
                        },
                        {
                            "days": [0, 1, 2, 3, 4],
                            "start_local": "10:30",
                            "end_local": "17:00",
                            "kind": "work",
                            "effect": "context",
                            "label": "Work window",
                        },
                    ],
                    "buffer_minutes": {
                        "appointment_before": 30,
                        "appointment_after": 15,
                    },
                    "accepted_chores_required": True,
                    "one_confirm_write": True,
                    "configured_at": now.isoformat(),
                },
            ),
            EventEnvelope(
                **common,
                type="calendar.event_added",
                schema_version=2,
                idempotency_key="gate7:visual:calendar-event:appointment",
                payload={
                    "member_id": PRINCIPAL,
                    "provider": "google_calendar",
                    "provider_account_id": "visual-calendar-account",
                    "calendar_external_id": "visual-personal",
                    "calendar_name": "Personal",
                    "external_event_id": "visual-appointment",
                    "summary": "Reviewed appointment",
                    "location": "Provider location",
                    "start": (day + timedelta(hours=13)).isoformat(),
                    "end": (day + timedelta(hours=14)).isoformat(),
                    "updated_at": now.isoformat(),
                },
            ),
            EventEnvelope(
                **common,
                type="calendar.event_added",
                schema_version=2,
                idempotency_key="gate7:visual:calendar-event:context",
                payload={
                    "member_id": PRINCIPAL,
                    "provider": "google_calendar",
                    "provider_account_id": "visual-calendar-account",
                    "calendar_external_id": "visual-household",
                    "calendar_name": "Household context",
                    "external_event_id": "visual-household-day",
                    "summary": "Household context day",
                    "start": day.isoformat(),
                    "end": (day + timedelta(days=1)).isoformat(),
                    "all_day": True,
                    "updated_at": now.isoformat(),
                },
            ),
        ]
        last_event_id = 0
        for event in events:
            last_event_id = await runtime.events.write(event)
        await runtime.wait_for_projection("calendars", last_event_id)
        return {"seeded": len(events), "last_event_id": last_event_id}

    @app.post("/__gate7/advance-clock")
    async def advance_clock():
        """Advance only the deterministic credential-free fixture clock."""

        FIXTURE_CLOCK[0] += timedelta(seconds=2)
        expired = app.state.runtime.temporary_surfaces.expire_due()
        return {
            "now": FIXTURE_CLOCK[0].isoformat(),
            "expired": [spec.stable_id for spec in expired],
        }

    @app.post("/__gate7/seed-triage-task")
    async def seed_triage_task():
        """Seed one unconfirmed task proposal for structured-form evidence."""

        runtime = app.state.runtime
        now = fixture_now()
        event_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(now.timestamp() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="household.action.proposed",
                schema_version=1,
                occurred_at=now.isoformat(),
                source_adapter="gate7-triage-fixture",
                source_account_id=PRINCIPAL,
                owner_scope=f"private:{PRINCIPAL}",
                visibility_scope=f"private:{PRINCIPAL}",
                sensitivity="normal",
                actor_identity=PRINCIPAL,
                idempotency_key="gate7:visual:triage-task",
                payload={
                    "proposal_id": "gate7-visual-triage-task",
                    "kind": "task",
                    "title": "Send the completed enrollment form",
                    "owner_member_id": PRINCIPAL,
                    "due": (now + timedelta(days=2)).replace(hour=17).isoformat(),
                    "reason": "explicit_proposal",
                    "provenance": {"fixture": "gate7-visual"},
                },
            )
        )
        await runtime.wait_for_projection("triage", event_id)
        return {"seeded": 1, "event_id": event_id}

    @app.post("/__gate7/seed-triage-google-task")
    async def seed_triage_google_task():
        """Seed a routed Google Tasks proposal for capability-ceiling QA."""

        runtime = app.state.runtime
        now = fixture_now()
        common = {
            "event_at_ms": int(now.timestamp() * 1000),
            "tenant_id": runtime.settings.instance.tenant_id,
            "occurred_at": now.isoformat(),
            "source_adapter": "gate7-triage-fixture",
            "source_account_id": PRINCIPAL,
            "owner_scope": f"private:{PRINCIPAL}",
            "visibility_scope": f"private:{PRINCIPAL}",
            "sensitivity": "normal",
            "actor_identity": PRINCIPAL,
        }
        list_event = await runtime.events.write(
            EventEnvelope(
                **common,
                type="list.created",
                schema_version=1,
                idempotency_key="gate7:visual:google-list",
                payload={
                    "member_id": PRINCIPAL,
                    "provider": "google_tasks",
                    "provider_account_id": "visual-google-tasks",
                    "list_external_id": "visual-google-list",
                    "list_name": "Personal tasks",
                    "is_default": True,
                    "changed_at": now.isoformat(),
                },
            )
        )
        await runtime.wait_for_projection("lists", list_event)
        proposal_event = await runtime.events.write(
            EventEnvelope(
                **common,
                type="triage.item.proposed",
                schema_version=1,
                idempotency_key="gate7:visual:triage-google-task",
                causation_id=list_event,
                payload={
                    "proposal_id": "gate7-visual-google-task",
                    "target_kind": "reminder",
                    "owner_member_id": PRINCIPAL,
                    "title": "Renew the annual registration",
                    "candidate": {
                        "due": (now + timedelta(days=10)).replace(hour=14).isoformat(),
                        "notes": "Provider-ceiling visual fixture.",
                    },
                    "route": {
                        "member_id": PRINCIPAL,
                        "provider": "google_tasks",
                        "provider_account_id": "visual-google-tasks",
                        "container_external_id": "visual-google-list",
                    },
                    "source_event_id": list_event,
                    "confidence": 0.98,
                    "reasons": ["explicit provider reminder proposal"],
                },
            )
        )
        await runtime.wait_for_projection("triage", proposal_event)
        return {"seeded": 2, "last_event_id": proposal_event}

    @app.post("/__gate7/seed-triage-continuity")
    async def seed_triage_continuity():
        """Seed queue-continuity and historical-receipt browser evidence."""

        runtime = app.state.runtime
        now = fixture_now()
        common = {
            "event_at_ms": int(now.timestamp() * 1000),
            "tenant_id": runtime.settings.instance.tenant_id,
            "occurred_at": now.isoformat(),
            "source_adapter": "gate7-triage-fixture",
            "source_account_id": PRINCIPAL,
            "owner_scope": f"private:{PRINCIPAL}",
            "visibility_scope": f"private:{PRINCIPAL}",
            "sensitivity": "normal",
            "actor_identity": PRINCIPAL,
        }
        proposals = [
            ("gate7-visual-dismiss-task", "Ignore the promotional digest"),
            ("gate7-visual-next-task", "Review the household insurance quote"),
            ("gate7-visual-ambiguous-task", "Verify the summer camp packing list"),
            (
                "gate7-visual-mismatched-receipt-task",
                "Review the caregiver authorization receipt",
            ),
            (
                "gate7-visual-system-dismiss-task",
                "Review the system-transition dismissal",
            ),
            ("gate7-visual-guarded-task", "Reconcile the guarded camp form"),
            (
                "gate7-visual-disposition-task",
                "Attach the insurance note to its existing task",
            ),
            ("gate7-visual-retired-task", "Archive the outdated enrollment reminder"),
        ]
        event_ids: list[str] = []
        for proposal_id, title in proposals:
            event_ids.append(
                await runtime.events.write(
                    EventEnvelope(
                        **common,
                        type="household.action.proposed",
                        schema_version=1,
                        idempotency_key=f"gate7:visual:{proposal_id}",
                        payload={
                            "proposal_id": proposal_id,
                            "kind": "task",
                            "title": title,
                            "owner_member_id": PRINCIPAL,
                            "reason": "explicit_proposal",
                            "provenance": {"fixture": "gate7-visual"},
                        },
                    )
                )
            )
        superseded_id = await runtime.events.write(
            EventEnvelope(
                **common,
                type="triage.item.superseded",
                schema_version=2,
                idempotency_key="gate7:visual:retired-eligibility",
                causation_id=event_ids[-1],
                payload={
                    "proposal_id": "gate7-visual-retired-task",
                    "superseded_at": now.isoformat(),
                    "replacement_source_event_id": event_ids[-1],
                    "reason": "eligibility_rule_replaced",
                },
            )
        )
        await runtime.wait_for_projection("triage", superseded_id)
        return {"seeded": 9, "last_event_id": superseded_id}

    @app.post("/__gate7/fail-next-triage-materialization")
    async def fail_next_triage_materialization():
        """Force one local guard refusal after its domain confirmation append."""

        install_triage_failure_hooks()
        app.state.fail_next_task_materialization = True
        return {"armed": True}

    @app.post("/__gate7/settle")
    async def settle_fixture_bus():
        """Wait until fixture-derived writes and every durable consumer settle."""

        runtime = app.state.runtime
        if runtime.bus is None or runtime.event_log is None:
            raise HTTPException(503, "fixture runtime bus is unavailable")
        deadline = time.monotonic() + 8.0
        stable_event_id: str | None = None
        stable_cycles = 0
        while time.monotonic() < deadline:
            # The fixture must cover every registered consumer, including
            # projections, pipelines, and the central provider dispatcher.
            subscriber_ids = sorted(runtime.bus._subscribers)  # noqa: SLF001
            statuses = [
                await runtime.bus.subscriber_status(subscriber_id)
                for subscriber_id in subscriber_ids
            ]
            latest = await runtime.event_log.latest_event_id()
            drained = all(
                not status.get("degraded") and int(status.get("lag_count") or 0) == 0
                for status in statuses
            )
            if drained and latest == stable_event_id:
                stable_cycles += 1
            else:
                stable_event_id = latest
                stable_cycles = 0
            if stable_cycles >= 2:
                return {
                    "settled": True,
                    "latest_event_id": latest,
                    "subscriber_count": len(subscriber_ids),
                }
            await asyncio.sleep(0.01)
        raise HTTPException(503, "fixture bus did not settle before timeout")

    @app.post("/__gate7/fail-next-intent-after-capture")
    async def fail_next_intent_after_capture():
        """Force one handler failure after exact intent text is durable."""

        install_triage_failure_hooks()
        app.state.fail_next_intent_feature_append = True
        return {"armed": True}

    @app.post("/__gate7/fail-next-intent-proposals")
    async def fail_next_intent_proposals():
        """Deny both same-ID proposal attempts after intent/features exist."""

        install_triage_failure_hooks()
        app.state.fail_next_intent_proposal_guards = 2
        return {"armed": True}

    @app.post("/__gate7/seed-triage-final-ready")
    async def seed_triage_final_ready():
        """Seed one isolated adult Ready item for empty-queue browser proof."""

        runtime = app.state.runtime
        now = fixture_now()
        event_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(now.timestamp() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="household.action.proposed",
                schema_version=1,
                occurred_at=now.isoformat(),
                source_adapter="gate7-triage-fixture",
                source_account_id=ADULT,
                owner_scope=f"private:{ADULT}",
                visibility_scope=f"private:{ADULT}",
                sensitivity="normal",
                actor_identity=ADULT,
                idempotency_key="gate7:visual:final-ready",
                payload={
                    "proposal_id": "gate7-visual-final-ready",
                    "kind": "task",
                    "title": "Review the only ready household item",
                    "owner_member_id": ADULT,
                    "reason": "explicit_proposal",
                    "provenance": {"fixture": "gate7-visual-final-ready"},
                },
            )
        )
        await runtime.wait_for_projection("triage", event_id)
        return {"seeded": 1, "event_id": event_id}

    @app.post("/__gate7/seed-notes")
    async def seed_notes():
        """Seed synthetic owner-private Notes and one Triage link."""

        runtime = app.state.runtime
        now = fixture_now()
        common = {
            "event_at_ms": int(now.timestamp() * 1000),
            "tenant_id": runtime.settings.instance.tenant_id,
            "occurred_at": now.isoformat(),
            "source_adapter": "gate7-notes-fixture",
            "source_account_id": "credential-free",
            "owner_scope": f"private:{PRINCIPAL}",
            "visibility_scope": f"private:{PRINCIPAL}",
            "sensitivity": "normal",
            "actor_identity": PRINCIPAL,
        }
        adult_party_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(now.timestamp() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="party.created",
                schema_version=1,
                occurred_at=now.isoformat(),
                source_adapter="gate7-notes-fixture",
                source_account_id="credential-free",
                owner_scope="shared:household",
                visibility_scope="shared:household",
                sensitivity="normal",
                actor_identity=PRINCIPAL,
                idempotency_key="gate7:visual:notes:adult-party",
                payload={
                    "party_id": ADULT,
                    "kind": "person",
                    "display_name": "Fixture household adult",
                    "sort_name": "Adult, Fixture Household",
                    "attributes": {},
                },
            )
        )
        await runtime.wait_for_projection("parties", adult_party_id)
        note_events = [
            EventEnvelope(
                **common,
                type="member_knowledge.note_added",
                schema_version=3,
                idempotency_key="gate7:visual:notes:meeting",
                payload={
                    "knowledge_id": "gate7-visual-meeting-note",
                    "member_id": PRINCIPAL,
                    "title": "School planning call",
                    "body": (
                        "The school planning call covered the autumn pickup "
                        "routine and the form due Friday."
                    ),
                    "body_format": "transcript",
                    "source_kind": "fathom",
                    "source_type": "meeting_note",
                    "source_external_id": "visual-meeting-note",
                    "source_url": "https://example.invalid/visual-meeting-note",
                    "account_external_id": "visual-fathom",
                    "account_name": "Meeting notes",
                    "folder_path": "Calls/School",
                    "content_sha256": "a" * 64,
                    "captured_at": now.isoformat(),
                    "provider_modified_at": now.isoformat(),
                    "provider_revision": "visual-revision-1",
                    "tags": ["school", "planning"],
                    "capability_warnings": ["speaker_identity_unverified"],
                },
            ),
            EventEnvelope(
                **common,
                type="member_knowledge.note_added",
                schema_version=3,
                idempotency_key="gate7:visual:notes:household",
                payload={
                    "knowledge_id": "gate7-visual-household-note",
                    "member_id": PRINCIPAL,
                    "title": "Household landing list",
                    "body": "Put bags by the door and check tomorrow's calendar.",
                    "body_format": "plain",
                    "source_kind": "apple_notes",
                    "source_type": "note",
                    "source_external_id": "visual-household-note",
                    "account_external_id": "visual-icloud",
                    "account_name": "Personal iCloud",
                    "folder_path": "Shared household",
                    "content_sha256": "b" * 64,
                    "captured_at": now.isoformat(),
                    "provider_modified_at": now.isoformat(),
                    "provider_revision": "visual-revision-2",
                },
            ),
        ]
        note_ids = []
        for event in note_events:
            note_ids.append(await runtime.events.write(event))
        await runtime.wait_for_projection("member_knowledge", note_ids[-1])
        proposal_id = await runtime.events.write(
            EventEnvelope(
                **common,
                type="triage.item.proposed",
                schema_version=1,
                idempotency_key="gate7:visual:notes:triage",
                causation_id=note_ids[0],
                payload={
                    "proposal_id": "gate7-visual-note-candidate",
                    "target_kind": "task",
                    "owner_member_id": PRINCIPAL,
                    "title": "Return the school form",
                    "candidate": {"due": now.date().isoformat()},
                    "route": {"member_id": PRINCIPAL},
                    "source_event_id": note_ids[0],
                    "confidence": 0.96,
                    "reasons": ["note evidence: form due Friday"],
                },
            )
        )
        await runtime.wait_for_projection("triage", proposal_id)
        return {"seeded": 4, "last_event_id": proposal_id}

    @app.post("/__gate7/seed-finance")
    async def seed_finance():
        """Seed synthetic Finance facts and planning inputs for browser QA."""

        runtime = app.state.runtime
        now = fixture_now()
        private = {
            "event_at_ms": int(now.timestamp() * 1000),
            "tenant_id": runtime.settings.instance.tenant_id,
            "occurred_at": now.isoformat(),
            "source_adapter": "gate7-finance-fixture",
            "source_account_id": "credential-free",
            "owner_scope": f"private:{PRINCIPAL}",
            "visibility_scope": f"private:{PRINCIPAL}",
            "sensitivity": "privileged",
            "actor_identity": PRINCIPAL,
        }
        shared = {
            **private,
            "owner_scope": "shared:household",
            "visibility_scope": "shared:household",
            "sensitivity": "sensitive",
        }
        events = [
            EventEnvelope(
                **private,
                type="financial.account_observed",
                schema_version=1,
                idempotency_key="gate7:visual:finance:account",
                payload={
                    "member_id": PRINCIPAL,
                    "plaid_item_id": "visual-item",
                    "account_id": "visual-checking",
                    "institution_id": "visual-institution",
                    "institution_name": "Fixture institution",
                    "display_name": "Household checking",
                    "account_type": "depository",
                    "last4": "4242",
                    "current_balance_minor": 2_400_000,
                    "available_balance_minor": 2_350_000,
                    "currency": "USD",
                    "as_of": now.isoformat(),
                    "link_healthy": True,
                },
            ),
            EventEnvelope(
                **private,
                type="money_flow.provider_observed",
                schema_version=1,
                idempotency_key="gate7:visual:finance:income",
                payload={
                    "member_id": PRINCIPAL,
                    "plaid_item_id": "visual-item",
                    "account_id": "visual-checking",
                    "transaction_id": "visual-income",
                    "amount_minor": 700_000,
                    "currency": "USD",
                    "occurred_at": now.date().isoformat(),
                    "kind": "received",
                    "merchant_name": "Income deposit",
                    "provider_category": "Income",
                    "pending": False,
                    "provider_revision": "visual-cursor",
                    "observed_at": now.isoformat(),
                },
            ),
            EventEnvelope(
                **private,
                type="money_flow.provider_observed",
                schema_version=1,
                idempotency_key="gate7:visual:finance:expense",
                payload={
                    "member_id": PRINCIPAL,
                    "plaid_item_id": "visual-item",
                    "account_id": "visual-checking",
                    "transaction_id": "visual-grocery",
                    "amount_minor": 36_000,
                    "currency": "USD",
                    "occurred_at": now.date().isoformat(),
                    "kind": "paid",
                    "merchant_name": "Neighborhood market",
                    "provider_category": "Groceries",
                    "pending": False,
                    "provider_revision": "visual-cursor",
                    "observed_at": now.isoformat(),
                },
            ),
        ]
        planning = [
            (
                "finance.assumption_set",
                "assumption-income",
                {
                    "assumption_id": "base_monthly_income",
                    "category": "cash_flow",
                    "label": "Base monthly income",
                    "value_decimal": "7000",
                    "unit": "USD/month",
                    "as_of": now.date().isoformat(),
                },
            ),
            (
                "finance.assumption_set",
                "assumption-expenses",
                {
                    "assumption_id": "base_monthly_expenses",
                    "category": "cash_flow",
                    "label": "Base monthly expenses",
                    "value_decimal": "5200",
                    "unit": "USD/month",
                    "as_of": now.date().isoformat(),
                },
            ),
            (
                "finance.assumption_set",
                "assumption-reserve",
                {
                    "assumption_id": "minimum_cash_floor",
                    "category": "safety",
                    "label": "Minimum cash reserve",
                    "value_decimal": "15000",
                    "unit": "USD",
                    "as_of": now.date().isoformat(),
                },
            ),
            (
                "finance.budget_set",
                "budget",
                {
                    "budget_id": "visual-groceries",
                    "category": "Groceries",
                    "amount_minor": 30_000,
                    "currency": "USD",
                    "cadence": "monthly",
                    "active": True,
                    "as_of": now.date().isoformat(),
                },
            ),
            (
                "finance.scenario_set",
                "baseline",
                {
                    "scenario_id": "visual-current-plan",
                    "name": "Current plan",
                    "is_baseline": True,
                    "horizon_months": 36,
                    "active": True,
                },
            ),
            (
                "finance.scenario_set",
                "spending",
                {
                    "scenario_id": "visual-reduce-burn",
                    "name": "Reduce monthly burn",
                    "is_baseline": False,
                    "horizon_months": 36,
                    "monthly_expense_delta_minor": -50_000,
                    "active": True,
                },
            ),
            (
                "finance.goal_set",
                "goal",
                {
                    "goal_id": "visual-reserve-goal",
                    "name": "Emergency reserve",
                    "target_minor": 3_000_000,
                    "current_minor": 2_400_000,
                    "priority": 10,
                    "status": "active",
                    "notes": "Planning target only; no funds are moved.",
                },
            ),
        ]
        events.extend(
            EventEnvelope(
                **shared,
                type=event_type,
                schema_version=1,
                idempotency_key=f"gate7:visual:finance:{suffix}",
                payload=payload,
            )
            for event_type, suffix, payload in planning
        )
        last_event_id = 0
        for event in events:
            last_event_id = await runtime.events.write(event)
        await runtime.wait_for_projection("money", last_event_id)
        return {"seeded": len(events), "last_event_id": last_event_id}

    return app


if __name__ == "__main__":
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, log_level="warning")
