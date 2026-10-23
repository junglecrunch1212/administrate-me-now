"""Authenticated self-service Google enrollment; no provider writes here."""

from __future__ import annotations

import asyncio
import secrets
import time
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from adminme.lib.session import Session
from adminme.member_google import (
    AuthorizationRequest,
    PermissionRequest,
    GoogleHandoffs,
    SERVICES,
    TTL,
    EVENT,
    COMPLETE_EVENT,
    account_id,
    canonical,
    digest,
    oauth_scopes,
    owned_accounts,
    propose_config,
)
from adminme.products.auth import session_from_request

router = APIRouter()
SessionDep = Annotated[Session, Depends(session_from_request)]
BASE = "/api/member/v1/settings/google-connections"


def owner(session: Session) -> None:
    from adminme.products.member.main import _require_self_settings

    _require_self_settings(session)
    if session.auth_role != "principal":
        raise HTTPException(403, "Google connections require their adult owner")


def vault(runtime: Any) -> GoogleHandoffs:
    return GoogleHandoffs(
        runtime.settings.instance.config_dir / "member-google-handoffs.enc",
        runtime.settings.encryption_key,
    )


def lock(runtime: Any) -> asyncio.Lock:
    return runtime.settings_command_locks.setdefault("settings-control", asyncio.Lock())


def require_publisher(runtime: Any) -> None:
    if (
        runtime.settings.google_config_committer is None
        or runtime.adapters is None
        or runtime.event_log is None
    ):
        raise HTTPException(
            503, "Google connection setup is not available on this server"
        )


def require_oauth(runtime: Any) -> Any:
    require_publisher(runtime)
    if runtime.settings.google_oauth is None:
        raise HTTPException(
            503,
            "Google sign-in needs server setup. Existing connections are unchanged.",
        )
    return runtime.settings.google_oauth


def exact_prior(
    runtime: Any, session: Session, request_id: str, fingerprint: str
) -> dict[str, Any] | None:
    record = vault(runtime).read().get(request_id)
    if record and (
        record["member_id"] != session.auth_member_id
        or record["fingerprint"] != fingerprint
    ):
        raise HTTPException(
            409,
            "This request identifier is already in use. Refresh Settings before reviewing again.",
        )
    return record


def public_start(record: dict[str, Any]) -> dict[str, Any]:
    result = GoogleHandoffs.public(record)
    result["authorization_required"] = result["state"] == "pending"
    result["accepted"] = result["state"] == "configured"
    return result


def begin(
    runtime: Any,
    session: Session,
    body: AuthorizationRequest,
    *,
    fingerprint: str,
    expected_revision: str | None,
) -> dict[str, Any]:
    oauth = require_oauth(runtime)
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    requested_scopes = oauth_scopes(body.services.model_dump())
    expires = time.time() + TTL
    try:
        url = oauth.start(state=state, verifier=verifier, scopes=requested_scopes)
    except Exception:
        raise HTTPException(
            503, "Google sign-in is unavailable. No connection changed."
        ) from None
    record = {
        "request_id": body.request_id,
        "member_id": session.auth_member_id,
        "tenant_id": session.tenant_id,
        "fingerprint": fingerprint,
        "account_id": account_id(session.auth_member_id, body.expected_account_id),
        "email": body.expected_account_id,
        "workspace": body.workspace,
        "services": body.services.model_dump(),
        "expected_revision": expected_revision,
        "state": "pending",
        "state_sha256": digest(state),
        "verifier": verifier,
        "scopes": requested_scopes,
        "expires_epoch": expires,
        "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
        "authorization_url": url,
    }
    vault(runtime).put(record)
    return public_start(record)


async def receipt(
    runtime: Any, session: Session, record: dict[str, Any], event_type: str
) -> None:
    from adminme.products.member.main import _write_settings_receipt

    await _write_settings_receipt(
        runtime,
        session,
        event_type=event_type,
        payload={
            "member_id": session.auth_member_id,
            "changed_by_member_id": session.auth_member_id,
            "account_id": record["account_id"],
            "request_id": record["request_id"],
            "services": record["services"],
            "configuration_sha256": record["proof"],
            "provider_effects": False,
            "automated_candidate_extraction": False,
            "sharing_changed": False,
        },
        request_id=record["request_id"],
        request_fingerprint=record["fingerprint"],
        owner_member_id=session.auth_member_id,
    )


async def publish(
    runtime: Any, session: Session, record: dict[str, Any], credential_ref: str | None
) -> dict[str, Any]:
    from adminme.products.member.main import _guard_member_write

    require_publisher(runtime)
    current = owned_accounts(
        runtime.settings.adapter_config, session.auth_member_id
    ).get(record["account_id"])
    if (current["revision"] if current else None) != record["expected_revision"]:
        raise HTTPException(
            409,
            "This Google account changed. Refresh Settings and review its permissions again.",
        )
    proposed, proof = propose_config(
        runtime.settings.adapter_config,
        member=session.auth_member_id,
        email=record["email"],
        workspace=record["workspace"],
        services=record["services"],
        credential_ref=credential_ref,
    )
    await _guard_member_write(
        runtime,
        session,
        EVENT,
        {
            "member_id": session.auth_member_id,
            "account_id": record["account_id"],
            "configuration_sha256": proof,
        },
    )
    # Validate the whole binding before journaling or touching a running adapter.
    runtime.validate_google_config(proposed)
    record.update(
        proof=proof,
        state="publishing",
        proposed_revision=owned_accounts(proposed, session.auth_member_id)[
            record["account_id"]
        ]["revision"],
    )
    vault(runtime).put(record)
    await receipt(runtime, session, record, EVENT)
    committer = runtime.settings.google_config_committer
    # Read exact on-disk bytes, then compare their parsed value with memory.
    # commit also checks the exact bytes under the shared updater lock.
    previous = committer.index_path.read_bytes()
    import json

    if json.loads(previous) != runtime.settings.adapter_config:
        raise HTTPException(
            409, "Server configuration changed. Refresh Settings before trying again."
        )
    evidence = committer.commit(
        previous_payload=previous,
        next_payload=canonical(proposed) + b"\n",
        authority={
            "kind": "member-google",
            "member_id": session.auth_member_id,
            "actor_member_id": session.auth_member_id,
            "tenant_id": session.tenant_id,
            "request_id": record["request_id"],
            "proof_sha256": proof,
        },
    )
    affected = {
        row["id"]
        for row in owned_accounts(proposed, session.auth_member_id)[
            record["account_id"]
        ]["rows"].values()
    }
    await runtime.apply_google_config(proposed, evidence, affected)
    await receipt(runtime, session, record, COMPLETE_EVENT)
    record.update(
        state="configured",
        message="Permissions saved. Each service below reports its current sync status.",
    )
    record.pop("verifier", None)
    record.pop("authorization_url", None)
    vault(runtime).put(record)
    return public_start(record)


async def reconcile_request(
    runtime: Any, session: Session, record: dict[str, Any]
) -> dict[str, Any]:
    if record["state"] == "authorizing":
        # Callback and GET share the lock. An authorizing record seen here can
        # only be an interrupted exchange; never replay its code.
        record.update(
            state="failed",
            message="Google sign-in was interrupted. Start a fresh request; existing permissions are unchanged.",
        )
        record.pop("verifier", None)
        record.pop("authorization_url", None)
        vault(runtime).put(record)
    if record["state"] == "publishing":
        import json

        committer = runtime.settings.google_config_committer
        if committer is None or committer.pending:
            return {
                "request_id": record["request_id"],
                "state": "authorizing",
                "account_id": record["account_id"],
                "message": "The server is recovering this saved permission change. Do not repeat it.",
            }
        # The authenticated transaction may have committed before memory was
        # rebound. Inspect its durable result; never call OAuth or commit again.
        durable = json.loads(committer.index_path.read_bytes())
        saved = owned_accounts(durable, session.auth_member_id).get(
            record["account_id"]
        )
        if saved and saved["revision"] == record.get("proposed_revision"):
            if runtime.settings.adapter_config != durable:
                runtime.validate_google_config(durable)
                evidence = json.loads(committer.readiness_path.read_bytes())
                affected = {row["id"] for row in saved["rows"].values()}
                await runtime.apply_google_config(durable, evidence, affected)
            await receipt(runtime, session, record, COMPLETE_EVENT)
            record.update(
                state="configured",
                message="Permissions saved. Check the service sync statuses below.",
            )
        else:
            record.update(
                state="failed",
                message="The requested permissions are not the current saved selection. Refresh Settings before reviewing again.",
            )
        record.pop("verifier", None)
        record.pop("authorization_url", None)
        vault(runtime).put(record)
    return GoogleHandoffs.public(record)


@router.get(BASE)
async def connections(request: Request, session: SessionDep) -> dict[str, Any]:
    owner(session)
    runtime = request.app.state.runtime
    inventory = runtime.adapters.inventory() if runtime.adapters else {}
    accounts = []
    async with lock(runtime):
        for account in owned_accounts(
            runtime.settings.adapter_config, session.auth_member_id
        ).values():
            services = {}
            for name, selected in account["services"].items():
                row = account["rows"].get(name)
                state = (
                    inventory.get(row["id"], {}).get("state", "not_running")
                    if row
                    else "not_connected"
                )
                services[name] = {
                    **selected,
                    "setup": "connected"
                    if selected["read"] and state == "running"
                    else "configured"
                    if selected["read"]
                    else "disabled",
                    "runtime": state,
                }
            accounts.append(
                {key: account[key] for key in ("id", "email", "workspace", "revision")}
                | {"services": services, "cos_context_access": False}
            )
    # Resolve actual private authority rather than treating a Google grant as it.
    if runtime.projections is not None:
        from adminme.projections.parties.queries import (
            resolve_service_delegation,
            ServiceDelegationResolutionError,
        )
        from adminme.service_identities import (
            COS_AUTOMATION_OPERATOR_ID,
            COS_SERVICE_PRINCIPAL_ID,
        )

        try:
            context = resolve_service_delegation(
                runtime.projections.connection("parties"),
                tenant_id=session.tenant_id,
                service_principal_id=COS_SERVICE_PRINCIPAL_ID,
                operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
                context_scope="member_private",
                context_member_id=session.auth_member_id,
                owner_party_id=None,
            )
            for account in accounts:
                account["cos_context_access"] = "read_context" in context.permissions
        except ServiceDelegationResolutionError:
            pass
    available = (
        runtime.settings.google_oauth is not None
        and runtime.settings.google_config_committer is not None
    )
    return {
        "available": available,
        "reason": None
        if available
        else "Google sign-in needs server setup. Existing connections are unchanged.",
        "services": [
            {
                "id": name,
                "label": label,
                "read_description": read,
                "write_description": write,
            }
            for name, (_kind, label, read, write) in SERVICES.items()
        ],
        "accounts": accounts,
        "cos_permission_href": "/member/settings?section=cos-work",
    }


@router.post(BASE + "/authorize")
async def authorize(
    body: AuthorizationRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    owner(session)
    runtime = request.app.state.runtime
    fingerprint = digest({"operation": "authorize", **body.model_dump()})
    async with lock(runtime):
        prior = exact_prior(runtime, session, body.request_id, fingerprint)
        if prior:
            return public_start(prior)
        current = owned_accounts(
            runtime.settings.adapter_config, session.auth_member_id
        ).get(account_id(session.auth_member_id, body.expected_account_id))
        if current:
            raise HTTPException(
                409,
                {
                    "code": "account_already_connected",
                    "reason": "This account is already connected. Use its permission controls.",
                },
            )
        return begin(
            runtime, session, body, fingerprint=fingerprint, expected_revision=None
        )


@router.post(BASE + "/{connection_id}/permissions")
async def permissions(
    connection_id: str, body: PermissionRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    owner(session)
    runtime = request.app.state.runtime
    fingerprint = digest(
        {"operation": "permissions", "account_id": connection_id, **body.model_dump()}
    )
    async with lock(runtime):
        prior = exact_prior(runtime, session, body.request_id, fingerprint)
        if prior:
            return public_start(prior)
        require_publisher(runtime)
        current = owned_accounts(
            runtime.settings.adapter_config, session.auth_member_id
        ).get(connection_id)
        if current is None:
            raise HTTPException(404, "Google account not found")
        if current["revision"] != body.expected_revision:
            raise HTTPException(
                409,
                {
                    "code": "stale_revision",
                    "reason": "Google permissions changed. Refresh Settings before reviewing again.",
                },
            )
        services = body.services.model_dump()
        increasing = any(
            services[name][permission] and not current["services"][name][permission]
            for name in SERVICES
            for permission in ("read", "write")
        )
        if increasing:
            auth = AuthorizationRequest(
                request_id=body.request_id,
                expected_account_id=current["email"],
                workspace=current["workspace"],
                services=body.services,
            )
            return begin(
                runtime,
                session,
                auth,
                fingerprint=fingerprint,
                expected_revision=body.expected_revision,
            )
        record = {
            "request_id": body.request_id,
            "member_id": session.auth_member_id,
            "tenant_id": session.tenant_id,
            "account_id": connection_id,
            "email": current["email"],
            "workspace": current["workspace"],
            "services": services,
            "expected_revision": body.expected_revision,
            "fingerprint": fingerprint,
            "state": "publishing",
        }
        return await publish(runtime, session, record, None)


@router.get(BASE + "/requests/{request_id}")
async def request_status(
    request_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    owner(session)
    runtime = request.app.state.runtime
    async with lock(runtime):
        record = vault(runtime).get(request_id, session.auth_member_id)
        if record is None:
            raise HTTPException(404, "Google connection request not found")
        return await reconcile_request(runtime, session, record)


@router.get(BASE + "/callback")
async def callback(
    request: Request,
    session: SessionDep,
    state: str = "",
    code: str = "",
    error: str = "",
) -> RedirectResponse:
    owner(session)
    runtime = request.app.state.runtime
    async with lock(runtime):
        matches = [
            record
            for record in vault(runtime).read().values()
            if record.get("state_sha256") == digest(state)
            and record["member_id"] == session.auth_member_id
            and record["tenant_id"] == session.tenant_id
        ]
        if not state or len(state) > 200 or len(code) > 8192 or len(matches) != 1:
            raise HTTPException(
                400, "Google sign-in does not match this member's request"
            )
        record = matches[0]
        if record["state"] == "pending":
            if record["expires_epoch"] <= time.time():
                record.update(
                    state="expired",
                    message="This Google sign-in expired. Review a fresh connection request.",
                )
            elif error or not code:
                record.update(
                    state="cancelled",
                    message="Google access was not granted. Existing connections are unchanged.",
                )
            else:
                # Claim before exchanging: replay/restart never redeems a code twice.
                record.update(
                    state="authorizing",
                    message="Checking the Google account and selected permissions.",
                )
                vault(runtime).put(record)
                try:
                    from bootstrap.google_oauth_contract import credential_member_digest

                    grant = await asyncio.to_thread(
                        require_oauth(runtime).exchange,
                        code=code,
                        verifier=record["verifier"],
                        scopes=record["scopes"],
                        expected_account_id=record["email"],
                        member_id=session.auth_member_id,
                        secret_name=f"google-oauth-member-{credential_member_digest(session.auth_member_id)}-{record['request_id']}",
                    )
                    await publish(runtime, session, record, grant.credential_ref)
                except Exception:
                    # Do not log provider responses (may contain codes/tokens).
                    # A durable publication is reconciled rather than undone.
                    if record["state"] == "publishing":
                        await reconcile_request(runtime, session, record)
                    else:
                        record.update(
                            state="failed",
                            message="Google sign-in could not be completed. Verify the selected account and grant every selected permission, then start a new request.",
                        )
            record.pop("verifier", None)
            vault(runtime).put(record)
        elif record["state"] == "publishing":
            await reconcile_request(runtime, session, record)
        response = RedirectResponse(
            "/member/settings?section=connections&google_request="
            + record["request_id"],
            status_code=303,
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
