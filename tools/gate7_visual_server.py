"""Deterministic local-only portal server for Gate 7 rendered evidence."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn

from adminme.runtime.application import RuntimeSettings, create_application
from adminme.events.envelope import EventEnvelope


ROOT = Path(__file__).resolve().parents[1]
INSTANCE = Path(os.environ["ADMINME_GATE7_VISUAL_INSTANCE"]).resolve()
PORT = int(os.environ.get("ADMINME_GATE7_VISUAL_PORT", "3347"))
MODEL_CONFIGURED = os.environ.get("ADMINME_GATE7_VISUAL_MODEL", "1") == "1"

PRINCIPAL = "visual_principal"
ADULT = "visual_adult"
CHILD = "visual_child"
DEVICE = "visual_shared_screen"


def build_app():
    INSTANCE.mkdir(parents=True, exist_ok=True)
    config = INSTANCE / "config"
    config.mkdir(exist_ok=True)
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
    )
    app = create_application(settings)

    @app.post("/__gate7/seed-calendar")
    async def seed_calendar():
        """Seed synthetic scoped facts for rendered Calendar evidence only."""

        runtime = app.state.runtime
        now = datetime.now(timezone.utc)
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

    return app


if __name__ == "__main__":
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, log_level="warning")
