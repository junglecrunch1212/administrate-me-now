"""
Projection protocol — the contract every L3 projection satisfies.

Per SYSTEM_INVARIANTS.md §2 and ADMINISTRATEME_BUILD.md §L3.

Each projection:
- Has a ``name`` (string) and a ``version`` (integer; bump to trigger rebuild).
- Subscribes to a list of event types (or ``"*"``).
- Owns its own SQLite database under ``InstanceConfig.projections_dir``
  (§15/D15).
- Exposes an idempotent ``apply(envelope, conn)`` per event.
- Is rebuilt via the runner's ``rebuild()`` which drops its DB and replays
  from event 0 (§2 invariant 1).
- NEVER writes to the event log (§2 invariant 2).

Projection handlers are deterministic pure functions over ``(state, event)``
— no wall-clock reads, no random, no UUIDs, no network, no calls to other
projections, no calls back to the event log beyond the cursor advance
(§2 invariant 3).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Projection(ABC):
    """Abstract base class for every L3 projection."""

    name: str
    version: int
    subscribes_to: list[str] | str
    schema_path: Path
    replay_retained_events_on_version_change: bool = False

    @abstractmethod
    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        """Apply one event. MUST be idempotent: re-applying the same event
        produces the same row state. Handlers that need cross-event state
        MUST read it from the projection's own DB, not from handler-local
        caches — rebuild correctness depends on it."""

    def after_batch(self, conn: Any) -> None:
        """Optional hook called after a batch of events has been applied.
        Default: no-op."""
        return None

    def before_replay(self, conn: Any) -> None:
        """Optional hook called on the connection owner before a replay."""

        return None

    def after_replay(self, conn: Any, *, succeeded: bool) -> None:
        """Optional hook called on the connection owner after replay settles."""

        return None

    def on_connection_opened(self, conn: Any) -> None:
        """Optional hook called once per connection, after PRAGMA key is set
        and before schema.sql is executed. Default: no-op. Override for
        projections that need extension loading (sqlite-vec's vec0) or
        PRAGMAs beyond the defaults."""
        return None

    def migrate_schema(self, conn: Any) -> None:
        """Optional additive migration hook called after ``schema.sql``.

        Projection databases are disposable, but a normal runtime upgrade
        must still open an existing projection safely before an operator
        chooses to rebuild it.
        """
        return None
