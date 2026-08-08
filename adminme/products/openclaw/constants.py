"""Exact OpenClaw contract pinned by the Phase 4 seam."""

OPENCLAW_VERSION = "2026.7.2"
OPENCLAW_COMMIT = "ce67ffb70e83e5ebb56ea02e814e577153f84f61"
PLUGIN_ID = "administrateme"

BRIDGE_EVENT_KINDS = frozenset(
    {"commitment_candidate", "household_action", "delivery_outcome"}
)
PHASE3_SKILL_IDS = frozenset(
    {
        "classify_commitment_candidate",
        "classify_message_nature",
        "classify_thank_you_candidate",
        "extract_commitment_fields",
        "extract_thank_you_fields",
    }
)

__all__ = [
    "BRIDGE_EVENT_KINDS",
    "OPENCLAW_COMMIT",
    "OPENCLAW_VERSION",
    "PHASE3_SKILL_IDS",
    "PLUGIN_ID",
]
