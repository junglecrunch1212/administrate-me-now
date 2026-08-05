"""Encrypted, resumable bootstrap journal."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bootstrap.contracts import (
    BOOTSTRAP_SCHEMA_VERSION,
    SECTION_NAMES,
    atomic_write,
)


class BootstrapStateError(RuntimeError):
    pass


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_state(*, fixture: str | None, secret_provider: str) -> dict[str, Any]:
    return {
        "schema_version": BOOTSTRAP_SCHEMA_VERSION,
        "created_at": now_utc(),
        "updated_at": now_utc(),
        "fixture": fixture,
        "secret_provider": secret_provider,
        "sections": {
            str(index): {
                "name": name,
                "status": "not_started",
                "version": 1,
                "attempts": 0,
            }
            for index, name in enumerate(SECTION_NAMES, start=1)
        },
        "events": [],
        "follow_ups": [],
        "answers": {},
        "secret_references": {},
        "observation": {
            "active": True,
            "briefing_complete": False,
            "trust_not_before": None,
            "trusted": False,
        },
    }


class EncryptedStateStore:
    def __init__(self, path: Path, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("bootstrap-state key must be exactly 32 bytes")
        self.path = path
        self.key = key

    def save(self, state: dict[str, Any]) -> None:
        state["updated_at"] = now_utc()
        plaintext = json.dumps(
            state,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key).encrypt(
            nonce,
            plaintext,
            b"administrateme-bootstrap-state-v1",
        )
        envelope = {
            "format": "administrateme-bootstrap-state-v1",
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        atomic_write(
            self.path,
            json.dumps(envelope, sort_keys=True).encode("utf-8"),
            mode=0o600,
        )

    def load(self) -> dict[str, Any]:
        try:
            envelope = json.loads(self.path.read_text("utf-8"))
            if envelope.get("format") != "administrateme-bootstrap-state-v1":
                raise BootstrapStateError("unsupported bootstrap-state format")
            plaintext = AESGCM(self.key).decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                b"administrateme-bootstrap-state-v1",
            )
            state = json.loads(plaintext)
        except BootstrapStateError:
            raise
        except Exception as exc:
            raise BootstrapStateError(
                "bootstrap state is corrupt, tampered with, or uses another key"
            ) from exc
        if state.get("schema_version") != BOOTSTRAP_SCHEMA_VERSION:
            raise BootstrapStateError("unsupported bootstrap-state schema")
        return state
