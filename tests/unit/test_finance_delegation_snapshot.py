"""Current finance authority must include the exact quorum and owner in one read."""

from pathlib import Path
import json

import pytest
import sqlcipher3

from adminme.projections.parties.queries import (
    ServiceDelegationResolutionError,
    resolve_service_delegation,
)
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_SERVICE_PRINCIPAL_ID,
)

SCHEMA = Path(__file__).resolve().parents[2] / "adminme/projections/parties/schema.sql"


@pytest.fixture
def authority():
    conn = sqlcipher3.connect(":memory:")
    conn.row_factory = sqlcipher3.Row
    conn.executescript(SCHEMA.read_text())
    yield conn
    conn.close()


def grant(conn, *, tenant="tenant-synthetic", grant_id="grant-synthetic", **overrides):
    row = {
        "grant_id": grant_id,
        "tenant_id": tenant,
        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "service_party_id": "service-party:synthetic",
        "context_scope": "member_private",
        "context_member_id": "member-synthetic",
        "resource_owner_principal_id": "member-synthetic",
        "requested_by_member_id": "member-synthetic",
        "required_approvals_json": json.dumps(["member-synthetic"]),
        "approval_quorum": "member_owner",
        "permissions_json": json.dumps(["read_context"]),
        "audience_json": json.dumps(["private:member-synthetic"]),
        "visibility_json": json.dumps(["private:member-synthetic"]),
        "requested_at": "2026-01-01T00:00:00Z",
        "owner_scope": "private:member-synthetic",
        "visibility_scope": "private:member-synthetic",
        "last_event_id": "event-synthetic",
        **overrides,
    }
    conn.execute(
        "INSERT INTO service_delegation_grants ("
        + ",".join(row)
        + ") VALUES ("
        + ",".join("?" for _ in row)
        + ")",
        tuple(row.values()),
    )


def approve(
    conn,
    *,
    tenant="tenant-synthetic",
    grant_id="grant-synthetic",
    member="member-synthetic",
):
    conn.execute(
        "INSERT INTO service_delegation_approvals VALUES (?,?,?,?,?)",
        (tenant, grant_id, member, "2026-01-01T00:00:00Z", "event-approval"),
    )


def resolve(conn, **overrides):
    return resolve_service_delegation(
        conn,
        **{
            "tenant_id": "tenant-synthetic",
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "operator_principal_id": COS_AUTOMATION_OPERATOR_ID,
            "context_scope": "member_private",
            "context_member_id": "member-synthetic",
            **overrides,
        },
    )


def test_grant_and_quorum_are_one_snapshot_and_revocation_is_not_cached(authority):
    grant(authority)
    approve(authority)
    statements = []
    authority.set_trace_callback(statements.append)
    result = resolve(authority)
    authority.set_trace_callback(None)
    assert len(statements) == 1  # No gap between the grant and its quorum read.
    assert result.approving_member_ids == ("member-synthetic",)
    assert result.allowed_scopes == {"private:member-synthetic"}
    authority.execute(
        "UPDATE service_delegation_grants SET revoked_at='2026-01-02T00:00:00Z'"
    )
    with pytest.raises(ServiceDelegationResolutionError):
        resolve(authority)


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "extra",
        "wrong_tenant",
        "wrong_grant",
        "expired",
        "malformed_expiry",
        "ambiguous",
        "wrong_owner",
        "wrong_service",
    ],
)
def test_incomplete_extra_crossed_or_ambiguous_authority_never_resolves(
    authority, case
):
    grant(authority)
    if case == "wrong_tenant":
        approve(authority, tenant="tenant-other")
    elif case == "wrong_grant":
        approve(authority, grant_id="grant-other")
    elif case != "missing":
        approve(authority)
    if case == "extra":
        approve(authority, member="member-extra")
    elif case == "expired":
        authority.execute(
            "UPDATE service_delegation_grants SET expires_at='2000-01-01T00:00:00Z'"
        )
    elif case == "malformed_expiry":
        authority.execute("UPDATE service_delegation_grants SET expires_at='invalid'")
    elif case == "ambiguous":
        grant(authority, grant_id="grant-second")
        approve(authority, grant_id="grant-second")
    elif case == "wrong_owner":
        authority.execute(
            "UPDATE service_delegation_grants SET owner_party_id='party-other'"
        )
    elif case == "wrong_service":
        authority.execute(
            "UPDATE service_delegation_grants SET service_principal_id='service-principal:other'"
        )
    with pytest.raises(ServiceDelegationResolutionError):
        resolve(authority)


def test_shared_quorum_keeps_every_approval_and_cannot_borrow_another_tenant(authority):
    grant(
        authority,
        context_scope="household_shared",
        context_member_id=None,
        resource_owner_principal_id=None,
        approval_quorum="all_adult_principals",
        required_approvals_json=json.dumps(["member-synthetic", "member-second"]),
        audience_json=json.dumps(["shared:household"]),
        visibility_json=json.dumps(["shared:household"]),
    )
    approve(authority)
    approve(authority, tenant="tenant-other", member="member-second")
    with pytest.raises(ServiceDelegationResolutionError):
        resolve(authority, context_scope="household_shared", context_member_id=None)
    approve(authority, member="member-second")
    result = resolve(
        authority, context_scope="household_shared", context_member_id=None
    )
    assert result.approving_member_ids == ("member-second", "member-synthetic")
    assert result.allowed_scopes == {"shared:household"}
