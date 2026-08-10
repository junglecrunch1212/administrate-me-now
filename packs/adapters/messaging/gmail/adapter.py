"""Gmail adapter pack — L1 standalone adapter for messaging (Gmail v1 API).

Inherits :class:`adminme.lib.adapters.CommunicationAdapter` (Cat-A) per
``docs/08-architecture-amendment-adapter-rebase.md`` §3.3 (Cohort C
retrofit) and §3.4 (Cohort D step 1 / prompt 11a). Real Gmail outbound
(``users.messages.send`` + MIME builder) ships in prompt 11a c2; the
:meth:`send` body calls Gmail v1's ``users.messages.send`` endpoint
directly when the observation seam slots are unpopulated (Phase A),
and routes through :func:`adminme.lib.observation.outbound` when the
slots are populated by the supervisor (Phase B). D28 emits the additive
``messaging.sent@v2`` schema with exact member/account/workspace coordinates;
the existing v1 registration remains immutable for historical producers.

Per ADMINISTRATEME_BUILD.md §L1 (adapter contract), DECISIONS.md §D6
(no LLM SDK), §D7 (schemas at v1), §D8 (correlation_id propagation),
SYSTEM_INVARIANTS.md §6 invariant 10 (sensitivity-floor downgrade
forbidden), §6.20 (observation mode wraps outbound), §7.3 + §D17
(adapters never write projections), §8 invariant 3 (channel transport
is OpenClaw's territory — adapters emit events only).

Gmail API endpoints used (see ``docs/reference/google-gmail/gmail-v1.ts``):

  - ``users.getProfile``    — bootstrap cursor (gmail-v1.ts line 1082)
  - ``users.messages.list`` — bounded initial history acquisition
  - ``users.history.list``  — incremental sync (gmail-v1.ts line 2603)
  - ``users.messages.get``  — per-message fetch (gmail-v1.ts line 4219)
  - ``users.watch``         — push-notification setup (gmail-v1.ts line 1360; deferred)

Authentication: Google service-account JSON with domain-wide delegation.
The service-account JSON is resolved from a 1Password reference
(``op://<vault>/<item>/<field>``); the secret never lives in config
files or environment variables.
"""

# [§8.2, D6] No LLM SDK imports under packs/adapters/ — channel
# transport is OpenClaw's job; this module only emits typed events.
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import formatdate, getaddresses
from typing import Any, Callable, ClassVar, Literal

from adminme.adapters.base import (
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)
from adminme.adapters.gmail_backfill import GmailInitialBackfillPolicy
from adminme.adapters.emission import write_adapter_event
from adminme.adapters.credentials import resolve_json_credentials
from adminme.events.registry import EventValidationError
from adminme.lib.adapters import (
    CommunicationAdapter,
    InboundEnvelope,
    OutboundMessage,
    SendResult,
)
from adminme.lib.observation import outbound

_log = logging.getLogger(__name__)


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


# Ordering used by the sensitivity-floor downgrade check.
# [§6 invariant 10] privileged > normal; config may raise but not lower.
_FLOOR_RANK: dict[str, int] = {"normal": 0, "privileged": 1}


CredentialsResolver = Callable[[str], dict[str, Any]]
"""Resolves an ``op://`` reference to a service-account JSON dict.
Tests inject a stub; production calls the 1Password CLI."""


ServiceFactory = Callable[[dict[str, Any], list[str], str], Any]
"""Builds a Gmail API service object from credentials JSON + scopes +
subject email. Tests inject a fake; production uses ``google-auth`` /
``google-api-python-client`` (lazily imported)."""


def _default_credentials_resolver(reference: str) -> dict[str, Any]:
    return resolve_json_credentials(reference)


def _default_service_factory(
    credentials_json: dict[str, Any],
    scopes: list[str],
    subject_email: str,
) -> Any:
    """Build a Gmail API service. ``google-auth`` and
    ``google-api-python-client`` are imported lazily so tests can
    substitute a fake without the deps being installed in CI."""
    from googleapiclient.discovery import build

    if {"client_id", "client_secret", "refresh_token"} <= credentials_json.keys():
        from google.oauth2.credentials import Credentials as UserCredentials

        creds = UserCredentials(
            token=None,
            refresh_token=str(credentials_json["refresh_token"]),
            token_uri=str(
                credentials_json.get("token_uri")
                or "https://oauth2.googleapis.com/token"
            ),
            client_id=str(credentials_json["client_id"]),
            client_secret=str(credentials_json["client_secret"]),
            scopes=scopes,
        )
    else:
        from google.oauth2.service_account import Credentials as ServiceCredentials

        creds = ServiceCredentials.from_service_account_info(
            credentials_json, scopes=scopes
        )
        if subject_email:
            creds = creds.with_subject(subject_email)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailAdapter(CommunicationAdapter):
    """Gmail Cat-A standalone adapter (retrofitted onto CommunicationAdapter).

    Polls the Gmail history API (``users.history.list``) every 2 minutes
    per ``pack.yaml`` ``triggers.schedule`` and emits ``messaging.received``
    v3 per fetched message. Cursor-advance emission is milestone-only
    (every 100 messages OR every 5 minutes — see
    ``AdapterCursorAdvancedV1`` docstring) per anti-vision §"Quiet by
    default".

    Retrofitted onto :class:`CommunicationAdapter` per prompt 11r-a
    (Cohort C retrofit; ``docs/08-architecture-amendment-adapter-rebase.md``
    §3.3). The inheritance flip preserves the simple ``Adapter`` Protocol
    surface (``start`` / ``stop`` / ``status`` / ``name`` / ``family`` /
    ``version`` / ``sensitivity_floor``) and adds the Cat-A contract
    methods :meth:`send` + :meth:`_on_inbound`. Live write capability is
    account-negotiated: read-only OAuth remains the safe default, while an
    explicitly re-consented account may expose reply/read/archive operations.
    """

    pack_id: ClassVar[str] = "adapter:messaging-gmail-api"
    name: ClassVar[str] = "messaging-gmail-api"
    family: ClassVar[Literal["messaging"]] = "messaging"
    version: ClassVar[str] = "1.1.1"

    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-A",
            "runtime": "central",
            "event_families": ["messaging.received", "messaging.sent"],
        }
    ]
    """Class-level mirror of the manifest's ``capabilities`` block per
    memo 05 §3.2 (runtime introspection without re-parsing pack.yaml).
    This is the code ceiling. ``enable_writes`` and the granted OAuth scopes
    determine the lower per-account capability actually shown in Comms."""

    HARDCODED_FLOOR: ClassVar[SensitivityFloor] = "normal"
    """Class-level minimum sensitivity_floor. Config may RAISE the floor
    (e.g. to ``privileged`` for a law-practice Gmail) but cannot lower
    it. [§6 invariant 10]"""

    SCOPES: ClassVar[list[str]] = [GMAIL_READONLY_SCOPE]

    POLL_INTERVAL_S: ClassVar[int] = 120
    """In-process poll cadence. Matches ``pack.yaml`` ``triggers.schedule``
    (every 2 minutes). The supervisor only restarts on crash; it does NOT
    drive the poll itself."""

    CURSOR_MILESTONE_EVENTS: ClassVar[int] = 100
    CURSOR_MILESTONE_INTERVAL_S: ClassVar[int] = 300
    INITIAL_BACKFILL_DAYS: ClassVar[int] = 730
    INITIAL_BACKFILL_MAX_MESSAGES: ClassVar[int] = 1000
    INITIAL_BACKFILL_MAX_PAGES: ClassVar[int] = 100
    """Cursor-advance emission is gated on whichever milestone fires
    first — 100 events processed OR 5 minutes of wall clock. Avoids the
    anti-vision pattern of one ``adapter.cursor_advanced`` per message."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        credentials_resolver: CredentialsResolver | None = None,
        service_factory: ServiceFactory | None = None,
        max_poll_cycles: int | None = None,
        observation_session: Any = None,
        observation_manager: Any = None,
        observation_event_log: Any = None,
    ) -> None:
        self._config = dict(config)
        self._scopes = (
            [GMAIL_MODIFY_SCOPE, GMAIL_SEND_SCOPE]
            if bool(self._config.get("enable_writes"))
            else [GMAIL_READONLY_SCOPE]
        )
        self._credentials_resolver = (
            credentials_resolver or _default_credentials_resolver
        )
        self._service_factory = service_factory or _default_service_factory
        # [§6 invariant 10] resolve floor at construction; raise on downgrade.
        # The CommunicationAdapter ABC declares sensitivity_floor as a
        # ClassVar default; Gmail overrides per-instance to honor config-
        # raise semantics (config may raise the floor, never lower it).
        self.sensitivity_floor: SensitivityFloor = (  # type: ignore[misc]
            self._resolve_sensitivity_floor()
        )
        self._status = AdapterStatus()
        self._service: Any = None
        self._cursor: str | None = None
        self._cursor_id: str | None = None
        self._initial_backfill_complete = False
        self._lease_owner = f"gmail-{uuid.uuid4()}"
        self._events_since_advance: int = 0
        self._last_advance_at: datetime | None = None
        self._stop_requested: bool = False
        # Test seam: bound the loop. None = run forever (production); the
        # supervisor stops the task on its lifecycle events.
        self._max_poll_cycles: int | None = max_poll_cycles
        # ctx is captured at start() so :meth:`send` (a Cat-A contract
        # method the supervisor invokes without re-passing ctx) can emit
        # ``messaging.sent@v2`` / ``adapter.error@v1`` events through the
        # supervisor-bound event log. Mirrors the apple-eventkit pattern.
        self._ctx: AdapterContext | None = None
        # Observation-mode wiring slots for :meth:`send`. Phase A leaves
        # these unset (constructor default None); the Phase B prompt
        # extending the supervisor's adapter-loading path will populate
        # them via constructor injection so Gmail outbound routes through
        # :func:`adminme.lib.observation.outbound` per [§6.20]. When
        # unset, :meth:`send` calls Gmail v1 ``users.messages.send``
        # directly (memo 08 §3.4 Phase-A-conditional pattern, mirroring
        # 11r-c's :meth:`_apply_write_impl`).
        self._observation_session: Any = observation_session
        self._observation_manager: Any = observation_manager
        self._observation_event_log: Any = observation_event_log

    # ------------------------------------------------------------------
    # sensitivity-floor invariant [§6 invariant 10]
    # ------------------------------------------------------------------
    def _resolve_sensitivity_floor(self) -> SensitivityFloor:
        """Combine the hardcoded floor with any config override.

        Config may RAISE the floor (e.g., a Gmail adapter pointing at a
        law-practice account can set ``privileged``); it may NEVER
        lower it below the hardcoded class default.
        """
        cfg_value = self._config.get("sensitivity_floor", self.HARDCODED_FLOOR)
        if cfg_value not in _FLOOR_RANK:
            raise ValueError(
                f"sensitivity_floor must be 'normal' or 'privileged', got {cfg_value!r}"
            )
        if _FLOOR_RANK[cfg_value] < _FLOOR_RANK[self.HARDCODED_FLOOR]:
            raise ValueError(
                f"config sensitivity_floor={cfg_value!r} lower than "
                f"hardcoded floor {self.HARDCODED_FLOOR!r}; refusing to "
                "downgrade [SYSTEM_INVARIANTS.md §6 invariant 10]"
            )
        return cfg_value  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Adapter Protocol
    # ------------------------------------------------------------------
    async def start(self, *, ctx: AdapterContext) -> None:
        """Authenticate against Gmail via the configured service-account
        reference, emit ``adapter.authenticated``, then enter the
        polling loop until ``stop()`` is invoked (or ``max_poll_cycles``
        is exhausted, in tests)."""
        op_ref = self._config.get("oauth_credentials_ref") or self._config.get(
            "service_account_key_ref"
        )
        user_email = self._config.get("user_email")
        if not isinstance(op_ref, str) or not op_ref.startswith(
            ("op://", "keychain://")
        ):
            raise ValueError(
                "credential must be a 1Password reference or macOS Keychain reference"
            )
        if not isinstance(user_email, str) or "@" not in user_email:
            raise ValueError("user_email must be a valid email address")

        creds_json = self._credentials_resolver(op_ref)
        self._service = self._service_factory(creds_json, self._scopes, user_email)
        self._ctx = ctx
        self._cursor_id = str(
            getattr(self, "instance_name", None)
            or self._config.get("id")
            or ":".join(
                (
                    self.name,
                    str(self._config.get("member_id") or "unbound"),
                    str(self._config.get("user_email") or "gmail"),
                )
            )
        )
        if ctx.cursor_store is not None:
            self._cursor = ctx.cursor_store.get(self._cursor_id).provider_watermark
        self._status.started_at = ctx.now_factory()
        self._status.state = "running"
        self._last_advance_at = ctx.now_factory()
        # [D7] adapter.authenticated registered at v1 in events/schemas/adapters.py.
        # [D8] correlation_id minted by the supervisor-provided factory.
        await self._emit_authenticated(ctx=ctx, op_ref=op_ref)

        await self._poll_loop(ctx=ctx)

    async def _poll_loop(self, *, ctx: AdapterContext) -> None:
        """Drive the polling loop. Each cycle:

        - calls ``_run_poll_cycle`` to fetch + emit
        - catches expected SDK + network errors, emits ``adapter.error``
        - sleeps ``POLL_INTERVAL_S`` before the next cycle

        Fatal errors (auth) emit ``adapter.error`` AND stop the loop —
        the supervisor's restart-with-backoff will retry after a delay.
        Transient errors stay in the loop.
        """
        cycles = 0
        while not self._stop_requested:
            if self._max_poll_cycles is not None and cycles >= self._max_poll_cycles:
                return
            try:
                await self._run_poll_cycle(ctx=ctx)
            except BaseException as exc:  # noqa: BLE001 — classify & emit
                severity, error_kind, retry_after_s = self._classify_gmail_error(exc)
                await self._emit_error(
                    ctx=ctx,
                    severity=severity,
                    error_kind=error_kind,
                    error_message=str(exc) or type(exc).__name__,
                    retry_after_s=retry_after_s,
                )
                if ctx.cursor_store is not None and self._cursor_id is not None:
                    cursor = ctx.cursor_store.record_error(self._cursor_id)
                    self._status.extra["durable_cursor"] = cursor.public()
                if severity == "fatal" or error_kind == "api_changed":
                    self._status.state = "crashed"
                    return
                self._status.state = "backoff"
            cycles += 1
            try:
                await asyncio.sleep(self.POLL_INTERVAL_S)
            except asyncio.CancelledError:
                return

    async def _run_poll_cycle(self, *, ctx: AdapterContext) -> int:
        """Run a single poll cycle. Returns the number of
        ``messaging.received`` events emitted.

        On first cycle the adapter snapshots ``users.getProfile`` before a
        bounded, durable, idempotent acquisition of recent mailbox history.
        The snapshot remains the incremental cursor floor, so mail arriving
        during backfill is fetched by ``users.history.list`` afterward rather
        than falling into a bootstrap gap.
        """
        if self._service is None:
            raise RuntimeError("Gmail service not initialized; call start() first")
        store = ctx.cursor_store
        cursor_id = self._cursor_id or self.name
        claimed = store is None or store.claim(
            cursor_id,
            self._lease_owner,
            ttl_ms=max(30_000, self.POLL_INTERVAL_S * 2000),
        )
        if not claimed:
            self._status.state = "backoff"
            self._status.extra["cursor_lease"] = "held_by_another_runtime"
            return 0
        try:
            if store is not None:
                self._cursor = store.get(cursor_id).provider_watermark
            bootstrapped = False
            if self._cursor is None:
                self._cursor = self._bootstrap_cursor()
                bootstrapped = True
                if store is not None:
                    store.record_success(cursor_id, provider_watermark=self._cursor)
                    self._status.extra["durable_cursor"] = store.get(cursor_id).public()

            backfill_emitted, backfill_quarantined = await self._run_initial_backfill(
                ctx=ctx,
                store=store,
                cursor_id=cursor_id,
            )
            if bootstrapped:
                self._status.extra["last_poll"] = {
                    "emitted": backfill_emitted,
                    "quarantined": backfill_quarantined,
                    "provider_watermark": self._cursor,
                }
                # A record-level quarantine is a successful terminal disposition,
                # not a provider transport failure. Gmail may return a message id
                # from list/history and then 404 if that message is deleted before
                # the bounded get. Keep the adapter available after accounting for
                # that race instead of degrading the whole source.
                self._status.state = "running"
                await self._maybe_emit_cursor_advance(
                    ctx=ctx, cursor_value=str(self._cursor)
                )
                return backfill_emitted

            new_history_id, history_records = self._fetch_history(self._cursor)
            message_ids = self._extract_added_message_ids(history_records)
            emitted, quarantined = await self._ingest_message_ids(
                ctx=ctx,
                store=store,
                cursor_id=cursor_id,
                message_ids=message_ids,
                provider_watermark=new_history_id,
            )
            emitted += backfill_emitted
            quarantined += backfill_quarantined

            if new_history_id and new_history_id != self._cursor:
                self._cursor = new_history_id
            if store is not None:
                store.record_success(cursor_id, provider_watermark=self._cursor)
                self._status.extra["durable_cursor"] = store.get(cursor_id).public()
            self._status.extra["last_poll"] = {
                "emitted": emitted,
                "quarantined": quarantined,
                "provider_watermark": self._cursor,
            }
            self._status.state = "running"
            await self._maybe_emit_cursor_advance(ctx=ctx, cursor_value=self._cursor)
            return emitted
        finally:
            if store is not None:
                store.release(cursor_id, self._lease_owner)

    def _initial_backfill_settings(self) -> GmailInitialBackfillPolicy:
        days = self._config.get("initial_backfill_days", self.INITIAL_BACKFILL_DAYS)
        limit = self._config.get(
            "initial_backfill_max_messages", self.INITIAL_BACKFILL_MAX_MESSAGES
        )
        seed_queries = self._config.get("historical_seed_queries", [])
        seed_limit = self._config.get("historical_seed_max_messages_per_query", 100)
        related_participants = self._config.get(
            "historical_related_max_participants", 20
        )
        related_limit = self._config.get(
            "historical_related_messages_per_participant", 25
        )
        related_days = self._config.get("historical_related_context_days", 730)
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 3650:
            raise ValueError("initial_backfill_days must be an integer from 0 to 3650")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 0 <= limit <= 5000
        ):
            raise ValueError(
                "initial_backfill_max_messages must be an integer from 0 to 5000"
            )
        if not isinstance(seed_queries, list) or len(seed_queries) > 20:
            raise ValueError("historical_seed_queries must be a list of at most 20")
        normalized_queries: list[str] = []
        for value in seed_queries:
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value.strip()) > 500
                or any(character in value for character in ("\x00", "\r", "\n"))
            ):
                raise ValueError(
                    "historical_seed_queries entries must be 1-500 characters "
                    "without control newlines"
                )
            normalized_queries.append(value.strip())
        integer_bounds = {
            "historical_seed_max_messages_per_query": (seed_limit, 0, 500),
            "historical_related_max_participants": (
                related_participants,
                0,
                50,
            ),
            "historical_related_messages_per_participant": (
                related_limit,
                0,
                100,
            ),
            "historical_related_context_days": (related_days, 0, 3650),
        }
        for name, (value, minimum, maximum) in integer_bounds.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise ValueError(
                    f"{name} must be an integer from {minimum} to {maximum}"
                )
        return GmailInitialBackfillPolicy(
            recent_days=days,
            max_messages=limit,
            seed_queries=tuple(normalized_queries),
            seed_max_messages_per_query=seed_limit,
            related_max_participants=related_participants,
            related_messages_per_participant=related_limit,
            related_context_days=related_days,
        )

    @staticmethod
    def _backfill_cursor_id(
        cursor_id: str, *, policy: GmailInitialBackfillPolicy
    ) -> str:
        return f"{cursor_id}:initial-backfill:{policy.receipt_digest()}"

    async def _run_initial_backfill(
        self,
        *,
        ctx: AdapterContext,
        store: Any,
        cursor_id: str,
    ) -> tuple[int, int]:
        policy = self._initial_backfill_settings()
        if policy.max_messages == 0 or (
            policy.recent_days == 0 and not policy.seed_queries
        ):
            self._initial_backfill_complete = True
            self._status.extra["initial_backfill"] = {
                "contract": "gmail-initial-backfill-v2",
                "state": "disabled",
                "recent_days": policy.recent_days,
                "max_messages": policy.max_messages,
                "seed_query_count": len(policy.seed_queries),
            }
            return 0, 0
        backfill_id = self._backfill_cursor_id(cursor_id, policy=policy)
        if self._initial_backfill_complete or (
            store is not None
            and store.get(backfill_id).provider_watermark == "complete"
        ):
            self._initial_backfill_complete = True
            self._status.extra["initial_backfill"] = {
                "contract": "gmail-initial-backfill-v2",
                "state": "complete",
                "recent_days": policy.recent_days,
                "max_messages": policy.max_messages,
                "seed_query_count": len(policy.seed_queries),
                "durable_receipt_reused": True,
            }
            return 0, 0

        # ``googleapiclient`` is synchronous.  Initial acquisition can span many
        # bounded list/get calls, so running it on the central asyncio loop would
        # stall the HTTP health surface and every other adapter during startup.
        # Keep the deterministic acquisition algorithm intact, but execute its
        # blocking provider I/O in a worker thread.
        message_ids, prefetched, acquisition = await asyncio.to_thread(
            self._fetch_initial_backfill_ids, policy
        )
        emitted, quarantined = await self._ingest_message_ids(
            ctx=ctx,
            store=store,
            cursor_id=cursor_id,
            message_ids=message_ids,
            provider_watermark=self._cursor,
            prefetched_messages=prefetched,
        )
        if store is not None:
            store.record_success(backfill_id, provider_watermark="complete")
        self._initial_backfill_complete = True
        self._status.extra["initial_backfill"] = {
            "contract": "gmail-initial-backfill-v2",
            "state": "complete",
            "recent_days": policy.recent_days,
            "max_messages": policy.max_messages,
            "seed_query_count": len(policy.seed_queries),
            "discovered": len(message_ids),
            "emitted": emitted,
            "quarantined": quarantined,
            **acquisition,
        }
        return emitted, quarantined

    def _fetch_initial_backfill_ids(
        self, policy: GmailInitialBackfillPolicy
    ) -> tuple[list[str], dict[str, dict[str, Any]], dict[str, Any]]:
        """Acquire explicit historical seeds, their bounded context, then recency.

        Seed query text is member-private configuration and is never returned in
        status.  Priority order is intentional: an old explicitly reviewed
        matter cannot be crowded out by the newest generic messages.
        """
        if self._service is None:
            raise RuntimeError("Gmail service not initialized; call start() first")
        message_ids: list[str] = []
        prefetched: dict[str, dict[str, Any]] = {}
        seed_ids: list[str] = []
        related_ids: list[str] = []
        truncated = False

        for query in policy.seed_queries:
            if len(message_ids) >= policy.max_messages:
                truncated = True
                break
            ids, query_truncated = self._list_message_ids(
                query=query,
                limit=min(
                    policy.seed_max_messages_per_query,
                    policy.max_messages - len(message_ids),
                ),
            )
            truncated = truncated or query_truncated
            for message_id in ids:
                if message_id not in message_ids:
                    message_ids.append(message_id)
                    seed_ids.append(message_id)

        for message_id in seed_ids:
            try:
                prefetched[message_id] = self._fetch_message(message_id)
            except Exception as exc:
                _, error_kind, _ = self._classify_gmail_error(exc)
                if error_kind != "api_changed":
                    raise

        seed_ids = self._chronological_message_ids(seed_ids, prefetched)
        seed_messages = [
            prefetched[message_id]
            for message_id in seed_ids
            if message_id in prefetched
        ]
        participants, participants_truncated = self._historical_seed_participants(
            seed_messages,
            maximum=policy.related_max_participants,
        )
        truncated = truncated or participants_truncated
        date_clause = self._historical_related_date_clause(
            seed_messages, context_days=policy.related_context_days
        )
        for participant_index, participant in enumerate(participants):
            if len(message_ids) >= policy.max_messages:
                truncated = truncated or participant_index < len(participants)
                break
            if policy.related_messages_per_participant == 0:
                break
            ids, query_truncated = self._list_message_ids(
                query=(f'{date_clause} "{participant}"').strip(),
                limit=min(
                    policy.related_messages_per_participant,
                    policy.max_messages - len(message_ids),
                ),
            )
            truncated = truncated or query_truncated
            for message_id in ids:
                if message_id not in message_ids:
                    message_ids.append(message_id)
                    related_ids.append(message_id)

        recent_ids: list[str] = []
        if policy.recent_days and len(message_ids) < policy.max_messages:
            ids, recent_truncated = self._list_message_ids(
                query=f"newer_than:{policy.recent_days}d",
                limit=policy.max_messages - len(message_ids),
            )
            truncated = truncated or recent_truncated
            for message_id in ids:
                if message_id not in message_ids:
                    message_ids.append(message_id)
                    recent_ids.append(message_id)
        elif policy.recent_days and len(message_ids) >= policy.max_messages:
            truncated = True

        # Acquire every body before append, then place generic/relation context
        # ahead of the reviewed seed matters and order each group oldest-first.
        # Gmail search commonly returns newest-first; without this deterministic
        # batch order, the newest seed could be assessed before its older thread
        # facts existed in the event log and never receive a complete reassessment.
        for message_id in message_ids:
            if message_id in prefetched:
                continue
            try:
                prefetched[message_id] = self._fetch_message(message_id)
            except Exception as exc:
                _, error_kind, _ = self._classify_gmail_error(exc)
                if error_kind != "api_changed":
                    raise
        ordered_ids = [
            *self._chronological_message_ids(recent_ids, prefetched),
            *self._chronological_message_ids(related_ids, prefetched),
            *self._chronological_message_ids(seed_ids, prefetched),
        ]
        return (
            ordered_ids,
            prefetched,
            {
                "seed_discovered": len(seed_ids),
                "related_discovered": len(related_ids),
                "recent_discovered": len(recent_ids),
                "related_participant_count": len(participants),
                "related_participants_truncated": participants_truncated,
                "truncated": truncated,
            },
        )

    def _list_message_ids(self, *, query: str, limit: int) -> tuple[list[str], bool]:
        if self._service is None:
            raise RuntimeError("Gmail service not initialized; call start() first")
        if limit <= 0:
            return [], False
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        pages = 0
        truncated = False
        message_ids: list[str] = []
        while len(message_ids) < limit and pages < self.INITIAL_BACKFILL_MAX_PAGES:
            params: dict[str, Any] = {
                "userId": "me",
                "q": f"{query.strip()} -label:drafts".strip(),
                "includeSpamTrash": False,
                "maxResults": min(500, limit - len(message_ids)),
            }
            if page_token:
                params["pageToken"] = page_token
            response = self._service.users().messages().list(**params).execute()
            pages += 1
            for value in response.get("messages") or []:
                message_id = value.get("id") if isinstance(value, dict) else None
                if (
                    isinstance(message_id, str)
                    and message_id
                    and message_id not in message_ids
                ):
                    message_ids.append(message_id)
                    if len(message_ids) >= limit:
                        break
            page_token = response.get("nextPageToken")
            if not page_token:
                break
            if page_token in seen_page_tokens:
                truncated = True
                break
            seen_page_tokens.add(page_token)
        if page_token:
            truncated = True
        return message_ids, truncated

    def _historical_seed_participants(
        self,
        messages: Iterable[dict[str, Any]],
        *,
        maximum: int,
    ) -> tuple[tuple[str, ...], bool]:
        if maximum == 0:
            return (), False
        own_addresses = {
            address.casefold()
            for _, address in getaddresses([str(self._config.get("user_email") or "")])
            if address
        }
        result: list[str] = []
        for message in messages:
            payload = message.get("payload") or {}
            headers = self._headers_to_map(payload.get("headers"))
            participant_headers = [
                value
                for value in (
                    headers.get("from") or "",
                    headers.get("to") or "",
                    headers.get("cc") or "",
                    headers.get("bcc") or "",
                    headers.get("reply-to") or "",
                )
                if value
            ]
            for _, address in getaddresses(participant_headers):
                normalized = address.casefold()
                if (
                    normalized
                    and normalized not in own_addresses
                    and normalized not in result
                ):
                    if len(result) >= maximum:
                        return tuple(result), True
                    result.append(normalized)
        return tuple(result), False

    @classmethod
    def _chronological_message_ids(
        cls,
        message_ids: Iterable[str],
        messages: dict[str, dict[str, Any]],
    ) -> list[str]:
        indexed = list(enumerate(message_ids))
        return [
            message_id
            for _, message_id in sorted(
                indexed,
                key=lambda value: (
                    cls._internal_date_ms(messages.get(value[1])) is None,
                    cls._internal_date_ms(messages.get(value[1])) or 0,
                    value[0],
                ),
            )
        ]

    @staticmethod
    def _internal_date_ms(message: dict[str, Any] | None) -> int | None:
        raw_internal_date = (message or {}).get("internalDate")
        if isinstance(raw_internal_date, bool) or not isinstance(
            raw_internal_date, (str, int)
        ):
            return None
        try:
            return int(raw_internal_date)
        except ValueError:
            return None

    @staticmethod
    def _historical_related_date_clause(
        messages: Iterable[dict[str, Any]], *, context_days: int
    ) -> str:
        if context_days == 0:
            return ""
        dates: list[datetime] = []
        for message in messages:
            internal_date_ms = GmailAdapter._internal_date_ms(message)
            if internal_date_ms is None:
                continue
            try:
                dates.append(
                    datetime.fromtimestamp(
                        internal_date_ms / 1000,
                        tz=timezone.utc,
                    )
                )
            except (ValueError, OverflowError):
                continue
        if not dates:
            return ""
        after = (min(dates) - timedelta(days=context_days)).strftime("%Y/%m/%d")
        before = (max(dates) + timedelta(days=context_days + 1)).strftime("%Y/%m/%d")
        return f"after:{after} before:{before}"

    async def _ingest_message_ids(
        self,
        *,
        ctx: AdapterContext,
        store: Any,
        cursor_id: str,
        message_ids: list[str],
        provider_watermark: str | None,
        prefetched_messages: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[int, int]:
        emitted = 0
        quarantined = 0
        prefetched = prefetched_messages or {}
        for msg_id in message_ids:
            try:
                full_msg = prefetched.get(msg_id) or self._fetch_message(msg_id)
            except Exception as exc:
                _, error_kind, _ = self._classify_gmail_error(exc)
                if error_kind != "api_changed":
                    raise
                quarantined += 1
                fingerprint = hashlib.sha256(
                    f"gmail-message-id:{msg_id}".encode()
                ).hexdigest()
                quarantine_event_id = await self._emit_quarantine(
                    ctx=ctx,
                    external_id=msg_id,
                    fingerprint=fingerprint,
                    error=exc,
                    provider_watermark=provider_watermark,
                )
                if store is not None:
                    store.record_success(
                        cursor_id,
                        external_id=msg_id,
                        fingerprint=fingerprint,
                        event_id=quarantine_event_id,
                    )
                continue
            fingerprint = self._message_fingerprint(full_msg)
            if store is not None and store.dedupe_event(cursor_id, msg_id, fingerprint):
                continue
            try:
                event_id = await self._emit_message_observation(
                    ctx=ctx, message=full_msg, fingerprint=fingerprint
                )
                if store is not None:
                    store.record_success(
                        cursor_id,
                        external_id=msg_id,
                        fingerprint=fingerprint,
                        event_id=event_id,
                    )
                emitted += 1
                self._events_since_advance += 1
            except EventValidationError as exc:
                quarantined += 1
                quarantine_event_id = await self._emit_quarantine(
                    ctx=ctx,
                    external_id=msg_id,
                    fingerprint=fingerprint,
                    error=exc,
                    provider_watermark=provider_watermark,
                )
                if store is not None:
                    store.record_success(
                        cursor_id,
                        external_id=msg_id,
                        fingerprint=fingerprint,
                        event_id=quarantine_event_id,
                    )
        return emitted, quarantined

    async def stop(self) -> None:
        """Release the Gmail service handle. Idempotent."""
        self._stop_requested = True
        self._service = None
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    # ------------------------------------------------------------------
    # CommunicationAdapter contract — Cat-A surface
    # ------------------------------------------------------------------
    async def send(self, message: OutboundMessage) -> SendResult:
        """Send an outbound Gmail message via ``users.messages.send``.

        Builds an RFC 2822 MIME envelope from ``message`` via
        :meth:`_build_mime_message`, base64url-encodes it, and POSTs to
        the Gmail v1 ``users.messages.send`` endpoint. Routes through
        :func:`adminme.lib.observation.outbound` when the
        observation-seam slots are populated (Phase B); otherwise calls
        the Gmail service directly (Phase A — memo 08 §3.4
        Phase-A-conditional, mirroring 11r-c's
        :meth:`_apply_write_impl`). On success, emits one
        ``messaging.sent@v2`` event with field-mapping from Gmail
        semantics plus the exact Comms account coordinates; on
        observation-suppress, the same event is emitted with
        ``delivery_status="queued"`` and ``thread_id=None``. The immutable
        ``messaging.sent@v1`` registration remains available per [D7].

        Errors classify per :meth:`_classify_gmail_error` into the
        ``AdapterErrorV1`` taxonomy (4xx auth → fatal ``auth_failed``,
        5xx → transient ``network``, 429 → transient ``rate_limited``),
        emit one ``adapter.error@v1`` event, and re-raise so the
        supervisor's backoff handles retry. ``messaging.sent@v2`` is
        NOT emitted on the error path — the action did not, in fact,
        send.
        """
        # [§7.3, D17]: defense-in-depth input check; the Cat-A ABC's
        # OutboundMessage is frozen but does not validate non-emptiness.
        if not message.recipient:
            raise ValueError("OutboundMessage.recipient must be a non-empty string")
        if self._config.get("enable_writes") is False:
            raise PermissionError(
                "Gmail writes are disabled; reconnect with modify/send scopes"
            )
        ctx = self._ctx
        if self._service is None:
            raise RuntimeError("Gmail service not initialized; call start() first")

        mime_b64 = self._build_mime_message(message)
        service = self._service

        async def _gmail_api_send() -> dict[str, Any]:
            # Wrapped in a closure so :func:`outbound` can suppress it
            # under observation mode. ``execute()`` is sync; the
            # observation seam awaits the closure.
            request_body = {"raw": mime_b64}
            thread_id = message.metadata.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                request_body["threadId"] = thread_id
            return (
                service.users()
                .messages()
                .send(userId="me", body=request_body)
                .execute()
            )

        session = self._observation_session
        manager = self._observation_manager
        event_log = self._observation_event_log

        response: dict[str, Any] = {}
        suppressed = False
        try:
            if session is not None and manager is not None and event_log is not None:
                # [§6.20] Phase B: route through the observation seam so
                # ``observation.suppressed`` / ``external.sent`` are
                # emitted around the actual Gmail API call. The 11-β
                # validator's ``observation_mode_wrapping`` AST-walk
                # finds this call site.
                outbound_result = await outbound(
                    session,
                    "messaging.send",
                    {
                        "channel": message.channel,
                        "recipient": message.recipient,
                        "body": message.body,
                    },
                    _gmail_api_send,
                    manager=manager,
                    event_sink=event_log,
                    target_channel=message.channel,
                    target_identifier=message.recipient,
                )
                if outbound_result.suppressed:
                    suppressed = True
                else:
                    response = outbound_result.result or {}
            else:
                # Phase A: seams unset; direct Gmail API call. The
                # supervisor's adapter-loading path keeps the simple
                # ``Adapter`` Protocol surface unchanged per memo 08 §5.
                response = await _gmail_api_send()
        except BaseException as exc:  # noqa: BLE001 — classify & emit
            severity, error_kind, retry_after_s = self._classify_gmail_error(exc)
            if ctx is not None:
                await self._emit_error(
                    ctx=ctx,
                    severity=severity,
                    error_kind=error_kind,
                    error_message=str(exc) or type(exc).__name__,
                    retry_after_s=retry_after_s,
                )
            raise

        # D28 retains the delivery fields and adds the account-bound
        # ``messaging.sent@v2`` coordinates required by Comms.
        if ctx is not None:
            now_dt = ctx.now_factory()
        else:
            now_dt = datetime.now(timezone.utc)
        sent_at_iso = now_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        subject_meta = (
            message.metadata.get("subject")
            if isinstance(message.metadata, dict)
            else None
        )
        subject_value: str | None = str(subject_meta) if subject_meta else None
        thread_id_value: str | None = None if suppressed else response.get("threadId")
        delivery_status: Literal["queued", "sent"] = "queued" if suppressed else "sent"
        sent_payload: dict[str, Any] = {
            "source_channel": "gmail",
            "to_identifier": message.recipient,
            "thread_id": thread_id_value,
            "subject": subject_value,
            "body_text": message.body,  # FULL body — schema allows str | None
            "sent_at": sent_at_iso,
            "delivery_status": delivery_status,
            "member_id": str(self._config.get("member_id") or "unbound"),
            "provider": "gmail",
            "provider_account_id": str(self._config.get("user_email") or "gmail"),
            "workspace": str(self._config.get("workspace") or "personal"),
            "native_message_id": None if suppressed else response.get("id"),
            "in_reply_to_interaction_id": message.metadata.get("interaction_id"),
        }
        if ctx is not None:
            await self._append(
                ctx=ctx,
                event_type="messaging.sent",
                payload=sent_payload,
                schema_version=2,
            )

        external_id_value: str | None = None if suppressed else response.get("id")
        return SendResult(
            delivered=not suppressed,
            external_id=external_id_value,
            suppressed=suppressed,
        )

    def _build_mime_message(self, message: OutboundMessage) -> str:
        """Build an RFC 2822 MIME envelope from an
        :class:`OutboundMessage` and return its base64url-encoded form
        per the Gmail v1 ``users.messages.send`` contract
        (``body.raw`` field).

        Phase A supports ``text/plain`` only; HTML and attachments are
        deferred per prompt 11a §6. ``From`` is taken from the configured
        ``user_email``; ``To`` is ``message.recipient``; ``Subject`` is
        sourced from ``message.metadata["subject"]`` when present.
        """
        mime = MIMEText(message.body, "plain", "utf-8")
        sender = self._config.get("user_email", "")
        if sender:
            mime["From"] = str(sender)
        mime["To"] = message.recipient
        subject_meta = (
            message.metadata.get("subject")
            if isinstance(message.metadata, dict)
            else None
        )
        if subject_meta:
            mime["Subject"] = str(subject_meta)
        in_reply_to = message.metadata.get("in_reply_to")
        if isinstance(in_reply_to, str) and in_reply_to:
            mime["In-Reply-To"] = in_reply_to
            mime["References"] = in_reply_to
        mime["Date"] = formatdate(localtime=False, usegmt=True)
        return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")

    async def manage_message(
        self, *, operation: str, native_message_id: str | None
    ) -> dict[str, Any]:
        """Apply Gmail read/archive state through the observation seam."""

        if self._config.get("enable_writes") is False:
            raise PermissionError(
                "Gmail writes are disabled; reconnect with modify/send scopes"
            )
        if self._service is None:
            raise RuntimeError("Gmail service not initialized; call start() first")
        if not isinstance(native_message_id, str) or not native_message_id:
            raise ValueError("Gmail message id is unavailable")
        changes = {
            "mark_read": ({"removeLabelIds": ["UNREAD"]}, {"unread": False}),
            "mark_unread": ({"addLabelIds": ["UNREAD"]}, {"unread": True}),
            "archive": ({"removeLabelIds": ["INBOX"]}, {"archived": True}),
            "restore": ({"addLabelIds": ["INBOX"]}, {"archived": False}),
        }
        if operation not in changes:
            raise ValueError(f"unsupported Gmail message operation: {operation}")
        request_body, state = changes[operation]

        async def _gmail_api_modify() -> dict[str, Any]:
            return (
                self._service.users()
                .messages()
                .modify(userId="me", id=native_message_id, body=request_body)
                .execute()
            )

        suppressed = False
        if (
            self._observation_session is not None
            and self._observation_manager is not None
            and self._observation_event_log is not None
        ):
            result = await outbound(
                self._observation_session,
                "messaging.manage",
                {
                    "operation": operation,
                    "native_message_id": native_message_id,
                },
                _gmail_api_modify,
                manager=self._observation_manager,
                event_sink=self._observation_event_log,
                target_channel="gmail",
                target_identifier=str(self._config.get("user_email") or "gmail"),
            )
            suppressed = result.suppressed
        else:
            await _gmail_api_modify()
        return {
            "applied": not suppressed,
            "suppressed": suppressed,
            "external_id": native_message_id,
            "state": state,
        }

    async def _on_inbound(self, envelope: InboundEnvelope) -> None:
        """Inbound hook — Phase A no-op satisfying the ABC contract.

        The existing :meth:`_poll_loop` / :meth:`_run_poll_cycle` /
        :meth:`_emit_messaging_received` path stays in place and emits
        ``messaging.received`` v1 events directly per memo 08 §5
        ("inheritance + manifest only, not a rewrite"). A future internal
        refactor can route the polling loop through this hook for
        in-memory dedup or per-envelope metrics; for now the hook's
        existence-and-callability is what the
        :class:`CommunicationAdapter` ABC requires.
        """
        return None

    # ------------------------------------------------------------------
    # Gmail API helpers
    # ------------------------------------------------------------------
    def _bootstrap_cursor(self) -> str:
        """Snapshot the mailbox's current ``historyId`` via
        ``users.getProfile`` so the next poll fetches only messages
        added AFTER startup. Avoids back-flooding the event log with
        pre-existing mail on first launch."""
        resp = self._service.users().getProfile(userId="me").execute()
        history_id = resp.get("historyId")
        if not history_id:
            raise RuntimeError(
                "Gmail getProfile returned no historyId; cannot bootstrap"
            )
        return str(history_id)

    def _fetch_history(self, start_history_id: str) -> tuple[str, list[dict[str, Any]]]:
        """Page through ``users.history.list`` until ``nextPageToken``
        is absent. Returns (latest_history_id, [history_records...])."""
        records: list[dict[str, Any]] = []
        latest = start_history_id
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "userId": "me",
                "startHistoryId": start_history_id,
                "historyTypes": ["messageAdded"],
            }
            if page_token:
                params["pageToken"] = page_token
            resp = self._service.users().history().list(**params).execute()
            records.extend(resp.get("history") or [])
            if resp.get("historyId"):
                latest = str(resp["historyId"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return latest, records

    @staticmethod
    def _extract_added_message_ids(
        history_records: list[dict[str, Any]],
    ) -> list[str]:
        """Walk ``messagesAdded`` across history records, preserving order
        and dropping duplicates (the API can repeat a message across
        history pages when multiple changes touched it)."""
        seen: list[str] = []
        for record in history_records:
            for added in record.get("messagesAdded") or []:
                msg = added.get("message") or {}
                msg_id = msg.get("id")
                if isinstance(msg_id, str) and msg_id and msg_id not in seen:
                    seen.append(msg_id)
        return seen

    def _fetch_message(self, msg_id: str) -> dict[str, Any]:
        return (
            self._service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )

    # ------------------------------------------------------------------
    # Body / header decoding
    # ------------------------------------------------------------------
    @staticmethod
    def _decode_body_data(data: str | None) -> str | None:
        """Decode a base64url-encoded MIME body. Tolerates missing
        padding (Gmail sometimes omits it)."""
        if not isinstance(data, str) or not data:
            return None
        try:
            padding = "=" * (-len(data) % 4)
            return base64.urlsafe_b64decode(data + padding).decode(
                "utf-8", errors="replace"
            )
        except (ValueError, TypeError):
            return None

    @classmethod
    def _extract_bodies(cls, payload: dict[str, Any]) -> tuple[str | None, str | None]:
        """BFS the MIME tree; return (text_plain, text_html) — first hit
        per MIME type wins."""
        body_text: str | None = None
        body_html: str | None = None
        queue: list[dict[str, Any]] = [payload]
        while queue:
            part = queue.pop(0)
            mime = (part.get("mimeType") or "").lower()
            body = part.get("body") or {}
            data = body.get("data")
            decoded = cls._decode_body_data(data)
            if decoded is not None:
                if mime == "text/plain" and body_text is None:
                    body_text = decoded
                elif mime == "text/html" and body_html is None:
                    body_html = decoded
            for sub in part.get("parts") or []:
                queue.append(sub)
        return body_text, body_html

    @staticmethod
    def _epoch_ms_to_iso(epoch_ms: str | int | None) -> str:
        """Convert Gmail's ``internalDate`` (epoch ms as string) to a Z-
        suffixed UTC ISO string. Falls back to now() on missing/invalid."""
        if epoch_ms is None:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            ms = int(epoch_ms)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    @staticmethod
    def _headers_to_map(
        headers: list[dict[str, Any]] | None,
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for h in headers or []:
            name = h.get("name")
            value = h.get("value")
            if isinstance(name, str) and isinstance(value, str):
                out[name.lower()] = value
        return out

    @classmethod
    def _attachment_metadata(cls, part: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect safe Gmail attachment metadata without fetching content."""

        result: list[dict[str, Any]] = []
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")
        filename = part.get("filename")
        if attachment_id or filename:
            result.append(
                {
                    "provider_attachment_id": str(attachment_id or ""),
                    "filename": str(filename or "Attachment"),
                    "mime_type": str(
                        part.get("mimeType") or "application/octet-stream"
                    ),
                    "size": int(body.get("size") or 0),
                    "content_available": False,
                }
            )
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                result.extend(cls._attachment_metadata(child))
        return result

    # ------------------------------------------------------------------
    # Emission helpers
    # ------------------------------------------------------------------
    async def _emit_authenticated(self, *, ctx: AdapterContext, op_ref: str) -> None:
        """Append an ``adapter.authenticated`` v1 event to the event
        log. Carries the 1Password reference, NEVER the secret itself."""
        payload = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "token_storage_ref": op_ref,
            "authenticated_at": ctx.now_factory().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scopes": list(self._scopes),
        }
        await self._append(ctx=ctx, event_type="adapter.authenticated", payload=payload)

    async def _emit_messaging_received(
        self,
        *,
        ctx: AdapterContext,
        message: dict[str, Any],
        fingerprint: str | None = None,
    ) -> str:
        """Translate a Gmail ``users.messages.get`` response into a
        ``messaging.received`` v3 event. The pack's ``user_email`` is the
        ``to_identifier``; ``from_identifier`` comes from the From header.
        """
        payload_dict = message.get("payload") or {}
        headers = self._headers_to_map(payload_dict.get("headers"))
        body_text, body_html = self._extract_bodies(payload_dict)
        received_iso = self._epoch_ms_to_iso(message.get("internalDate"))
        configured_to = self._config.get("user_email") or ""
        to_addresses = [
            address for _, address in getaddresses([headers.get("to") or ""]) if address
        ]
        cc_addresses = [
            address for _, address in getaddresses([headers.get("cc") or ""]) if address
        ]
        bcc_addresses = [
            address
            for _, address in getaddresses([headers.get("bcc") or ""])
            if address
        ]
        labels = [str(value) for value in (message.get("labelIds") or [])]
        event_payload: dict[str, Any] = {
            "source_channel": "gmail",
            "from_identifier": headers.get("from") or "unknown@unknown",
            "to_identifier": headers.get("to") or configured_to or "unknown@unknown",
            "thread_id": message.get("threadId"),
            "subject": headers.get("subject"),
            "body_text": body_text,
            "body_html": body_html,
            "received_at": received_iso,
            "attachments": self._attachment_metadata(payload_dict),
            "native_message_id": str(message.get("id") or "unknown"),
            "member_id": str(self._config.get("member_id") or "unbound"),
            "provider": "gmail",
            "provider_account_id": str(configured_to or "gmail"),
            "workspace": str(self._config.get("workspace") or "personal"),
            "to_identifiers": to_addresses,
            "cc_identifiers": cc_addresses,
            "bcc_identifiers": bcc_addresses,
            "reply_to_identifier": headers.get("reply-to") or headers.get("from"),
            "rfc_message_id": headers.get("message-id"),
            "references": (headers.get("references") or "").split(),
            "provider_labels": labels,
            "unread": "UNREAD" in labels,
            "archived": "INBOX" not in labels,
            "starred": "STARRED" in labels,
            "important": "IMPORTANT" in labels,
        }
        native_message_id = str(message.get("id") or "unknown")
        stable_fingerprint = fingerprint or self._message_fingerprint(message)
        return await self._append(
            ctx=ctx,
            event_type="messaging.received",
            payload=event_payload,
            schema_version=3,
            idempotency_key=(f"gmail-message:{native_message_id}:{stable_fingerprint}"),
            raw_ref=(
                f"gmail://messages/{native_message_id}#sha256={stable_fingerprint}"
            ),
        )

    async def _emit_message_observation(
        self,
        *,
        ctx: AdapterContext,
        message: dict[str, Any],
        fingerprint: str,
    ) -> str:
        """Preserve Gmail direction instead of treating SENT as inbound."""

        labels = {str(value).upper() for value in message.get("labelIds") or []}
        if "SENT" not in labels:
            return await self._emit_messaging_received(
                ctx=ctx, message=message, fingerprint=fingerprint
            )
        payload_dict = message.get("payload") or {}
        headers = self._headers_to_map(payload_dict.get("headers"))
        body_text, _body_html = self._extract_bodies(payload_dict)
        native_message_id = str(message.get("id") or "unknown")
        configured_account = str(self._config.get("user_email") or "gmail")
        return await self._append(
            ctx=ctx,
            event_type="messaging.sent",
            schema_version=2,
            idempotency_key=(f"gmail-sent-message:{native_message_id}:{fingerprint}"),
            raw_ref=(f"gmail://messages/{native_message_id}#sha256={fingerprint}"),
            payload={
                "source_channel": "gmail",
                "to_identifier": headers.get("to") or "unknown@unknown",
                "thread_id": message.get("threadId"),
                "subject": headers.get("subject"),
                "body_text": body_text,
                "sent_at": self._epoch_ms_to_iso(message.get("internalDate")),
                "delivery_status": "sent",
                "member_id": str(self._config.get("member_id") or "unbound"),
                "provider": "gmail",
                "provider_account_id": configured_account,
                "workspace": str(self._config.get("workspace") or "personal"),
                "native_message_id": native_message_id,
                "in_reply_to_interaction_id": None,
            },
        )

    @staticmethod
    def _message_fingerprint(message: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                message, sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest()

    async def _emit_quarantine(
        self,
        *,
        ctx: AdapterContext,
        external_id: str,
        fingerprint: str,
        error: Exception,
        provider_watermark: str | None,
    ) -> str:
        now = ctx.now_factory().astimezone(timezone.utc).isoformat()
        self._status.last_error = {
            "error_kind": "api_changed",
            "error_message": f"{type(error).__name__}: {error}"[:2000],
            "external_id_fingerprint": hashlib.sha256(external_id.encode()).hexdigest(),
        }
        return await self._append(
            ctx=ctx,
            event_type="adapter.record.quarantined",
            payload={
                "adapter_name": self.name,
                "adapter_family": self.family,
                "external_id": external_id,
                "content_fingerprint": fingerprint,
                "error_kind": "api_changed",
                "error_message": f"{type(error).__name__}: {error}"[:2000],
                "quarantined_at": now,
                "provider_watermark": provider_watermark,
            },
            idempotency_key=f"gmail-quarantine:{external_id}:{fingerprint}",
            raw_ref=f"gmail://messages/{external_id}#quarantined={fingerprint}",
        )

    async def _maybe_emit_cursor_advance(
        self, *, ctx: AdapterContext, cursor_value: str
    ) -> None:
        """Emit ``adapter.cursor_advanced`` v1 only if one of the
        milestone thresholds has been crossed since the last emission.

        Anti-vision §"Quiet by default": skipping this gate would
        produce one cursor event per poll cycle, drowning the event
        log."""
        now = ctx.now_factory()
        events = self._events_since_advance
        elapsed_s: float | None = None
        if self._last_advance_at is not None:
            elapsed_s = (now - self._last_advance_at).total_seconds()
        events_threshold = events >= self.CURSOR_MILESTONE_EVENTS
        time_threshold = (
            elapsed_s is not None
            and elapsed_s >= self.CURSOR_MILESTONE_INTERVAL_S
            and events > 0
        )
        if not (events_threshold or time_threshold):
            return
        payload = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "cursor_kind": "history_id",
            "cursor_value": cursor_value,
            "events_since_last": events,
            "advanced_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        await self._append(
            ctx=ctx, event_type="adapter.cursor_advanced", payload=payload
        )
        self._events_since_advance = 0
        self._last_advance_at = now
        self._status.last_cursor_advance_at = now

    async def _emit_error(
        self,
        *,
        ctx: AdapterContext,
        severity: str,
        error_kind: str,
        error_message: str,
        retry_after_s: int | None,
    ) -> None:
        """Emit ``adapter.error`` v1. Carries the classification used by
        the supervisor's backoff. ``retry_after_s`` is honored if set
        (e.g. Gmail rate-limit response)."""
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
        schema_version: int = 1,
        idempotency_key: str | None = None,
        raw_ref: str | None = None,
    ) -> str:
        """Append a typed event via the supervisor-provided event log.

        [§7.3, D17] adapters never write to projections — append-only
        event log is the sole write surface.
        """
        return await write_adapter_event(
            ctx,
            adapter_name=self.name,
            sensitivity_floor=self.sensitivity_floor,
            config=self._config,
            event_type=event_type,
            payload=payload,
            schema_version=schema_version,
            idempotency_key=idempotency_key,
            raw_ref=raw_ref,
        )

    # ------------------------------------------------------------------
    # Error classification
    # ------------------------------------------------------------------
    @staticmethod
    def _classify_gmail_error(
        exc: BaseException,
    ) -> tuple[str, str, int | None]:
        """Map a raised exception to ``(severity, error_kind, retry_after_s)``.

        Duck-types ``googleapiclient.errors.HttpError`` via the ``resp``
        attribute (a ``httplib2.Response``-like mapping with ``status``
        and header keys) so we don't have to import the SDK at module
        load."""
        status: int | None = None
        retry_after: int | None = None
        resp = getattr(exc, "resp", None)
        if resp is not None:
            raw_status: Any = getattr(resp, "status", None)
            if raw_status is None and hasattr(resp, "get"):
                raw_status = resp.get("status")
            try:
                if raw_status is not None:
                    status = int(raw_status)
            except (TypeError, ValueError):
                status = None
            retry_header: Any = None
            if hasattr(resp, "get"):
                retry_header = resp.get("retry-after") or resp.get("Retry-After")
            if retry_header is not None:
                try:
                    retry_after = int(retry_header)
                except (TypeError, ValueError):
                    retry_after = None

        if status == 429:
            return ("transient", "rate_limited", retry_after)
        if status in (401, 403):
            return ("fatal", "auth_failed", retry_after)
        if status is not None and 500 <= status < 600:
            return ("transient", "network", retry_after)
        if status is not None and 400 <= status < 500:
            return ("transient", "api_changed", retry_after)
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return ("transient", "network", retry_after)
        if isinstance(exc, OSError):
            return ("transient", "network", retry_after)
        return ("transient", "unknown", retry_after)


__all__ = [
    "GMAIL_MODIFY_SCOPE",
    "GMAIL_READONLY_SCOPE",
    "GMAIL_SEND_SCOPE",
    "CredentialsResolver",
    "GmailAdapter",
    "ServiceFactory",
]
