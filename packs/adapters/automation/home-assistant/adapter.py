"""Home Assistant adapter pack — first canonical Cat-E reference (prompt 11g).

Inherits :class:`adminme.lib.adapters.OutboundActionAdapter` per memo 05
§3.2 / [D19] / [D24] / [PM-34] / [PM-35]. Multi-capability: Cat-C state
reads (GET /api/states → ``ha.state_changed@v1``) + Cat-E service calls
(POST /api/services/<domain>/<service> → ``action.executed@v1`` /
``action.failed@v1``) wrapped through :func:`observation_outbound` per
[§6.20]. F-3: suppressed path emits NEITHER action.executed NOR
action.failed (``observation.suppressed`` is the audit trail).

Structural transplant from ``packs/adapters/calendaring/google-calendar/
adapter.py``: lazy client factory, observation seam slots,
``_classify_*_error``, ``_emit_*`` / ``_append`` helpers. Swaps
``googleapiclient`` for ``httpx`` since HA is REST (no LLM SDK per
[§8.2, D6]).
"""

# [§8.2, D6] no LLM SDK imports under packs/adapters/.
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from typing import Any, Callable, ClassVar, Literal

from adminme.adapters.base import AdapterContext, AdapterStatus, SensitivityFloor
from adminme.adapters.emission import write_adapter_event
from adminme.lib.adapters import (
    ActionRequest,
    ActionResult,
    OutboundActionAdapter,
)
from adminme.lib.observation import outbound as observation_outbound

_log = logging.getLogger(__name__)

# [§6 invariant 10] privileged > normal; config may RAISE but not LOWER.
_FLOOR_RANK: dict[str, int] = {"normal": 0, "privileged": 1}

_DEFAULT_POLL_INTERVAL_S: int = 60

TokenResolver = Callable[[str], str]
"""``op://`` ref → long-lived access token. Tests stub; prod uses 1Password CLI."""

ClientFactory = Callable[[str, str], Any]
"""(base_url, access_token) → httpx-like async client exposing
``get`` / ``post`` / ``aclose``. Production lazily imports httpx."""


def _default_token_resolver(op_ref: str) -> str:
    """Read HA long-lived access token from an ``op://`` reference."""
    if not op_ref or not op_ref.startswith("op://"):
        raise ValueError(f"invalid op-ref: {op_ref!r}")
    if op_ref.startswith("op://test/"):
        return "test-token-do-not-use-in-production"
    op_bin = shutil.which("op")
    if op_bin is None:
        raise RuntimeError(f"1Password CLI `op` not on PATH; cannot resolve {op_ref!r}")
    proc = subprocess.run(
        [op_bin, "read", "--no-newline", op_ref],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return proc.stdout.decode("utf-8").strip()


def _default_client_factory(base_url: str, access_token: str) -> Any:
    """Build ``httpx.AsyncClient`` with the bearer header. Lazy import."""
    import httpx

    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(15.0, connect=10.0),
    )


class HomeAssistantAdapter(OutboundActionAdapter):
    """Home Assistant Cat-C+E multi-capability adapter (canonical 11g).

    Cat-C ``_poll_states_once`` GETs /api/states and emits
    ``ha.state_changed@v1`` per transition; Cat-E :meth:`execute_action`
    for ``kind="ha.service_call"`` POSTs /api/services/<domain>/<service>
    via :func:`observation_outbound` per [§6.20], emitting
    ``action.executed@v1`` / ``action.failed@v1`` (NEITHER when
    suppressed — F-3)."""

    name: ClassVar[str] = "automation-home-assistant"
    # [PM-34] inherits one ABC (Cat-E OutboundActionAdapter); ``family``
    # is the Cat-E family literal even though this pack ALSO declares a
    # Cat-C capability per [PM-35].
    family: ClassVar[Literal["home_automation", "telephony", "external_action"]] = (
        "home_automation"
    )
    version: ClassVar[str] = "1.0.0"
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    # [memo 05 §3.2, PM-35, D24] multi-capability: Cat-E writes (PRIMARY;
    # ABC inherited is OutboundActionAdapter so capabilities[0] MUST be
    # Cat-E per the validator's base_class_conformance check) + Cat-C
    # reads (secondary). The Cat-C per-capability override sets
    # ``observation_mode_required: false`` (reads are NOT side effects
    # per memo §8); the Cat-E entry inherits the pack-level Cat-E gate.
    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-E",
            "runtime": "central",
            "event_families": ["action.executed", "action.failed"],
            "observation_mode_required": True,
        },
        {
            "kind": "Cat-C",
            "runtime": "central",
            "event_families": ["ha.state_changed"],
            "observation_mode_required": False,
        },
    ]

    # [§6.20] Cat-E ABC contract: cannot opt out of observation.
    observation_mode_required: ClassVar[bool] = True

    PACK_ID: ClassVar[str] = "adapter:automation-home-assistant"
    HARDCODED_FLOOR: ClassVar[SensitivityFloor] = "normal"
    POLL_INTERVAL_S: ClassVar[int] = _DEFAULT_POLL_INTERVAL_S
    # The single declared outbound action kind for this pack. Defense-
    # in-depth in :meth:`execute_action` rejects any other kind.
    ACTION_KIND: ClassVar[str] = "ha.service_call"

    def __init__(
        self,
        *,
        config: dict[str, Any],
        token_resolver: TokenResolver | None = None,
        client_factory: ClientFactory | None = None,
        max_poll_cycles: int | None = None,
        observation_session: Any = None,
        observation_manager: Any = None,
        observation_event_log: Any = None,
    ) -> None:
        self._config = dict(config)
        self._token_resolver = token_resolver or _default_token_resolver
        self._client_factory = client_factory or _default_client_factory
        # [§6 invariant 10] resolve floor at construction; raise on downgrade.
        self.sensitivity_floor: SensitivityFloor = (  # type: ignore[misc]
            self._resolve_sensitivity_floor()
        )
        self._poll_interval_s: int = int(
            self._config.get("poll_interval_s", self.POLL_INTERVAL_S)
        )
        self._status = AdapterStatus()
        self._client: Any = None
        self._ctx: AdapterContext | None = None
        self._stop_requested: bool = False
        self._max_poll_cycles: int | None = max_poll_cycles
        # Dedupe Cat-C poll emissions by entity_id; value is the
        # ``last_changed`` timestamp last seen so a poll that returns
        # the same value is a no-op.
        self._seen_states: dict[str, str] = {}
        # [§6.20, PM-27] observation seam slots; Phase A leaves unset.
        self._observation_session: Any = observation_session
        self._observation_manager: Any = observation_manager
        self._observation_event_log: Any = observation_event_log

    def _resolve_sensitivity_floor(self) -> SensitivityFloor:
        """[§6 invariant 10] config may RAISE; never LOWER."""
        cfg_value = self._config.get("sensitivity_floor", self.HARDCODED_FLOOR)
        if cfg_value not in _FLOOR_RANK:
            raise ValueError(
                f"sensitivity_floor must be 'normal' or 'privileged', got {cfg_value!r}"
            )
        if _FLOOR_RANK[cfg_value] < _FLOOR_RANK[self.HARDCODED_FLOOR]:
            raise ValueError(
                f"config sensitivity_floor={cfg_value!r} lower than hardcoded "
                f"{self.HARDCODED_FLOOR!r}; refusing to downgrade "
                "[SYSTEM_INVARIANTS.md §6 invariant 10]"
            )
        return cfg_value  # type: ignore[return-value]

    async def start(self, *, ctx: AdapterContext) -> None:
        """Authenticate, emit ``adapter.authenticated``, run Cat-C poll loop."""
        op_ref = self._config.get("access_token_ref")
        base_url = self._config.get("base_url")
        if not isinstance(op_ref, str) or not op_ref.startswith("op://"):
            raise ValueError(
                "access_token_ref must be a 1Password reference "
                "(op://<vault>/<item>/<field>)"
            )
        if not isinstance(base_url, str) or not base_url:
            raise ValueError("base_url must be a non-empty string")
        token = self._token_resolver(op_ref)
        self._client = self._client_factory(base_url, token)
        self._ctx = ctx
        self._status.started_at = ctx.now_factory()
        self._status.state = "running"
        # [D7, D8] adapter.authenticated v1; correlation_id from factory.
        await self._emit_authenticated(ctx=ctx, op_ref=op_ref)
        await self._poll_state_loop(ctx=ctx)

    async def stop(self) -> None:
        """Release the HA HTTP client. Idempotent."""
        self._stop_requested = True
        client = self._client
        self._client = None
        if client is not None:
            close = getattr(client, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001 — best-effort
                    _log.exception("home-assistant adapter failed to close client")
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    async def _poll_state_loop(self, *, ctx: AdapterContext) -> None:
        """Cat-C inbound loop. Fatal halts; transient stays. Tests bound
        via ``max_poll_cycles``."""
        cycles = 0
        while not self._stop_requested:
            if self._max_poll_cycles is not None and cycles >= self._max_poll_cycles:
                return
            try:
                await self._poll_states_once(ctx=ctx)
            except BaseException as exc:  # noqa: BLE001 — classify & emit
                severity, error_kind, retry_after_s = self._classify_ha_error(exc)
                await self._emit_error(
                    ctx=ctx,
                    severity=severity,
                    error_kind=error_kind,
                    error_message=str(exc) or type(exc).__name__,
                    retry_after_s=retry_after_s,
                )
                if severity == "fatal":
                    self._status.state = "crashed"
                    return
                self._status.state = "backoff"
            cycles += 1
            try:
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                return

    async def _poll_states_once(self, *, ctx: AdapterContext) -> int:
        """Fetch ``/api/states`` + dedupe + emit. ``_seen_states`` gates
        re-poll on unchanged ``last_changed``. Returns emit count."""
        if self._client is None:
            raise RuntimeError(
                "Home Assistant client not initialized; call start() first"
            )
        states = await self._fetch_states()
        emitted = 0
        for state in states:
            entity_id = state.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                continue
            last_changed = str(state.get("last_changed") or "")
            prior = self._seen_states.get(entity_id)
            if prior is not None and prior == last_changed:
                continue
            await self._emit_state_changed(ctx=ctx, state=state)
            self._seen_states[entity_id] = last_changed
            emitted += 1
        return emitted

    async def _fetch_states(self) -> list[dict[str, Any]]:
        """GET ``/api/states`` → list of HA state dicts per [A-5].

        Raises ``httpx.HTTPStatusError`` on non-2xx (classified upstream).
        """
        client = self._client
        assert client is not None
        response = await client.get("/api/states")
        # raise_for_status duck-typed for test fakes.
        raise_for = getattr(response, "raise_for_status", None)
        if raise_for is not None:
            raise_for()
        data = response.json()
        if not isinstance(data, list):
            return []
        return [s for s in data if isinstance(s, dict)]

    async def _emit_state_changed(
        self,
        *,
        ctx: AdapterContext,
        state: dict[str, Any],
    ) -> None:
        """Map HA state dict onto ``HaStateChangedV1`` (c1 schema)."""
        await self._append(
            ctx=ctx,
            event_type="ha.state_changed",
            payload={
                "entity_id": str(state.get("entity_id") or ""),
                "state": str(state.get("state") or ""),
                "attributes": dict(state.get("attributes") or {}),
                "last_changed": str(state.get("last_changed") or ""),
                "last_updated": str(state.get("last_updated") or ""),
                "source": "home_assistant",
            },
        )

    async def execute_action(self, request: ActionRequest) -> ActionResult:
        """Cat-E entry: POST /api/services/<domain>/<service>.

        Phase B (seam slots populated) routes through
        :func:`observation_outbound` per [§6.20]; suppression returns
        ``ActionResult(executed=False, suppressed=True)`` and emits
        NEITHER action.executed NOR action.failed per F-3. Phase A
        (seams unset) direct-calls + emits action.executed. Any error
        classifies via :meth:`_classify_ha_error`, emits action.failed,
        and re-raises for supervisor backoff.
        """
        if request.kind != self.ACTION_KIND:
            # Defense-in-depth: Cat-E has no write-capabilities gate so
            # we raise ValueError rather than WriteCapabilityError.
            raise ValueError(
                f"HomeAssistantAdapter.execute_action received undeclared "
                f"kind={request.kind!r}; expected {self.ACTION_KIND!r}"
            )
        payload = dict(request.payload)
        domain = str(payload.get("domain") or "")
        service = str(payload.get("service") or "")
        service_data: dict[str, Any] = dict(payload.get("service_data") or {})
        if not domain or not service:
            raise ValueError(
                "ha.service_call payload requires non-empty 'domain' and 'service'"
            )

        client = self._client
        if client is None:
            return ActionResult(executed=False, external_id=None, suppressed=False)

        async def _do_service_call() -> Any:
            response = await client.post(
                f"/api/services/{domain}/{service}",
                json=service_data,
            )
            raise_for = getattr(response, "raise_for_status", None)
            if raise_for is not None:
                raise_for()
            return response

        session = self._observation_session
        manager = self._observation_manager
        event_log = self._observation_event_log

        try:
            if session is not None and manager is not None and event_log is not None:
                # [§6.20] Phase B: observation seam wraps the service call.
                outbound_result = await observation_outbound(
                    session,
                    self.ACTION_KIND,
                    {
                        "domain": domain,
                        "service": service,
                        "service_data": service_data,
                    },
                    _do_service_call,
                    manager=manager,
                    event_sink=event_log,
                    target_channel="home_assistant",
                    target_identifier=f"{domain}.{service}",
                )
                if outbound_result.suppressed:
                    # F-3 (HARD): suppressed → NEITHER action.executed
                    # NOR action.failed emitted. outbound() itself
                    # emits observation.suppressed for the audit trail.
                    return ActionResult(
                        executed=False, external_id=None, suppressed=True
                    )
                # Phase B success path.
                await self._emit_action_executed(
                    request=request, domain=domain, service=service
                )
                return ActionResult(executed=True, external_id=None, suppressed=False)
            # Phase A: no seam slots; direct call + emit.
            await _do_service_call()
            await self._emit_action_executed(
                request=request, domain=domain, service=service
            )
            return ActionResult(executed=True, external_id=None, suppressed=False)
        except BaseException as exc:  # noqa: BLE001 — classify & emit
            severity, error_kind, _retry = self._classify_ha_error(exc)
            await self._emit_action_failed(
                request=request,
                domain=domain,
                service=service,
                error_message=str(exc) or type(exc).__name__,
                severity=severity,
                error_kind=error_kind,
            )
            raise

    def _action_payload(
        self,
        *,
        request: ActionRequest,
        domain: str,
        service: str,
    ) -> dict[str, Any]:
        """Common payload shape for action.executed / action.failed."""
        return {
            "domain": domain,
            "service": service,
            "service_data": dict(request.payload.get("service_data") or {}),
        }

    async def _emit_action_executed(
        self,
        *,
        request: ActionRequest,
        domain: str,
        service: str,
    ) -> None:
        """``action.executed@v1`` Cat-E success emission. ``external_id``
        is None — HA's service-call returns changed-state dicts, not a
        remote correlation token per [A-6]."""
        ctx = self._ctx
        if ctx is None:
            return
        await self._append(
            ctx=ctx,
            event_type="action.executed",
            payload={
                "action_kind": request.kind,
                "target_external_id": request.target_external_id,
                "payload": self._action_payload(
                    request=request,
                    domain=domain,
                    service=service,
                ),
                "external_id": None,
                "executed_at": ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "correlation_id": request.correlation_id,
            },
        )

    async def _emit_action_failed(
        self,
        *,
        request: ActionRequest,
        domain: str,
        service: str,
        error_message: str,
        severity: str,
        error_kind: str,
    ) -> None:
        """``action.failed@v1`` Cat-E error emission. ``severity`` drives
        the supervisor's backoff policy."""
        ctx = self._ctx
        if ctx is None:
            return
        await self._append(
            ctx=ctx,
            event_type="action.failed",
            payload={
                "action_kind": request.kind,
                "target_external_id": request.target_external_id,
                "payload": self._action_payload(
                    request=request,
                    domain=domain,
                    service=service,
                ),
                "error_message": error_message[:2000],
                "severity": severity,
                "error_kind": error_kind,
                "failed_at": ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "correlation_id": request.correlation_id,
            },
        )

    async def _emit_authenticated(self, *, ctx: AdapterContext, op_ref: str) -> None:
        """``adapter.authenticated@v1``; carries op-ref, NEVER the secret."""
        await self._append(
            ctx=ctx,
            event_type="adapter.authenticated",
            payload={
                "adapter_name": self.name,
                "adapter_family": self.family,
                "token_storage_ref": op_ref,
                "authenticated_at": ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scopes": [],
            },
        )

    async def _emit_error(
        self,
        *,
        ctx: AdapterContext,
        severity: str,
        error_kind: str,
        error_message: str,
        retry_after_s: int | None,
    ) -> None:
        """``adapter.error@v1`` — drives the supervisor's backoff."""
        payload: dict[str, Any] = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "severity": severity,
            "error_kind": error_kind,
            "error_message": error_message[:2000],
            "retry_after_s": retry_after_s,
            "occurred_at": ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._status.last_error = dict(payload)
        await self._append(ctx=ctx, event_type="adapter.error", payload=payload)

    async def _append(
        self,
        *,
        ctx: AdapterContext,
        event_type: str,
        payload: dict[str, Any],
        causation_id: str | None = None,
    ) -> None:
        """[§7.3, D17] append to the event log; never write projections."""
        try:
            await write_adapter_event(
                ctx,
                adapter_name=self.name,
                sensitivity_floor=self.sensitivity_floor,
                config=self._config,
                event_type=event_type,
                payload=payload,
                causation_id=causation_id,
            )
        except Exception:  # noqa: BLE001 — best-effort emission
            _log.exception("home-assistant adapter failed to emit %s event", event_type)

    @staticmethod
    def _classify_ha_error(
        exc: BaseException,
    ) -> tuple[str, str, int | None]:
        """Classify HA REST error → (severity, error_kind, retry_after_s).
        Duck-types ``httpx.HTTPStatusError`` via ``.response.status_code``."""
        status: int | None = None
        retry_after: int | None = None
        response = getattr(exc, "response", None)
        if response is not None:
            raw_status: Any = getattr(response, "status_code", None)
            try:
                status = int(raw_status) if raw_status is not None else None
            except (TypeError, ValueError):
                status = None
            headers = getattr(response, "headers", None)
            retry_header: Any = None
            if headers is not None and hasattr(headers, "get"):
                retry_header = headers.get("retry-after") or headers.get("Retry-After")
            try:
                retry_after = int(retry_header) if retry_header is not None else None
            except (TypeError, ValueError):
                retry_after = None
        if status == 429:
            return ("transient", "rate_limited", retry_after)
        if status in (401, 403):
            return ("fatal", "auth_failed", retry_after)
        if status is not None and 500 <= status < 600:
            return ("transient", "network", retry_after)
        if status is not None and 400 <= status < 500:
            return ("fatal", "auth_failed", retry_after)
        # httpx connect/timeout duck-typed by class-name suffix so this
        # module need not import httpx at load time.
        type_name = type(exc).__name__
        if type_name in {
            "ConnectError",
            "TimeoutException",
            "ReadTimeout",
            "ConnectTimeout",
        }:
            return ("transient", "network", retry_after)
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return ("transient", "network", retry_after)
        return ("transient", "unknown", retry_after)


__all__ = [
    "ClientFactory",
    "HomeAssistantAdapter",
    "TokenResolver",
]
