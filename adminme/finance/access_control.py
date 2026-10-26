"""Revocable owner/book ceiling derived from encrypted Finance control events.

The cache is not authority of its own. Production startup must restore each
configured principal from the bounded event-log reader before serving requests.
The owner command holds its existing per-owner lock, blocks disclosure before
its first persistence await, then restores from the durable read-back. An
ambiguous write/read-back keeps that owner blocked until reconciliation. Readers
must fetch the current snapshot at final delivery, never retain one across I/O.
Finance-only CoS authority comes from this book control. Other service grants
and transport/audience checks remain independent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from adminme.lib.session import Session

from adminme.events.schemas.finance_access import (
    FinanceAccessControlV1,
    FinanceAccessControlV2,
    FinanceBookBinding,
)

CONTROL_EVENT = "settings.finance_access_changed"


class FinanceAccessError(ValueError):
    """Safe code only; never include a retained payload or private identifier."""


@dataclass(frozen=True)
class FinanceAccessSnapshot:
    revision: int = 0
    event_id: str | None = None
    binding: FinanceBookBinding | None = None
    books_enabled: bool = False
    cos_analysis_enabled: bool = False
    owner_id: str | None = None
    reader_member_ids: tuple[str, ...] = ()

    def allows_book(self, book_id: str | None) -> bool:
        return bool(
            self.books_enabled
            and self.binding is not None
            and book_id in (None, self.binding.book_id)
        )

    def authority_coordinates(self) -> dict[str, Any]:
        return {
            "owner_id": self.owner_id,
            "reader_member_ids": list(self.reader_member_ids),
            "event_id": self.event_id,
            "revision": self.revision,
            "binding": self.binding.model_dump() if self.binding else None,
            "books_enabled": self.books_enabled,
            "cos_analysis_enabled": self.cos_analysis_enabled,
        }


class FinanceAccessCache:
    def __init__(self, tenant_id: str, principal_ids: frozenset[str]) -> None:
        if not tenant_id or not all(principal_ids):
            raise FinanceAccessError("finance_control_identity_invalid")
        self.tenant_id = tenant_id
        self._principals = frozenset(principal_ids)
        self._states: dict[str, FinanceAccessSnapshot] = {}
        self._blocked: set[str] = set(principal_ids)
        self._transitions: set[str] = set()

    def block(self, tenant_id: str, owner_id: str) -> None:
        self._require_owner(tenant_id, owner_id)
        self._blocked.add(owner_id)

    def begin_change(self, tenant_id: str, owner_id: str) -> None:
        self.block(tenant_id, owner_id)
        self._transitions.add(owner_id)

    def snapshot(self, tenant_id: str, owner_id: str) -> FinanceAccessSnapshot:
        self._require_owner(tenant_id, owner_id)
        if owner_id in self._blocked or owner_id not in self._states:
            raise FinanceAccessError("finance_control_unavailable")
        return self._states[owner_id]

    def restore(
        self,
        tenant_id: str,
        owner_id: str,
        event: Mapping[str, Any] | None,
        *,
        settle_change: bool = False,
    ) -> FinanceAccessSnapshot:
        """Install only validated durable read-back, never request body state."""
        self.block(tenant_id, owner_id)
        previous = self._states.get(owner_id)
        if event is None:
            if previous is not None and previous.revision:
                raise FinanceAccessError("finance_control_history_missing")
            state = FinanceAccessSnapshot(
                owner_id=owner_id, reader_member_ids=(owner_id,)
            )
        else:
            try:
                version = event["schema_version"]
                model = (
                    FinanceAccessControlV2 if version == 2 else FinanceAccessControlV1
                )
                payload = model.model_validate(event["payload"])
                readers = (
                    tuple(payload.reader_member_ids)
                    if isinstance(payload, FinanceAccessControlV2)
                    else (owner_id,)
                )
                if (
                    event["tenant_id"] != tenant_id
                    or event["type"] != CONTROL_EVENT
                    or version not in (1, 2)
                    or event["actor_identity"] != owner_id
                    or event["source_account_id"] != owner_id
                    or event["source_adapter"] != "member-settings-control-plane"
                    or event["owner_scope"] != f"private:{owner_id}"
                    or event["visibility_scope"] != f"private:{owner_id}"
                    or payload.owner_id != owner_id
                    or not isinstance(event["event_id"], str)
                    or not 1 <= len(event["event_id"]) <= 160
                ):
                    raise ValueError("invalid authority")
                state = FinanceAccessSnapshot(
                    owner_id=owner_id,
                    reader_member_ids=readers,
                    revision=payload.revision,
                    event_id=event["event_id"],
                    binding=payload.binding,
                    books_enabled=payload.books_enabled,
                    cos_analysis_enabled=payload.cos_analysis_enabled,
                )
            except (KeyError, TypeError, ValueError):
                raise FinanceAccessError("finance_control_invalid") from None
        if previous is not None and (
            state.revision < previous.revision
            or (state.revision == previous.revision and state != previous)
        ):
            raise FinanceAccessError("finance_control_history_conflict")
        self._states[owner_id] = state
        if settle_change:
            self._transitions.discard(owner_id)
        if owner_id in self._transitions:
            raise FinanceAccessError("finance_control_unavailable")
        self._blocked.discard(owner_id)
        return state

    def shared_for(self, tenant_id: str, actor_id: str) -> list[FinanceAccessSnapshot]:
        self._require_owner(tenant_id, actor_id)
        result = []
        for owner_id, state in self._states.items():
            if actor_id != owner_id and actor_id in state.reader_member_ids:
                # An unsettled owner change cannot leave a cached shared grant usable.
                result.append(self.snapshot(tenant_id, owner_id))
        return result

    def _require_owner(self, tenant_id: str, owner_id: str) -> None:
        if tenant_id != self.tenant_id or owner_id not in self._principals:
            raise FinanceAccessError("finance_control_identity_invalid")


def control_lock(runtime: Any, session: Any):
    import asyncio

    return runtime.settings_command_locks.setdefault(
        f"finance-access:{session.tenant_id}:{session.auth_member_id}", asyncio.Lock()
    )


async def refresh_control(
    runtime: Any, owner_id: str, *, settle_change: bool = False
) -> FinanceAccessSnapshot:
    tenant_id = runtime.settings.instance.tenant_id
    if runtime.event_log is None:
        runtime.finance_access.block(tenant_id, owner_id)
        raise FinanceAccessError("finance_control_unavailable")
    try:
        event = await runtime.event_log.read_finance_access_control(tenant_id, owner_id)
        return runtime.finance_access.restore(
            tenant_id, owner_id, event, settle_change=settle_change
        )
    except (RuntimeError, ValueError):
        runtime.finance_access.block(tenant_id, owner_id)
        raise FinanceAccessError("finance_control_unavailable") from None


def policy_for_owner(runtime: Any, owner_id: str) -> FinanceAccessSnapshot:
    if runtime.settings.member_roles.get(owner_id) != "principal":
        raise FinanceAccessError("finance_control_identity_invalid")
    state = runtime.finance_access.snapshot(
        runtime.settings.instance.tenant_id, owner_id
    )
    # Historical synthetic fixtures explicitly configure their Finance boundary.
    # Real installations can enable only the encrypted owner control; environment
    # variables and a mutable runtime member allowlist never grant production access.
    if state.revision == 0 and runtime.settings._credential_free_fixture:
        return FinanceAccessSnapshot(
            owner_id=owner_id,
            reader_member_ids=(owner_id,),
            books_enabled=runtime.settings.finance_publications_enabled,
            cos_analysis_enabled=(
                runtime.settings.finance_publications_enabled
                and owner_id in runtime.settings.finance_model_members
            ),
        )
    return state


def policy_for_session(runtime: Any, session: Any) -> FinanceAccessSnapshot:
    if (
        session.tenant_id != runtime.settings.instance.tenant_id
        or session.auth_role != "principal"
        or session.is_view_as
        or f"private:{session.auth_member_id}" not in session.allowed_scopes
    ):
        raise FinanceAccessError("finance_control_identity_invalid")
    return policy_for_owner(runtime, session.auth_member_id)


def books_enabled(runtime: Any, session: Any) -> bool:
    try:
        return read_scope(runtime, session).policy.books_enabled
    except FinanceAccessError:
        return False


def analysis_enabled(runtime: Any, session: Any) -> bool:
    try:
        return read_scope(runtime, session, advisor=True).policy.cos_analysis_enabled
    except FinanceAccessError:
        return False


def bound_book(runtime: Any, session: Any, requested: str | None = None) -> str | None:
    state = read_scope(runtime, session, requested).policy
    if not state.books_enabled:
        raise FinanceAccessError("finance_books_disabled")
    if state.binding is None:
        if runtime.settings._credential_free_fixture and state.revision == 0:
            return requested
        raise FinanceAccessError("finance_book_binding_required")
    if requested not in (None, state.binding.book_id):
        raise FinanceAccessError("finance_book_not_authorized")
    return state.binding.book_id


def require_publication_binding(
    runtime: Any, session: Any, value: Mapping[str, Any]
) -> None:
    state = policy_for_session(runtime, session)
    bound_book(runtime, session, value.get("book_id"))
    if state.binding is not None and any(
        value.get(key) != expected
        for key, expected in state.binding.model_dump().items()
    ):
        raise FinanceAccessError("finance_publication_binding_mismatch")


@dataclass(frozen=True)
class FinanceReadScope:
    """A book read grant alongside the real Session, never an owner Session.

    Callers resolve it before work and compare current authority at delivery.
    It is accepted only by Finance queries, not generic scope or write APIs.
    """

    session: Session
    data_owner_id: str
    policy: FinanceAccessSnapshot

    @property
    def tenant_id(self) -> str:
        return self.session.tenant_id

    @property
    def auth_member_id(self) -> str:
        return self.session.auth_member_id

    def require_book(self, book_id: str | None) -> str | None:
        if self.policy.binding is not None:
            if book_id not in (None, self.policy.binding.book_id):
                raise FinanceAccessError("finance_book_not_authorized")
            return self.policy.binding.book_id
        return book_id  # Explicit credential-free owner fixtures only.

    def authority_coordinates(self) -> dict[str, Any]:
        return {
            "actor_id": self.auth_member_id,
            "data_owner_id": self.data_owner_id,
            **self.policy.authority_coordinates(),
        }


def read_scope(
    runtime: Any, session: Any, requested: str | None = None, *, advisor: bool = False
) -> FinanceReadScope:
    # The current actor and data owner are checked separately. No role name or
    # shared:household scope grants Books access (children also have that scope).
    if (
        session.tenant_id != runtime.settings.instance.tenant_id
        or session.auth_role != "principal"
        or session.is_view_as
        or runtime.settings.member_roles.get(session.auth_member_id) != "principal"
    ):
        raise FinanceAccessError("finance_control_identity_invalid")
    own = policy_for_owner(runtime, session.auth_member_id)
    candidates = [
        own,
        *runtime.finance_access.shared_for(session.tenant_id, session.auth_member_id),
    ]
    matches = [
        state
        for state in candidates
        if state.books_enabled
        and (state.binding is None or requested in (None, state.binding.book_id))
        and (not advisor or state.cos_analysis_enabled)
        and runtime.settings.member_roles.get(state.owner_id) == "principal"
    ]
    if len(matches) != 1:
        raise FinanceAccessError(
            "finance_book_selection_required" if matches else "finance_books_disabled"
        )
    state = matches[0]
    if state.binding is None and not runtime.settings._credential_free_fixture:
        raise FinanceAccessError("finance_book_binding_required")
    assert state.owner_id is not None
    return FinanceReadScope(session, state.owner_id, state)


async def refresh_read_scope(
    runtime: Any, scope: FinanceReadScope, *, advisor: bool = False
) -> FinanceReadScope:
    await refresh_control(runtime, scope.data_owner_id)
    latest = read_scope(
        runtime,
        scope.session,
        scope.policy.binding.book_id if scope.policy.binding else None,
        advisor=advisor,
    )
    if latest != scope:
        raise FinanceAccessError("finance_authority_changed")
    return latest
