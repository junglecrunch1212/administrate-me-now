"""Durable, metadata-only OpenClaw allow-once approval receipts.

The plugin resolves an approval through OpenClaw's ``before_tool_call`` gate,
then records the signed receipt over the authenticated loopback seam.  The
domain endpoint consumes it atomically for the exact tool/body fingerprint.
No reusable credential or household content is stored here.
"""

from __future__ import annotations

import hmac
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


class ApprovalReceiptError(RuntimeError):
    """A receipt was forged, stale, mismatched, or already consumed."""


@dataclass(frozen=True)
class ApprovalReceipt:
    receipt_id: str
    tool_name: str
    request_fingerprint: str
    issued_at_ms: int
    expires_at_ms: int
    openclaw_version: str
    openclaw_commit: str
    signature: str
    consumed_at_ms: int | None
    consumed_by_member_id: str | None
    authorization: dict[str, str] | None = None


def approval_signature_material(
    *,
    receipt_id: str,
    tool_name: str,
    request_fingerprint: str,
    issued_at_ms: int,
    expires_at_ms: int,
    openclaw_version: str,
    openclaw_commit: str,
    authorization: dict[str, str] | None = None,
) -> str:
    if authorization is not None:
        return "|".join(
            (
                "administrateme-request-approval-v1",
                receipt_id,
                tool_name,
                request_fingerprint,
                str(issued_at_ms),
                str(expires_at_ms),
                "authenticated_request",
                openclaw_version,
                openclaw_commit,
                json.dumps(authorization, sort_keys=True, separators=(",", ":")),
            )
        )
    return "|".join(
        (
            "administrateme-approval-v1",
            receipt_id,
            tool_name,
            request_fingerprint,
            str(issued_at_ms),
            str(expires_at_ms),
            "allow-once",
            openclaw_version,
            openclaw_commit,
        )
    )


def sign_approval_receipt(
    secret: str,
    *,
    receipt_id: str,
    tool_name: str,
    request_fingerprint: str,
    issued_at_ms: int,
    expires_at_ms: int,
    openclaw_version: str,
    openclaw_commit: str,
    authorization: dict[str, str] | None = None,
) -> str:
    material = approval_signature_material(
        receipt_id=receipt_id,
        tool_name=tool_name,
        request_fingerprint=request_fingerprint,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        openclaw_version=openclaw_version,
        openclaw_commit=openclaw_commit,
        authorization=authorization,
    )
    return hmac.new(secret.encode(), material.encode(), sha256).hexdigest()


class OpenClawApprovalStore:
    """SQLite-backed receipt resolution and atomic single-use consumption."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS openclaw_approval_receipts (
                receipt_id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                issued_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                openclaw_version TEXT NOT NULL,
                openclaw_commit TEXT NOT NULL,
                signature TEXT NOT NULL,
                consumed_at_ms INTEGER,
                consumed_by_member_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_openclaw_approval_expiry
                ON openclaw_approval_receipts(expires_at_ms);
            """
        )
        # Separate table keeps existing allow-once rows and predecessor reads intact.
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS openclaw_request_approval_receipts (
                receipt_id TEXT PRIMARY KEY, tool_name TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL, issued_at_ms INTEGER NOT NULL,
                expires_at_ms INTEGER NOT NULL, openclaw_version TEXT NOT NULL,
                openclaw_commit TEXT NOT NULL, signature TEXT NOT NULL,
                consumed_at_ms INTEGER, consumed_by_member_id TEXT,
                authorization_json TEXT NOT NULL
            );
        """)

    @staticmethod
    def _table(receipt_id: str) -> str:
        return (
            "openclaw_request_approval_receipts"
            if receipt_id.startswith("ocreq_")
            else "openclaw_approval_receipts"
        )

    @staticmethod
    def _receipt(row: sqlite3.Row) -> ApprovalReceipt:
        values = dict(row)
        if "authorization_json" in values:
            values["authorization"] = json.loads(values.pop("authorization_json"))
        return ApprovalReceipt(**values)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def resolve(
        self,
        *,
        secret: str,
        receipt_id: str,
        tool_name: str,
        request_fingerprint: str,
        issued_at_ms: int,
        expires_at_ms: int,
        openclaw_version: str,
        openclaw_commit: str,
        signature: str,
        now_ms: int | None = None,
        authorization: dict[str, str] | None = None,
    ) -> ApprovalReceipt:
        if (authorization is not None) != receipt_id.startswith("ocreq_"):
            raise ApprovalReceiptError("approval receipt kind is mismatched")
        table = self._table(receipt_id)
        if authorization is not None and (
            set(authorization)
            != {
                "source_event_id",
                "request_authorization_event_id",
                "domain_approval_event_id",
                "action_fingerprint",
                "rule_id",
            }
            or any(not isinstance(v, str) or not v for v in authorization.values())
            or (tool_name, authorization["rule_id"])
            not in {
                (
                    "administrateme_execute_pending_provider_action",
                    "current-human-request.apple-reminder-create.v1",
                ),
                (
                    "administrateme_execute_pending_provider_action",
                    "current-human-request.cos-reminder-create.v1",
                ),
                (
                    "administrateme_cos_response",
                    "current-human-request.cos-email-to-requester.v1",
                ),
            }
        ):
            raise ApprovalReceiptError("request authorization binding is invalid")
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if issued_at_ms > now + 30_000 or expires_at_ms <= now:
            raise ApprovalReceiptError("approval receipt is outside its valid window")
        if expires_at_ms - issued_at_ms > 120_000 or expires_at_ms <= issued_at_ms:
            raise ApprovalReceiptError("approval receipt lifetime is invalid")
        expected = sign_approval_receipt(
            secret,
            receipt_id=receipt_id,
            tool_name=tool_name,
            request_fingerprint=request_fingerprint,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            openclaw_version=openclaw_version,
            openclaw_commit=openclaw_commit,
            authorization=authorization,
        )
        if not hmac.compare_digest(signature, expected):
            raise ApprovalReceiptError("approval receipt signature is invalid")
        values: tuple[str | int, ...] = (
            receipt_id,
            tool_name,
            request_fingerprint,
            issued_at_ms,
            expires_at_ms,
            openclaw_version,
            openclaw_commit,
            signature,
        )
        if authorization is not None:
            values += (
                json.dumps(authorization, sort_keys=True, separators=(",", ":")),
            )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    f"SELECT * FROM {table} WHERE receipt_id=?",
                    (receipt_id,),
                ).fetchone()
                if existing is None:
                    extra_column = (
                        ", authorization_json" if authorization is not None else ""
                    )
                    extra_value = ", ?" if authorization is not None else ""
                    self._conn.execute(
                        f"""
                        INSERT INTO {table} (
                            receipt_id, tool_name, request_fingerprint,
                            issued_at_ms, expires_at_ms, openclaw_version,
                            openclaw_commit, signature{extra_column}
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?{extra_value})
                        """,
                        values,
                    )
                else:
                    stored = tuple(
                        existing[key]
                        for key in (
                            "receipt_id",
                            "tool_name",
                            "request_fingerprint",
                            "issued_at_ms",
                            "expires_at_ms",
                            "openclaw_version",
                            "openclaw_commit",
                            "signature",
                        )
                    )
                    if authorization is not None:
                        stored += (existing["authorization_json"],)
                    if stored != values:
                        raise ApprovalReceiptError(
                            "approval receipt id belongs to different content"
                        )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return self.get(receipt_id)

    def get(self, receipt_id: str) -> ApprovalReceipt:
        table = self._table(receipt_id)
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM {table} WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
        if row is None:
            raise ApprovalReceiptError("approval receipt is unknown")
        return self._receipt(row)

    def consume(
        self,
        *,
        receipt_id: str,
        tool_name: str,
        request_fingerprint: str,
        member_id: str,
        now_ms: int | None = None,
        allow_same_binding_recovery: bool = False,
    ) -> ApprovalReceipt:
        table = self._table(receipt_id)
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    f"SELECT * FROM {table} WHERE receipt_id=?",
                    (receipt_id,),
                ).fetchone()
                if row is None:
                    raise ApprovalReceiptError("approval receipt is unknown")
                if row["tool_name"] != tool_name:
                    raise ApprovalReceiptError("approval receipt names another tool")
                if row["request_fingerprint"] != request_fingerprint:
                    raise ApprovalReceiptError("approval receipt content changed")
                if int(row["expires_at_ms"]) <= now:
                    raise ApprovalReceiptError("approval receipt expired")
                if row["consumed_at_ms"] is not None:
                    if (
                        allow_same_binding_recovery
                        and row["consumed_by_member_id"] == member_id
                    ):
                        self._conn.execute("COMMIT")
                        return self._receipt(row)
                    raise ApprovalReceiptError("approval receipt was already consumed")
                self._conn.execute(
                    f"""
                    UPDATE {table}
                       SET consumed_at_ms=?, consumed_by_member_id=?
                     WHERE receipt_id=? AND consumed_at_ms IS NULL
                    """,
                    (now, member_id, receipt_id),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return self.get(receipt_id)


__all__ = [
    "ApprovalReceipt",
    "ApprovalReceiptError",
    "OpenClawApprovalStore",
    "approval_signature_material",
    "sign_approval_receipt",
]
