"""Bridge daemon lifecycle for opt-in Cat-B and Cat-D connector packs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.bridge_authorization import BRIDGE_PACK_POLICIES, BridgePackPolicy

from adminme_bridge.adapters.base import AdapterStatus, BridgeAdapter, BridgeContext
from adminme_bridge.config import BridgeConfig, load_config
from adminme_bridge.ingest_client import IngestClient
from adminme_bridge.packs import (
    PackHandle,
    PackLoadError,
    discover_packs,
    instantiate_pack,
)


DEFAULT_CONFIG_PATH = Path("~/.adminme-bridge/config.yaml").expanduser()

logger = logging.getLogger(__name__)


class _CommandLedger:
    """Durable result envelope cache preventing duplicate create side effects."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS bridge_command_results ("
            "command_id TEXT PRIMARY KEY, envelope_json TEXT NOT NULL)"
        )

    def get(self, command_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT envelope_json FROM bridge_command_results WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, command_id: str, envelope: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO bridge_command_results VALUES (?, ?)",
            (command_id, json.dumps(envelope, separators=(",", ":"), sort_keys=True)),
        )

    def close(self) -> None:
        self._conn.close()


def configured_packs(config: BridgeConfig) -> list[PackHandle]:
    """Combine installed packs with explicitly enabled built-in packs."""

    installed = {
        handle.name: handle for handle in discover_packs(config.resolved_packs_dir())
    }
    built_in_root = Path(__file__).parent / "connector_packs"
    built_ins = {handle.name: handle for handle in discover_packs(built_in_root)}
    enabled = set(config.enabled_packs)
    if len(enabled) != len(config.enabled_packs):
        raise PackLoadError("enabled bridge packs must be unique")
    if not enabled <= set(config.configured_packs):
        raise PackLoadError("enabled bridge packs not found in configured set")
    unknown = set(config.configured_packs) - (set(installed) | set(built_ins))
    if unknown:
        raise PackLoadError(f"configured bridge packs not found: {sorted(unknown)}")
    selected: dict[str, PackHandle] = {}
    for name in enabled:
        if name in installed:
            selected[name] = installed[name]
        elif name in built_ins:
            selected[name] = built_ins[name]
    return [selected[name] for name in sorted(selected)]


class BridgeSupervisor:
    """Async supervisor for Cat-D knowledge-source adapters."""

    def __init__(
        self,
        *,
        config: BridgeConfig,
        ingest_client: IngestClient,
        packs: list[PackHandle] | None = None,
        adapters: list[BridgeAdapter] | None = None,
    ) -> None:
        self._config = config
        self._ingest_client = ingest_client
        self._packs = list(packs or [])
        if adapters is None:
            self._adapters = [
                instantiate_pack(
                    handle,
                    config_override={
                        **self._config.pack_config.get(handle.name, {}),
                        **(
                            {
                                "cursor_db_path": str(
                                    self._config.resolved_cursor_db_path()
                                )
                            }
                            if handle.subkind == "knowledge-source"
                            else {}
                        ),
                    },
                )
                for handle in self._packs
            ]
        else:
            self._adapters = list(adapters)
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False
        self._command_ledger = _CommandLedger(config.resolved_cursor_db_path())

    @property
    def adapters(self) -> list[BridgeAdapter]:
        return list(self._adapters)

    def make_context(self) -> BridgeContext:
        configuration_sha256 = hashlib.sha256(
            json.dumps(
                self._config.pack_config,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return BridgeContext(
            member_id=self._config.member_id,
            ingest_client=self._ingest_client,
            correlation_id_factory=lambda: str(uuid.uuid4()),
            now_factory=lambda: datetime.now(timezone.utc),
            tenant_id=self._config.tenant_id,
            enrollment_id=self._config.enrollment_id,
            configuration_sha256=configuration_sha256,
        )

    async def start(self) -> None:
        for adapter in self._adapters:
            self._tasks.append(
                asyncio.create_task(
                    adapter.start(ctx=self.make_context()),
                    name=f"bridge-adapter:{type(adapter).__name__}",
                )
            )
        if any(
            getattr(adapter, "provider", None)
            and callable(getattr(adapter, "apply_write", None))
            for adapter in self._adapters
        ) and callable(getattr(self._ingest_client, "fetch_commands", None)):
            self._tasks.append(
                asyncio.create_task(
                    self._command_loop(), name="bridge-command-dispatcher"
                )
            )

    async def stop(self) -> None:
        self._stopping = True
        for adapter in self._adapters:
            try:
                await adapter.stop()
            except Exception:  # noqa: BLE001 — best-effort
                logger.exception("bridge adapter stop() raised")
        # Give in-flight startup/emission work a bounded chance to commit
        # before cancellation. Bridge sinks can be waiting on SQLCipher or
        # an HTTP response when shutdown begins.
        _done, pending = (
            await asyncio.wait(self._tasks, timeout=5.0)
            if self._tasks
            else (set(), set())
        )
        for task in pending:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        self._command_ledger.close()

    async def _command_loop(self) -> None:
        adapters: dict[str, Any] = {
            str(getattr(adapter, "provider")): adapter
            for adapter in self._adapters
            if getattr(adapter, "provider", None)
            and callable(getattr(adapter, "apply_write", None))
        }
        while not self._stopping:
            try:
                response = await self._ingest_client.fetch_commands(list(adapters))
                for command in response["commands"]:
                    command_id = str(command["command_id"])
                    result_envelope = self._command_ledger.get(command_id)
                    if result_envelope is None:
                        try:
                            adapter = self._authorized_command_adapter(
                                command, adapters
                            )
                        except ValueError as exc:
                            result_envelope = self._result_envelope(
                                command, error=exc, provider_called=False
                            )
                        else:
                            provider_called = True
                            try:
                                result = await getattr(adapter, "apply_write")(
                                    str(command["operation"]),
                                    dict(command.get("payload") or {}),
                                    command.get("target_external_id"),
                                )
                            except RuntimeError as exc:
                                if "has not finished starting" in str(exc):
                                    continue
                                result_envelope = self._result_envelope(
                                    command,
                                    error=exc,
                                    provider_called=provider_called,
                                )
                            except Exception as exc:  # noqa: BLE001 - provider failure
                                result_envelope = self._result_envelope(
                                    command,
                                    error=exc,
                                    provider_called=provider_called,
                                )
                            else:
                                result_envelope = self._result_envelope(
                                    command,
                                    result=result,
                                    provider_called=provider_called,
                                )
                        self._command_ledger.put(command_id, result_envelope)
                    submit = await self._ingest_client.submit(result_envelope)
                    if not submit.ok:
                        logger.warning(
                            "bridge command result upload failed for %s: %s",
                            command_id,
                            submit.error,
                        )
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001 - polling supervisor retries
                logger.exception("bridge command poll failed")
            try:
                await asyncio.sleep(self._config.command_poll_interval_s)
            except asyncio.CancelledError:
                return

    @staticmethod
    def _command_container(
        policy: BridgePackPolicy, command: dict[str, Any]
    ) -> str | None:
        payload = command.get("payload")
        if not isinstance(payload, dict):
            return None
        aliases = {
            "calendar_identifier": (
                "calendar_identifier",
                "calendar_external_id",
                "calendar_id",
            ),
            "target_list_identifier": (
                "target_list_identifier",
                "target_list_id",
                "list_external_id",
                "list_identifier",
            ),
            "container_external_id": ("container_external_id",),
        }.get(str(policy.container_key), ())
        for key in aliases:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _authorized_command_adapter(
        self, command: dict[str, Any], adapters: dict[str, Any]
    ) -> Any:
        """Validate the central command again at the provider-process edge."""

        violations: list[str] = []
        if int(command.get("schema_version") or 0) != 2:
            violations.append("schema_version")
        if command.get("member_id") != self._config.member_id:
            violations.append("member_id")
        provider = command.get("provider")
        matching = [
            BRIDGE_PACK_POLICIES[pack]
            for pack in self._config.enabled_packs
            if BRIDGE_PACK_POLICIES[pack].provider == provider
        ]
        policy = matching[0] if len(matching) == 1 else None
        if policy is None:
            violations.append("provider")
        config = self._config.pack_config.get(policy.pack, {}) if policy else {}
        if command.get("provider_account_id") != config.get("provider_account_id"):
            violations.append("provider_account_id")
        if policy is None or command.get("operation") not in policy.write_operations:
            violations.append("operation")
        if policy is not None and policy.container_key is not None:
            container = self._command_container(policy, command)
            allowed_raw = config.get("allowed_container_ids")
            allowed_containers = (
                {str(value) for value in allowed_raw}
                if isinstance(allowed_raw, list)
                else set()
            )
            if container not in allowed_containers:
                violations.append("container")
        for key in (
            "command_id",
            "event_id",
            "domain_approval_event_id",
            "openclaw_approval_event_id",
        ):
            if not isinstance(command.get(key), str) or not command[key]:
                violations.append(key)
        fingerprint = command.get("request_fingerprint")
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            violations.append("request_fingerprint")
        adapter = adapters.get(str(provider))
        if adapter is None:
            violations.append("adapter")
        if violations:
            raise ValueError(
                "bridge command contract denied: " + ", ".join(sorted(set(violations)))
            )
        return adapter

    def _result_envelope(
        self,
        command: dict,
        *,
        result: dict | None = None,
        error: Exception | None = None,
        provider_called: bool,
    ) -> dict:
        now = datetime.now(timezone.utc)
        if int(command.get("schema_version") or 1) != 2:
            raise ValueError("member bridge commands require external_sync v2")
        if error is None:
            event_type = "external_sync.write_completed"
            payload = {
                "command_id": command["command_id"],
                "member_id": self._config.member_id,
                "provider": command["provider"],
                "operation": command["operation"],
                "external_id": (result or {}).get("external_id"),
                "provider_revision": (result or {}).get("provider_revision"),
                "result": result or {},
                "completed_at": now.isoformat(),
                "suppressed": bool((result or {}).get("suppressed", False)),
            }
        else:
            event_type = "external_sync.write_failed"
            payload = {
                "command_id": command["command_id"],
                "member_id": self._config.member_id,
                "provider": command["provider"],
                "operation": command["operation"],
                "error_kind": (
                    "conflict"
                    if "conflict" in type(error).__name__.casefold()
                    or "conflict" in str(error).casefold()
                    else "not_found"
                    if isinstance(error, LookupError)
                    else "validation"
                    if isinstance(error, ValueError)
                    else "provider"
                ),
                "message": f"{type(error).__name__}: {error}",
                "retryable": not isinstance(error, (LookupError, ValueError)),
                "failed_at": now.isoformat(),
            }
        payload.update(
            {
                "command_event_id": command["event_id"],
                "domain_approval_event_id": command["domain_approval_event_id"],
                "openclaw_approval_event_id": command["openclaw_approval_event_id"],
                "provider_account_id": command["provider_account_id"],
                "request_fingerprint": command["request_fingerprint"],
                "provider_called": provider_called,
                "origin_proposal_id": command.get("origin_proposal_id"),
            }
        )
        return EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=self._config.tenant_id,
            type=event_type,
            schema_version=2,
            occurred_at=now.isoformat(),
            source_adapter="adminme-bridge-command-dispatcher",
            source_account_id=str(command["provider_account_id"]),
            owner_scope=f"private:{self._config.member_id}",
            visibility_scope=f"private:{self._config.member_id}",
            sensitivity="normal",
            causation_id=str(command["event_id"]),
            idempotency_key=f"external-sync-result:{command['command_id']}",
            payload=payload,
        ).model_dump(mode="json")

    def snapshot(self) -> dict:
        return {
            "supervisor": {
                "stopping": self._stopping,
                "adapter_count": len(self._adapters),
                "packs_discovered": len(self._packs),
            },
            "adapters": [
                _status_to_dict(a.status() if hasattr(a, "status") else AdapterStatus())
                for a in self._adapters
            ],
        }


def _status_to_dict(status: AdapterStatus) -> dict:
    return {
        "state": status.state,
        "last_error": status.last_error,
        "restart_count": status.restart_count,
    }


async def _run_until_signal(supervisor: BridgeSupervisor) -> None:
    """Wait for SIGTERM / SIGINT; then stop the supervisor cleanly."""
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    await supervisor.start()
    try:
        await stop_event.wait()
    finally:
        await supervisor.stop()


async def main_async(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    config = load_config(config_path)
    packs = configured_packs(config)
    ingest_client = IngestClient(
        central_endpoint_url=config.central_endpoint,
        tailscale_identity_header=config.tailscale_identity,
        enrollment_id=config.enrollment_id,
    )
    supervisor = BridgeSupervisor(
        config=config, ingest_client=ingest_client, packs=packs
    )
    try:
        await _run_until_signal(supervisor)
    finally:
        await ingest_client.aclose()


def run() -> None:  # pragma: no cover — script entrypoint shape only
    logging.basicConfig(level=logging.INFO)
    configured = Path(
        os.environ.get("ADMINME_BRIDGE_CONFIG", str(DEFAULT_CONFIG_PATH))
    ).expanduser()
    asyncio.run(main_async(configured))


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "BridgeSupervisor",
    "configured_packs",
    "main_async",
    "run",
]


if __name__ == "__main__":  # pragma: no cover - launchd module entrypoint
    run()
