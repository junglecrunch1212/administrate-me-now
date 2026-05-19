"""KnowledgeAdapter Protocol — Cat-D bridge-side adapter contract.

Per [PM-34]: distinct from the five central ABCs in
``adminme/lib/adapters/`` because Cat-D knowledge-source adapters
emit to an HTTP client, not to a local event log. Structural Protocol
(not abstract). Adapter packs land in 11c-ii.

Per memo 04 §3.1: packs declare ``kind: adapter, subkind:
knowledge-source``; the bridge ``packs`` loader filters by that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Literal, Protocol, runtime_checkable

from adminme_bridge.ingest_client import IngestClient

if TYPE_CHECKING:
    from adminme_bridge.adapters.sinks import BridgeEventSink


AdapterState = Literal["starting", "running", "backoff", "stopped", "crashed"]


@dataclass
class AdapterStatus:
    state: AdapterState = "starting"
    last_error: dict[str, Any] | None = None
    last_cursor_advance_at: datetime | None = None
    started_at: datetime | None = None
    restart_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BridgeContext:
    """Per-adapter dependency bag handed to ``KnowledgeAdapter.start()``.

    Frozen so adapters cannot rebind dependencies post-start.

    ``ingest_client`` is preserved for 11c-i backward compatibility; new code
    should use ``sink`` (a ``BridgeEventSink``) which abstracts the emit pathway
    over the production HTTP path and the test/central-fallback EventLog path
    (see ``adminme_bridge.adapters.sinks``). F-3: ``sink`` is added at the END
    of the field list as an optional with default ``None`` to preserve
    positional-arg compatibility with the 11c-i constructor shape."""

    member_id: str
    ingest_client: IngestClient
    correlation_id_factory: Callable[[], str]
    now_factory: Callable[[], datetime]
    sink: "BridgeEventSink | None" = None


@runtime_checkable
class KnowledgeAdapter(Protocol):
    """Structural contract every L1-bridge knowledge-source adapter satisfies."""

    subkind: ClassVar[str]

    async def start(self, *, ctx: BridgeContext) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> AdapterStatus: ...


__all__ = ["AdapterState", "AdapterStatus", "BridgeContext", "KnowledgeAdapter"]
