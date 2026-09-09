"""Deterministic, privacy-scoped context packs for message interpretation.

Messages are immutable source facts.  This module never decides that an ask is
household truth and never reads a projection.  It selects a bounded set of
authorized source events from the append-only log, removes duplicated quoted
history, and assigns stable aliases that the pinned language seam can cite.

Relationship edges are deliberately code-owned. Provider threads are bounded
into source-time episodes, and cross-thread context requires exact RFC
continuity, exact attachment identity, or explicit member confirmation. Weak
participant/name/subject/filename overlap is explainable corroboration only. A
model may interpret the resulting language, but it cannot widen the
member/account/workspace boundary or invent another source.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import unicodedata
import weakref
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


MESSAGE_EVENT_TYPES = (
    "messaging.received",
    "messaging.sent",
    "telephony.sms_received",
)
MAX_THREAD_MESSAGES = 12
MAX_RELATED_MESSAGES = 6
MAX_MESSAGE_CHARS = 12_000
MAX_CONTEXT_CHARS = 48_000
MAX_EPISODE_DAYS = 30
MAX_EPISODE_SOURCES = 100

_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_SUBJECT_PREFIX = re.compile(r"^(?:(?:re|fw|fwd|rv|aw)\s*:\s*)+", re.IGNORECASE)
_TOKEN = re.compile(r"[\w@.+\-]{3,}", re.UNICODE)
_QUOTE_BOUNDARY = re.compile(
    r"(?im)^(?:>+\s*|-{2,}\s*(?:original|forwarded) message\s*-{2,}|"
    r"begin forwarded message:\s*$|forwarded message:\s*$|"
    r"on\s+.+?\s+wrote:\s*$|el\s+.+?\s+escribi[oó]:\s*$|"
    r"(?:from|de):\s+.+$)"
)
_HTML_QUOTE_BOUNDARY = re.compile(
    r"(?is)(?:<blockquote\b[^>]*>|<div\b[^>]*(?:class|id)\s*=\s*['\"][^'\"]*"
    r"(?:gmail_quote|gmail_attr|divRplyFwdMsg)[^'\"]*['\"][^>]*>)"
)
_PLAIN_QUOTE_HEADER_HINT = re.compile(r"(?im)^(?:sent|date|to|cc|bcc|subject):\s+\S.+$")
_HTML_QUOTE_HINT = re.compile(
    r"(?is)(?:moz-cite-prefix|data-(?:quoted|quote)|(?:class|id)\s*=\s*['\"]"
    r"[^'\"]*(?:quote|reply|forward)[^'\"]*['\"])"
)
_HTML_BREAK = re.compile(r"(?is)<(?:br|/p|/div|/li|/tr)\b[^>]*>")
_HTML_TAG = re.compile(r"(?is)<[^>]+>")
_STOPWORDS = {
    "about",
    "and",
    "con",
    "del",
    "for",
    "from",
    "para",
    "por",
    "re",
    "the",
    "this",
    "with",
    "your",
}

_CONTEXT_INPUT_TYPES = (
    *MESSAGE_EVENT_TYPES,
    "context.relationship.rejected",
    "context.relationship.confirmed",
    "context.source_text.corrected",
    "context.source_text.reinstated",
)


@dataclass
class _ContextInputHistory:
    """One append-only, owner-scoped context view for a live event log."""

    cursor: str | None = None
    messages_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages_by_thread: dict[tuple[str, ...], set[str]] = field(default_factory=dict)
    messages_by_rfc_id: dict[tuple[str, ...], set[str]] = field(default_factory=dict)
    messages_by_reference: dict[tuple[str, ...], set[str]] = field(default_factory=dict)
    messages_by_attachment: dict[tuple[str, ...], set[str]] = field(
        default_factory=dict
    )
    corrections: list[dict[str, Any]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_CONTEXT_INPUT_HISTORY: weakref.WeakKeyDictionary[
    Any, dict[str, _ContextInputHistory]
] = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class AuthoredMessageText:
    """Deterministically isolated, canonical message text used everywhere.

    The canonical form normalizes line endings once. Candidate admission and
    read-time evidence use this same representation, so an exact quote cannot
    pass extraction and later disappear because the provider used CRLF.
    """

    text: str
    reliable: bool
    quoted_history_removed: bool
    canonical_source_text: str = ""
    authored_span: tuple[int, int] | None = None
    contract: str = "authored-message-text-v2"
    text_truncated: bool = False


@dataclass(frozen=True)
class ContextMessage:
    alias: str
    source_event_id: str
    relation: str
    relation_reason_codes: tuple[str, ...]
    relationship_source_event_ids: tuple[str, ...]
    direction: str
    occurred_at: str
    source_channel: str
    sender: str | None
    recipients: tuple[str, ...]
    subject: str | None
    body_text: str
    authored_text_reliable: bool
    authored_text_reason_codes: tuple[str, ...]
    attachment_names: tuple[str, ...]
    text_truncated: bool = False

    def skill_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.alias,
            "relation": self.relation,
            "relation_reason_codes": list(self.relation_reason_codes),
            "direction": self.direction,
            "occurred_at": self.occurred_at,
            "source_channel": self.source_channel,
            "sender": self.sender,
            "recipients": list(self.recipients),
            "subject": self.subject,
            "body_text": self.body_text,
            "attachment_names": list(self.attachment_names),
            "text_truncated": self.text_truncated,
        }

    def source_text(self) -> str:
        return (
            f"Subject: {self.subject}\n\n{self.body_text}"
            if self.subject
            else self.body_text
        )


@dataclass(frozen=True)
class MessageContextPack:
    anchor_event_id: str
    thread_key: str
    fingerprint: str
    messages: tuple[ContextMessage, ...]
    episode_source_event_ids: tuple[str, ...]
    current_is_thread_anchor: bool

    def skill_payload(self) -> dict[str, Any]:
        return {
            "contract": "message-context-pack-v2",
            "thread_key": self.thread_key,
            "context_fingerprint": self.fingerprint,
            "current_source_id": next(
                (
                    message.alias
                    for message in self.messages
                    if message.source_event_id == self.anchor_event_id
                ),
                "m01",
            ),
            "messages": [message.skill_payload() for message in self.messages],
        }

    def alias_map(self) -> dict[str, ContextMessage]:
        return {message.alias: message for message in self.messages}


def authored_message_text(payload: Mapping[str, Any]) -> AuthoredMessageText:
    """Return only the provider message's confidently authored portion.

    Provider bodies are untrusted evidence. Recognized reply/forward boundaries
    are removed even at offset zero; an all-forward body therefore yields no
    authored content instead of promoting quoted history into a candidate.
    HTML-only Gmail/Apple/Outlook bodies are reduced deterministically after
    cutting their explicit quote container.
    """

    raw_text = payload.get("body_text") or payload.get("body")
    raw_html = payload.get("body_html")
    quoted_history_removed = False
    if isinstance(raw_text, str):
        text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if isinstance(raw_html, str):
            html_value = raw_html
            html_boundary = _HTML_QUOTE_BOUNDARY.search(html_value)
            if html_boundary is not None:
                html_value = html_value[: html_boundary.start()]
                html_text = html.unescape(
                    _HTML_TAG.sub("", _HTML_BREAK.sub("\n", html_value))
                )
                html_text = html_text.replace("\r\n", "\n").replace("\r", "\n").strip()
                html_plain_boundary = _QUOTE_BOUNDARY.search(html_text)
                if html_plain_boundary is not None:
                    html_text = html_text[: html_plain_boundary.start()].rstrip()
                elif len(_PLAIN_QUOTE_HEADER_HINT.findall(html_text)) >= 2:
                    return AuthoredMessageText(
                        "", False, False, canonical_source_text=text
                    )
                if not html_text:
                    return AuthoredMessageText(
                        "", True, True, canonical_source_text=text
                    )
                if text == html_text or text.startswith(f"{html_text}\n"):
                    bounded_html_text = html_text[:MAX_MESSAGE_CHARS]
                    return AuthoredMessageText(
                        text=bounded_html_text,
                        reliable=True,
                        quoted_history_removed=True,
                        canonical_source_text=text,
                        authored_span=(0, len(bounded_html_text)),
                        text_truncated=len(html_text) > MAX_MESSAGE_CHARS,
                    )
                return AuthoredMessageText("", False, False, canonical_source_text=text)
            if _HTML_QUOTE_HINT.search(raw_html):
                return AuthoredMessageText("", False, False, canonical_source_text=text)
    elif isinstance(raw_html, str):
        html_value = raw_html
        html_boundary = _HTML_QUOTE_BOUNDARY.search(html_value)
        if html_boundary is not None:
            html_value = html_value[: html_boundary.start()]
            quoted_history_removed = True
        text = html.unescape(_HTML_TAG.sub("", _HTML_BREAK.sub("\n", html_value)))
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if html_boundary is None and _HTML_QUOTE_HINT.search(raw_html):
            return AuthoredMessageText("", False, False, canonical_source_text=text)
    else:
        return AuthoredMessageText("", False, False)
    canonical_source_text = text
    boundary = _QUOTE_BOUNDARY.search(text)
    if boundary is not None:
        text = text[: boundary.start()].rstrip()
        quoted_history_removed = True
    elif len(_PLAIN_QUOTE_HEADER_HINT.findall(text)) >= 2:
        return AuthoredMessageText(
            "", False, False, canonical_source_text=canonical_source_text
        )
    return AuthoredMessageText(
        text=text[:MAX_MESSAGE_CHARS],
        reliable=True,
        quoted_history_removed=quoted_history_removed,
        canonical_source_text=canonical_source_text,
        authored_span=(0, min(len(text), MAX_MESSAGE_CHARS)) if text else None,
        text_truncated=len(text) > MAX_MESSAGE_CHARS,
    )


def strip_quoted_history(value: str) -> str:
    """Compatibility wrapper over the authored-message-text v2 contract."""

    return authored_message_text({"body_text": value}).text


def _normalized_identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    match = _EMAIL.search(value)
    return (match.group(0) if match else value.strip()).casefold()


def _identifiers(payload: Mapping[str, Any]) -> frozenset[str]:
    values: list[Any] = [
        payload.get("from_identifier"),
        payload.get("to_identifier"),
        payload.get("reply_to_identifier"),
        payload.get("from_number"),
        payload.get("to_number"),
    ]
    for key in ("to_identifiers", "cc_identifiers", "bcc_identifiers"):
        values.extend(payload.get(key) or [])
    return frozenset(
        normalized
        for value in values
        if (normalized := _normalized_identifier(value)) is not None
    )


def _referenced_identifiers(payload: Mapping[str, Any]) -> frozenset[str]:
    """Return exact email references from newly authored message content.

    These values are discovery edges, never identity or authorization truth.
    Quoted history is removed first so a forwarded chain does not multiply
    relationships already represented by its own source messages.
    """

    authored = authored_message_text(payload).text
    return frozenset(match.group(0).casefold() for match in _EMAIL.finditer(authored))


def _subject_tokens(payload: Mapping[str, Any]) -> frozenset[str]:
    subject = str(payload.get("subject") or "")
    subject = unicodedata.normalize("NFKC", _SUBJECT_PREFIX.sub("", subject)).casefold()
    return frozenset(
        token
        for token in _TOKEN.findall(subject)
        if token not in _STOPWORDS and not token.isdigit()
    )


def _attachment_names(payload: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    for attachment in payload.get("attachments") or []:
        if not isinstance(attachment, Mapping):
            continue
        value = attachment.get("filename") or attachment.get("name")
        if isinstance(value, str) and value.strip():
            names.append(value.strip()[:240])
    return tuple(dict.fromkeys(names))[:20]


def _attachment_tokens(payload: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        token
        for name in _attachment_names(payload)
        for token in _TOKEN.findall(unicodedata.normalize("NFKC", name).casefold())
        if token not in _STOPWORDS and not token.isdigit()
    )


def _attachment_identities(payload: Mapping[str, Any]) -> frozenset[str]:
    identities: set[str] = set()
    for attachment in payload.get("attachments") or []:
        if not isinstance(attachment, Mapping):
            continue
        digest = attachment.get("sha256") or attachment.get("content_sha256")
        provider_id = attachment.get("stable_attachment_id") or attachment.get(
            "provider_file_id"
        )
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            identities.add(f"sha256:{digest.casefold()}")
        if isinstance(provider_id, str) and provider_id.strip():
            identities.add(f"provider:{provider_id.strip()}")
    return frozenset(identities)


def _coordinate(
    event: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[str, str]:
    account = str(
        payload.get("provider_account_id") or event.get("source_account_id") or ""
    )
    workspace = str(payload.get("workspace") or "")
    return account, workspace


def _provider(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    return str(
        payload.get("provider")
        or event.get("source_adapter")
        or payload.get("source_channel")
        or ""
    )


def _thread_key(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    native = str(payload.get("thread_id") or "").strip()
    provider = str(
        payload.get("provider") or payload.get("source_channel") or "message"
    )
    account, workspace = _coordinate(event, payload)
    if native:
        raw = f"{provider}|{account}|{workspace}|{native}"
    else:
        participants = "|".join(sorted(_identifiers(payload)))
        subject = "|".join(sorted(_subject_tokens(payload)))
        native_message = str(
            payload.get("native_message_id")
            or payload.get("carrier_message_id")
            or payload.get("rfc_message_id")
            or ""
        )
        observation = (
            native_message
            or hashlib.sha256(
                f"{_message_time(event, payload)}|{_body(event, payload)}".encode(
                    "utf-8"
                )
            ).hexdigest()
        )
        raw = f"{provider}|{account}|{workspace}|single|{participants}|{subject}|{observation}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _message_time(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    return str(
        payload.get("received_at")
        or payload.get("sent_at")
        or event.get("occurred_at")
        or event.get("recorded_at")
        or ""
    )


def _parsed_message_time(
    event: Mapping[str, Any], payload: Mapping[str, Any]
) -> datetime | None:
    value = _message_time(event, payload)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _direction(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    labels = {str(value).upper() for value in payload.get("provider_labels") or []}
    return (
        "outbound"
        if event.get("type") == "messaging.sent" or "SENT" in labels
        else "inbound"
    )


def _body(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    return authored_message_text(payload).text


def _legacy_gmail_sent_without_quote_provenance(
    event: Mapping[str, Any], payload: Mapping[str, Any]
) -> bool:
    try:
        schema_version = int(event.get("schema_version") or 0)
    except (TypeError, ValueError):
        schema_version = 0
    return bool(
        event.get("type") == "messaging.sent"
        and schema_version < 4
        and "gmail" in _provider(event, payload).casefold()
    )


def _event_authored_message_text(
    event: Mapping[str, Any], payload: Mapping[str, Any]
) -> AuthoredMessageText:
    """Apply source-version provenance before treating a body as authored."""

    authored = authored_message_text(payload)
    if _legacy_gmail_sent_without_quote_provenance(event, payload):
        # Historical Gmail SENT observations retained only the plain MIME
        # alternative. It is impossible to prove whether that text was newly
        # authored or copied from an HTML quote container, so reviewed replay must
        # retain the fact while excluding its body from authored evidence.
        return AuthoredMessageText(
            "",
            False,
            False,
            canonical_source_text=authored.canonical_source_text,
        )
    return authored


def _eligible_event(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    if candidate.get("tenant_id") != current.get("tenant_id"):
        return False
    if candidate.get("owner_scope") != current.get("owner_scope"):
        return False
    if candidate.get("visibility_scope") != current.get("visibility_scope"):
        return False
    if candidate.get("sensitivity", "normal") != current.get("sensitivity", "normal"):
        return False
    payload = candidate.get("payload")
    current_payload = current.get("payload")
    if not isinstance(payload, Mapping) or not isinstance(current_payload, Mapping):
        return False
    candidate_member = payload.get("member_id")
    current_member = current_payload.get("member_id")
    if (
        candidate_member is not None
        and current_member is not None
        and candidate_member != current_member
    ):
        return False
    return _provider(candidate, payload) == _provider(
        current, current_payload
    ) and _coordinate(candidate, payload) == _coordinate(current, current_payload)


def _context_coordinate_key(event: Mapping[str, Any]) -> tuple[str, ...] | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    account, workspace = _coordinate(event, payload)
    return (
        str(event.get("tenant_id") or ""),
        str(event.get("owner_scope") or ""),
        str(event.get("visibility_scope") or ""),
        str(event.get("sensitivity") or "normal"),
        _provider(event, payload),
        account,
        workspace,
    )


def _index_context_event(history: _ContextInputHistory, event: dict[str, Any]) -> None:
    if event.get("type") not in MESSAGE_EVENT_TYPES:
        history.corrections.append(event)
        return
    event_id = str(event.get("event_id") or "")
    coordinate = _context_coordinate_key(event)
    payload = event.get("payload")
    if not event_id or coordinate is None or not isinstance(payload, Mapping):
        return
    history.messages_by_id[event_id] = event
    thread_id = str(payload.get("thread_id") or "").strip()
    if thread_id:
        history.messages_by_thread.setdefault((*coordinate, thread_id), set()).add(
            event_id
        )
    rfc_message_id = str(payload.get("rfc_message_id") or "").strip()
    if rfc_message_id:
        history.messages_by_rfc_id.setdefault((*coordinate, rfc_message_id), set()).add(
            event_id
        )
    for reference in list(payload.get("references") or []) + [
        payload.get("in_reply_to")
    ]:
        if isinstance(reference, str) and reference.strip():
            history.messages_by_reference.setdefault(
                (*coordinate, reference.strip()), set()
            ).add(event_id)
    for attachment_id in _attachment_identities(payload):
        history.messages_by_attachment.setdefault(
            (*coordinate, attachment_id), set()
        ).add(event_id)


def _indexed_context_inputs(
    history: _ContextInputHistory, current: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    """Return only sources that can satisfy the existing relationship rules."""

    coordinate = _context_coordinate_key(current)
    payload = current.get("payload")
    if coordinate is None or not isinstance(payload, Mapping):
        return tuple(history.corrections)
    current_id = str(current.get("event_id") or "")
    native_thread = str(payload.get("thread_id") or "").strip()
    primary_ids = (
        set(history.messages_by_thread.get((*coordinate, native_thread), ()))
        if native_thread
        else set()
    )
    if current_id:
        primary_ids.add(current_id)
    primary_events: list[Mapping[str, Any]] = [
        history.messages_by_id[event_id]
        for event_id in primary_ids
        if event_id in history.messages_by_id
    ]
    if current_id not in history.messages_by_id:
        primary_events.append(current)

    candidate_ids = set(primary_ids)
    for primary in primary_events:
        primary_payload = primary.get("payload")
        if not isinstance(primary_payload, Mapping):
            continue
        primary_rfc_id = str(primary_payload.get("rfc_message_id") or "").strip()
        if primary_rfc_id:
            candidate_ids.update(
                history.messages_by_reference.get((*coordinate, primary_rfc_id), ())
            )
        for reference in list(primary_payload.get("references") or []) + [
            primary_payload.get("in_reply_to")
        ]:
            if isinstance(reference, str) and reference.strip():
                candidate_ids.update(
                    history.messages_by_rfc_id.get((*coordinate, reference.strip()), ())
                )
        for attachment_id in _attachment_identities(primary_payload):
            candidate_ids.update(
                history.messages_by_attachment.get((*coordinate, attachment_id), ())
            )

    # Explicit confirmations are strong edges even when the provider has no
    # RFC or stable-attachment continuity. Include both ends here; the existing
    # latest-correction and privacy checks below remain authoritative.
    for correction in history.corrections:
        if correction.get("type") != "context.relationship.confirmed":
            continue
        correction_payload = correction.get("payload")
        if not isinstance(correction_payload, Mapping):
            continue
        first = str(correction_payload.get("source_event_id") or "")
        second = str(correction_payload.get("related_source_event_id") or "")
        if first in primary_ids and second:
            candidate_ids.add(second)
        if second in primary_ids and first:
            candidate_ids.add(first)

    indexed = [
        history.messages_by_id[event_id]
        for event_id in candidate_ids
        if event_id in history.messages_by_id
    ]
    indexed.sort(key=lambda value: str(value.get("event_id") or ""))
    return (*indexed, *history.corrections)


def _related_edge_reasons(
    payload: Mapping[str, Any],
    *,
    primary_events: tuple[Mapping[str, Any], ...],
) -> dict[str, tuple[str, ...]]:
    """Return exact relationship edges without scanning candidate body text."""

    edge_reasons: dict[str, set[str]] = {}
    candidate_message_id = str(payload.get("rfc_message_id") or "").strip()
    candidate_references = {
        str(reference).strip()
        for reference in (
            list(payload.get("references") or []) + [payload.get("in_reply_to")]
        )
        if isinstance(reference, str) and reference.strip()
    }
    candidate_attachment_ids = _attachment_identities(payload)
    for primary_event in primary_events:
        source_event_id = str(primary_event.get("event_id") or "")
        primary = primary_event.get("payload") or {}
        if not source_event_id or not isinstance(primary, Mapping):
            continue
        primary_message_id = str(primary.get("rfc_message_id") or "").strip()
        primary_references = {
            str(reference).strip()
            for reference in (
                list(primary.get("references") or []) + [primary.get("in_reply_to")]
            )
            if isinstance(reference, str) and reference.strip()
        }
        if (primary_message_id and primary_message_id in candidate_references) or (
            candidate_message_id and candidate_message_id in primary_references
        ):
            edge_reasons.setdefault(source_event_id, set()).add("rfc_reference")
        if candidate_attachment_ids & _attachment_identities(primary):
            edge_reasons.setdefault(source_event_id, set()).add("attachment_identity")

    return {
        source_event_id: tuple(sorted(reasons))
        for source_event_id, reasons in edge_reasons.items()
    }


def _related_corroboration(
    payload: Mapping[str, Any],
    *,
    primary_participants: frozenset[str],
    primary_subject: frozenset[str],
    primary_attachments: frozenset[str],
) -> tuple[str, ...]:
    """Explain an admitted source; weak text overlap never admits one."""

    participant_overlap = len(_identifiers(payload) & primary_participants)
    referenced_overlap = len(_referenced_identifiers(payload) & primary_participants)
    subject_overlap = len(_subject_tokens(payload) & primary_subject)
    attachment_overlap = len(_attachment_tokens(payload) & primary_attachments)
    corroboration: list[str] = []
    if participant_overlap:
        corroboration.append("participant_overlap")
    if referenced_overlap:
        corroboration.append("participant_reference_overlap")
    if subject_overlap:
        corroboration.append("subject_token_overlap")
    if attachment_overlap:
        corroboration.append("attachment_filename_overlap")
    # Identity/address/name/subject/filename overlap is corroboration only.
    return tuple(corroboration)


async def _read_context_inputs(
    event_log: Any, owner_scope: str, current: Mapping[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    if not hasattr(event_log, "read_since"):
        return
    try:
        owner_histories = _CONTEXT_INPUT_HISTORY.setdefault(event_log, {})
    except TypeError:
        # Some isolated test doubles are deliberately unhashable. They retain
        # the safe historical behavior instead of becoming globally retained.
        async for event in event_log.read_since(
            owner_scope=owner_scope,
            types=list(_CONTEXT_INPUT_TYPES),
        ):
            yield event
        return

    history = owner_histories.setdefault(owner_scope, _ContextInputHistory())
    async with history.lock:
        suffix = [
            event
            async for event in event_log.read_since(
                cursor=history.cursor,
                owner_scope=owner_scope,
                types=list(_CONTEXT_INPUT_TYPES),
            )
        ]
        if suffix:
            for event in suffix:
                _index_context_event(history, event)
            history.cursor = str(suffix[-1]["event_id"])
        snapshot = _indexed_context_inputs(history, current)
    for event in snapshot:
        yield event


def _thread_episodes(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    def source_time_key(value: Mapping[str, Any]) -> tuple[int, float, str]:
        parsed = _parsed_message_time(value, value.get("payload") or {})
        return (
            0 if parsed is not None else 1,
            parsed.timestamp() if parsed is not None else 0.0,
            str(value.get("event_id") or ""),
        )

    ordered = sorted(
        events,
        key=source_time_key,
    )
    episodes: list[list[dict[str, Any]]] = []
    for event in ordered:
        event_time = _parsed_message_time(event, event.get("payload") or {})
        if not episodes:
            episodes.append([event])
            continue
        current = episodes[-1]
        first_time = _parsed_message_time(current[0], current[0].get("payload") or {})
        if (
            len(current) >= MAX_EPISODE_SOURCES
            or event_time is None
            or first_time is None
            or event_time - first_time > timedelta(days=MAX_EPISODE_DAYS)
        ):
            episodes.append([event])
        else:
            current.append(event)
    return episodes


def _relationship_pair(first: str, second: str) -> tuple[str, str]:
    return tuple(sorted((first, second)))  # type: ignore[return-value]


def _correction_applies(event: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    if any(
        event.get(field) != current.get(field)
        for field in ("tenant_id", "owner_scope", "visibility_scope", "sensitivity")
    ):
        return False
    payload = event.get("payload")
    current_payload = current.get("payload")
    if not isinstance(payload, Mapping) or not isinstance(current_payload, Mapping):
        return False
    member_id = str(current_payload.get("member_id") or "")
    return (
        payload.get("member_id") == member_id
        and str(payload.get("provider") or "")
        in {"", str(current_payload.get("provider") or "")}
        and str(payload.get("provider_account_id") or "")
        in {"", _coordinate(current, current_payload)[0]}
        and str(payload.get("workspace") or "")
        in {"", _coordinate(current, current_payload)[1]}
    )


def _corrected_authored_text(
    authored: AuthoredMessageText,
    corrections: tuple[Mapping[str, Any], ...],
) -> AuthoredMessageText:
    """Remove only member-identified evidence spans from one immutable body."""

    if not corrections or not authored.reliable:
        return authored
    original = authored.text
    original_digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    spans: set[tuple[int, int]] = set()
    for correction in corrections:
        if (
            correction.get("algorithm") != authored.contract
            or correction.get("authored_text_digest") != original_digest
        ):
            return AuthoredMessageText(
                "",
                False,
                authored.quoted_history_removed,
                canonical_source_text=authored.canonical_source_text,
            )
        start = correction.get("evidence_start")
        end = correction.get("evidence_end")
        if not isinstance(start, int) or not isinstance(end, int):
            return AuthoredMessageText(
                "",
                False,
                authored.quoted_history_removed,
                canonical_source_text=authored.canonical_source_text,
            )
        quote = original[start:end]
        if (
            start < 0
            or end <= start
            or end > len(original)
            or hashlib.sha256(quote.encode("utf-8")).hexdigest()
            != correction.get("evidence_quote_digest")
        ):
            return AuthoredMessageText(
                "",
                False,
                authored.quoted_history_removed,
                canonical_source_text=authored.canonical_source_text,
            )
        spans.add((start, end))
    merged_spans: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged_spans and start <= merged_spans[-1][1]:
            prior_start, prior_end = merged_spans[-1]
            merged_spans[-1] = (prior_start, max(prior_end, end))
        else:
            merged_spans.append((start, end))
    corrected = original
    for start, end in reversed(merged_spans):
        corrected = corrected[:start] + corrected[end:]
    corrected = corrected.strip()
    return AuthoredMessageText(
        corrected,
        True,
        authored.quoted_history_removed,
        canonical_source_text=authored.canonical_source_text,
        authored_span=(0, len(corrected)) if corrected else None,
    )


async def build_message_context_pack(
    current: Mapping[str, Any], event_log: Any
) -> MessageContextPack:
    """Return a bounded context pack rooted in ``current``.

    Fake/minimal event logs used by isolated tests may not expose ``read_since``;
    in that case the contract degrades safely to the current source only.
    """

    current_payload = current.get("payload")
    if not isinstance(current_payload, Mapping):
        current_payload = {}
    all_events: list[dict[str, Any]] = [dict(current)]
    corrections: list[dict[str, Any]] = []
    async for candidate in _read_context_inputs(
        event_log, str(current.get("owner_scope") or ""), current
    ):
        if candidate.get("type") not in MESSAGE_EVENT_TYPES:
            if _correction_applies(candidate, current):
                corrections.append(candidate)
            continue
        if not _eligible_event(candidate, current):
            continue
        if candidate.get("event_id") == current.get("event_id"):
            continue
        all_events.append(candidate)

    relationship_state: dict[tuple[str, str], bool] = {}
    source_text_state: dict[str, tuple[bool, Mapping[str, Any]]] = {}
    for correction in corrections:
        correction_payload = correction.get("payload") or {}
        if correction.get("type") in {
            "context.relationship.rejected",
            "context.relationship.confirmed",
        }:
            pair = _relationship_pair(
                str(correction_payload.get("source_event_id") or ""),
                str(correction_payload.get("related_source_event_id") or ""),
            )
            relationship_state[pair] = (
                correction.get("type") == "context.relationship.confirmed"
            )
        elif correction.get("type") in {
            "context.source_text.corrected",
            "context.source_text.reinstated",
        }:
            correction_id = str(correction_payload.get("correction_id") or "")
            source_text_state[correction_id] = (
                correction.get("type") == "context.source_text.corrected",
                correction_payload,
            )

    native_thread = str(current_payload.get("thread_id") or "").strip()
    provider_thread_events: list[dict[str, Any]] = []
    if native_thread:
        for candidate in all_events:
            payload = candidate.get("payload")
            if (
                isinstance(payload, Mapping)
                and str(payload.get("thread_id") or "").strip() == native_thread
            ):
                provider_thread_events.append(candidate)
    else:
        provider_thread_events = [dict(current)]
    episodes = _thread_episodes(provider_thread_events)
    current_event_id = str(current.get("event_id") or "")
    full_episode = next(
        (
            episode
            for episode in episodes
            if current_event_id
            in {str(value.get("event_id") or "") for value in episode}
        ),
        [dict(current)],
    )
    same_thread = full_episode[-MAX_THREAD_MESSAGES:]
    if current_event_id not in {
        str(value.get("event_id") or "") for value in same_thread
    }:
        retained_ids = {
            current_event_id,
            *(
                str(value.get("event_id") or "")
                for value in same_thread[-(MAX_THREAD_MESSAGES - 1) :]
            ),
        }
        same_thread = [
            value
            for value in full_episode
            if str(value.get("event_id") or "") in retained_ids
        ]

    primary_participants = frozenset(
        value
        for event in same_thread
        for value in _identifiers(event.get("payload") or {})
    )
    primary_subject = frozenset(
        value
        for event in same_thread
        for value in _subject_tokens(event.get("payload") or {})
    )
    primary_attachments = frozenset(
        value
        for event in same_thread
        for value in _attachment_tokens(event.get("payload") or {})
    )
    primary_events = tuple(full_episode)
    episode_times = [
        parsed
        for event in full_episode
        if (parsed := _parsed_message_time(event, event.get("payload") or {}))
        is not None
    ]
    episode_start = min(episode_times) if episode_times else None
    episode_end = max(episode_times) if episode_times else None
    same_ids = {str(event.get("event_id") or "") for event in full_episode}
    related_ranked: list[
        tuple[int, float, dict[str, Any], tuple[str, ...], tuple[str, ...]]
    ] = []
    for candidate in all_events:
        if str(candidate.get("event_id") or "") in same_ids:
            continue
        payload = candidate.get("payload")
        if not isinstance(payload, Mapping):
            continue
        candidate_id = str(candidate.get("event_id") or "")
        candidate_time = _parsed_message_time(candidate, payload)
        within_episode = (
            candidate_time is not None
            and episode_start is not None
            and episode_end is not None
            and episode_start - timedelta(days=MAX_EPISODE_DAYS)
            <= candidate_time
            <= episode_end + timedelta(days=MAX_EPISODE_DAYS)
        )
        confirmed_sources = {
            primary_id
            for primary_id in same_ids
            if relationship_state.get(_relationship_pair(primary_id, candidate_id))
            is True
        }
        # Old native-thread episodes and malformed source times cannot admit
        # an automatic edge. Explicit confirmation keeps its existing exception.
        if not within_episode and not confirmed_sources:
            continue
        edge_reasons = (
            _related_edge_reasons(payload, primary_events=primary_events)
            if within_episode
            else {}
        )
        active_strong_sources = {
            primary_id
            for primary_id in edge_reasons
            if within_episode
            and relationship_state.get(_relationship_pair(primary_id, candidate_id))
            is not False
        }
        relationship_source_ids = tuple(
            sorted(confirmed_sources | active_strong_sources)
        )
        if relationship_source_ids:
            corroboration = _related_corroboration(
                payload,
                primary_participants=primary_participants,
                primary_subject=primary_subject,
                primary_attachments=primary_attachments,
            )
            reasons: list[str] = []
            if confirmed_sources:
                reasons.append("member_confirmed_context")
            for primary_id in sorted(active_strong_sources):
                reasons.extend(edge_reasons[primary_id])
            reasons.extend(corroboration)
            reasons_tuple = tuple(dict.fromkeys(reasons))
            related_ranked.append(
                (
                    1 if confirmed_sources else 0,
                    candidate_time.timestamp() if candidate_time is not None else 0.0,
                    candidate,
                    reasons_tuple,
                    relationship_source_ids,
                )
            )
    related_ranked.sort(
        key=lambda value: (value[0], value[1], str(value[2].get("event_id") or ""))
    )
    related = [
        (value[2], value[3], value[4])
        for value in related_ranked[-MAX_RELATED_MESSAGES:]
    ]

    selected: list[tuple[dict[str, Any], str, tuple[str, ...], tuple[str, ...]]] = []
    selected.extend(
        (event, "thread", ("provider_thread_id",), ()) for event in same_thread
    )
    selected.extend(
        (event, "related", reasons, sources) for event, reasons, sources in related
    )
    messages: list[ContextMessage] = []
    consumed = 0
    anchor_event_id = str(
        (full_episode[-1] if full_episode else current).get("event_id")
        or current.get("event_id")
        or ""
    )
    authored_by_event_id: dict[str, AuthoredMessageText] = {}
    for selected_event, *_rest in selected:
        selected_event_id = str(selected_event.get("event_id") or "")
        selected_payload = selected_event.get("payload") or {}
        selected_corrections = tuple(
            correction_payload
            for active, correction_payload in source_text_state.values()
            if active
            and str(correction_payload.get("source_event_id") or "")
            == selected_event_id
        )
        authored_by_event_id[selected_event_id] = _corrected_authored_text(
            _event_authored_message_text(selected_event, selected_payload),
            selected_corrections,
        )
    protected_reserves = {
        event_id: len(authored_by_event_id[event_id].text[:MAX_MESSAGE_CHARS])
        for event_id in {current_event_id, anchor_event_id}
        if event_id in authored_by_event_id
    }
    pending_protected_reserve = sum(protected_reserves.values())
    for index, (
        event,
        relation,
        relation_reasons,
        relationship_source_ids,
    ) in enumerate(selected, start=1):
        payload = event.get("payload") or {}
        event_id = str(event.get("event_id") or "")
        authored = authored_by_event_id[event_id]
        body = authored.text
        is_protected = event_id in protected_reserves
        protected_reserve = protected_reserves.pop(event_id, 0)
        pending_protected_reserve -= protected_reserve
        remaining = max(
            MAX_CONTEXT_CHARS - consumed - pending_protected_reserve,
            0,
        )
        if remaining == 0 and not is_protected:
            continue
        body = body[:remaining]
        consumed += len(body)
        recipient_values: list[str] = []
        for key in ("to_identifier", "to_identifiers", "cc_identifiers"):
            raw_values = payload.get(key)
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            for raw in values:
                normalized = _normalized_identifier(raw)
                if normalized is not None:
                    recipient_values.append(normalized)
        recipients = tuple(dict.fromkeys(recipient_values))[:30]
        sender = _normalized_identifier(
            payload.get("from_identifier") or payload.get("from_number")
        )
        messages.append(
            ContextMessage(
                alias=f"m{index:02d}",
                source_event_id=event_id,
                relation=relation,
                relation_reason_codes=relation_reasons,
                relationship_source_event_ids=relationship_source_ids,
                direction=_direction(event, payload),
                occurred_at=_message_time(event, payload),
                source_channel=str(payload.get("source_channel") or "message"),
                sender=sender,
                recipients=recipients,
                subject=str(payload.get("subject") or "").strip()[:1000] or None,
                body_text=body,
                authored_text_reliable=authored.reliable,
                authored_text_reason_codes=(
                    ("legacy_gmail_sent_quote_boundary_unavailable",)
                    if _legacy_gmail_sent_without_quote_provenance(event, payload)
                    else (() if authored.reliable else ("authored_text_uncertain",))
                ),
                attachment_names=_attachment_names(payload),
                text_truncated=len(body) < len(authored.text)
                or authored.text_truncated,
            )
        )
    stable = [
        {
            "relation": message.relation,
            "relation_reason_codes": message.relation_reason_codes,
            "direction": message.direction,
            "occurred_at": message.occurred_at,
            "text_sha256": hashlib.sha256(
                message.source_text().encode("utf-8")
            ).hexdigest(),
            "authored_text_reliable": message.authored_text_reliable,
        }
        for message in messages
    ]
    account, workspace = _coordinate(current, current_payload)
    provider_thread_key = _thread_key(current, current_payload)
    episode_first = full_episode[0] if full_episode else current
    # Provider time keeps exact duplicate observations in one identity while
    # splitting a provider thread id that is reused in a later episode.
    episode_identity = (
        _message_time(episode_first, episode_first.get("payload") or {}) or "isolated"
    )
    episode_thread_key = hashlib.sha256(
        f"{provider_thread_key}|episode|{episode_identity}".encode("utf-8")
    ).hexdigest()[:24]
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "tenant_id": current.get("tenant_id"),
                "owner_scope": current.get("owner_scope"),
                "visibility_scope": current.get("visibility_scope"),
                "sensitivity": current.get("sensitivity", "normal"),
                "provider_account_id": account,
                "workspace": workspace,
                "thread_key": episode_thread_key,
                "messages": stable,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return MessageContextPack(
        anchor_event_id=anchor_event_id,
        thread_key=episode_thread_key,
        fingerprint=fingerprint,
        messages=tuple(messages),
        episode_source_event_ids=tuple(
            str(value.get("event_id") or "")
            for value in full_episode
            if value.get("event_id")
        ),
        current_is_thread_anchor=str(current.get("event_id") or "") == anchor_event_id,
    )


__all__ = [
    "AuthoredMessageText",
    "ContextMessage",
    "MessageContextPack",
    "authored_message_text",
    "build_message_context_pack",
    "strip_quoted_history",
]
