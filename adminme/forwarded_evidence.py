"""Bounded evidence decoding for deliberate submissions, never passive mail.

Content is untrusted source evidence. No decoder follows URLs, executes content,
resolves identities, or treats a calendar invitation as permission to RSVP.
"""

from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser
import hashlib
from html.parser import HTMLParser
import re
from typing import Any

MAX_SUBMISSION_TEXT = 32_000
MAX_ATTACHMENT_BYTES = 262_144
MAX_EVIDENCE_BYTES = 524_288
MAX_EVIDENCE_PARTS = 64
SUPPORTED_MIME = frozenset(
    {"text/plain", "text/html", "text/calendar", "message/rfc822"}
)


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"br", "p", "div", "li", "tr"} and not self.hidden:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li", "tr"} and not self.hidden:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.text.append(data)


def html_text(value: str) -> str:
    parser = _HTMLText()
    parser.feed(value)
    return "".join(parser.text).strip()


def decode_part_data(value: Any) -> bytes | None:
    if not isinstance(value, str) or len(value) > MAX_EVIDENCE_BYTES * 2:
        return None
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError):
        return None


def evidence_mime(part: dict[str, Any]) -> str:
    mime = str(part.get("mimeType") or "application/octet-stream").lower()
    filename = str(part.get("filename") or "").lower()
    if mime in {"application/octet-stream", "application/ics"}:
        if filename.endswith(".ics"):
            return "text/calendar"
        if filename.endswith(".eml"):
            return "message/rfc822"
    return mime


def submission_evidence(
    payload: dict[str, Any], *, subject: str = ""
) -> dict[str, Any]:
    """Decode only supported MIME leaves, retaining full bounded source parts.

    A truncated, missing, invalid or unsupported part makes the entire submission
    incomplete. The interpreter must leave that capture for member clarification.
    """

    parts: list[dict[str, str]] = []
    issues: list[str] = []
    used = 0
    visited = 0

    def issue(code: str) -> None:
        if code not in issues and len(issues) < 20:
            issues.append(code)

    def add(text: str, mime: str, filename: str, role: str) -> None:
        nonlocal used
        encoded = text.encode("utf-8")
        if len(parts) >= MAX_EVIDENCE_PARTS or used + len(encoded) > MAX_EVIDENCE_BYTES:
            issue("evidence_storage_limit")
            return
        used += len(encoded)
        parts.append(
            {
                "mime_type": mime,
                "filename": filename[:500],
                "role": role,
                "text": text,
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )

    def walk_eml(data: bytes, filename: str, depth: int) -> None:
        message = BytesParser(policy=policy.default).parsebytes(data)
        if message.defects:
            issue("malformed_attached_email")
        # Original envelope fields are evidence only, never routing metadata.
        original_headers = "\n".join(
            f"{key}: {message.get(key)}"
            for key in ("From", "To", "Date", "Subject", "Message-ID")
            if message.get(key)
        )
        if original_headers:
            add(original_headers, "message/rfc822", filename, "forwarded")

        def convert(message_part: Any, mime_depth: int = 0) -> dict[str, Any]:
            if mime_depth + depth > 10:
                return {
                    "mimeType": "text/plain",
                    "_adminme_attachment_issue": "mime_complexity_limit",
                }
            if message_part.is_multipart():
                return {
                    "mimeType": message_part.get_content_type(),
                    "parts": [
                        convert(child, mime_depth + 1)
                        for child in message_part.iter_parts()
                    ],
                }
            raw = message_part.get_payload(decode=True)
            if raw is None:
                raw = str(message_part.get_payload()).encode("utf-8")
            return {
                "mimeType": message_part.get_content_type(),
                "filename": message_part.get_filename() or "",
                "body": {
                    "data": base64.urlsafe_b64encode(raw).decode(),
                    "size": len(raw),
                },
                "headers": [
                    {
                        "name": "Content-Type",
                        "value": str(message_part.get("Content-Type") or ""),
                    }
                ],
            }

        walk(convert(message), depth + 1, "forwarded")

    def walk(part: dict[str, Any], depth: int, role: str = "outer") -> None:
        nonlocal visited
        visited += 1
        if depth > 10 or visited > MAX_EVIDENCE_PARTS:
            issue("mime_complexity_limit")
            return
        mime = evidence_mime(part)
        filename = str(part.get("filename") or "")
        children = [
            child for child in part.get("parts") or [] if isinstance(child, dict)
        ]
        if mime == "multipart/alternative":
            plain = [
                child for child in children if child.get("mimeType") == "text/plain"
            ]
            html = [child for child in children if child.get("mimeType") == "text/html"]
            selected = plain[:1] or html[:1]
            if not selected:
                issue("unsupported_body_alternative")
            for child in selected:
                walk(child, depth + 1, role)
            # Non-body attachments cannot be silently hidden by alternatives.
            for child in children:
                if child not in selected and child.get("filename"):
                    walk(child, depth + 1, role)
            return
        if children:
            for child in children:
                walk(
                    child, depth + 1, "forwarded" if mime == "message/rfc822" else role
                )
            return
        if part.get("_adminme_attachment_issue"):
            issue(str(part["_adminme_attachment_issue"]))
            return
        if mime not in SUPPORTED_MIME:
            issue("unsupported_attachment:" + mime[:120])
            return
        body = part.get("body") or {}
        data = decode_part_data(body.get("data"))
        if data is None:
            issue("missing_or_invalid_mime_content")
            return
        if len(data) > MAX_ATTACHMENT_BYTES:
            issue("attachment_size_limit")
            return
        if mime == "message/rfc822":
            walk_eml(data, filename, depth)
            return
        charset = "utf-8"
        for header in part.get("headers") or []:
            if str(header.get("name") or "").lower() == "content-type":
                match = re.search(
                    r'charset\s*=\s*["\']?([\w-]+)',
                    str(header.get("value") or ""),
                    re.I,
                )
                if match:
                    charset = match.group(1)
        try:
            text = data.decode(charset)
        except (UnicodeError, LookupError):
            issue("unsupported_or_invalid_text_encoding")
            text = data.decode("utf-8", errors="replace")
        if mime == "text/html":
            text = html_text(text)
        if mime == "text/calendar":
            role = "calendar"
            if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
                issue("malformed_calendar_attachment")
        add(text, mime, filename, role)

    walk(payload, 0)
    text = "\n\n".join(
        value for value in [subject, *(part["text"] for part in parts)] if value
    )
    if not text.strip():
        issue("empty_submission")
    if len(text) > MAX_SUBMISSION_TEXT:
        issue("interpretation_size_limit")
    return {
        "text": text[:MAX_SUBMISSION_TEXT],
        "complete": not issues,
        "issues": issues,
        "parts": parts,
    }
