"""
Cat-A: :class:`CommunicationAdapter` — bidirectional messaging adapter ABC.

Per ``docs/05-architecture-amendment-personal-data-layer.md`` §1.2 (Cat-A
contract), §3.2 (the 5-ABC pattern), [D19] (five-category taxonomy),
[PM-34] (5 ABCs at ``adminme/lib/adapters/``), and SYSTEM_INVARIANTS.md
§6.20 (observation mode wraps the final outbound filter).

Extends the simple ``Adapter`` Protocol from ``adminme/adapters/base.py``
with:

- :meth:`CommunicationAdapter.send` — abstract; subclasses route the
  external send through :func:`adminme.lib.observation.outbound` so the
  observation toggle suppresses the side effect per §6.13-6.20.
- :meth:`CommunicationAdapter._on_inbound` — abstract; hook invoked by the
  subclass's polling / streaming loop on each inbound envelope.

The contract is **declared** here. The validator (11-β) checks at install
time that every subclass actually wires :meth:`send` through
:func:`adminme.lib.observation.outbound`; this ABC documents the rule but
does not enforce the wrapping at instantiation (it would require a live
``Session`` / ``ObservationManager`` / ``EventLog`` which adapters do not
own).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from adminme.adapters.base import (
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)


@dataclass(frozen=True)
class OutboundMessage:
    """A message handed to :meth:`CommunicationAdapter.send`.

    Frozen so the adapter cannot mutate the in-flight payload after the
    supervisor hands it over. ``metadata`` carries arbitrary
    family-specific extras (e.g. Gmail thread id, iMessage chat guid).
    """

    channel: str
    recipient: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SendResult:
    """Result of a :meth:`CommunicationAdapter.send` call.

    ``delivered=True`` means the external API accepted the send (200 or
    family-equivalent). ``suppressed=True`` means observation mode short-
    circuited the call before it reached the external system per §6.20.
    """

    delivered: bool
    external_id: str | None = None
    suppressed: bool = False


@dataclass(frozen=True)
class InboundEnvelope:
    """Envelope handed to :meth:`CommunicationAdapter._on_inbound`.

    The polling / streaming loop in the subclass constructs one of these
    per inbound message and passes it through the hook before emitting
    the ``messaging.received`` family event.
    """

    external_id: str
    channel: str
    sender: str
    received_at: str
    payload: dict[str, Any]


class CommunicationAdapter(ABC):
    """Cat-A base class for bidirectional messaging adapters.

    Subclasses (Gmail, Microsoft Graph mail, iMessage via BlueBubbles
    plugin once retrofitted, etc.) declare a ``messaging`` family and at
    least one Cat-A entry in :attr:`capabilities`. The Cohort C retrofit
    prompts will rebase the shipped Gmail adapter (``packs/adapters/
    messaging/gmail/adapter.py``, PR #91 series) onto this base class.

    Required class attributes inherited from the simple ``Adapter``
    Protocol (each subclass must set):

    - ``name``: stable pack identifier.
    - ``version``: pack SemVer.
    - ``sensitivity_floor``: ``"normal"`` for general messaging;
      ``"privileged"`` for law-practice / custody-bound mailboxes.

    Required abstract methods:

    - :meth:`start`, :meth:`stop`, :meth:`status` — lifecycle (from the
      simple Adapter Protocol; re-declared here as abstract so subclasses
      cannot forget them and still instantiate).
    - :meth:`send` — outbound send; MUST route through
      :func:`adminme.lib.observation.outbound` per §6.20.
    - :meth:`_on_inbound` — inbound hook.
    """

    name: ClassVar[str]
    family: ClassVar[Literal["messaging"]] = "messaging"
    version: ClassVar[str]
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    # List of Capability blocks declared by the subclass / its pack.yaml.
    # The strong type :class:`adminme.lib.pack_registry.models.Capability`
    # ships in 11-α commit 3; until then the attribute is typed as Any to
    # keep the contract layer self-contained.
    capabilities: ClassVar[list[Any]] = []

    @abstractmethod
    async def start(self, *, ctx: AdapterContext) -> None:
        """Authenticate, emit ``adapter.authenticated``, begin the
        polling / streaming loop. See the simple ``Adapter`` Protocol
        for the lifecycle contract."""

    @abstractmethod
    async def stop(self) -> None:
        """Release external resources, persist any cursor."""

    @abstractmethod
    def status(self) -> AdapterStatus:
        """Return the current adapter lifecycle snapshot."""

    @abstractmethod
    async def send(self, message: OutboundMessage) -> SendResult:
        """Send an outbound message.

        Subclasses MUST route the actual external API call through
        :func:`adminme.lib.observation.outbound` so observation mode
        suppresses the side effect when active. The install-time
        validator (11-β) verifies this wiring; this ABC only declares
        the contract.
        """

    @abstractmethod
    async def _on_inbound(self, envelope: InboundEnvelope) -> None:
        """Hook invoked by the polling / streaming loop on each inbound
        envelope, BEFORE the subclass emits ``messaging.received``. The
        default implementation in a subclass typically updates an
        in-memory dedup set and forwards the envelope to the event log.
        """
