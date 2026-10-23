"""Conservative text-only Apple Notes mutation contract.

The Notes application remains authoritative. Unsupported rich documents are
never flattened by a body replacement, even when a caller supplies plain text.
"""

from __future__ import annotations

import html
import json
from html.parser import HTMLParser
from typing import Any, Mapping


NOTE_OPERATIONS = frozenset(
    {"create_note", "update_note", "append_note", "delete_note"}
)


class _PlainNoteHTML(HTMLParser):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"div", "p", "br", "b", "strong", "h1", "h2", "h3"} or attrs:
            raise ValueError(
                "This note contains rich formatting or embedded content; edit it in Apple Notes."
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        self.handle_starttag(tag, [])

    def handle_comment(self, data: str) -> None:
        raise ValueError(
            "This note contains unsupported content; edit it in Apple Notes."
        )

    def handle_decl(self, decl: str) -> None:
        raise ValueError(
            "This note contains unsupported content; edit it in Apple Notes."
        )


def assert_plain_note_html(body: str) -> None:
    parser = _PlainNoteHTML(convert_charrefs=True)
    parser.feed(body)
    parser.close()


def assert_editable_note(note: Mapping[str, Any]) -> None:
    if note.get("locked") or note.get("shared"):
        raise ValueError("Locked or shared notes must be changed in Apple Notes.")
    attachments = note.get("attachments", note.get("attachments_json", []))
    if isinstance(attachments, str):
        try:
            attachments = json.loads(attachments)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "Note attachment state is unavailable; refresh Notes."
            ) from exc
    if attachments:
        raise ValueError("Notes with attachments must be changed in Apple Notes.")
    if not note.get("provider_revision"):
        raise ValueError(
            "The note revision is unavailable; refresh Notes before changing it."
        )
    body_format = note.get("body_format", "html")
    if body_format == "html":
        assert_plain_note_html(str(note.get("body") or ""))
    elif body_format != "plain":
        raise ValueError(
            "This note's format cannot be safely changed through AdministrateMe."
        )


def note_body_html(*, body: str, title: str | None = None) -> str:
    if not isinstance(body, str) or not body.strip() or len(body) > 100_000:
        raise ValueError("A note needs between 1 and 100,000 characters of text.")
    if title is not None and (not title.strip() or len(title) > 1_000):
        raise ValueError("A note title needs between 1 and 1,000 characters.")
    paragraphs = [
        f"<div>{html.escape(line) or '<br>'}</div>" for line in body.splitlines()
    ]
    if title is not None:
        paragraphs.insert(0, f"<div><b>{html.escape(title.strip())}</b></div>")
    return "".join(paragraphs)
