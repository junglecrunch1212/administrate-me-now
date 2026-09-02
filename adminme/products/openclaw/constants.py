"""Exact OpenClaw contract pinned by the Phase 4 seam."""

from adminme.openclaw_contract import OPENCLAW_COMMIT, OPENCLAW_VERSION, PLUGIN_ID

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
