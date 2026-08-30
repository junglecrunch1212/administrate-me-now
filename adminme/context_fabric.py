"""Deterministic identifiers and classifications for the Context Fabric.

The fabric is a rebuildable relationship index.  These helpers deliberately
use only typed event coordinates and normalized exact values; language-model
scores and embeddings are never identity, admission, or routing authority.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping


CONTEXT_ALGORITHM = "context-fabric-v1"
_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def stable_digest(*parts: object, length: int = 32) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:length]


def normalized_exact_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _SPACE.sub(" ", _NON_WORD.sub(" ", text)).strip()


def exact_text_fingerprint(*values: object) -> str:
    return stable_digest(*(normalized_exact_text(value) for value in values), length=64)


def policy_scope_digest(scope_kind: str, scope_value: str) -> str:
    """Hash a reviewed feedback scope without retaining its raw selector."""

    return hashlib.sha256(
        f"{scope_kind}|{scope_value.strip().casefold()}".encode("utf-8")
    ).hexdigest()


def owner_member_id(envelope: Mapping[str, Any]) -> str | None:
    scope = str(envelope.get("owner_scope") or "")
    if scope.startswith("private:"):
        return scope.removeprefix("private:")
    return None


def provider_coordinates(
    envelope: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[str, str, str, str]:
    member = str(payload.get("member_id") or owner_member_id(envelope) or "")
    provider = str(payload.get("provider") or payload.get("source_channel") or "")
    account = str(
        payload.get("provider_account_id") or envelope.get("source_account_id") or ""
    )
    workspace = str(payload.get("workspace") or "personal")
    return member, provider, account, workspace


def provider_conversation_context_id(
    envelope: Mapping[str, Any], payload: Mapping[str, Any]
) -> str | None:
    """Return an exact provider-conversation context, when one exists."""

    thread_id = str(payload.get("thread_id") or "").strip()
    if not thread_id:
        return None
    member, provider, account, workspace = provider_coordinates(envelope, payload)
    if not member or not provider or not account:
        return None
    return "ctx_provider_" + stable_digest(
        envelope.get("tenant_id"), member, provider, account, workspace, thread_id
    )


def triage_context_id(
    envelope: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[str, str]:
    """Choose a bounded deterministic grouping context for one proposal.

    A reviewed provider-thread key is code-produced by ``message_context`` and
    is safe to reuse as an exact conversation coordinate.  A model-produced
    context fingerprint is intentionally ignored.  Sources without an exact
    conversation remain isolated rather than being weakly merged.
    """

    interpretation = payload.get("interpretation")
    if isinstance(interpretation, Mapping):
        thread_key = str(interpretation.get("thread_key") or "").strip()
        if thread_key:
            member = str(
                payload.get("owner_member_id") or owner_member_id(envelope) or ""
            )
            return (
                "ctx_thread_"
                + stable_digest(envelope.get("tenant_id"), member, thread_key),
                "provider_conversation",
            )
    source_id = str(payload.get("source_event_id") or envelope.get("event_id") or "")
    return (
        "ctx_source_"
        + stable_digest(
            envelope.get("tenant_id"),
            payload.get("owner_member_id") or owner_member_id(envelope),
            source_id,
        ),
        "isolated_source",
    )


def evidence_state(payload: Mapping[str, Any]) -> str:
    interpretation = payload.get("interpretation")
    if isinstance(interpretation, Mapping) and interpretation.get("evidence_refs"):
        return "grounded_spans"
    reasons = payload.get("reasons")
    if isinstance(reasons, list) and any(str(value).strip() for value in reasons):
        return "source_reasons"
    return "source_link_only"


def ownership_state(payload: Mapping[str, Any]) -> str:
    interpretation = payload.get("interpretation")
    owner = (
        str(interpretation.get("action_owner") or "unknown")
        if isinstance(interpretation, Mapping)
        else "unknown"
    )
    return owner if owner in {"member", "group_including_member"} else "unknown"


def lifecycle_state(payload: Mapping[str, Any]) -> str:
    interpretation = payload.get("interpretation")
    value = (
        str(interpretation.get("lifecycle_state") or "unknown")
        if isinstance(interpretation, Mapping)
        else "unknown"
    )
    return value if value in {"open", "closed"} else "unknown"


def queue_lane(payload: Mapping[str, Any]) -> str:
    """Classify attention using explicit fields and a small reviewed lexicon."""

    candidate = payload.get("candidate")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    target = str(payload.get("target_kind") or "")
    interpretation = payload.get("interpretation")
    interpretation = interpretation if isinstance(interpretation, Mapping) else {}
    exact = " ".join(
        (
            normalized_exact_text(payload.get("title")),
            normalized_exact_text(
                " ".join(str(v) for v in payload.get("reasons") or [])
            ),
        )
    )
    words = set(exact.split())
    if any(
        token in words for token in ("security", "fraud", "breach", "password")
    ) or any(
        phrase in exact
        for phrase in (
            "new sign in",
            "sign in attempt",
            "unrecognized login",
            "verification code",
            "new device",
            "unauthorized access",
        )
    ):
        return "security"
    if target in {"recurrence"} and any(
        candidate.get(key) for key in ("exception", "missed", "overdue", "failed")
    ):
        return "recurring_exception"
    if target in {"commitment"} or interpretation.get("action_owner") == "member":
        return "member_promise"
    if any(
        candidate.get(key) not in (None, "", [])
        for key in ("due", "due_at", "due_date", "deadline", "start", "start_at")
    ):
        return "time_bound"
    if any(
        token in words
        for token in ("bill", "invoice", "payment", "pay", "balance", "overdue")
    ):
        return "financial"
    if (
        candidate.get("priority") in {"high", 1}
        or candidate.get("member_priority") is True
    ):
        return "member_priority"
    return "standard"


__all__ = [
    "CONTEXT_ALGORITHM",
    "evidence_state",
    "exact_text_fingerprint",
    "lifecycle_state",
    "normalized_exact_text",
    "owner_member_id",
    "ownership_state",
    "policy_scope_digest",
    "provider_conversation_context_id",
    "provider_coordinates",
    "queue_lane",
    "stable_digest",
    "triage_context_id",
]
