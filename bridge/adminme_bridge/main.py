"""Bridge daemon entrypoint — async lifecycle for knowledge-source adapters.

Mirrors ``adminme/daemons/adapter_supervisor.py`` but Cat-D-only and
HTTP-emit. 11c-i ships the shell; the instantiate-from-PackHandle hook
lands in 11c-ii."""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from datetime import datetime, timezone
from pathlib import Path

from adminme_bridge.adapters.base import AdapterStatus, BridgeContext, KnowledgeAdapter
from adminme_bridge.config import BridgeConfig, load_config
from adminme_bridge.ingest_client import IngestClient
from adminme_bridge.packs import PackHandle, discover_packs


DEFAULT_CONFIG_PATH = Path("~/.adminme-bridge/config.yaml").expanduser()

logger = logging.getLogger(__name__)


class BridgeSupervisor:
    """Async supervisor for Cat-D knowledge-source adapters."""

    def __init__(
        self,
        *,
        config: BridgeConfig,
        ingest_client: IngestClient,
        packs: list[PackHandle] | None = None,
        adapters: list[KnowledgeAdapter] | None = None,
    ) -> None:
        self._config = config
        self._ingest_client = ingest_client
        self._packs = packs or []
        self._adapters: list[KnowledgeAdapter] = adapters or []
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False

    @property
    def adapters(self) -> list[KnowledgeAdapter]:
        return list(self._adapters)

    def make_context(self) -> BridgeContext:
        return BridgeContext(
            member_id=self._config.member_id,
            ingest_client=self._ingest_client,
            correlation_id_factory=lambda: str(uuid.uuid4()),
            now_factory=lambda: datetime.now(timezone.utc),
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
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

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
    packs = list(discover_packs(config.resolved_packs_dir()))
    ingest_client = IngestClient(
        central_endpoint_url=config.central_endpoint,
        tailscale_identity_header=config.tailscale_identity,
    )
    supervisor = BridgeSupervisor(config=config, ingest_client=ingest_client, packs=packs)
    try:
        await _run_until_signal(supervisor)
    finally:
        await ingest_client.aclose()


def run() -> None:  # pragma: no cover — script entrypoint shape only
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async())


__all__ = ["DEFAULT_CONFIG_PATH", "BridgeSupervisor", "main_async", "run"]
