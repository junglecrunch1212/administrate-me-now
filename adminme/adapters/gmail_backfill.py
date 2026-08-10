"""Content-free policy receipt for bounded Gmail source acquisition."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class GmailInitialBackfillPolicy:
    """Validated private source-acquisition policy.

    Seed query text is necessary to call Gmail but is never copied into status,
    an event, or a durable cursor identifier. Only its irreversible digest is
    included in the policy receipt.
    """

    recent_days: int
    max_messages: int
    seed_queries: tuple[str, ...]
    seed_max_messages_per_query: int
    related_max_participants: int
    related_messages_per_participant: int
    related_context_days: int

    def receipt_digest(self) -> str:
        material = {
            "contract": "gmail-initial-backfill-v2",
            "recent_days": self.recent_days,
            "max_messages": self.max_messages,
            "seed_query_digests": [
                hashlib.sha256(query.encode("utf-8")).hexdigest()
                for query in self.seed_queries
            ],
            "seed_max_messages_per_query": self.seed_max_messages_per_query,
            "related_max_participants": self.related_max_participants,
            "related_messages_per_participant": (self.related_messages_per_participant),
            "related_context_days": self.related_context_days,
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]


__all__ = ["GmailInitialBackfillPolicy"]
