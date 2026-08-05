"""
Plaid L1 adapter — financial-family worked example.

Implements the ``adminme.adapters.base.Adapter`` Protocol per the operator
decision in ``docs/06-split-memo-11.md`` §4. Domain-shaped substitutions
from the messaging-family pattern:

- Sensitivity floor is hardcoded ``"privileged"`` per
  ``[§6 invariant 10, D11]`` — financial adapters cannot be downgraded
  via member configuration. The class attribute is the source of truth;
  ``config.schema.json``'s ``"sensitivity_floor": {"const": "privileged"}``
  mirrors it so JSON-schema validation rejects malformed configs before
  the loader runs; ``start()`` re-asserts the value after secret resolution
  per the Gate-4-equivalent runtime check.

- Auth model is two-stage: an operator-mediated Link flow (prompt 16
  wizard) yields a long-lived Item access token; the adapter consumes
  the access token at ``start()`` via the ``op://`` 1Password reference
  shape and instantiates the Plaid client. ``adapter.authenticated`` is
  emitted once per startup-auth success carrying the reference (NEVER
  the secret value) per ``AdapterAuthenticatedV1``.

- Sync model is webhook-driven: Plaid pushes ``SYNC_UPDATES_AVAILABLE``
  to a Funnel endpoint; the adapter's ``webhook_handler`` dispatches to
  ``/transactions/sync``. A daily cron backstop covers silent webhook
  drops (the backstop poll loop body ships in commit 4).
  ``_sync_transactions`` paginates the Plaid response while ``has_more``
  is true, accumulates ``added`` + ``modified`` + ``removed`` across
  pages, persists the post-pagination cursor at the
  ``(member_id, plaid_item_id)`` granularity per the BUILD.md PLAID
  DETAILED SPEC §"Sync cursor" deliverable, then emits one
  ``money_flow.plaid_synced`` SUMMARY event per cycle (NEVER per
  transaction — that would be the anti-vision "stream of everything"
  pattern per VISION §"Quiet by default").

- ``adapter.cursor_advanced`` emission is milestone-only per
  ``[§L1 cursor discipline]``: every 100 accumulated transactions OR
  every 5 minutes since the prior advance, whichever fires first. Pure
  per-cycle emission would defeat the cross-cutting milestone rule the
  3 cross-cutting schemas were designed for.

- Institution health is queried at startup and again on the daily
  backstop cycle via ``/institutions/get_by_id`` with
  ``options.include_status=True``. Any non-``HEALTHY`` per-product
  status (e.g. ``DEGRADED`` ``transactions_updates``) emits
  ``adapter.error`` ``severity=transient`` ``error_kind=api_changed``
  so the supervisor surfaces the institution-side outage to the
  operator without flapping the adapter's own backoff loop. The check
  is best-effort: a failure inside ``/institutions/get_by_id`` itself
  classifies through the same 7-tuple as ``/transactions/sync``.

- Error classification routes the Plaid SDK exception hierarchy
  (``ApiException`` body's ``error_code`` parsed into one of the 7
  in-module exception types) through ``_PLAID_SDK_EXCEPTIONS`` and
  ``_ERROR_KIND_BY_TYPE`` into the ``AdapterErrorV1`` severity +
  ``error_kind`` taxonomy. ``ITEM_LOGIN_REQUIRED`` (the canonical
  re-auth signal per ``docs/reference/plaid/link-update-mode.md``) maps
  to ``severity=fatal`` + ``error_kind=auth_failed`` so the supervisor
  surfaces it for operator re-Link via the prompt-16 wizard instead of
  silently retrying.

PM-27: ``plaid_client_factory`` and ``webhook_verifier_factory`` are
constructor-injected provider boundaries. Deterministic fixtures inject fakes;
configured production activation must supply real factories.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ClassVar, Literal

from adminme.adapters.base import (
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)
from adminme.adapters.emission import write_adapter_event
from adminme.lib.adapters import InboundDataAdapter

logger = logging.getLogger(__name__)


PlaidClientFactory = Callable[[str, str, str], Any]
"""Factory taking ``(client_id, secret, item_access_token)`` and returning
a Plaid API client."""


_DEFAULT_BACKSTOP_POLL_INTERVAL_S = 24 * 60 * 60
"""Provider-watch recovery cadence for webhook gaps. Tests override the
interval to drive the watcher on a sub-second budget."""

WebhookVerifierFactory = Callable[[str], Callable[[dict[str, Any]], bool]]
"""Factory taking the configured ``webhook_route`` and returning a
verifier callable that validates Plaid webhook JWTs per the Plaid
webhook-verification protocol. Configured production activation supplies a
verifier that fetches Plaid's ``/webhook_verification_key/get`` JWKs."""


_OP_REF_PATTERN_PREFIX = "op://"

# [§L1 cursor discipline]: cursor_advanced fires at 100 accumulated
# transactions OR 5 minutes since the prior advance — milestone-only,
# never per cycle. Mirrors the value the 3 cross-cutting schemas were
# designed for per adminme/events/schemas/adapters.py module docstring.
_CURSOR_ADVANCE_EVENT_THRESHOLD = 100
_CURSOR_ADVANCE_TIME_THRESHOLD = timedelta(minutes=5)
_CURSOR_KIND = "transactions_cursor"


# [Plaid SDK error taxonomy] — modeled on the
# ``compose_morning_digest`` 7-tuple discipline per
# ``packs/pipelines/reminder_dispatch/handler.py`` ``_SKILL_FAILURE_EXCEPTIONS``.
# Production wraps ``plaid.ApiException`` and classifies by
# ``response.body["error_code"]`` into one of these subclasses; Phase A
# tests instantiate them directly so the defensive-catch path is
# exercisable without a live Plaid SDK dep.
class PlaidSDKError(Exception):
    """Base for Plaid-SDK-style errors raised inside the adapter."""


class TransientError(PlaidSDKError):
    """Catch-all transient failure (e.g. ``INSTITUTION_NOT_RESPONDING``)."""


class RateLimitError(PlaidSDKError):
    """Rate-limit exceeded (e.g. ``RATE_LIMIT_EXCEEDED`` / ``TRANSACTIONS_SYNC_LIMIT``)."""


class NetworkError(PlaidSDKError):
    """Network-layer failure reaching the Plaid endpoint."""


class APIChangedError(PlaidSDKError):
    """Response shape changed in a way the adapter cannot parse."""


class QuotaExceededError(PlaidSDKError):
    """Plaid account quota or plan limit exceeded."""


class AuthError(PlaidSDKError):
    """Authentication failure (e.g. ``ITEM_LOGIN_REQUIRED`` surfacing as
    an SDK exception rather than as a webhook). Maps to ``severity=fatal``."""


class UnknownError(PlaidSDKError):
    """Any other Plaid SDK exception that doesn't match a more specific bucket."""


_PLAID_SDK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TransientError,
    RateLimitError,
    NetworkError,
    APIChangedError,
    QuotaExceededError,
    AuthError,
    UnknownError,
)
"""7-tuple of Plaid-SDK-style exception classes the adapter catches
defensively around ``/transactions/sync``. Order doesn't matter — Python
``except`` matches by class, not order."""


_ERROR_KIND_BY_TYPE: dict[type[BaseException], tuple[str, str]] = {
    TransientError: ("transient", "unknown"),
    RateLimitError: ("transient", "rate_limited"),
    NetworkError: ("transient", "network"),
    APIChangedError: ("transient", "api_changed"),
    QuotaExceededError: ("transient", "quota_exceeded"),
    AuthError: ("fatal", "auth_failed"),
    UnknownError: ("transient", "unknown"),
}
"""Map of Plaid SDK exception class → ``(severity, error_kind)`` for
``AdapterErrorV1`` emission. ``AuthError`` is the only ``fatal`` entry
because re-auth requires operator action via the prompt-16 wizard —
retrying without re-Linking will keep failing forever."""


def _noop_plaid_client_factory(
    _client_id: str, _secret: str, _access_token: str
) -> Any:
    """PM-27 default. Returns ``None`` so any attempt to call Plaid via
    the adapter without a real factory hits an AttributeError loudly
    instead of silently no-oping."""
    return None


def _noop_webhook_verifier_factory(
    _route: str,
) -> Callable[[dict[str, Any]], bool]:
    """PM-27 default. Returns a verifier that accepts every payload —
    fine for unit tests that never exercise webhook signature paths
    (those tests inject a strict verifier explicitly)."""

    def _verify(_payload: dict[str, Any]) -> bool:
        return True

    return _verify


class PlaidAdapter(InboundDataAdapter):
    """L1 standalone adapter for the Plaid financial-data system.

    See module docstring for the broader contract. This class satisfies
    the ``adminme.adapters.base.Adapter`` runtime Protocol; the
    supervisor at ``adminme.daemons.adapter_supervisor`` discovers it via
    a loader callable that reads ``config/channels.yaml``.

    Retrofitted onto :class:`InboundDataAdapter` per prompt 11r-b (Cohort
    C retrofit; ``docs/08-architecture-amendment-adapter-rebase.md``
    §3.3). The inheritance flip + the ``capabilities`` manifest block +
    the ``family`` / ``capabilities`` ClassVars close the
    ``advisory_pending_retrofit`` finding the 11-β validator surfaces on
    ``base_class_conformance``. Cat-C has NO write surface — the ABC's
    abstract methods are exactly ``start`` / ``stop`` / ``status`` which
    Plaid already implements, so no stub methods are added. Existing
    ingestion logic (``webhook_handler``, ``_sync_transactions``,
    ``_fetch_institution_health``, the ``PlaidSDKError`` hierarchy) is
    preserved byte-for-byte. [§6 invariant 10] sensitivity_floor override
    to ``"privileged"`` is legal (subclasses may RAISE the ABC default,
    never lower it).
    """

    # [§6 invariant 10, D11]: sensitivity floor hardcoded at the class
    # level; financial adapters CANNOT be downgraded via configuration.
    name: ClassVar[str] = "financial-plaid"
    family: ClassVar[Literal["financial", "iot", "telemetry"]] = "financial"
    version: ClassVar[str] = "1.0.0"
    sensitivity_floor: ClassVar[SensitivityFloor] = "privileged"

    # [memo 05 §3.2, PM-35]: class-level mirror of the manifest's
    # ``capabilities`` block for runtime introspection without re-parsing
    # ``pack.yaml``. Plaid emits one event family today
    # (``money_flow.plaid_synced``); the manifest's capabilities block in
    # ``pack.yaml`` is the source of truth and this ClassVar mirrors it.
    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-C",
            "runtime": "central",
            "event_families": ["money_flow.plaid_synced"],
        }
    ]

    def __init__(
        self,
        *,
        plaid_client_factory: PlaidClientFactory = _noop_plaid_client_factory,
        webhook_verifier_factory: WebhookVerifierFactory = _noop_webhook_verifier_factory,
    ) -> None:
        self._plaid_client_factory = plaid_client_factory
        self._webhook_verifier_factory = webhook_verifier_factory
        self._status = AdapterStatus()
        self._stop_event: asyncio.Event | None = None
        self._plaid_client: Any | None = None
        self._webhook_verifier: Callable[[dict[str, Any]], bool] | None = None
        self._ctx: AdapterContext | None = None
        # Cursor persistence — keyed by (member_id, plaid_item_id) per
        # BUILD.md PLAID DETAILED SPEC §"Sync cursor". In-memory in
        # Phase A; commit 4 (institution health) does not migrate this
        # to disk either — durable cursor storage lands when the
        # ``money_flow`` projection consumes these events.
        self._cursors: dict[tuple[str, str], str] = {}
        # Milestone state for adapter.cursor_advanced emission.
        self._events_since_last_cursor_advance: int = 0
        self._last_cursor_advance_at: datetime | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self, *, ctx: AdapterContext) -> None:
        """Resolve credentials, instantiate the Plaid client, emit
        ``adapter.authenticated``, register the webhook route, and block
        on the stop event so the supervisor's task stays alive until
        ``stop()`` is called.

        The daily backstop-poll body lands in commit 4; in commit 3 the
        start path's job is to capture ``ctx`` for the webhook + sync
        handlers (which are invoked externally by the supervisor's
        route mount) and produce the ``adapter.authenticated`` audit
        trail."""
        config = ctx.config

        # [§6 invariant 10, D11]: re-assert the class-level sensitivity
        # floor against the resolved config. JSON-schema rejects most
        # malformed inputs at load time; this is the defense-in-depth
        # runtime check the constitution requires.
        configured_floor = config.get("sensitivity_floor", "privileged")
        if configured_floor != "privileged":
            raise PlaidAdapterError(
                f"sensitivity_floor must be 'privileged' for the Plaid adapter; "
                f"got {configured_floor!r}. [§6 invariant 10, D11]"
            )

        item_access_token_ref = _require_op_ref(config, "item_access_token_ref")
        client_id_ref = _require_op_ref(config, "plaid_client_id_ref")
        secret_ref = _require_op_ref(config, "plaid_secret_ref")
        webhook_route = _require_webhook_route(config)

        self._plaid_client = self._plaid_client_factory(
            client_id_ref, secret_ref, item_access_token_ref
        )
        self._webhook_verifier = self._webhook_verifier_factory(webhook_route)
        self._ctx = ctx
        self._last_cursor_advance_at = _to_datetime(ctx.now_factory())

        self._status.state = "running"
        self._status.started_at = ctx.now_factory()

        await self._emit_authenticated(ctx, item_access_token_ref)

        # One institution-health probe at startup so the operator sees a
        # current DEGRADED / DOWN status in their inbox without waiting
        # for the first backstop tick. The probe is best-effort and
        # routes any SDK exception through the same 7-tuple defensive
        # catch as ``/transactions/sync``.
        institution_id = str(ctx.config.get("institution_id", ""))
        if institution_id:
            await self._fetch_institution_health(institution_id)

        # Daily backstop poll covers silent webhook drops (Plaid has
        # documented cases of ``SYNC_UPDATES_AVAILABLE`` getting lost in
        # transit; ``docs/reference/plaid/_index.md`` recommends a 24-hour
        # fallback poll). This provider-watcher loop is the recovery path;
        # it is not AdministrateMe product scheduling. The interval follows
        # ``BUILD.md PLAID DETAILED SPEC §"Sync cursor"``.
        interval_s = float(
            ctx.config.get(
                "backstop_poll_interval_s", _DEFAULT_BACKSTOP_POLL_INTERVAL_S
            )
        )
        plaid_item_id = str(ctx.config.get("plaid_item_id", ""))

        self._stop_event = asyncio.Event()
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval_s)
                    break
                except asyncio.TimeoutError:
                    if plaid_item_id:
                        await self._sync_transactions(
                            plaid_item_id, webhook_triggered=False
                        )
                    if institution_id:
                        await self._fetch_institution_health(institution_id)
        except asyncio.CancelledError:
            self._status.state = "stopped"
            raise
        self._status.state = "stopped"

    async def stop(self) -> None:
        """Release the stop event so ``start()``'s bare wait returns.
        Commit 4 will also cancel pending sync tasks and close the HTTP
        session."""
        if self._stop_event is not None and not self._stop_event.is_set():
            self._stop_event.set()
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    # ------------------------------------------------------------------
    # webhook dispatch
    # ------------------------------------------------------------------
    async def webhook_handler(self, payload: dict[str, Any]) -> None:
        """Verify the Plaid webhook signature, then dispatch by
        ``webhook_code``:

        - ``SYNC_UPDATES_AVAILABLE`` → call ``/transactions/sync`` via
          ``_sync_transactions(item_id, webhook_triggered=True)``.
        - ``ITEM_LOGIN_REQUIRED`` → emit ``adapter.error`` at
          ``severity=fatal``, ``error_kind=auth_failed`` so the supervisor
          surfaces it for operator re-Link per
          ``docs/reference/plaid/link-update-mode.md``.
        - Any other code (e.g. ``DEFAULT_UPDATE``, ``LOGIN_REPAIRED``):
          logged + ignored. Adding behavior for these codes is a future-
          prompt deliverable; the receiver must not crash on them today.
        """
        if self._webhook_verifier is None:
            logger.warning("plaid adapter: webhook arrived before start(); dropping")
            return
        if not self._webhook_verifier(payload):
            logger.warning(
                "plaid adapter: webhook signature verification failed; dropping"
            )
            return

        webhook_code = payload.get("webhook_code")
        item_id = payload.get("item_id") or ""

        if webhook_code == "SYNC_UPDATES_AVAILABLE":
            await self._sync_transactions(item_id, webhook_triggered=True)
        elif webhook_code == "ITEM_LOGIN_REQUIRED":
            await self._emit_adapter_error(
                severity="fatal",
                error_kind="auth_failed",
                message=(
                    f"plaid item {item_id!r} requires re-Link via update mode "
                    f"(ITEM_LOGIN_REQUIRED)"
                ),
                retry_after_s=None,
            )
        else:
            logger.info(
                "plaid adapter: ignoring webhook_code=%r for item=%r",
                webhook_code,
                item_id,
            )

    # ------------------------------------------------------------------
    # transactions/sync
    # ------------------------------------------------------------------
    async def _sync_transactions(
        self, item_id: str, *, webhook_triggered: bool = False
    ) -> None:
        """Paginated ``/transactions/sync`` cycle. Accumulates added /
        modified / removed across pages, persists the post-pagination
        ``next_cursor`` for the ``(member_id, plaid_item_id)`` tuple,
        emits one ``money_flow.plaid_synced`` SUMMARY event per cycle,
        and emits ``adapter.cursor_advanced`` on milestone.

        Catches the 7-tuple of Plaid SDK exception classes and maps each
        to the ``(severity, error_kind)`` pair the
        ``AdapterErrorV1`` schema constrains. Any non-SDK exception is
        re-raised so the supervisor's general-purpose backoff loop sees
        it.
        """
        ctx = self._ctx
        if ctx is None or self._plaid_client is None:
            logger.warning(
                "plaid adapter: _sync_transactions called before start(); skipping"
            )
            return

        member_id = str(ctx.config.get("member_id", ""))
        plaid_item_id = item_id or str(ctx.config.get("plaid_item_id", ""))
        institution_id = str(ctx.config.get("institution_id", ""))
        cursor_key = (member_id, plaid_item_id)
        cursor = self._cursors.get(cursor_key, "")
        access_token_ref = str(ctx.config.get("item_access_token_ref", ""))

        added: list[Any] = []
        modified: list[Any] = []
        removed: list[Any] = []
        accounts_seen: set[str] = set()
        institution_name = ""
        next_cursor = cursor

        try:
            while True:
                response = self._plaid_client.transactions_sync(
                    access_token=access_token_ref,
                    cursor=next_cursor,
                )
                page = _coerce_sync_response(response)
                added.extend(page.get("added", []))
                modified.extend(page.get("modified", []))
                removed.extend(page.get("removed", []))
                for acct in page.get("accounts", []):
                    acct_id = acct.get("account_id") if isinstance(acct, dict) else None
                    if acct_id:
                        accounts_seen.add(str(acct_id))
                item = page.get("item")
                if isinstance(item, dict):
                    institution_name = str(
                        item.get("institution_name") or institution_name
                    )
                next_cursor = str(page.get("next_cursor") or next_cursor)
                if not page.get("has_more", False):
                    break
        except _PLAID_SDK_EXCEPTIONS as exc:
            severity, error_kind = _ERROR_KIND_BY_TYPE.get(
                type(exc), ("transient", "unknown")
            )
            await self._emit_adapter_error(
                severity=severity,
                error_kind=error_kind,
                message=f"{type(exc).__name__}: {exc}",
                retry_after_s=_retry_after_for(type(exc)),
            )
            return

        self._cursors[cursor_key] = next_cursor

        total_events = len(added) + len(modified) + len(removed)
        self._events_since_last_cursor_advance += total_events

        await self._emit_money_flow_plaid_synced(
            member_id=member_id,
            plaid_item_id=plaid_item_id,
            institution_id=institution_id,
            institution_name=institution_name or institution_id,
            sync_cursor=next_cursor,
            transactions_added=len(added),
            transactions_modified=len(modified),
            transactions_removed=len(removed),
            accounts_modified_count=len(accounts_seen),
            webhook_triggered=webhook_triggered,
        )

        await self._maybe_emit_cursor_advanced(next_cursor)

    # ------------------------------------------------------------------
    # institution health
    # ------------------------------------------------------------------
    async def _fetch_institution_health(self, institution_id: str) -> None:
        """Query Plaid's ``/institutions/get_by_id`` with
        ``options.include_status=True`` per
        ``docs/reference/plaid/institutions-api.md`` §"Status object
        structure"; for each sub-product (``item_logins``,
        ``transactions_updates``, ``auth``, ``identity``,
        ``investments_updates``, ``liabilities_updates``,
        ``liabilities``, ``investments``) whose ``status`` is not
        ``HEALTHY``, emit one ``adapter.error`` at
        ``severity=transient`` ``error_kind=api_changed`` so the
        supervisor surfaces the degradation without flapping the
        adapter's own backoff loop.

        Defensively catches the 7-tuple of Plaid SDK exceptions so an
        institution-status outage cannot crash the start() path; a
        failure inside this probe classifies through the same
        ``(severity, error_kind)`` map as ``/transactions/sync`` and
        the adapter keeps running."""
        client = self._plaid_client
        if client is None or self._ctx is None:
            return
        getter = getattr(client, "institutions_get_by_id", None)
        if not callable(getter):
            return
        try:
            response = getter(
                institution_id=institution_id,
                country_codes=["US"],
                options={"include_status": True},
            )
        except _PLAID_SDK_EXCEPTIONS as exc:
            severity, error_kind = _ERROR_KIND_BY_TYPE.get(
                type(exc), ("transient", "unknown")
            )
            await self._emit_adapter_error(
                severity=severity,
                error_kind=error_kind,
                message=(
                    f"institutions_get_by_id({institution_id!r}) failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                retry_after_s=_retry_after_for(type(exc)),
            )
            return

        data = _coerce_sync_response(response)
        institution = data.get("institution")
        if not isinstance(institution, dict):
            return
        status = institution.get("status")
        if not isinstance(status, dict):
            return
        institution_name = str(institution.get("name") or institution_id)
        for product, detail in status.items():
            if not isinstance(detail, dict):
                continue
            product_status = detail.get("status")
            if product_status in (None, "HEALTHY"):
                continue
            await self._emit_adapter_error(
                severity="transient",
                error_kind="api_changed",
                message=(
                    f"plaid institution {institution_name!r} ({institution_id}) "
                    f"product {product!r} status={product_status!r}"
                ),
                retry_after_s=None,
            )

    # ------------------------------------------------------------------
    # internals — emit helpers
    # ------------------------------------------------------------------
    async def _emit_authenticated(
        self, ctx: AdapterContext, token_storage_ref: str
    ) -> None:
        """Write an ``adapter.authenticated`` event through the context sink."""
        payload = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "token_storage_ref": token_storage_ref,
            "authenticated_at": _format_iso(ctx.now_factory()),
            "scopes": ["transactions:read", "institutions:read"],
        }
        await self._append(ctx, "adapter.authenticated", payload)

    async def _emit_adapter_error(
        self,
        *,
        severity: str,
        error_kind: str,
        message: str,
        retry_after_s: int | None,
    ) -> None:
        """Append an ``adapter.error`` event. The retry-after value
        guides the supervisor's backoff; ``None`` means use the
        supervisor's default schedule."""
        ctx = self._ctx
        if ctx is None:
            return
        payload: dict[str, Any] = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "severity": severity,
            "error_kind": error_kind,
            "error_message": message,
            "retry_after_s": retry_after_s,
            "occurred_at": _format_iso(ctx.now_factory()),
        }
        self._status.last_error = payload
        await self._append(ctx, "adapter.error", payload)

    async def _emit_money_flow_plaid_synced(
        self,
        *,
        member_id: str,
        plaid_item_id: str,
        institution_id: str,
        institution_name: str,
        sync_cursor: str,
        transactions_added: int,
        transactions_modified: int,
        transactions_removed: int,
        accounts_modified_count: int,
        webhook_triggered: bool,
    ) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        payload = {
            "member_id": member_id,
            "plaid_item_id": plaid_item_id,
            "institution_id": institution_id,
            "institution_name": institution_name,
            "sync_cursor": sync_cursor,
            "transactions_added": transactions_added,
            "transactions_modified": transactions_modified,
            "transactions_removed": transactions_removed,
            "accounts_modified_count": accounts_modified_count,
            "synced_at": _format_iso(ctx.now_factory()),
            "webhook_triggered": webhook_triggered,
        }
        await self._append(ctx, "money_flow.plaid_synced", payload)

    async def _maybe_emit_cursor_advanced(self, cursor_value: str) -> None:
        """Emit ``adapter.cursor_advanced`` if the event threshold or
        the time threshold has been crossed. Resets the running tallies
        when it fires. Per the cross-cutting milestone discipline:
        emitting per-cycle would swamp downstream consumers; milestone-
        only keeps the audit signal load-bearing instead of noisy."""
        ctx = self._ctx
        if ctx is None:
            return
        now = _to_datetime(ctx.now_factory())
        time_elapsed_ok = (
            self._last_cursor_advance_at is None
            or (now - self._last_cursor_advance_at) >= _CURSOR_ADVANCE_TIME_THRESHOLD
        )
        events_ok = (
            self._events_since_last_cursor_advance >= _CURSOR_ADVANCE_EVENT_THRESHOLD
        )
        if not (events_ok or time_elapsed_ok):
            return
        payload = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "cursor_kind": _CURSOR_KIND,
            "cursor_value": cursor_value,
            "events_since_last": self._events_since_last_cursor_advance,
            "advanced_at": _format_iso(now),
        }
        await self._append(ctx, "adapter.cursor_advanced", payload)
        self._events_since_last_cursor_advance = 0
        self._last_cursor_advance_at = now
        self._status.last_cursor_advance_at = now

    async def _append(
        self, ctx: AdapterContext, event_type: str, payload: dict[str, Any]
    ) -> None:
        """Best-effort write through the typed event-sink contract.

        A failed emit must not crash the adapter; the structured adapter log
        remains the diagnostic path when the event store itself is down.
        """
        try:
            await write_adapter_event(
                ctx,
                adapter_name=self.name,
                sensitivity_floor=self.sensitivity_floor,
                config=ctx.config,
                event_type=event_type,
                payload=payload,
            )
        except Exception:  # noqa: BLE001
            logger.exception("plaid adapter failed to emit %s", event_type)


class PlaidAdapterError(RuntimeError):
    """Raised when the Plaid adapter rejects a configuration or
    encounters an unrecoverable runtime condition during ``start()``.
    The supervisor catches this on the same path it catches any other
    adapter exception (see ``_run_adapter``)."""


def _require_op_ref(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.startswith(_OP_REF_PATTERN_PREFIX):
        raise PlaidAdapterError(
            f"config field {key!r} must be a 1Password 'op://...' reference; "
            f"got {value!r}. [§L1 token-storage discipline]"
        )
    return value


def _require_webhook_route(config: dict[str, Any]) -> str:
    value = config.get("webhook_route")
    if not isinstance(value, str) or not value.startswith("/"):
        raise PlaidAdapterError(
            f"config field 'webhook_route' must be an absolute HTTP path "
            f"starting with '/'; got {value!r}."
        )
    return value


def _coerce_sync_response(response: Any) -> dict[str, Any]:
    """Plaid's ``transactions_sync`` returns a SDK model object whose
    ``to_dict()`` matches the openapi.yaml schema; tests pass a plain
    ``dict``. Accept either."""
    if isinstance(response, dict):
        return response
    to_dict = getattr(response, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, dict):
            return result
    raise APIChangedError(
        f"unexpected transactions_sync response shape: {type(response).__name__}"
    )


def _retry_after_for(exc_type: type[BaseException]) -> int | None:
    """Map Plaid SDK error class → retry_after_s hint for the supervisor's
    backoff loop. ``None`` defers to the supervisor's default."""
    if exc_type is RateLimitError:
        return 60
    if exc_type is QuotaExceededError:
        return 3600
    return None


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"cannot coerce {value!r} to datetime")


def _format_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


__all__ = [
    "PlaidAdapter",
    "PlaidAdapterError",
    "PlaidClientFactory",
    "WebhookVerifierFactory",
    "PlaidSDKError",
    "TransientError",
    "RateLimitError",
    "NetworkError",
    "APIChangedError",
    "QuotaExceededError",
    "AuthError",
    "UnknownError",
]
