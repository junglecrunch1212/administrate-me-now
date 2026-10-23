"""
Adapter supervisor — async lifecycle manager for L1 standalone adapters.

Per ADMINISTRATEME_BUILD.md §L1 and SYSTEM_INVARIANTS.md §7 (pipelines /
daemons). Reads ``config/channels.yaml`` at
startup, instantiates each configured adapter via a loader callable
(PM-27: constructor-injected; an empty loader represents no configured providers),
spawns each as an ``asyncio.Task``, watches for crashes, and restarts
with exponential backoff (1s, 2s, 4s, ..., capped at 300s; resets to 1s
after 5+ minutes of uptime).

Emits ``adapter.error`` (severity ``transient`` or ``fatal``) on each
crash before scheduling the restart. The supervisor itself is NOT a
projection [§2.2], NOT a pipeline [§7], and NOT an OpenClaw plugin
[§8 invariant 3] — it's a process-management primitive.

The optional ``/status`` JSON endpoint is served via stdlib
``asyncio.start_server`` to avoid pulling aiohttp; ``aiohttp`` is not a
project dep. Port defaults to 8081 (distinct from the OpenClaw runner's
8080). Set ``adapter_supervisor_port: 0`` in ``config/channels.yaml`` to
disable the HTTP endpoint (tests do this).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from adminme.adapters.base import Adapter, AdapterContext, AdapterStatus
from adminme.events.envelope import EventEnvelope
from adminme.lib.adapters import (
    CommunicationAdapter,
    OutboundMessage,
    SendResult,
    WriteOperation,
    WriteResult,
)

logger = logging.getLogger(__name__)


DEFAULT_PORT = 8081
"""Default HTTP port for the /status endpoint. Distinct from the
OpenClaw skill-runner port (8080)."""

INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 300.0
"""Exponential-backoff window: 1s, 2s, 4s, ..., capped at 5 minutes."""

UPTIME_RESET_S = 300.0
"""Restart-counter reset threshold. After this much uptime since the
last restart, the backoff window resets to ``INITIAL_BACKOFF_S``."""


AdapterLoader = Callable[[dict[str, Any]], list[Adapter]]
"""Callable that converts ``channels.yaml``'s parsed dict into adapter
instances. The empty default is the truthful no-provider configuration;
configured providers require an explicit production loader."""


def _empty_loader(_config: dict[str, Any]) -> list[Adapter]:
    """Return the explicit no-configured-provider adapter set."""
    return []


@dataclass
class _ManagedAdapter:
    """Per-adapter bookkeeping inside the supervisor."""

    adapter: Adapter
    status: AdapterStatus = field(default_factory=AdapterStatus)
    task: asyncio.Task[None] | None = None
    backoff_s: float = INITIAL_BACKOFF_S


class AdapterSupervisor:
    """Async supervisor for L1 standalone adapters.

    Usage::

        sup = AdapterSupervisor(
            config_path=Path("config/channels.yaml"),
            ctx_factory=lambda: AdapterContext(event_sink=..., config={}, ...),
            adapter_loader=load_my_adapters,
        )
        await sup.start()
        ...
        await sup.stop()
    """

    def __init__(
        self,
        *,
        config_path: Path,
        ctx_factory: Callable[[], AdapterContext],
        adapter_loader: AdapterLoader = _empty_loader,
        clock: Callable[[], float] | None = None,
        config_data: dict[str, Any] | None = None,
    ) -> None:
        self._config_path = config_path
        self._ctx_factory = ctx_factory
        self._loader = adapter_loader
        self._clock = clock or _monotonic
        self._managed: dict[str, _ManagedAdapter] = {}
        self._config: dict[str, Any] = dict(config_data or {})
        self._http_server: asyncio.base_events.Server | None = None
        self._stopping = False
        self._prepared = False
        self._reconcile_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------
    def load_config(self) -> dict[str, Any]:
        """Parse ``channels.yaml``. Raises if file is missing or malformed."""
        text = self._config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"{self._config_path}: top-level YAML must be a mapping, got {type(data).__name__}"
            )
        self._config = data
        return data

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Load config, instantiate adapters, spawn supervised tasks, and
        (optionally) start the /status HTTP endpoint."""
        self.prepare()

        for name, managed in self._managed.items():
            managed.task = asyncio.create_task(
                self._run_adapter(managed), name=f"adapter:{name}"
            )

        port = int(self._config.get("adapter_supervisor_port", DEFAULT_PORT))
        host = str(self._config.get("adapter_supervisor_host", "127.0.0.1"))
        if port > 0:
            self._http_server = await asyncio.start_server(
                self._handle_http, host=host, port=port
            )

    def prepare(self) -> None:
        """Load configured instances without starting provider I/O."""

        if self._prepared:
            return
        if not self._config:
            self.load_config()
        for index, adapter in enumerate(self._loader(self._config)):
            base = str(getattr(adapter, "instance_name", None) or adapter.name)
            name = base if base not in self._managed else f"{base}:{index}"
            self._managed[name] = _ManagedAdapter(adapter=adapter)
        self._prepared = True

    async def dispatch_write(
        self,
        *,
        provider: str,
        member_id: str,
        provider_account_id: str,
        operation: WriteOperation,
        exact_coordinate: tuple[str, ...] | None = None,
    ) -> WriteResult:
        """Route a typed command to exactly one matching provider instance."""

        provider_names = {
            "google_calendar": "calendaring-google-calendar",
            "google_tasks": "tasks-google-tasks",
            "apple_calendar": "calendaring-apple-calendar",
            "apple_reminders": "reminders-apple-eventkit",
        }
        expected_name = provider_names.get(provider)
        candidates: list[Adapter] = []
        for managed in self._managed.values():
            adapter = managed.adapter
            config = getattr(adapter, "_config", {})
            if expected_name and getattr(adapter, "name", None) != expected_name:
                continue
            if exact_coordinate is None:
                if config.get("member_id") not in (None, "", member_id):
                    continue
            else:
                b03 = config.get("b03_google_tasks_create")
                if not isinstance(b03, dict):
                    continue
                candidate = (
                    str(b03.get("resource_owner_principal_id")),
                    str(config.get("service_principal_id")),
                    str(b03.get("represented_service_principal_id")),
                    str(b03.get("context_scope")),
                    str(b03.get("on_behalf_of")),
                    provider,
                    str(config.get("provider_account_id") or "").casefold(),
                    str(getattr(adapter, "name", "")),
                    str(b03.get("destination_task_list_id")),
                    operation.kind,
                )
                if candidate != exact_coordinate:
                    continue
            configured_account = (
                config.get("provider_account_id")
                or config.get("subject_email")
                or config.get("user_email")
            )
            if exact_coordinate is None:
                if configured_account not in (None, "", provider_account_id):
                    continue
            elif (
                str(configured_account or "").casefold()
                != provider_account_id.casefold()
            ):
                continue
            if callable(getattr(adapter, "apply_write", None)):
                candidates.append(adapter)
        if len(candidates) != 1:
            raise LookupError(
                f"external write route expected one {provider} adapter for "
                f"{member_id}/{provider_account_id}, found {len(candidates)}"
            )
        result = await candidates[0].apply_write(operation)  # type: ignore[attr-defined]
        if not result.applied and not result.suppressed:
            raise RuntimeError("provider adapter is not ready to accept writes")
        return result

    async def dispatch_cos_email(
        self, *, provider_account_id: str, message: OutboundMessage
    ) -> SendResult:
        """Select one exact service-owned Gmail adapter, never a member account."""
        from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

        matches: list[CommunicationAdapter] = []
        for managed in self._managed.values():
            adapter = managed.adapter
            config = getattr(adapter, "_config", {})
            if (
                getattr(adapter, "name", None) == "messaging-gmail-api"
                and config.get("service_principal_id") == COS_SERVICE_PRINCIPAL_ID
                and not config.get("member_id")
                and config.get("provider_account_id") == provider_account_id
                and config.get("user_email") == provider_account_id
                and config.get("enable_writes") is True
                and isinstance(adapter, CommunicationAdapter)
            ):
                matches.append(adapter)
        if len(matches) != 1:
            raise LookupError("CoS response requires one exact writable Gmail account")
        return await matches[0].send(message)

    async def dispatch_communication_write(
        self,
        *,
        provider: str,
        member_id: str,
        provider_account_id: str,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Route a governed Comms mutation to one exact Cat-A account."""

        provider_names = {
            "gmail": "messaging-gmail-api",
            "microsoft_graph": "messaging-microsoft-graph",
            "bluebubbles": "messaging-bluebubbles",
            "twilio_sms": "messaging-twilio-sms",
            "twilio_whatsapp": "messaging-twilio-whatsapp",
        }
        expected_name = provider_names.get(provider)
        candidates: list[CommunicationAdapter] = []
        for managed in self._managed.values():
            adapter = managed.adapter
            if expected_name is None or getattr(adapter, "name", None) != expected_name:
                continue
            config = getattr(adapter, "_config", {})
            if config.get("member_id") != member_id:
                continue
            configured_account = (
                config.get("provider_account_id")
                or config.get("subject_email")
                or config.get("user_email")
            )
            if configured_account != provider_account_id:
                continue
            if isinstance(adapter, CommunicationAdapter):
                candidates.append(adapter)
        if len(candidates) != 1:
            raise LookupError(
                f"communication route expected one {provider} adapter for "
                f"{member_id}/{provider_account_id}, found {len(candidates)}"
            )
        communication_adapter = candidates[0]
        if operation == "reply":
            recipient = payload.get("recipient")
            body = payload.get("body")
            if not isinstance(recipient, str) or not recipient:
                raise ValueError("reply recipient is unavailable")
            if not isinstance(body, str) or not body.strip():
                raise ValueError("reply body is empty")
            result = await communication_adapter.send(
                OutboundMessage(
                    channel=provider,
                    recipient=recipient,
                    body=body,
                    metadata={
                        "subject": payload.get("subject"),
                        "thread_id": payload.get("thread_id"),
                        "in_reply_to": payload.get("rfc_message_id"),
                        "interaction_id": payload.get("interaction_id"),
                    },
                )
            )
            return {
                "applied": result.delivered,
                "suppressed": result.suppressed,
                "external_id": result.external_id,
                "state": {},
            }
        manage = getattr(communication_adapter, "manage_message", None)
        if not callable(manage):
            raise ValueError(f"{provider} does not implement {operation}")
        pending_result = manage(
            operation=operation,
            native_message_id=payload.get("native_message_id")
            or payload.get("target_external_id"),
        )
        if not inspect.isawaitable(pending_result):
            raise RuntimeError("communication adapter manage_message must be async")
        result = await pending_result
        if not isinstance(result, dict):
            raise RuntimeError("communication adapter returned an invalid result")
        return result

    async def stop(self) -> None:
        """Stop the HTTP endpoint and request every managed adapter to
        shut down. Tasks are awaited with a short grace window before
        being cancelled."""
        self._stopping = True

        if self._http_server is not None:
            self._http_server.close()
            await self._http_server.wait_closed()
            self._http_server = None

        for managed in self._managed.values():
            await self._stop_managed(managed)

    async def reconcile(
        self,
        config_data: dict[str, Any],
        *,
        affected_instance_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """Replace configured instances, optionally within exact instance IDs.

        Construct and validate the candidate before touching a running adapter.
        An explicit selection preserves unrelated adapter objects, tasks and
        backoff state. It cannot smuggle in an unrelated configuration change.
        Omitting the selection retains the existing full-reconciliation path.
        """

        async with self._reconcile_lock:
            candidate_config = dict(config_data)
            selected = (
                set(affected_instance_ids)
                if affected_instance_ids is not None
                else None
            )
            if selected is not None:
                self._validate_selected_reconciliation(candidate_config, selected)
            candidates: dict[str, _ManagedAdapter] = {}
            for index, adapter in enumerate(self._loader(candidate_config)):
                base = str(getattr(adapter, "instance_name", None) or adapter.name)
                name = base if base not in candidates else f"{base}:{index}"
                candidates[name] = _ManagedAdapter(adapter=adapter)

            old_managed = self._managed
            retained: dict[str, _ManagedAdapter] = {}
            if selected is None:
                replaced = old_managed
                next_managed = candidates
                starts = candidates
            else:
                retained = {
                    name: managed
                    for name, managed in old_managed.items()
                    if name not in selected
                }
                if set(retained) != set(candidates) - selected:
                    raise ValueError(
                        "selected reconciliation changed unrelated adapter instances"
                    )
                replaced = {
                    name: managed
                    for name, managed in old_managed.items()
                    if name in selected
                }
                starts = {
                    name: managed
                    for name, managed in candidates.items()
                    if name in selected
                }
                next_managed = {**retained, **starts}
            if selected is not None:
                # A revoked instance must leave dispatch before shutdown awaits.
                self._managed = retained
            for managed in replaced.values():
                await self._stop_managed(managed)

            self._managed = next_managed
            self._config = candidate_config
            self._prepared = True
            if not self._stopping:
                for name, managed in starts.items():
                    managed.task = asyncio.create_task(
                        self._run_adapter(managed), name=f"adapter:{name}"
                    )
                await asyncio.sleep(0)
            return self.snapshot()

    def _validate_selected_reconciliation(
        self, candidate_config: dict[str, Any], selected: set[str]
    ) -> None:
        """Only explicit, unique configured IDs may identify targeted rows."""

        if any(not isinstance(value, str) or not value for value in selected):
            raise ValueError("selected reconciliation requires nonempty instance IDs")

        def without_selected(
            config: dict[str, Any],
        ) -> tuple[dict[str, Any], set[str]]:
            entries = config.get("adapters") or []
            if not isinstance(entries, list):
                raise ValueError("adapter configuration must be a list")
            retained: list[dict[str, Any]] = []
            configured_ids: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("adapter entry must be a mapping")
                instance_id = entry.get("id")
                if instance_id is not None:
                    if (
                        not isinstance(instance_id, str)
                        or not instance_id
                        or instance_id in configured_ids
                    ):
                        raise ValueError(
                            "selected reconciliation requires unique configured IDs"
                        )
                    configured_ids.add(instance_id)
                if instance_id not in selected:
                    retained.append(entry)
            from adminme.adapters.member_google_connections import (
                CONFIG_KEY,
                GoogleConnectionPolicy,
            )

            policy = GoogleConnectionPolicy.model_validate(
                config.get(CONFIG_KEY, {"selections": []})
            ).model_dump(mode="json")
            policy["selections"] = sorted(
                (
                    row
                    for row in policy["selections"]
                    if row["adapter_id"] not in selected
                ),
                key=lambda row: row["adapter_id"],
            )
            return {
                **config,
                "adapters": retained,
                CONFIG_KEY: policy,
            }, configured_ids

        old_remainder, old_ids = without_selected(self._config)
        new_remainder, new_ids = without_selected(candidate_config)
        if not selected <= old_ids | new_ids:
            raise ValueError("selected reconciliation names an unconfigured instance")
        if old_remainder != new_remainder:
            raise ValueError(
                "selected reconciliation changed unrelated adapter configuration"
            )

    async def stop_instances(self, instance_ids: set[str]) -> dict[str, Any]:
        """Contain exact revoked instances without constructing replacements.

        Remove them from dispatch as well as stopping polling. This supports
        fail-closed recovery when permission history has already committed and
        a selected reconciliation cannot start its replacement adapters.
        """

        selected = set(instance_ids)
        if any(not isinstance(value, str) or not value for value in selected):
            raise ValueError("stopping instances requires nonempty instance IDs")
        async with self._reconcile_lock:
            removed = [
                self._managed.pop(name) for name in selected if name in self._managed
            ]
            for managed in removed:
                await self._stop_managed(managed)
            return self.snapshot()

    @staticmethod
    async def _stop_managed(managed: _ManagedAdapter) -> None:
        try:
            await managed.adapter.stop()
        except Exception:  # noqa: BLE001 — best-effort shutdown
            logger.exception("adapter %s raised on stop()", managed.adapter.name)
        if managed.task is not None and not managed.task.done():
            managed.task.cancel()
        if managed.task is not None:
            try:
                await managed.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        managed.status.state = "stopped"

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Aggregated status dict — what ``/status`` serves and what tests
        assert against."""
        return {
            "supervisor": {
                "stopping": self._stopping,
                "adapter_count": len(self._managed),
            },
            "adapters": {
                name: _status_to_dict(m.status) for name, m in self._managed.items()
            },
        }

    def inventory(self) -> dict[str, dict[str, Any]]:
        """Return secret-free per-instance evidence for product composers."""

        values: dict[str, dict[str, Any]] = {}
        for name, managed in self._managed.items():
            supervisor_status = _status_to_dict(managed.status)
            try:
                native_status = _status_to_dict(managed.adapter.status())
            except Exception:  # noqa: BLE001 — status must not break reads
                native_status = {}
            state = str(native_status.get("state") or supervisor_status["state"])
            if supervisor_status["state"] in {"backoff", "stopped", "crashed"}:
                state = str(supervisor_status["state"])
            values[name] = {
                "instance_id": name,
                "adapter_name": managed.adapter.name,
                "family": managed.adapter.family,
                "version": managed.adapter.version,
                "state": state,
                "started_at": native_status.get("started_at")
                or supervisor_status.get("started_at"),
                "last_cursor_advance_at": native_status.get("last_cursor_advance_at")
                or supervisor_status.get("last_cursor_advance_at"),
                "last_error": native_status.get("last_error")
                or supervisor_status.get("last_error"),
                "restart_count": supervisor_status.get("restart_count", 0),
                "next_backoff_s": supervisor_status.get("next_backoff_s"),
                "extra": dict(native_status.get("extra") or {}),
                # This is the adapter's reviewed code ceiling, not proof that
                # an external provider granted the corresponding scopes.
                "write_capabilities": list(
                    getattr(managed.adapter, "write_capabilities", []) or []
                ),
            }
        return values

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _run_adapter(self, managed: _ManagedAdapter) -> None:
        """Per-adapter supervision loop. Calls ``start(ctx)``; on crash,
        emits ``adapter.error`` and reschedules with exponential
        backoff. Resets backoff after sustained uptime."""
        adapter = managed.adapter
        while not self._stopping:
            ctx = self._ctx_factory()
            self._bind_observation_seams(adapter, ctx)
            started_at = self._clock()
            managed.status.state = "starting"
            managed.status.started_at = datetime.now(timezone.utc)
            try:
                managed.status.state = "running"
                await adapter.start(ctx=ctx)
                managed.status.state = "stopped"
                return
            except asyncio.CancelledError:
                managed.status.state = "stopped"
                raise
            except Exception as exc:  # noqa: BLE001 — supervisor swallows on purpose
                uptime = self._clock() - started_at
                if uptime >= UPTIME_RESET_S:
                    managed.backoff_s = INITIAL_BACKOFF_S
                error_payload = {
                    "adapter_name": adapter.name,
                    "adapter_family": adapter.family,
                    "severity": "transient",
                    "error_kind": "unknown",
                    "error_message": "Adapter failed; inspect the authorized source status.",
                    "retry_after_s": int(managed.backoff_s),
                    "occurred_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                }
                managed.status.last_error = error_payload
                managed.status.state = "backoff"
                managed.status.next_backoff_s = managed.backoff_s
                managed.status.restart_count += 1
                logger.warning(
                    "adapter %s crashed (%s); restarting in %.1fs",
                    adapter.name,
                    type(exc).__name__,
                    managed.backoff_s,
                )
                await self._emit_adapter_error(ctx, error_payload)
                try:
                    await asyncio.sleep(managed.backoff_s)
                except asyncio.CancelledError:
                    managed.status.state = "stopped"
                    raise
                managed.backoff_s = min(managed.backoff_s * 2, MAX_BACKOFF_S)
        managed.status.state = "stopped"

    @staticmethod
    def _bind_observation_seams(adapter: Adapter, ctx: AdapterContext) -> None:
        """Bind the shared outbound guard to adapters that expose its seam slots.

        Adapter packs retain constructor injection for isolated tests, while the
        supervisor is the production composition point.  Never replace an
        explicitly injected seam; fill only missing slots from ``AdapterContext``.
        """
        bindings = {
            "_observation_session": ctx.observation_session,
            "_observation_manager": ctx.observation_manager,
            "_observation_event_log": ctx.observation_event_sink,
        }
        for attribute, value in bindings.items():
            if value is not None and getattr(adapter, attribute, None) is None:
                setattr(adapter, attribute, value)

    async def _emit_adapter_error(
        self, ctx: AdapterContext, payload: dict[str, Any]
    ) -> None:
        """Write ``adapter.error`` through the one asynchronous EventSink."""
        try:
            correlation_id = ctx.correlation_id_factory()
            await ctx.event_sink.write(
                EventEnvelope(
                    event_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                    tenant_id=ctx.event_sink.tenant_id or "tenant-default",
                    type="adapter.error",
                    schema_version=1,
                    occurred_at=EventEnvelope.now_utc_iso(),
                    source_adapter="adapter_supervisor",
                    source_account_id=str(payload.get("adapter_name") or "system"),
                    owner_scope="shared:household",
                    visibility_scope="shared:household",
                    sensitivity="normal",
                    correlation_id=correlation_id,
                    payload=payload,
                ),
                correlation_id=correlation_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "supervisor failed to emit adapter.error for %s",
                payload.get("adapter_name"),
            )

    async def _handle_http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Minimal HTTP/1.1 handler that serves GET /status as JSON.

        Stdlib-only — keeps the supervisor free of an aiohttp dep. Any
        non-``GET /status`` request gets a 404."""
        try:
            request_line = await reader.readline()
            # Drain headers.
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b""):
                    break
            parts = request_line.split()
            if (
                len(parts) >= 2
                and parts[0] == b"GET"
                and parts[1].startswith(b"/status")
            ):
                body = json.dumps(self.snapshot()).encode("utf-8")
                headers = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                    b"Connection: close\r\n\r\n"
                )
                writer.write(headers + body)
            else:
                writer.write(
                    b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass


def _monotonic() -> float:
    return asyncio.get_event_loop().time()


def _status_to_dict(status: AdapterStatus) -> dict[str, Any]:
    return {
        "state": status.state,
        "last_error": status.last_error,
        "last_cursor_advance_at": status.last_cursor_advance_at.isoformat()
        if status.last_cursor_advance_at is not None
        else None,
        "started_at": status.started_at.isoformat()
        if status.started_at is not None
        else None,
        "restart_count": status.restart_count,
        "next_backoff_s": status.next_backoff_s,
        "extra": dict(status.extra),
    }


__all__ = [
    "AdapterSupervisor",
    "AdapterLoader",
    "DEFAULT_PORT",
    "INITIAL_BACKOFF_S",
    "MAX_BACKOFF_S",
    "UPTIME_RESET_S",
]
