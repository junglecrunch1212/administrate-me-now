"""Read-side and state-mirror protocols for bridge-hosted adapters.

Per [PM-34]: distinct from the five central ABCs in
``adminme/lib/adapters/`` because Cat-D knowledge-source adapters
emit to an HTTP client, not to a local event log. Structural Protocol
(not abstract). Adapter packs land in 11c-ii.

``CommunicationSourceBridgeAdapter`` is deliberately read-only.  It is the
bridge-runtime shape for a Cat-A communication *observation* source, and does
not expose ``send`` or ``apply_write``.  Keeping that absence structural makes
it impossible for a G10 Messages source to enter the bridge command dispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Literal,
    Protocol,
    TypeAlias,
    runtime_checkable,
)

from adminme_bridge.ingest_client import IngestClient

if TYPE_CHECKING:
    from adminme_bridge.adapters.sinks import BridgeEventSink


AdapterState = Literal[
    "starting", "running", "degraded", "backoff", "stopped", "crashed"
]


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
    (see ``adminme_bridge.adapters.sinks``). ``sink`` remains the fifth field
    for positional compatibility; Gate 6 appends optional ``tenant_id`` after
    it so the central tenant is explicit without rebinding older callers."""

    member_id: str
    ingest_client: IngestClient
    correlation_id_factory: Callable[[], str]
    now_factory: Callable[[], datetime]
    sink: "BridgeEventSink | None" = None
    tenant_id: str = "tenant-default"
    enrollment_id: str = ""
    configuration_sha256: str = ""
    device_identity_sha256: str = ""


@runtime_checkable
class KnowledgeAdapter(Protocol):
    """Structural contract every L1-bridge knowledge-source adapter satisfies."""

    subkind: ClassVar[str]

    async def start(self, *, ctx: BridgeContext) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> AdapterStatus: ...


@runtime_checkable
class CommunicationSourceBridgeAdapter(Protocol):
    """Read-only Cat-A communication observation source on a member bridge.

    Runtime placement is orthogonal to the five adapter categories.  Unlike a
    central communication adapter, this protocol represents ingestion only;
    write methods are intentionally absent from the contract.
    """

    subkind: ClassVar[Literal["communication-source"]]

    async def start(self, *, ctx: BridgeContext) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> AdapterStatus: ...


@runtime_checkable
class StateMirrorBridgeAdapter(Protocol):
    """Structural contract every L1-bridge state-mirror (Cat-B) adapter satisfies.

    Per [D19] + memo 05 §3.1 + ``docs/10-split-memo-11b-ii-alpha.md`` §1:
    runtime substrate (central / bridge / dual-deployment) is orthogonal
    to the 5-category taxonomy. Cat-B ``ExternalStateMirrorAdapter`` packs
    can ship on a bridge; this Protocol is the structural sibling to
    :class:`KnowledgeAdapter` for that runtime path.

    ``family`` mirrors the central Cat-B ABC's discriminator at
    ``adminme/lib/adapters/external_state_mirror_base.py``
    (``family: ClassVar[Literal[\"reminders\", \"calendaring\", \"contacts\", \"tasks\"]]``).
    Kept as ``str`` here because α-1 is pure infrastructure; α-2 wires
    concrete packs whose ``family`` values must match the central Literal.
    """

    family: ClassVar[str]

    async def start(self, *, ctx: BridgeContext) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> AdapterStatus: ...


BridgeAdapter: TypeAlias = (
    KnowledgeAdapter | CommunicationSourceBridgeAdapter | StateMirrorBridgeAdapter
)


__all__ = [
    "AdapterState",
    "AdapterStatus",
    "BridgeAdapter",
    "BridgeContext",
    "CommunicationSourceBridgeAdapter",
    "KnowledgeAdapter",
    "StateMirrorBridgeAdapter",
]
