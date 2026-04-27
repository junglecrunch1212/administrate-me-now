"""Pack-loader canary for identity_resolution.

Mirrors the shape of tests/unit/test_pipeline_pack_loader.py — confirms
the manifest + handler load cleanly and the resulting class satisfies
the runtime-checkable Pipeline protocol."""

from __future__ import annotations

from pathlib import Path

from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.base import Pipeline

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    pack = load_pipeline_pack(PACK_ROOT)
    assert pack.pack_id == "pipeline:identity_resolution"
    assert pack.version == "1.0.0"
    assert pack.triggers["events"] == [
        "messaging.received",
        "messaging.sent",
        "telephony.sms_received",
    ]
    assert "identity.merge_suggested" in pack.events_emitted
    assert "party.created" in pack.events_emitted
    assert "identifier.added" in pack.events_emitted
    assert isinstance(pack.instance, Pipeline)
