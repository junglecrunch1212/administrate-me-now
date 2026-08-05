"""Deterministic feature vectors for AdministrateMe domain search.

This is deliberately not conversation memory and does not call a model SDK.
It creates a stable, local lexical-semantic representation only for typed,
authorized domain facts. OpenClaw continues to own generic memory and model
routing. The fixed 1536-dimensional contract matches the existing sqlite-vec
projection and provides a credential-free rebuild path.
"""

from __future__ import annotations

import hashlib
import math
import re


DOMAIN_VECTOR_DIMENSIONS = 1536
DOMAIN_VECTOR_MODEL = "adminme-domain-hash-v1"
_TOKEN = re.compile(r"[\w'-]+", re.UNICODE)


def domain_vector(text: str) -> list[float]:
    """Return a stable normalized signed feature-hash vector for ``text``."""

    values = [0.0] * DOMAIN_VECTOR_DIMENSIONS
    tokens = [token.casefold() for token in _TOKEN.findall(text)]
    for position, token in enumerate(tokens):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % DOMAIN_VECTOR_DIMENSIONS
        sign = 1.0 if digest[4] & 1 else -1.0
        # Repeated terms matter, but later occurrences contribute slightly less.
        values[index] += sign / math.sqrt(1.0 + position / 32.0)
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude:
        values = [value / magnitude for value in values]
    return values


__all__ = ["DOMAIN_VECTOR_DIMENSIONS", "DOMAIN_VECTOR_MODEL", "domain_vector"]
