"""Bridge daemon lifecycle for opt-in Cat-B and Cat-D connector packs."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from datetime import datetime, timezone
from pathlib import Path

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


def configured_packs(config: BridgeConfig) -> list[PackHandle]:
    """Combine installed packs with explicitly enabled built-in packs."""

    installed = {
        handle.name: handle for handle in discover_packs(config.resolved_packs_dir())
    }
    built_in_root = Path(__file__).parent / "connector_packs"
    built_ins = {handle.name: handle for handle in discover_packs(built_in_root)}
    enabled = set(config.enabled_packs) | set(config.pack_config)
    unknown = enabled - (set(installed) | set(built_ins))
    if unknown:
        raise PackLoadError(f"configured bridge packs not found: {sorted(unknown)}")
    selected = dict(installed)
    for name in enabled:
        if name in built_ins:
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

    @property
    def adapters(self) -> list[BridgeAdapter]:
        return list(self._adapters)

    def make_context(self) -> BridgeContext:
        return BridgeContext(
            member_id=self._config.member_id,
            ingest_client=self._ingest_client,
            correlation_id_factory=lambda: str(uuid.uuid4()),
            now_factory=lambda: datetime.now(timezone.utc),
            tenant_id=self._config.tenant_id,
        )

    async def start(self) -> None:
        for adapter in self._adapters:
            self._tasks.append(
                asyncio.create_task(
                    adapter.start(ctx=self.make_context()),
                    name=f"bridge-adapter:{type(adapter).__name__}",
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
