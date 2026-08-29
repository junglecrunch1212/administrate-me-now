"""Content-free identity and scope coordinates for personal Google OAuth."""

from __future__ import annotations

import hashlib


GOOGLE_READONLY_ADAPTER_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
)


def credential_member_digest(member_id: str) -> str:
    """Return the non-identifying owner coordinate used by OAuth bindings."""

    normalized = member_id.strip()
    if not normalized:
        raise ValueError("member_id must be a non-empty string")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def member_google_secret_name(member_id: str) -> str:
    """Return a stable, non-identifying Keychain name for one member grant."""

    return f"google-oauth-member-{credential_member_digest(member_id)}"


__all__ = [
    "GOOGLE_READONLY_ADAPTER_SCOPES",
    "credential_member_digest",
    "member_google_secret_name",
]
