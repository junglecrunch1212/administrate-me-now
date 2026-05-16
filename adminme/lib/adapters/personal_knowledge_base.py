"""
Cat-D: :class:`PersonalKnowledgeAdapter` — per-member knowledge adapter ABC.

Per ``docs/05-architecture-amendment-personal-data-layer.md`` §1.2 (Cat-D
contract), §2.6 (``owner_scope_overridability_at_install`` invariant), §3.2
(5-ABC pattern), [D19] (five-category taxonomy), [PM-34] (5 ABCs at
``adminme/lib/adapters/``), and ``docs/04-architecture-amendment-knowledge-
vaults-and-member-bridges.md`` §2.2 (the
Tailscale-identity-binds-owner-scope invariant).

Cat-D adapters run on a **member bridge** — a dedicated Mac Mini signed
into one family member's iCloud, running the ``adminme-bridge`` daemon.
They ingest from per-member knowledge sources (Apple Notes, Voice Memos,
Obsidian vaults) and emit events over the tailnet to the central CoS Mac
Mini's bridge-ingest endpoint at ``:3337``.

The cornerstone invariant of Cat-D — and the one this ABC surfaces in code
— is that **the bridge cannot stamp its own ``owner_scope``**. Per memo 04
§2.2, the ``:3337 bridge`` ingest endpoint resolves the Tailscale identity
via ``Tailscale-User-Login`` and stamps ``owner_scope = private:<member_id>``
on every event. An adapter that tries to override this is a hard refusal at
the ingest endpoint (logged as an attempted scope violation).

The base class makes the rule legible in code via
:attr:`_owner_scope_immutable` (``Final[bool] = True``). The ingest
endpoint is the runtime enforcement point; the type contract is the
authorship-time enforcement point. The 11-β validator additionally
verifies that ``owner_scope_overridability_at_install=false`` is set on
every Cat-D pack manifest.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Final, Literal

from adminme.adapters.base import (
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)


class PersonalKnowledgeAdapter(ABC):
    """Cat-D base class for per-member personal-knowledge adapters.

    Subclasses (Apple Notes via NoteStore.sqlite, Voice Memos folder
    watcher, Obsidian vault watcher, future Notion / Logseq / Roam
    connector packs) run on the member's own bridge Mac Mini and emit
    knowledge events over the tailnet. Cohort D's 11c-ii ships the first
    set of canonical Cat-D adapters built against this base class.

    Required class attributes (in addition to the simple Adapter
    Protocol's ``name`` / ``version`` / ``sensitivity_floor``):

    - :attr:`family` — pinned to ``"knowledge"``.
    - :attr:`runtime` — pinned to ``"bridge"`` (Cat-D is bridge-only per
      memo 04 §2.1 — the central CoS Mac Mini never signs into a member's
      iCloud and therefore cannot host a Cat-D adapter).
    - :attr:`capabilities` — list of capability blocks; ≥1 Cat-D entry.
    - :attr:`_owner_scope_immutable` — ``Final[bool] = True``. The type
      contract that the bridge-ingest endpoint stamps ``owner_scope``,
      not the adapter. A subclass MUST NOT override this; mypy + the
      11-β validator catch the override at type-check / install time.

    Required abstract methods: :meth:`start`, :meth:`stop`,
    :meth:`status`. No write method — Cat-D is ingestion-only; the
    member's edits flow through the source app (Notes, Obsidian, etc.)
    and the adapter only observes.
    """

    name: ClassVar[str]
    family: ClassVar[Literal["knowledge"]] = "knowledge"
    version: ClassVar[str]
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    runtime: ClassVar[Literal["bridge"]] = "bridge"
    """Cat-D is bridge-only per memo 04 §2.1. The validator (11-β)
    rejects a Cat-D pack manifest declaring any other runtime."""

    capabilities: ClassVar[list[Any]] = []

    _owner_scope_immutable: Final[bool] = True
    """The type contract that the bridge-ingest endpoint stamps
    ``owner_scope = private:<member_id>`` based on the resolved Tailscale
    identity per memo 04 §2.2 — the adapter MUST NOT participate in
    setting it. Marked ``Final`` so a subclass that tries to override
    triggers a mypy error AND the 11-β validator failure.
    """

    @abstractmethod
    async def start(self, *, ctx: AdapterContext) -> None:
        """Authenticate against the local knowledge source (file system
        access for Notes / Obsidian / Voice Memos), emit
        ``adapter.authenticated``, begin watching for changes. The
        emitted events flow over the tailnet to the ``:3337 bridge``
        ingest endpoint, which stamps ``owner_scope`` from the resolved
        Tailscale identity."""

    @abstractmethod
    async def stop(self) -> None:
        """Release file-system watchers, persist any cursor."""

    @abstractmethod
    def status(self) -> AdapterStatus:
        """Return the current adapter lifecycle snapshot."""
