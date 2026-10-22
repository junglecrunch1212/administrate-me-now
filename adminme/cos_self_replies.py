"""Exact self-conversation coordinates shared by activation and delivery."""

import hashlib
import json

from adminme.openclaw_topology import member_agent_id


def self_reply_binding(member_id: str, account_id: str, recipient: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                "cos-self-reply-v1",
                member_agent_id(member_id),
                "imessage",
                account_id,
                recipient,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def self_reply_session(member_id: str, account_id: str, recipient: str) -> str:
    return (
        f"agent:{member_agent_id(member_id)}:imessage:{account_id}:direct:{recipient}"
    )
