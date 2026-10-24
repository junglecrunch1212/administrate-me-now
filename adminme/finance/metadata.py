"""Bounded binding discovery; retained event metadata is not financial readiness."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from adminme.events.registry import registry
from adminme.finance.publication import canonical_bytes
from adminme.lib.session import Session

TYPES = ("finance.publication_accepted", "finance.projection_verified")


async def binding_metadata(
    runtime: Any, session: Session, book_id: str | None = None
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "result_kind": "binding_metadata",
        "state": "access_unavailable",
        "bindings": [],
        "completeness": {
            "complete": False,
            "through_event_id": None,
            "scanned_events": 0,
        },
        "qualification": "Retained app history at a fixed watermark; not source acceptance or financial readiness.",
    }
    if (
        session.auth_role != "principal"
        or session.is_view_as
        or session.tenant_id != runtime.settings.instance.tenant_id
        or runtime.settings.member_roles.get(session.auth_member_id) != "principal"
        or f"private:{session.auth_member_id}" not in session.allowed_scopes
    ):
        return base
    if any(registry.get(t, 1) is None for t in TYPES):
        return {**base, "state": "schema_unavailable"}
    if runtime.event_log is None:
        return {
            **base,
            "state": "history_incomplete",
            "reason": "event_log_unavailable",
        }
    snapshot = await runtime.event_log.read_finance_binding_metadata(
        session.tenant_id, session.auth_member_id
    )
    base["completeness"]["through_event_id"] = snapshot["through_event_id"]
    if snapshot["state"] != "complete":
        return {**base, "state": snapshot["state"], "reason": snapshot.get("reason")}
    rows = snapshot["rows"]
    books: dict[str, dict[str, Any]] = {}
    accounting: dict[tuple[str, str], str] = {}
    try:
        for row in rows:

            def field(name: str, nullable: bool = False) -> str | None:
                if nullable and row[name + "_type"] == "null":
                    return None
                value = row[name]
                if (
                    row[name + "_type"] != "text"
                    or not isinstance(value, str)
                    or not 1 <= len(value) <= 160
                ):
                    raise ValueError("invalid binding")
                return value

            if (
                field("tenant_id") != session.tenant_id
                or field("owner_id") != session.auth_member_id
            ):
                raise ValueError("mismatched owner")
            book = field("book_id")
            assert book is not None
            digest = field("content_sha256")
            if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
                raise ValueError("invalid hash")
            binding = books.setdefault(
                book, {"book_id": book, "accounting": None, "dated": None}
            )
            if row["type"] == TYPES[0]:
                current = field("publication_id")
                prior = field("prior_publication_id", True)
                if prior != (
                    binding["accounting"]["publication_id"]
                    if binding["accounting"]
                    else None
                ):
                    raise ValueError("incomplete accounting chain")
                if (book, str(current)) in accounting:
                    raise ValueError("duplicate accounting identity")
                accounting[(book, str(current))] = str(digest)
                binding.update(
                    entity_id=field("entity_id"),
                    audience_policy_id=field("audience_policy_id"),
                    audience_policy_version=field("audience_policy_version"),
                )
                binding["accounting"] = {
                    "publication_id": current,
                    "content_sha256": digest,
                    "prior_publication_id": prior,
                }
            else:
                current = field("projection_id")
                prior = field("prior_projection_id", True)
                anchor = field("accounting_publication_id")
                anchor_hash = field("accounting_content_sha256")
                if (
                    prior
                    != (binding["dated"]["projection_id"] if binding["dated"] else None)
                    or accounting.get((book, str(anchor))) != anchor_hash
                ):
                    raise ValueError("incomplete dated chain")
                binding["dated"] = {
                    "projection_id": current,
                    "content_sha256": digest,
                    "prior_projection_id": prior,
                    "accounting_publication_id": anchor,
                    "accounting_content_sha256": anchor_hash,
                }
    except (ValueError, KeyError, TypeError):
        return {
            **base,
            "state": "history_incomplete",
            "reason": "history_binding_or_predecessor_mismatch",
        }
    base["completeness"].update(
        complete=True,
        scanned_events=len(rows),
        history_sha256=hashlib.sha256(canonical_bytes(rows)).hexdigest(),
    )
    selected = [books[k] for k in sorted(books) if book_id is None or k == book_id]
    if len(selected) > 1:
        return {
            **base,
            "state": "book_selection_required",
            "book_ids": [b["book_id"] for b in selected][:8],
        }
    return {
        **base,
        "state": "populated" if selected else "verified_empty",
        "bindings": selected,
    }


async def household_entity_metadata(
    runtime: Any, session: Session, party_id: str | None
) -> dict[str, Any]:
    """Verify one supplied existing Party, never enumerate or mint identities."""
    if party_id is None:
        return {"state": "exact_existing_id_required", "authority": "parties"}
    if runtime.projections is None:
        return {"state": "schema_unavailable", "authority": "parties"}
    from adminme.projections.parties.queries import get_party

    def read(conn: Any) -> dict[str, Any]:
        party = get_party(conn, session, party_id=party_id)
        if party is None:
            # A scoped query cannot prove global absence. No guessed ID is echoed.
            return {"state": "not_visible_or_missing", "authority": "parties"}
        if party["kind"] != "household" or party.get("qualification") != "qualified":
            return {"state": "not_eligible_household", "authority": "parties"}
        return {
            "state": "visible_household_record",
            "authority": "parties",
            "party_id": party["party_id"],
            "last_event_id": party["last_event_id"],
            "binding_selected": False,
        }

    return await runtime.projections.read("parties", read)


async def inspect_metadata(
    runtime: Any, session: Session, book_id: str | None, household_party_id: str | None
) -> dict[str, Any]:
    """Already-authorized bounded read shared by direct owner and delegated CoS."""
    lock = runtime.settings_command_locks.setdefault(
        "finance-metadata:read", asyncio.Lock()
    )
    if lock.locked():
        return {
            "state": "temporarily_unavailable",
            "reason": "metadata_read_busy",
            "bindings": [],
            "completeness": {"complete": False},
        }
    async with lock:
        result = await binding_metadata(runtime, session, book_id)
        result["identity"] = {
            "tenant_id": session.tenant_id,
            "owner_id": session.auth_member_id,
        }
        result["household_entity"] = await household_entity_metadata(
            runtime, session, household_party_id
        )
        result["publisher_policy"] = {
            "state": "external_authority_required",
            "authority": "publisher_reviewed_policy",
            "local_registry_supported": False,
        }
        result["book_registration"] = {
            "state": "external_authority_required",
            "authority": "publisher_registry_or_retained_accepted_history",
            "local_registry_supported": False,
        }
        if len(json.dumps(result, ensure_ascii=True, allow_nan=False).encode()) > 8192:
            return {
                "state": "history_incomplete",
                "reason": "metadata_output_limit",
                "bindings": [],
                "completeness": {"complete": False},
            }
        return result
