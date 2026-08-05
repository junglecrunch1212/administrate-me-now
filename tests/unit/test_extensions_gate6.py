"""Gate 6 data-only profile/persona lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adminme.extensions import ExtensionCatalog, ExtensionError


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_all_builtin_extensions_discover_and_activate_atomically(
    tmp_path: Path,
) -> None:
    catalog = ExtensionCatalog(REPO_ROOT, tmp_path)
    assert {pack.pack_id for pack in catalog.packs("profile")} == {
        "adhd_executive",
        "ambient_entity",
        "kid_scoreboard",
        "minimalist_parent",
        "power_user",
    }
    assert {pack.pack_id for pack in catalog.packs("persona")} == {
        "butler_classic",
        "friendly_robot",
        "poopsy",
        "quiet_assistant",
    }

    profile_token = catalog.stage("profile", "adhd_executive")
    proof = catalog.test_stage(profile_token)
    assert proof["authority_changes"] is False
    assert proof["model_routing_changes"] is False
    catalog.activate(profile_token, member_id="member-a")
    assert catalog.profile_for("member-a")["profile_format"] == "fog_aware"

    persona_token = catalog.stage("persona", "friendly_robot")
    catalog.test_stage(persona_token)
    catalog.activate(persona_token)
    assert catalog.active_persona()["id"] == "friendly_robot"
    catalog.rollback()
    assert catalog.active_persona()["id"] == "quiet_assistant"


def test_untested_or_authority_bearing_extension_fails_closed(tmp_path: Path) -> None:
    catalog = ExtensionCatalog(REPO_ROOT, tmp_path)
    token = catalog.stage("profile", "power_user")
    with pytest.raises(ExtensionError, match="pass stage tests"):
        catalog.activate(token, member_id="member-a")

    bad = tmp_path / "packs" / "profiles" / "bad" / "pack.json"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        json.dumps(
            {
                "id": "bad",
                "kind": "profile",
                "version": "1.0.0",
                "behavior": {"available_minutes": 5},
                "permissions": ["household.write"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExtensionError, match="forbidden keys"):
        ExtensionCatalog(REPO_ROOT, tmp_path)
