"""Golden process acceptance: member bridge into private domain knowledge.

Gate 2 now has a black-box composition-root fixture in ``test_runtime_spine``.
This strict xfail remains for the Phase 3 product slice that fixture does not
yet claim: member-private bridge knowledge through scoped retrieval.
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
    reason="MISSING_CAPABILITY: the bridge authorization/search/replay product slice is not yet closed",
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
