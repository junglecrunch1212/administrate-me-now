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
  drops. **Sync + webhook bodies arrive in commit 3** — this commit
  (commit 2) only ships the auth path and stub raises NotImplementedError
  on those entrypoints so the Protocol surface is honest at import time.

PM-27: ``plaid_client_factory`` and ``webhook_verifier_factory`` are
constructor-injected callables with no-op defaults so Phase A tests can
substitute fakes without a live Plaid sandbox; production wires the real
factories at supervisor startup.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from adminme.adapters.base import (
    Adapter,
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)

logger = logging.getLogger(__name__)


PlaidClientFactory = Callable[[str, str, str], Any]
"""Factory taking ``(client_id, secret, item_access_token)`` and returning
a Plaid API client. Defaults to a no-op stub for tests per PM-27."""

WebhookVerifierFactory = Callable[[str], Callable[[dict[str, Any]], bool]]
"""Factory taking the configured ``webhook_route`` and returning a
verifier callable that validates Plaid webhook JWTs per the Plaid
webhook-verification protocol. Defaults to a no-op pass-through for
tests; production wires the real verifier that fetches Plaid's
``/webhook_verification_key/get`` JWKs."""


_OP_REF_PATTERN_PREFIX = "op://"


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


class PlaidAdapter:
    """L1 standalone adapter for the Plaid financial-data system.

    See module docstring for the broader contract. This class satisfies
    the ``adminme.adapters.base.Adapter`` runtime Protocol; the
    supervisor at ``adminme.daemons.adapter_supervisor`` discovers it via
    a loader callable that reads ``config/channels.yaml``.
    """

    # [§6 invariant 10, D11]: sensitivity floor hardcoded at the class
    # level; financial adapters CANNOT be downgraded via configuration.
    name: str = "financial-plaid"
    family: str = "financial"
    version: str = "1.0.0"
    sensitivity_floor: SensitivityFloor = "privileged"

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

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self, *, ctx: AdapterContext) -> None:
        """Resolve credentials, instantiate the Plaid client, emit
        ``adapter.authenticated``, register the webhook route, and block
        on the stop event so the supervisor's task stays alive until
        ``stop()`` is called.

        The polling-loop + webhook-dispatch bodies land in commit 3; in
        commit 2 the start path's job is to prove auth wiring is correct
        and produce the ``adapter.authenticated`` audit trail."""
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

        self._status.state = "running"
        self._status.started_at = ctx.now_factory()

        self._emit_authenticated(ctx, item_access_token_ref)

        # Block until stop() releases the event. Commit 3 replaces this
        # bare wait with the backstop poll loop + webhook receiver glue;
        # for commit 2 the auth path is the entire body and we let the
        # supervisor decide when to cancel.
        self._stop_event = asyncio.Event()
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            self._status.state = "stopped"
            raise
        self._status.state = "stopped"

    async def stop(self) -> None:
        """Release the stop event; commit 3 will also cancel pending
        sync tasks and close the HTTP session."""
        if self._stop_event is not None and not self._stop_event.is_set():
            self._stop_event.set()
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    # ------------------------------------------------------------------
    # commit-3 stubs — raising NotImplementedError keeps the Protocol
    # surface honest at import time. Tests in commit 3 will replace
    # these stub asserts with the real bodies + fixture-driven cases.
    # ------------------------------------------------------------------
    async def webhook_handler(self, payload: dict[str, Any]) -> None:
        """Handle inbound Plaid webhooks (``SYNC_UPDATES_AVAILABLE``,
        ``ITEM_LOGIN_REQUIRED``, …). Body lands in commit 3."""
        raise NotImplementedError(
            "webhook_handler is shipped in phase 11b commit 3; commit 2 "
            "only wires the auth path."
        )

    async def _sync_transactions(self, item_id: str) -> None:
        """Call ``/transactions/sync`` with the persisted cursor; emit
        ``money_flow.plaid_synced`` per cycle + ``adapter.cursor_advanced``
        at milestones. Body lands in commit 3."""
        raise NotImplementedError(
            "_sync_transactions is shipped in phase 11b commit 3; commit 2 "
            "only wires the auth path."
        )

    async def _fetch_institution_health(self, institution_id: str) -> None:
        """Query ``/institutions/status/get`` and emit
        ``adapter.error`` severity ``transient`` on degradation. Body
        lands in commit 4."""
        raise NotImplementedError(
            "_fetch_institution_health is shipped in phase 11b commit 4; "
            "commit 2 only wires the auth path."
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _emit_authenticated(
        self, ctx: AdapterContext, token_storage_ref: str
    ) -> None:
        """Append an ``adapter.authenticated`` event via the context's
        event_log. The supervisor's pattern (best-effort + tolerant of
        either ``append_sync`` or ``append``) is mirrored here."""
        appender = getattr(ctx.event_log, "append_sync", None) or getattr(
            ctx.event_log, "append", None
        )
        if appender is None:
            return
        now = ctx.now_factory()
        if isinstance(now, datetime):
            authenticated_at = now.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        else:
            authenticated_at = str(now)
        payload = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "token_storage_ref": token_storage_ref,
            "authenticated_at": authenticated_at,
            "scopes": ["transactions:read", "institutions:read"],
        }
        try:
            appender(
                type="adapter.authenticated",
                schema_version=1,
                payload=payload,
                correlation_id=ctx.correlation_id_factory(),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "plaid adapter failed to emit adapter.authenticated"
            )


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


__all__ = [
    "PlaidAdapter",
    "PlaidAdapterError",
    "PlaidClientFactory",
    "WebhookVerifierFactory",
]
