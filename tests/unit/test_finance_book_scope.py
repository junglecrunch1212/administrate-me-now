"""Book capability boundaries and immutable historical v1 compatibility."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from adminme.finance.access_control import (
    FinanceAccessCache,
    FinanceAccessError,
    read_scope,
)
from adminme.finance.queries import _owner
from adminme.lib.session import build_internal_session

TENANT, OWNER, READER, OUTSIDER = (
    "fixture-tenant",
    "fixture-owner",
    "fixture-reader",
    "fixture-outsider",
)
BINDING = {
    "book_id": "fixture-book",
    "entity_id": "fixture-entity",
    "audience_policy_id": "fixture-policy",
    "audience_policy_version": "1",
}


def event(*, version=2, revision=1, readers=(OWNER, READER), analysis=True):
    payload = {
        "owner_id": OWNER,
        "changed_by_member_id": OWNER,
        "revision": revision,
        "prior_event_id": None if revision == 1 else f"control-{revision - 1}",
        "binding": BINDING,
        "books_enabled": True,
        "cos_analysis_enabled": analysis,
        "provider_effects": False,
    }
    if version == 2:
        payload["reader_member_ids"] = list(readers)
    return {
        "tenant_id": TENANT,
        "event_id": f"control-{revision}",
        "type": "settings.finance_access_changed",
        "schema_version": version,
        "actor_identity": OWNER,
        "source_account_id": OWNER,
        "source_adapter": "member-settings-control-plane",
        "owner_scope": f"private:{OWNER}",
        "visibility_scope": f"private:{OWNER}",
        "payload": payload,
    }


def runtime():
    cache = FinanceAccessCache(TENANT, frozenset({OWNER, READER, OUTSIDER}))
    for member in (OWNER, READER, OUTSIDER):
        cache.restore(TENANT, member, None)
    settings = SimpleNamespace(
        instance=SimpleNamespace(tenant_id=TENANT),
        member_roles={member: "principal" for member in (OWNER, READER, OUTSIDER)},
        _credential_free_fixture=False,
    )
    return SimpleNamespace(settings=settings, finance_access=cache)


def test_scope_preserves_actor_and_pins_only_one_book():
    app = runtime()
    app.finance_access.restore(TENANT, OWNER, event())
    session = build_internal_session(READER, "principal", TENANT)
    scope = read_scope(app, session, BINDING["book_id"], advisor=True)
    assert scope.session is session and scope.auth_member_id == READER
    assert scope.data_owner_id == _owner(scope) == OWNER
    assert _owner(session) == READER  # The actual Session was never impersonated.
    with pytest.raises(FinanceAccessError):
        scope.require_book("different-book")
    with pytest.raises(FinanceAccessError):
        read_scope(app, build_internal_session(OUTSIDER, "principal", TENANT))
    with pytest.raises(FinanceAccessError):
        read_scope(app, replace(session, tenant_id="other-tenant"))
    with pytest.raises(FinanceAccessError):
        read_scope(app, replace(session, view_member_id=OWNER))
    app.finance_access.begin_change(TENANT, OWNER)
    with pytest.raises(FinanceAccessError):
        read_scope(app, session, advisor=True)


def test_historical_v1_remains_owner_only_and_v2_does_not_widen_source_ownership():
    app = runtime()
    owner = build_internal_session(OWNER, "principal", TENANT)
    reader = build_internal_session(READER, "principal", TENANT)
    original = event(version=1)
    app.finance_access.restore(TENANT, OWNER, original)
    assert read_scope(app, owner).data_owner_id == OWNER
    with pytest.raises(FinanceAccessError):
        read_scope(app, reader)
    app.finance_access.restore(TENANT, OWNER, event(revision=2))
    assert read_scope(app, reader).data_owner_id == OWNER
    assert "reader_member_ids" not in original["payload"]
    app.finance_access.restore(TENANT, OWNER, event(revision=3, analysis=False))
    assert read_scope(app, reader).policy.books_enabled
    with pytest.raises(FinanceAccessError):
        read_scope(app, reader, advisor=True)
    app.finance_access.restore(
        TENANT, OWNER, event(revision=4, readers=(OWNER,), analysis=False)
    )
    with pytest.raises(FinanceAccessError):
        read_scope(app, reader)


def test_removed_reader_role_does_not_revoke_other_authorized_readers():
    app = runtime()
    app.finance_access.restore(TENANT, OWNER, event())
    app.settings.member_roles[READER] = "child"
    with pytest.raises(FinanceAccessError):
        read_scope(app, build_internal_session(READER, "principal", TENANT))
    assert (
        read_scope(
            app, build_internal_session(OWNER, "principal", TENANT)
        ).data_owner_id
        == OWNER
    )
