"""
Lists projection — 13th projection per [D18].

Per ADMINISTRATEME_BUILD.md §L3 and SYSTEM_INVARIANTS.md §2 (invariants
1-4) + §6 (scope discipline on reads).

Subscribes ONLY to ``list_item.added@v1``. The other three list_item
event types (updated/completed/deleted) have no first-party producer on
main yet — declaring subscriptions without producers + handlers would
fail rebuild correctness per UT-27-style asymmetric-subscription
discipline. Follow-up sub-prompts (11b-i-β / 11b-iv-β) ship the producers
and a paired 11f-β extends subscriptions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.lists import handlers


class ListsProjection(Projection):
    name = "lists"
    version = 1
    # Asymmetric on purpose — other list_item.* types ship in 11f-β [D18, UT-27].
    subscribes_to = ["list_item.added"]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["ListsProjection"]
