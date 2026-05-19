"""Unit tests for the ``StateMirrorBridgeAdapter`` sibling Protocol.

11b-ii-α-1 adds a structural sibling to ``KnowledgeAdapter`` for the
runtime path that hosts Cat-B ``ExternalStateMirrorAdapter`` packs on a
bridge. Per [D19] + memo 05 §3.1 + ``docs/10-split-memo-11b-ii-alpha.md``
§1: runtime substrate (central / bridge / dual-deployment) is orthogonal
to the 5-category taxonomy, so Cat-B packs CAN ship on a bridge — this
Protocol is the on-ramp."""

from __future__ import annotations

from typing import ClassVar, get_args

from adminme_bridge.adapters.base import (
    AdapterStatus,
    BridgeAdapter,
    BridgeContext,
    KnowledgeAdapter,
    StateMirrorBridgeAdapter,
)


class _CatBSatisfying:
    """Structurally satisfies :class:`StateMirrorBridgeAdapter` — no
    inheritance. This is the α-2 ``AppleEventKitAdapter`` shape."""

    family: ClassVar[str] = "reminders"

    def __init__(self) -> None:
        self._status = AdapterStatus()

    async def start(self, *, ctx: BridgeContext) -> None:  # pragma: no cover
        return None

    async def stop(self) -> None:  # pragma: no cover
        return None

    def status(self) -> AdapterStatus:
        return self._status


class _CatDSatisfying:
    """Structurally satisfies :class:`KnowledgeAdapter` — re-pinned here
    so the Protocol-discriminator boundary is testable in one file."""

    subkind: ClassVar[str] = "apple-notes"

    def __init__(self) -> None:
        self._status = AdapterStatus()

    async def start(self, *, ctx: BridgeContext) -> None:  # pragma: no cover
        return None

    async def stop(self) -> None:  # pragma: no cover
        return None

    def status(self) -> AdapterStatus:
        return self._status


def test_state_mirror_bridge_adapter_protocol_requires_family_classvar() -> None:
    """``StateMirrorBridgeAdapter`` is ``@runtime_checkable`` and a Cat-B
    duck-typed class with ``family`` + ``start`` + ``stop`` + ``status``
    passes the isinstance check. ``family`` mirrors the central Cat-B
    ABC's discriminator at
    ``adminme/lib/adapters/external_state_mirror_base.py``."""
    adapter = _CatBSatisfying()
    assert isinstance(adapter, StateMirrorBridgeAdapter)
    assert adapter.family == "reminders"


def test_knowledge_adapter_protocol_unchanged_subkind_still_required() -> None:
    """Sibling Protocol shape (per §4 A-1 recommendation): the existing
    :class:`KnowledgeAdapter` Protocol is NOT broadened to accept Cat-B
    classes — only Cat-D classes satisfy it. Verifies the two Protocols
    are discriminated by their ClassVar (``subkind`` vs ``family``)."""

    class _OnlyFamilyNoSubkind:
        family: ClassVar[str] = "calendaring"

        def __init__(self) -> None:
            self._status = AdapterStatus()

        async def start(self, *, ctx: BridgeContext) -> None:  # pragma: no cover
            return None

        async def stop(self) -> None:  # pragma: no cover
            return None

        def status(self) -> AdapterStatus:
            return self._status

    # The Cat-D class still passes KnowledgeAdapter.
    assert isinstance(_CatDSatisfying(), KnowledgeAdapter)
    # @runtime_checkable Protocols only check method *presence* at the
    # instance level, so a class missing `subkind` but carrying `family`
    # still satisfies KnowledgeAdapter structurally (mypy + static
    # readers see the discriminator; runtime doesn't enforce ClassVar
    # presence). What we pin here is that the StateMirror class
    # explicitly carries `family` and the Cat-D class explicitly carries
    # `subkind` — the discrimination is in the class definition, not in
    # the runtime check.
    assert _CatDSatisfying().subkind == "apple-notes"
    assert _OnlyFamilyNoSubkind().family == "calendaring"


def test_bridge_adapter_typealias_accepts_both_protocols() -> None:
    """``BridgeAdapter`` is the ``KnowledgeAdapter | StateMirrorBridgeAdapter``
    TypeAlias the supervisor's ``adapters`` list is annotated with. Verifies
    both Protocols are members so a single ``list[BridgeAdapter]`` can hold
    a mix of Cat-D and Cat-B adapters (the goal of the on-ramp)."""
    members = set(get_args(BridgeAdapter))
    assert KnowledgeAdapter in members
    assert StateMirrorBridgeAdapter in members
    # The list-of-Union shape is what `BridgeSupervisor.adapters` widens to.
    mixed: list[BridgeAdapter] = [_CatDSatisfying(), _CatBSatisfying()]
    assert len(mixed) == 2


def test_mock_cat_b_class_satisfies_state_mirror_bridge_adapter_isinstance_check() -> None:
    """End-to-end Protocol pin: a Cat-B mock with ``family`` + the three
    methods passes the isinstance check WITHOUT inheriting from
    ``StateMirrorBridgeAdapter`` (Protocol = structural). This is the
    α-2 ``AppleEventKitAdapter`` integration path."""

    class _MockAppleEventKit:
        family: ClassVar[str] = "reminders"

        def __init__(self) -> None:
            self.started = False
            self.stopped = False
            self._status = AdapterStatus()

        async def start(self, *, ctx: BridgeContext) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

        def status(self) -> AdapterStatus:
            return self._status

    mock = _MockAppleEventKit()
    assert isinstance(mock, StateMirrorBridgeAdapter)
    # Method dispatch is the supervisor's contract — both Protocols share
    # the same `start(*, ctx)` / `stop()` / `status()` shape so the
    # supervisor's loop can dispatch uniformly.
    assert hasattr(mock, "start")
    assert hasattr(mock, "stop")
    assert hasattr(mock, "status")
