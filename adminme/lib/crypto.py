"""
Crypto utilities — SQLCipher key derivation and secret handling.

Per ADMINISTRATEME_BUILD.md §L2 and SYSTEM_INVARIANTS.md §1.

Prompt 03 scope:
- `derive_event_log_key(op_ref)`: resolve an `op://` 1Password service-account
  reference to raw SQLCipher key bytes. The 1Password CLI is not yet wired up
  in this sandbox phase; when the CLI is unavailable or the reference is a
  test stub (`op://test/...`), we derive a deterministic 32-byte key from the
  reference string via HKDF so tests remain hermetic.

Future prompts will extend this module with:
- Fernet key derivation for at-rest artifact encryption of oversized payloads
  (§1 invariant 7), keyed from the SQLCipher master key.
- Path resolution for artifact and raw-event sidecar directories via
  `adminme.lib.instance_config` (§15/D15).
"""

from __future__ import annotations

import os
import shutil
import subprocess

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

EVENT_LOG_KEY_BYTES = 32
_HKDF_INFO = b"adminme.event_log.sqlcipher.v1"
_HKDF_SALT = b"adminme.event_log.salt.v1"


class SecretResolutionError(RuntimeError):
    """Raised when a secret reference cannot be resolved."""


def derive_event_log_key(op_ref: str) -> bytes:
    """
    Resolve `op_ref` (an `op://vault/item/field` 1Password reference) into a
    32-byte SQLCipher key.

    In the sandbox phase (prompt 03), the 1Password CLI may be absent. If so,
    or if `op_ref` starts with `op://test/`, we derive a deterministic key by
    HKDF-expanding the reference string. This keeps tests hermetic and
    self-contained while preserving the real shape of the API.

    Callers in production pass a real `op://...` reference and require the
    1Password CLI to be installed and signed in; a later prompt will harden
    this to refuse non-test refs without `op` on PATH.
    """
    if not op_ref:
        raise SecretResolutionError("empty secret reference")

    if op_ref.startswith("op://test/"):
        return _hkdf_from_material(op_ref.encode("utf-8"))

    op_bin = shutil.which("op")
    if op_bin is None:
        if os.environ.get("ADMINME_ALLOW_DERIVED_KEYS") == "1":
            return _hkdf_from_material(op_ref.encode("utf-8"))
        raise SecretResolutionError(
            "1Password CLI (`op`) not on PATH and ref is not a test stub; "
            "set ADMINME_ALLOW_DERIVED_KEYS=1 for local dev or install `op`."
        )

    try:
        out = subprocess.run(
            [op_bin, "read", "--no-newline", op_ref],
            check=True,
            capture_output=True,
            timeout=15,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SecretResolutionError(f"failed to read {op_ref}: {exc}") from exc

    material = out.stdout
    if not material:
        raise SecretResolutionError(f"empty secret returned for {op_ref}")
    return _hkdf_from_material(material)


def _hkdf_from_material(material: bytes) -> bytes:
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=EVENT_LOG_KEY_BYTES,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    return kdf.derive(material)
