"""Golden process acceptance: member bridge into private domain knowledge.

This is intentionally red behind a strict xfail during Gate 1. Release gating
uses ``--runxfail``. Phase 2 must drive a real bridge client and central runtime;
direct handler calls or manually inserted projection rows are not substitutes.
"""

from __future__ import annotations

import pytest


REQUIRED_STAGES = (
    "a credential-free enrolled bridge authenticates as member A",
    "a Cat-D note event carries provenance and immutable private member scope",
    "central ingest derives owner scope from enrollment identity, not the payload claim",
    "the production append port commits before notifying the durable bus",
    "member_knowledge materializes the owner-scoped knowledge record idempotently",
    "vector_search indexes only an allowed non-privileged representation with provenance",
    "member A can retrieve the record through the narrow member API",
    "member B, a child, ambient/device sessions and OpenClaw generic memory cannot read it",
    "a privileged fixture is excluded from LLM, vector and cross-owner processing",
    "a correction event supersedes the read model without rewriting history",
    "restart and replay from event zero produce equivalent knowledge/search state",
    "two concurrently running household instances cannot cross-contaminate state",
)


@pytest.mark.xfail(
    strict=True,
    reason="MISSING_CAPABILITY: member_knowledge and its composed bridge/search path do not exist",
)
def test_member_bridge_knowledge_is_private_searchable_and_replayable() -> None:
    """Start real bridge/runtime processes and prove all required stages.

    The eventual fixture uses generated tenant/member ids, distinct encryption
    keys and temporary roots. It must assert sensitivity/visibility, trusted
    Tailscale identity handling, correlation/causation, schema/adapter versions,
    durable offsets, correction provenance and raw-database encryption without
    using a real member account, iCloud data or network credential.
    """

    pytest.fail("MISSING_CAPABILITY:\n- " + "\n- ".join(REQUIRED_STAGES))
