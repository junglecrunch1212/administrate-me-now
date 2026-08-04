"""Deterministic local-only portal server for Gate 7 rendered evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path

import uvicorn

from adminme.runtime.application import RuntimeSettings, create_application


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
    return create_application(settings)


if __name__ == "__main__":
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, log_level="warning")
