"""
money projection — the transaction ledger.

Per ADMINISTRATEME_BUILD.md §3.9 and SYSTEM_INVARIANTS.md §2, §12.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.money import handlers


class MoneyProjection(Projection):
    name = "money"
    version = 1
    subscribes_to = [
        "money_flow.recorded",
        "money_flow.manually_added",
        "money_flow.manually_deleted",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["MoneyProjection"]
