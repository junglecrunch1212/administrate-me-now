"""SYNTHETIC ONLY: temporary encrypted storage lifetime experiment.

Nothing in adminme imports this module. It is installed explicitly by an isolated
hosted fixture with a freshly generated test key. It is not a production switch,
OS sandbox, credential-free worker, or a changed resource acceptance contract.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import resource
import secrets
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adminme.finance.import_work import await_settled, run_cpu
from adminme.finance.publication import PublicationError
from adminme.finance.worker_protocol import (
    FINANCE_TYPES,
    UPLOAD_CHUNK,
    read_json,
    write_frame,
    json_bytes,
)

PROTOCOL = "synthetic-finance-storage-v1"
CONTROL_LIMIT = 128 * 1024
ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_FILES = (
    "tests/fixtures/finance_storage_isolation.py",
    "adminme/finance/worker_child.py",
    "adminme/finance/worker.py",
    "adminme/events/log.py",
    "adminme/projections/money/handlers.py",
    "poetry.lock",
)


def artifact_digest():
    digest = hashlib.sha256()
    for name in ARTIFACT_FILES:
        digest.update(name.encode())
        digest.update((ROOT / name).read_bytes())
    return digest.hexdigest()


def write_control(stream, value):
    raw = json_bytes(value)
    if len(raw) > CONTROL_LIMIT:
        raise PublicationError("synthetic_control_frame_limit")
    write_frame(stream, raw)


@dataclass(frozen=True)
class EventReference:
    operation: str
    tenant_id: str
    owner_id: str
    book_id: str
    type: str
    idempotency_key: str
    sha256: str
    prior_event_id: str | None = None

    def wire(self):
        return dict(self.__dict__)


class SyntheticStorage:
    """One temporary authority, one pipe owner and one executor at a time."""

    def __init__(self, root: Path, *, namespace_nonce: str | None = None):
        from tests.fixtures.finance_startup import store_name

        # Only an internal fresh namespace nonce is accepted, never a key or DB.
        self.root = root / store_name(
            secrets.token_hex(8) if namespace_nonce is None else namespace_nonce
        )
        self.root.mkdir(mode=0o700)
        self.key = secrets.token_bytes(32)
        self.runtime = None
        self.process = None
        self.lock = asyncio.Lock()
        self.imports = 0
        self.refs = []
        self.operation = None
        self.seq = 0
        self.birth = None
        self.timer = None
        self.lifetimes = []
        self.fault = None
        self.hook = None
        self.guards = []
        self.applied = []
        self.boundaries = []
        self.preparation_diagnostics = []
        self.quarantined = False

    def settings(self):
        from tests.acceptance.test_finance_activation import configured

        return replace(configured(self.root), encryption_key=self.key)

    async def checkpoint(self, stage):
        if self.hook is not None:
            await self.hook(stage)

    def _spawn(self):
        if self.quarantined:
            raise PublicationError("synthetic_reap_uncertain")
        if self.process is not None:
            return
        assert self.runtime is not None
        instance = self.runtime.settings.instance
        paths = [instance.event_log_path, instance.projection_db_path("money")]
        assert self.runtime.settings.encryption_key == self.key
        assert all(p.resolve().is_relative_to(self.root.resolve()) for p in paths)
        self.birth, self.seq = secrets.token_hex(16), 0
        self._cpu_at_spawn = resource.getrusage(resource.RUSAGE_CHILDREN)
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "executor"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            close_fds=True,
        )
        process = self.process
        self.timer = threading.Timer(
            180, lambda: process.kill() if process.poll() is None else None
        )
        self.timer.daemon = True
        self.timer.start()
        write_control(
            process.stdin,
            {
                "protocol": PROTOCOL,
                "artifact": artifact_digest(),
                "birth": self.birth,
                "root": str(self.root.resolve()),
                "log": str(paths[0].resolve()),
                "money": str(paths[1].resolve()),
                "test_key": self.key.hex(),
            },
        )
        ready = read_json(process.stdout, CONTROL_LIMIT)
        if ready != {
            "protocol": PROTOCOL,
            "artifact": artifact_digest(),
            "birth": self.birth,
        }:
            raise PublicationError("synthetic_handshake_invalid")

    def _send(self, command):
        self._spawn()
        self.seq += 1
        write_control(
            self.process.stdin, {"birth": self.birth, "seq": self.seq, **command}
        )

    def _receive(self):
        value = read_json(self.process.stdout, CONTROL_LIMIT)
        if (
            not isinstance(value, dict)
            or value.get("birth") != self.birth
            or value.get("seq") != self.seq
        ):
            raise PublicationError("synthetic_response_invalid")
        return value

    def _reap(self, graceful=True):
        process = self.process
        if process is None:
            return
        before = self._cpu_at_spawn
        reap_timer = threading.Timer(
            5, lambda: process.kill() if process.poll() is None else None
        )
        reap_timer.daemon = True
        reap_timer.start()
        try:
            if process.poll() is None and graceful:
                try:
                    self._send({"op": "close"})
                    self._receive()
                except (ValueError, OSError, PublicationError):
                    pass
            try:
                process.wait(timeout=5 if graceful else 0.1)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        except BaseException:
            self.quarantined = True
            raise
        else:
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            self.lifetimes.append(
                {
                    "pid": process.pid,
                    "exit_code": process.returncode,
                    "reaped": True,
                    "cpu_seconds": max(
                        0.0,
                        after.ru_utime
                        + after.ru_stime
                        - before.ru_utime
                        - before.ru_stime,
                    ),
                }
            )
            for stream in (process.stdin, process.stdout):
                stream.close()
            self.timer.cancel()
            self.process = self.timer = None
            self.operation = None
        finally:
            reap_timer.cancel()

    async def close(self):
        async with self.lock:
            await run_cpu(self._reap)
        assert self.process is None and not self.quarantined

    async def _rpc(self, command, guard=None):
        await run_cpu(self._send, command)
        while True:
            message = await run_cpu(self._receive)
            kind = message.get("kind")
            if kind == "guard":
                if guard is None:
                    raise PublicationError("synthetic_unexpected_guard")
                stage = message["stage"]
                await self.checkpoint(stage)
                try:
                    await self.runtime.event_log._run_on_connection_thread(guard)
                except BaseException:
                    await run_cpu(write_control, self.process.stdin, {"allowed": False})
                    # The child must acknowledge rollback before admission ends.
                    await run_cpu(self._receive)
                    raise
                self.guards.append(stage)
                await run_cpu(write_control, self.process.stdin, {"allowed": True})
            elif kind == "error":
                raise PublicationError(message["reason"])
            elif kind == "result":
                return message["result"]
            else:
                raise PublicationError("synthetic_response_invalid")

    async def prepare(self, request, runtime, session, manifest):
        from adminme.finance.worker import PreparedFinanceImport

        assert runtime is self.runtime
        async with self.lock:
            self.operation = secrets.token_hex(16)
            operation = self.operation
            await run_cpu(
                self._send,
                {
                    "op": "prepare",
                    "operation": operation,
                    "manifest": manifest.model_dump(),
                    "owner_id": session.auth_member_id,
                    "tenant_id": session.tenant_id,
                    "approval_id": request.path_params["approval_id"],
                },
            )
            total = 0
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > manifest.encoded_body_limit():
                        raise PublicationError("finance_import_body_limit")
                    for offset in range(0, len(chunk), UPLOAD_CHUNK):
                        await run_cpu(
                            write_frame,
                            self.process.stdin,
                            chunk[offset : offset + UPLOAD_CHUNK],
                        )
                await run_cpu(write_frame, self.process.stdin, b"")
            message = await run_cpu(self._receive)
            if message.get("kind") != "result":
                raise PublicationError(
                    message.get("reason", "synthetic_prepare_invalid")
                )
            result = message["result"]
            self.preparation_diagnostics.append(result["diagnostics"])
            if len(self.preparation_diagnostics) > 8:
                raise PublicationError("synthetic_diagnostic_limit")
            refs = [EventReference(**value) for value in result["references"]]
            if (
                not refs
                or len(refs) > 131
                or any(
                    (r.operation, r.tenant_id, r.owner_id, r.book_id)
                    != (
                        operation,
                        session.tenant_id,
                        session.auth_member_id,
                        manifest.binding.book_id,
                    )
                    or r.type not in FINANCE_TYPES
                    for r in refs
                )
            ):
                raise PublicationError("synthetic_reference_invalid")
            self.refs = list(refs)
        await self.checkpoint("prepared")
        return PreparedFinanceImport(result["metadata"], refs)

    async def append(
        self, tenant, owner, book, events, *, commit_guard, correlation_id=None
    ):
        from adminme.events.log import _EventIdGenerator

        if (
            not events
            or list(events) != self.refs
            or any(
                not isinstance(e, EventReference)
                or (e.tenant_id, e.owner_id, e.book_id) != (tenant, owner, book)
                for e in events
            )
        ):
            raise PublicationError("synthetic_reference_scope_denied")
        log = self.runtime.event_log
        async with log._lock, self.lock:
            fault = (
                self.fault
                if self.fault in {"event_before_commit", "event_after_commit"}
                else None
            )
            if fault:
                self.fault = None
            references = [e.wire() for e in events]
            try:
                result = await self._rpc(
                    {
                        "op": "commit",
                        "operation": events[0].operation,
                        "correlation_id": correlation_id,
                        "fault": fault,
                    },
                    guard=commit_guard,
                )
            except (OSError, ValueError) as exc:
                # No blind retry after an uncertain commit. Reap the failed
                # owner, inspect exact durable identities/hashes in a fresh
                # executor, and return only a complete atomic batch receipt.
                await run_cpu(self._reap, False)
                result = await self._rpc({"op": "recover", "references": references})
                self.boundaries.append(
                    {
                        "stage": "commit_ack_recovery",
                        "complete": bool(result),
                        "ids": result,
                    }
                )
                if not result:
                    raise PublicationError("synthetic_commit_not_durable") from exc
            finally:

                def sync_ids():
                    log._id_gen = _EventIdGenerator(log._latest_native_event_id())

                await log._run_on_connection_thread(sync_ids)
            await self.checkpoint("committed")
            return result

    async def apply(self, envelope):
        await self.checkpoint("applying")
        from adminme.projections.money import MoneyProjection

        if envelope["type"] not in MoneyProjection.subscribes_to:
            raise PublicationError("synthetic_event_type_denied")
        coordinates = {
            k: envelope[k]
            for k in (
                "event_id",
                "tenant_id",
                "owner_scope",
                "visibility_scope",
                "type",
                "source_account_id",
            )
        }
        async with self.lock:
            fault = (
                self.fault
                if self.fault
                in {"money_before_commit", "money_after_commit", "money_after_ack"}
                else None
            )
            if fault:
                self.fault = None
            try:
                result = await self._rpc(
                    {"op": "apply", "coordinates": coordinates, "fault": fault}
                )
            except (OSError, ValueError) as exc:
                await run_cpu(self._reap, False)
                raise PublicationError("synthetic_apply_ack_lost") from exc
            if fault == "money_after_ack":
                previous = self.runtime.bus._subscribers["projection:money"].checkpoint
                assert previous != result
                self.boundaries.append(
                    {
                        "stage": "money_ack_before_checkpoint",
                        "event_id": result,
                        "checkpoint": previous,
                    }
                )
                raise PublicationError("synthetic_checkpoint_ack_lost")
            assert result == envelope["event_id"]
            self.applied.append(result)

    def install(self, app, monkeypatch):
        from adminme.products.member import finance_import

        self.runtime = app.state.runtime
        start = self.runtime.start
        stop = self.runtime.stop

        async def stopped():
            try:
                await stop()
            finally:
                await await_settled(self.close())

        monkeypatch.setattr(self.runtime, "stop", stopped)

        async def started():
            await start()
            runtime = self.runtime
            assert runtime.event_log._db_path.resolve().is_relative_to(
                self.root.resolve()
            )
            async with asyncio.timeout(10):
                while True:
                    status = await runtime.bus.subscriber_status("projection:money")
                    state = runtime.bus._subscribers["projection:money"]
                    if (
                        not status["lag_count"]
                        and not state.draining
                        and not state.trigger.is_set()
                    ):
                        break
                    await asyncio.sleep(0.01)
            monkeypatch.setattr(runtime.event_log, "_append_finance_batch", self.append)
            state.raw_payload_types = frozenset()
            state.metadata_only_types = frozenset(state.types)

            async def apply(envelope):
                async with runtime.projections._write_locks["money"]:
                    await await_settled(self.apply(envelope))

            async def settle():
                if not self.imports:
                    await self.close()

            monkeypatch.setattr(state, "callback", apply)
            monkeypatch.setattr(state, "on_drain_end", settle)

        monkeypatch.setattr(self.runtime, "start", started)
        monkeypatch.setattr(finance_import, "prepare_import", self.prepare)

        @app.middleware("http")
        async def owned_import(request, call_next):
            if not request.url.path.startswith("/api/member/v1/finance/imports/"):
                return await call_next(request)
            self.imports += 1
            try:
                return await call_next(request)
            finally:
                self.imports -= 1
                if not self.imports:
                    await await_settled(self.close())


async def executor():
    """Trusted only for the controller-created disposable encrypted databases."""
    import hmac
    import sqlcipher3
    from datetime import datetime, timezone
    from adminme.events.log import EventLog, _row_to_event, _EventIdGenerator
    from adminme.finance import worker_child
    from adminme.finance.worker import ValidatedFinanceEvent, _sql
    from adminme.finance.access_control import FinanceAccessCache
    from adminme.events.schemas.finance_import import FinanceImportApprovedV1
    from adminme.projections.money import handlers, MoneyProjection

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    incoming, outgoing = sys.stdin.buffer, sys.stdout.buffer
    hello = read_json(incoming, CONTROL_LIMIT)
    if set(hello) != {
        "protocol",
        "artifact",
        "birth",
        "root",
        "log",
        "money",
        "test_key",
    }:
        raise ValueError("synthetic_handshake_invalid")
    if hello["protocol"] != PROTOCOL or not hmac.compare_digest(
        hello["artifact"], artifact_digest()
    ):
        raise ValueError("synthetic_artifact_mismatch")
    root = Path(hello["root"]).resolve(strict=True)
    paths = [Path(hello[k]).resolve(strict=True) for k in ("log", "money")]
    if not root.name.startswith("synthetic-store-") or any(
        not p.is_relative_to(root) for p in paths
    ):
        raise ValueError("synthetic_path_denied")
    key = bytes.fromhex(hello.pop("test_key"))
    if len(key) != 32:
        raise ValueError("synthetic_key_invalid")

    class ExistingLog(EventLog):
        def _migrate(self):
            # Startup already created this disposable database. The executor
            # performs no DDL alongside the parent writer.
            assert self._conn.execute(
                "SELECT MAX(version) FROM _schema_version"
            ).fetchone()[0]

    log = ExistingLog(paths[0], key)
    money = sqlcipher3.connect(str(paths[1]), isolation_level=None)
    money.row_factory = sqlcipher3.Row
    money.execute(f"PRAGMA key = \"x'{key.hex()}'\"").close()
    money.execute("PRAGMA cache_size=-512").close()
    money.execute("PRAGMA foreign_keys=ON").close()
    del key
    birth, seq = hello["birth"], 0
    write_control(
        outgoing, {"protocol": PROTOCOL, "artifact": artifact_digest(), "birth": birth}
    )
    stored = []
    scope = None
    prepared_header = None

    def reply(kind, **values):
        write_control(outgoing, {"birth": birth, "seq": seq, "kind": kind, **values})

    def durable_guard():
        tenant, owner, book = scope
        row = log._read_finance_import_approval(
            tenant, owner, prepared_header["approval_id"]
        )
        approval = FinanceImportApprovedV1.model_validate(row["payload"])
        control = log._read_finance_access_control(tenant, owner)
        state = FinanceAccessCache(tenant, frozenset({owner}))
        # The same typed restore checks as the parent verify persisted origin.
        policy = state.restore(tenant, owner, control)
        if (
            row["actor_identity"] != owner
            or row["source_adapter"] != "member-settings-control-plane"
            or row["visibility_scope"] != f"private:{owner}"
            or approval.owner_id != owner
            or approval.manifest.model_dump() != prepared_header["manifest"]
            or not policy.books_enabled
            or policy.revision != approval.policy_revision
            or policy.event_id != approval.policy_event_id
            or policy.binding != approval.manifest.binding
            or policy.binding.book_id != book
            or datetime.fromisoformat(approval.expires_at) <= datetime.now(timezone.utc)
        ):
            raise ValueError("synthetic_durable_authority_changed")

    try:
        while True:
            command = read_json(incoming, CONTROL_LIMIT)
            if (
                not isinstance(command, dict)
                or command.pop("birth", None) != birth
                or command.pop("seq", None) != seq + 1
            ):
                raise ValueError("synthetic_command_sequence")
            seq += 1
            op = command.get("op")
            try:
                if op == "close":
                    reply("result", result=True)
                    return
                if op == "prepare":
                    if stored:
                        raise ValueError("synthetic_operation_busy")
                    prepared_header = command
                    scope = (
                        command["tenant_id"],
                        command["owner_id"],
                        command["manifest"]["binding"]["book_id"],
                    )
                    metadata, info, total = None, None, 0
                    worker_child._input = incoming

                    class LocalMoney:
                        def execute(self, sql, parameters):
                            result = _sql(
                                money,
                                {
                                    "kind": "sql",
                                    "sql": sql,
                                    "parameters": list(parameters),
                                },
                                read_only=True,
                                scope=scope,
                            )
                            return worker_child.Cursor(result["rows"])

                    def collect_json(_stream, value):
                        nonlocal metadata, info
                        if value["kind"] == "prepared" and metadata is None:
                            metadata = value
                        elif value["kind"] == "event" and info is None:
                            info = value
                        else:
                            raise ValueError("synthetic_preparation_protocol")

                    def collect_frame(_stream, raw):
                        nonlocal info, total
                        total += len(raw)
                        if (
                            info is None
                            or len(stored) >= 131
                            or total
                            > 4 * sum(command["manifest"]["blobs"].values())
                            + 64 * 1024 * 1024
                            or info["bytes"] != len(raw)
                            or hashlib.sha256(raw).hexdigest() != info["sha256"]
                        ):
                            raise ValueError("synthetic_preparation_limit")
                        event = ValidatedFinanceEvent(
                            *scope,
                            info["type"],
                            info["source_adapter"],
                            info["idempotency_key"],
                            raw.decode(),
                            info["sha256"],
                            b"",
                        )
                        event = replace(event, _receipt=event._signature())
                        event.verify()
                        stored.append(event)
                        diagnostics.emitted(len(raw))
                        info = None

                    from tests.fixtures.finance_import_diagnostics import (
                        PreparationDiagnostics,
                    )

                    diagnostics = PreparationDiagnostics()
                    worker_child.MoneyConnection = LocalMoney
                    worker_child.write_json = collect_json
                    worker_child.write_frame = collect_frame
                    with diagnostics.instrument(), diagnostics.stage("prepare"):
                        worker_child.prepare(command)
                    if metadata is None or not stored or info is not None:
                        raise ValueError("synthetic_preparation_incomplete")

                    def prior_ids():
                        return {
                            e.idempotency_key: log._conn.execute(
                                "SELECT event_id FROM events WHERE tenant_id=? AND idempotency_key=?",
                                (e.tenant_id, e.idempotency_key),
                            ).fetchone()
                            for e in stored
                        }

                    existing = await log._run_on_connection_thread(prior_ids)
                    refs = [
                        EventReference(
                            command["operation"],
                            e.tenant_id,
                            e.owner_id,
                            e.book_id,
                            e.type,
                            e.idempotency_key,
                            e.sha256,
                            existing[e.idempotency_key][0]
                            if existing[e.idempotency_key]
                            else None,
                        ).wire()
                        for e in stored
                    ]
                    reply(
                        "result",
                        result={
                            "metadata": metadata,
                            "references": refs,
                            "diagnostics": diagnostics.snapshot(),
                        },
                    )
                elif op == "commit":
                    if (
                        not stored
                        or command["operation"] != prepared_header["operation"]
                    ):
                        raise ValueError("synthetic_operation_mismatch")
                    calls = 0

                    def guard():
                        nonlocal calls
                        calls += 1
                        durable_guard()
                        reply(
                            "guard",
                            stage="before_transaction"
                            if calls == 1
                            else "before_commit",
                        )
                        if read_json(incoming) != {"allowed": True}:
                            raise ValueError("synthetic_parent_authority_changed")
                        durable_guard()
                        if command.get("fault") == "event_before_commit" and calls == 2:
                            os._exit(71)

                    await log._run_on_connection_thread(
                        lambda: setattr(
                            log,
                            "_id_gen",
                            _EventIdGenerator(log._latest_native_event_id()),
                        )
                    )
                    ids = await log._append_finance_batch(
                        *scope,
                        stored,
                        commit_guard=guard,
                        correlation_id=command.get("correlation_id"),
                    )
                    if command.get("fault") == "event_after_commit":
                        os._exit(72)
                    stored.clear()
                    reply("result", result=ids)
                elif op == "recover":
                    refs = [EventReference(**value) for value in command["references"]]
                    if not 1 <= len(refs) <= 131:
                        raise ValueError("synthetic_recovery_limit")

                    def recover():
                        ids = []
                        for ref in refs:
                            row = log._conn.execute(
                                "SELECT * FROM events WHERE tenant_id=? AND idempotency_key=?",
                                (ref.tenant_id, ref.idempotency_key),
                            ).fetchone()
                            if row is None:
                                continue
                            if (
                                row["owner_scope"] != f"private:{ref.owner_id}"
                                or row["visibility_scope"] != f"private:{ref.owner_id}"
                                or row["source_account_id"] != ref.book_id
                                or row["type"] != ref.type
                                or hashlib.sha256(row["payload"].encode()).hexdigest()
                                != ref.sha256
                            ):
                                raise ValueError("synthetic_recovery_identity")
                            ids.append(row["event_id"])
                        if len(ids) != len(refs):
                            if set(ids) != {
                                r.prior_event_id
                                for r in refs
                                if r.prior_event_id is not None
                            }:
                                raise ValueError("synthetic_partial_atomic_batch")
                            return []
                        return ids

                    reply("result", result=await log._run_on_connection_thread(recover))
                elif op == "apply":
                    coordinates = command["coordinates"]
                    if coordinates["type"] not in MoneyProjection.subscribes_to:
                        raise ValueError("synthetic_apply_type_denied")

                    def read():
                        row = log._conn.execute(
                            "SELECT * FROM events WHERE event_id=?",
                            (coordinates["event_id"],),
                        ).fetchone()
                        if row is None or any(
                            row[k] != v for k, v in coordinates.items()
                        ):
                            raise ValueError("synthetic_apply_scope_denied")
                        return _row_to_event(row)

                    envelope = await log._run_on_connection_thread(read)
                    money.execute("BEGIN IMMEDIATE").close()
                    try:
                        handlers.apply_event(envelope, money)
                        if command.get("fault") == "money_before_commit":
                            os._exit(73)
                        money.execute("COMMIT").close()
                    except BaseException:
                        money.execute("ROLLBACK").close()
                        raise
                    if command.get("fault") == "money_after_commit":
                        os._exit(74)
                    del envelope
                    reply("result", result=coordinates["event_id"])
                else:
                    raise ValueError("synthetic_operation_denied")
            except Exception as exc:
                # Only a class name is returned; no keys, SQL or payload text.
                reply("error", reason="synthetic_" + type(exc).__name__)
                return
    finally:
        money.close()
        await log.close()


if __name__ == "__main__":
    if sys.argv[1:] != ["executor"]:
        raise SystemExit("synthetic executor entrypoint only")
    asyncio.run(executor())
