"""Deterministic, privacy-scoped context packs for message interpretation.

Messages are immutable source facts.  This module never decides that an ask is
household truth and never reads a projection.  It selects a bounded set of
authorized source events from the append-only log, removes duplicated quoted
history, and assigns stable aliases that the pinned language seam can cite.

Relationship edges are deliberately code-owned: exact provider thread,
participant overlap, normalized subject tokens, and attachment filename
tokens.  A model may interpret the resulting language, but it cannot widen the
member/account/workspace boundary or invent another source.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any


MESSAGE_EVENT_TYPES = (
    "messaging.received",
    "messaging.sent",
    "telephony.sms_received",
)
MAX_THREAD_MESSAGES = 12
MAX_RELATED_MESSAGES = 6
MAX_MESSAGE_CHARS = 2400
MAX_CONTEXT_CHARS = 24_000

_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_SUBJECT_PREFIX = re.compile(r"^(?:(?:re|fw|fwd|rv|aw)\s*:\s*)+", re.IGNORECASE)
_TOKEN = re.compile(r"[\w@.+\-]{3,}", re.UNICODE)
_QUOTE_BOUNDARY = re.compile(
    r"(?im)^(?:>+\s*|-{2,}\s*original message\s*-{2,}|"
    r"on\s+.+?\s+wrote:\s*$|el\s+.+?\s+escribi[oó]:\s*$|"
    r"from:\s+.+$|de:\s+.+$)"
)
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


@dataclass(frozen=True)
class ContextMessage:
    alias: str
    source_event_id: str
    relation: str
    relation_reason_codes: tuple[str, ...]
    direction: str
    occurred_at: str
    source_channel: str
    sender: str | None
    recipients: tuple[str, ...]
    subject: str | None
    body_text: str
    attachment_names: tuple[str, ...]

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
    current_is_thread_anchor: bool

    def skill_payload(self) -> dict[str, Any]:
        return {
            "contract": "message-context-pack-v1",
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


def strip_quoted_history(value: str) -> str:
    """Keep the authored portion; earlier messages are represented separately."""

    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    match = _QUOTE_BOUNDARY.search(text)
    if match and match.start() > 0:
        text = text[: match.start()].rstrip()
    return text[:MAX_MESSAGE_CHARS]


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


def _coordinate(
    event: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[str, str]:
    account = str(
        payload.get("provider_account_id") or event.get("source_account_id") or ""
    )
    workspace = str(payload.get("workspace") or "")
    return account, workspace


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


def _direction(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    labels = {str(value).upper() for value in payload.get("provider_labels") or []}
    return (
        "outbound"
        if event.get("type") == "messaging.sent" or "SENT" in labels
        else "inbound"
    )


def _body(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    value = (
        payload.get("body")
        if event.get("type") == "telephony.sms_received"
        else payload.get("body_text")
    )
    return strip_quoted_history(str(value or ""))


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
    return _coordinate(candidate, payload) == _coordinate(current, current_payload)


def _related_score(
    payload: Mapping[str, Any],
    *,
    primary_participants: frozenset[str],
    primary_subject: frozenset[str],
    primary_attachments: frozenset[str],
) -> tuple[int, tuple[str, ...]]:
    reasons: list[str] = []
    score = 0
    participant_overlap = len(_identifiers(payload) & primary_participants)
    subject_overlap = len(_subject_tokens(payload) & primary_subject)
    attachment_overlap = len(_attachment_tokens(payload) & primary_attachments)
    if participant_overlap:
        score += min(participant_overlap, 3) * 3
        reasons.append("participant_overlap")
    if subject_overlap:
        score += min(subject_overlap, 4) * 2
        reasons.append("subject_token_overlap")
    if attachment_overlap:
        score += min(attachment_overlap, 3) * 3
        reasons.append("attachment_token_overlap")
    # A single shared mailbox address plus one generic subject word is not
    # enough to relate two threads. Require two shared subject tokens, a shared
    # attachment token, or two shared parties (normally member + counterparty).
    if participant_overlap < 2 and not (subject_overlap >= 2 or attachment_overlap):
        return 0, ()
    return score, tuple(reasons)


async def _read_messages(
    event_log: Any, owner_scope: str
) -> AsyncIterator[dict[str, Any]]:
    if not hasattr(event_log, "read_since"):
        return
    async for event in event_log.read_since(
        owner_scope=owner_scope,
        types=list(MESSAGE_EVENT_TYPES),
    ):
        yield event


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
    async for candidate in _read_messages(
        event_log, str(current.get("owner_scope") or "")
    ):
        if not _eligible_event(candidate, current):
            continue
        if candidate.get("event_id") == current.get("event_id"):
            continue
        all_events.append(candidate)

    native_thread = str(current_payload.get("thread_id") or "").strip()
    same_thread: list[dict[str, Any]] = []
    if native_thread:
        for candidate in all_events:
            payload = candidate.get("payload")
            if (
                isinstance(payload, Mapping)
                and str(payload.get("thread_id") or "").strip() == native_thread
            ):
                same_thread.append(candidate)
    else:
        same_thread = [dict(current)]
    same_thread.sort(
        key=lambda value: (
            _message_time(value, value.get("payload") or {}),
            str(value.get("event_id") or ""),
        )
    )
    same_thread = same_thread[-MAX_THREAD_MESSAGES:]

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
    same_ids = {str(event.get("event_id") or "") for event in same_thread}
    related_ranked: list[tuple[int, str, dict[str, Any], tuple[str, ...]]] = []
    for candidate in all_events:
        if str(candidate.get("event_id") or "") in same_ids:
            continue
        payload = candidate.get("payload")
        if not isinstance(payload, Mapping):
            continue
        score, reasons = _related_score(
            payload,
            primary_participants=primary_participants,
            primary_subject=primary_subject,
            primary_attachments=primary_attachments,
        )
        if score:
            related_ranked.append(
                (score, _message_time(candidate, payload), candidate, reasons)
            )
    related_ranked.sort(
        key=lambda value: (-value[0], value[1], str(value[2].get("event_id") or ""))
    )
    related = [(value[2], value[3]) for value in related_ranked[:MAX_RELATED_MESSAGES]]

    selected: list[tuple[dict[str, Any], str, tuple[str, ...]]] = []
    selected.extend((event, "thread", ("provider_thread_id",)) for event in same_thread)
    selected.extend((event, "related", reasons) for event, reasons in related)
    messages: list[ContextMessage] = []
    consumed = 0
    for index, (event, relation, relation_reasons) in enumerate(selected, start=1):
        payload = event.get("payload") or {}
        body = _body(event, payload)
        remaining = max(MAX_CONTEXT_CHARS - consumed, 0)
        if remaining == 0:
            break
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
                source_event_id=str(event.get("event_id") or ""),
                relation=relation,
                relation_reason_codes=relation_reasons,
                direction=_direction(event, payload),
                occurred_at=_message_time(event, payload),
                source_channel=str(payload.get("source_channel") or "message"),
                sender=sender,
                recipients=recipients,
                subject=str(payload.get("subject") or "").strip()[:1000] or None,
                body_text=body,
                attachment_names=_attachment_names(payload),
            )
        )
    latest = same_thread[-1] if same_thread else current
    anchor_event_id = str(latest.get("event_id") or current.get("event_id") or "")
    stable = [
        {
            "relation": message.relation,
            "relation_reason_codes": message.relation_reason_codes,
            "direction": message.direction,
            "occurred_at": message.occurred_at,
            "text_sha256": hashlib.sha256(
                message.source_text().encode("utf-8")
            ).hexdigest(),
        }
        for message in messages
    ]
    account, workspace = _coordinate(current, current_payload)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "tenant_id": current.get("tenant_id"),
                "owner_scope": current.get("owner_scope"),
                "visibility_scope": current.get("visibility_scope"),
                "sensitivity": current.get("sensitivity", "normal"),
                "provider_account_id": account,
                "workspace": workspace,
                "thread_key": _thread_key(current, current_payload),
                "messages": stable,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return MessageContextPack(
        anchor_event_id=anchor_event_id,
        thread_key=_thread_key(current, current_payload),
        fingerprint=fingerprint,
        messages=tuple(messages),
        current_is_thread_anchor=str(current.get("event_id") or "") == anchor_event_id,
    )


__all__ = [
    "ContextMessage",
    "MessageContextPack",
    "build_message_context_pack",
    "strip_quoted_history",
]
