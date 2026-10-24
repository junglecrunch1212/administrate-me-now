"""Encrypted append-only proof archive and deterministic replay verification."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
import hashlib
import json
import time
from typing import Any, Literal

from pydantic import Field, model_validator

from adminme.events.envelope import EventEnvelope
from adminme.finance.publication import PublicationError
from adminme.finance.publication_provenance import (
    ClosedPackage,
    StrictModel,
    VerifiedProvenance,
    namespace_root,
    parse_blob,
    verify_package,
)


class ProvenanceBlobArchivedV1(StrictModel):
    owner_id: str = Field(min_length=1, max_length=96)
    book_id: str = Field(min_length=1, max_length=96)
    root_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: Literal["index", "chunk", "evidence"]
    canonical_json: str = Field(min_length=1, max_length=1048576)
    approved_evidence: list[str] = Field(max_length=128)

    @model_validator(mode="after")
    def closed_blob(self) -> ProvenanceBlobArchivedV1:
        raw = self.canonical_json.encode("utf-8")
        parse_blob(raw, 262144 if self.kind == "index" else 1048576)
        if hashlib.sha256(raw).hexdigest() != self.content_sha256:
            raise ValueError("proof blob hash mismatch")
        if self.kind == "index":
            if (
                self.content_sha256 != self.root_sha256
                or self.approved_evidence != sorted(set(self.approved_evidence))
            ):
                raise ValueError("proof index identity mismatch")
        elif self.approved_evidence:
            raise ValueError("only an owner-approved index may carry evidence pins")
        return self


def apply_provenance(envelope: dict[str, Any], conn: Any) -> None:
    p = ProvenanceBlobArchivedV1.model_validate(envelope["payload"])
    scope = f"private:{p.owner_id}"
    if (
        envelope["owner_scope"] != scope
        or envelope["visibility_scope"] != scope
        or envelope["sensitivity"] != "privileged"
        or envelope["actor_identity"] != p.owner_id
    ):
        raise PublicationError("provenance_scope_invalid")
    key = (
        envelope["tenant_id"],
        p.owner_id,
        p.book_id,
        p.root_sha256,
        p.content_sha256,
    )
    existing = conn.execute(
        "SELECT kind,canonical_json,approved_evidence_json FROM finance_provenance_blobs WHERE tenant_id=? AND owner_id=? AND book_id=? AND root_sha256=? AND content_sha256=?",
        key,
    ).fetchone()
    value = (p.kind, p.canonical_json, json.dumps(p.approved_evidence))
    if existing:
        if tuple(existing) != value:
            raise PublicationError("provenance_archive_conflict")
        return
    conn.execute(
        "INSERT INTO finance_provenance_blobs VALUES (?,?,?,?,?,?,?,?,?)",
        (*key, *value, envelope["event_id"]),
    )


class ArchivedBlobs(Mapping[str, bytes]):
    """A closed, lazy SQL mapping; at most one raw blob is materialized per read."""

    def __init__(self, conn: Any, key: tuple[str, ...]):
        self.conn, self.key = conn, key
        self.hashes = tuple(
            r[0]
            for r in conn.execute(
                "SELECT content_sha256 FROM finance_provenance_blobs WHERE tenant_id=? AND owner_id=? AND book_id=? AND root_sha256=? AND kind!='index' ORDER BY content_sha256 LIMIT 129",
                key,
            )
        )
        if len(self.hashes) > 128:
            raise PublicationError("provenance_archive_limit")

    def __len__(self) -> int:
        return len(self.hashes)

    def __iter__(self) -> Iterator[str]:
        return iter(self.hashes)

    def __getitem__(self, key: str) -> bytes:
        if key not in self.hashes:
            raise KeyError(key)
        row = self.conn.execute(
            "SELECT canonical_json FROM finance_provenance_blobs WHERE tenant_id=? AND owner_id=? AND book_id=? AND root_sha256=? AND content_sha256=?",
            (*self.key, key),
        ).fetchone()
        if row is None:
            raise PublicationError("provenance_recovery_hold")
        return str(row[0]).encode("utf-8")


def verify_archived(
    conn: Any, publication: dict[str, Any], previous: VerifiedProvenance | None = None
) -> VerifiedProvenance | None:
    root = namespace_root(publication)
    if root is None:
        return None
    key = (
        publication["tenant_id"],
        publication["owner_id"],
        publication["book_id"],
        root.removeprefix("hbprov1:"),
    )
    row = conn.execute(
        "SELECT canonical_json,approved_evidence_json FROM finance_provenance_blobs WHERE tenant_id=? AND owner_id=? AND book_id=? AND root_sha256=? AND kind='index' AND content_sha256=root_sha256",
        key,
    ).fetchone()
    if row is None:
        raise PublicationError("provenance_recovery_hold")
    package = ClosedPackage(
        str(row[0]).encode("utf-8"),
        ArchivedBlobs(conn, key),
        frozenset(json.loads(row[1])),
    )
    return verify_package(publication, package, previous)


async def archive_package(
    runtime: Any, session: Any, publication: dict[str, Any], package: ClosedPackage
) -> None:
    """Called only after full package verification and before publication append."""
    from adminme.finance.importer import require_import_owner

    root = namespace_root(publication)
    assert root is not None
    root_hash = root.removeprefix("hbprov1:")
    index = parse_blob(package.index, 262144)
    chunk_hashes = {r["sha256"] for r in index["chunks"]}
    for sha in [*sorted(package.blobs), root_hash]:
        require_import_owner(runtime, session)
        is_index = sha == root_hash
        raw = package.index if is_index else package.blobs[sha]
        payload = {
            "owner_id": session.auth_member_id,
            "book_id": publication["book_id"],
            "root_sha256": root_hash,
            "content_sha256": sha,
            "kind": "index"
            if is_index
            else "chunk"
            if sha in chunk_hashes
            else "evidence",
            "canonical_json": raw.decode("utf-8"),
            "approved_evidence": sorted(package.approved_evidence) if is_index else [],
        }
        now = EventEnvelope.now_utc_iso()
        await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=session.tenant_id,
                type="finance.provenance_archived",
                schema_version=1,
                occurred_at=now,
                source_adapter="accepted_books",
                source_account_id=publication["book_id"],
                owner_scope=f"private:{session.auth_member_id}",
                visibility_scope=f"private:{session.auth_member_id}",
                sensitivity="privileged",
                actor_identity=session.event_actor_identity,
                idempotency_key=f"finance-proof:{session.auth_member_id}:{root_hash}:{sha}",
                payload=payload,
            )
        )
