"""Decode exact approved bytes without paths, URLs or upload-supplied approvals."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from adminme.events.schemas.finance_import import FinanceImportManifest, ImportBlobPin
from adminme.finance.dated_projection import validate_projection
from adminme.finance.publication import (
    PublicationError,
    canonical_bytes,
    validate_publication,
)
from adminme.finance.publication_provenance import (
    ClosedPackage,
    freeze_package,
    namespace_root,
)


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise PublicationError("finance_import_duplicate_key")
        value[key] = item
    return value


def _decode(value: Any, pin: ImportBlobPin) -> bytes:
    if not isinstance(value, str) or len(value) != 4 * ((pin.bytes + 2) // 3):
        raise PublicationError("finance_import_component_size")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError, RecursionError):
        raise PublicationError("finance_import_encoding_invalid") from None
    if len(raw) != pin.bytes or hashlib.sha256(raw).hexdigest() != pin.sha256:
        raise PublicationError("finance_import_component_hash")
    return raw


def _canonical_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(value, dict) or canonical_bytes(value) != raw:
            raise ValueError("noncanonical component")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise PublicationError("finance_import_canonical_json_required") from None


def decode_package(
    body: bytes | bytearray, manifest: FinanceImportManifest
) -> tuple[dict[str, Any], dict[str, Any] | None, ClosedPackage]:
    if len(body) > manifest.encoded_body_limit():
        raise PublicationError("finance_import_body_limit")
    try:
        packet = json.loads(body, object_pairs_hook=_object)
        if not isinstance(packet, dict) or set(packet) != {
            "publication",
            "projection",
            "index",
            "blobs",
        }:
            raise ValueError("invalid closed packet")
        encoded_blobs = packet["blobs"]
        if (
            not isinstance(encoded_blobs, dict)
            or encoded_blobs.keys() != manifest.blobs.keys()
        ):
            raise ValueError("invalid closed blob set")
        publication = validate_publication(
            _canonical_json(_decode(packet["publication"], manifest.publication))
        )
        if namespace_root(publication) != "hbprov1:" + manifest.index.sha256:
            raise PublicationError("finance_import_provenance_root_mismatch")
        if any(
            publication.get(k) != v for k, v in manifest.binding.model_dump().items()
        ):
            raise PublicationError("finance_import_binding_mismatch")
        index_bytes = _decode(packet["index"], manifest.index)
        index = _canonical_json(index_bytes)
        if index.get("source_snapshot_sha256") != manifest.source_snapshot_sha256:
            raise PublicationError("finance_import_source_snapshot_mismatch")
        if {row["sha256"] for row in index["evidence"]} != set(
            manifest.independently_reviewed_evidence
        ):
            raise PublicationError("finance_import_reviewed_evidence_mismatch")
        blobs = {
            digest: _decode(
                encoded_blobs[digest], ImportBlobPin(sha256=digest, bytes=size)
            )
            for digest, size in manifest.blobs.items()
        }
        package = freeze_package(
            ClosedPackage(
                index_bytes, blobs, frozenset(manifest.independently_reviewed_evidence)
            )
        )
        projection = None
        if manifest.projection is None:
            if packet["projection"] is not None:
                raise ValueError("unexpected projection")
        else:
            projection = validate_projection(
                _canonical_json(_decode(packet["projection"], manifest.projection))
            )
            if any(
                projection.get(k) != publication[k]
                for k in ("tenant_id", "owner_id", "book_id")
            ):
                raise PublicationError("finance_import_projection_owner_mismatch")
            if (
                projection["accounting_publication_id"] != publication["publication_id"]
                or projection["accounting_content_sha256"]
                != publication["content_sha256"]
            ):
                raise PublicationError("finance_import_projection_anchor_mismatch")
        return publication, projection, package
    except PublicationError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise PublicationError("finance_import_packet_invalid") from None
