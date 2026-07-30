"""Gmail adapter pack — L1 standalone adapter for messaging (Gmail v1 API).

Inherits :class:`adminme.lib.adapters.CommunicationAdapter` (Cat-A) per
``docs/08-architecture-amendment-adapter-rebase.md`` §3.3 (Cohort C
retrofit) and §3.4 (Cohort D step 1 / prompt 11a). Real Gmail outbound
(``users.messages.send`` + MIME builder) ships in prompt 11a c2; the
:meth:`send` body calls Gmail v1's ``users.messages.send`` endpoint
directly when the observation seam slots are unpopulated (Phase A),
and routes through :func:`adminme.lib.observation.outbound` when the
slots are populated by the supervisor (Phase B). The emitted event is
the EXISTING ``messaging.sent@v1`` schema registered at
``adminme/events/schemas/ingest.py`` since phase 04-3 (commit
``2ce1cff``); NO new schema is registered by 11a per [D7] (v1 schemas
immutable, 47 production consumers).

Per ADMINISTRATEME_BUILD.md §L1 (adapter contract), DECISIONS.md §D6
(no LLM SDK), §D7 (schemas at v1), §D8 (correlation_id propagation),
SYSTEM_INVARIANTS.md §6 invariant 10 (sensitivity-floor downgrade
forbidden), §6.20 (observation mode wraps outbound), §7.3 + §D17
(adapters never write projections), §8 invariant 3 (channel transport
is OpenClaw's territory — adapters emit events only).

Gmail API endpoints used (see ``docs/reference/google-gmail/gmail-v1.ts``):

  - ``users.getProfile``    — bootstrap cursor (gmail-v1.ts line 1082)
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
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any, Callable, ClassVar, Literal

from adminme.adapters.base import (
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)
from adminme.adapters.emission import write_adapter_event
from adminme.lib.adapters import (
    CommunicationAdapter,
    InboundEnvelope,
    OutboundMessage,
    SendResult,
)
from adminme.lib.observation import outbound

_log = logging.getLogger(__name__)


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


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


def _default_credentials_resolver(op_ref: str) -> dict[str, Any]:
    """Read service-account JSON from a 1Password reference.

    Test stubs (``op://test/...``) return a deterministic minimal JSON
    shape that satisfies ``google.oauth2.service_account.Credentials``'s
    structural expectations; production shells out to ``op read``.
    """
    if not op_ref or not op_ref.startswith("op://"):
        raise ValueError(f"invalid op-ref: {op_ref!r}")
    if op_ref.startswith("op://test/"):
        return {
            "type": "service_account",
            "project_id": "test-project",
            "client_email": "test@test.iam.gserviceaccount.com",
            "private_key": (
                "-----BEGIN PRIVATE KEY-----\nTESTKEY\n" "-----END PRIVATE KEY-----\n"
            ),
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    op_bin = shutil.which("op")
    if op_bin is None:
        raise RuntimeError(
            "1Password CLI `op` not on PATH; cannot resolve "
            f"{op_ref!r} (set up `op` or use an op://test/... ref)"
        )
    proc = subprocess.run(
        [op_bin, "read", "--no-newline", op_ref],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return json.loads(proc.stdout.decode("utf-8"))


def _default_service_factory(
    credentials_json: dict[str, Any],
    scopes: list[str],
    subject_email: str,
) -> Any:
    """Build a Gmail API service. ``google-auth`` and
    ``google-api-python-client`` are imported lazily so tests can
    substitute a fake without the deps being installed in CI."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_info(credentials_json, scopes=scopes)
    if subject_email:
        creds = creds.with_subject(subject_email)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailAdapter(CommunicationAdapter):
    """Gmail Cat-A standalone adapter (retrofitted onto CommunicationAdapter).

    Polls the Gmail history API (``users.history.list``) every 2 minutes
    per ``pack.yaml`` ``triggers.schedule`` and emits ``messaging.received``
    v1 per fetched message. Cursor-advance emission is milestone-only
    (every 100 messages OR every 5 minutes — see
    ``AdapterCursorAdvancedV1`` docstring) per anti-vision §"Quiet by
    default".

    Retrofitted onto :class:`CommunicationAdapter` per prompt 11r-a
    (Cohort C retrofit; ``docs/08-architecture-amendment-adapter-rebase.md``
    §3.3). The inheritance flip preserves the simple ``Adapter`` Protocol
    surface (``start`` / ``stop`` / ``status`` / ``name`` / ``family`` /
    ``version`` / ``sensitivity_floor``) and adds the Cat-A contract
    methods :meth:`send` + :meth:`_on_inbound`. Today Gmail is
    inbound-only; the :meth:`send` stub raises ``NotImplementedError``
    via an ``action_fn`` routed through
    :func:`adminme.lib.observation.outbound`. Phase B will ship real
    Gmail outbound + the ``messaging.sent`` event schema + the second
    capability block in the manifest.
    """

    pack_id: ClassVar[str] = "adapter:messaging-gmail-api"
    name: ClassVar[str] = "messaging-gmail-api"
    family: ClassVar[Literal["messaging"]] = "messaging"
    version: ClassVar[str] = "1.0.0"

    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-A",
            "runtime": "central",
            "event_families": ["messaging.received"],
        }
    ]
    """Class-level mirror of the manifest's ``capabilities`` block per
    memo 05 §3.2 (runtime introspection without re-parsing pack.yaml).
    Today: one Cat-A entry declaring ``messaging.received`` only. Phase B
    adds a second entry covering ``messaging.sent`` when real Gmail
    outbound ships."""

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
        self._events_since_advance: int = 0
        self._last_advance_at: datetime | None = None
        self._stop_requested: bool = False
        # Test seam: bound the loop. None = run forever (production); the
        # supervisor stops the task on its lifecycle events.
        self._max_poll_cycles: int | None = max_poll_cycles
        # ctx is captured at start() so :meth:`send` (a Cat-A contract
        # method the supervisor invokes without re-passing ctx) can emit
        # ``messaging.sent@v1`` / ``adapter.error@v1`` events through the
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
                "sensitivity_floor must be 'normal' or 'privileged', "
                f"got {cfg_value!r}"
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
        op_ref = self._config.get("service_account_key_ref")
        user_email = self._config.get("user_email")
        if not isinstance(op_ref, str) or not op_ref.startswith("op://"):
            raise ValueError(
                "service_account_key_ref must be a 1Password reference "
                "(op://<vault>/<item>/<field>)"
            )
        if not isinstance(user_email, str) or "@" not in user_email:
            raise ValueError("user_email must be a valid email address")

        creds_json = self._credentials_resolver(op_ref)
        self._service = self._service_factory(creds_json, self.SCOPES, user_email)
        self._ctx = ctx
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
                if severity == "fatal":
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

        On first cycle (cursor uninitialized) the adapter bootstraps via
        ``users.getProfile`` and emits zero messages — the historyId
        snapshot becomes the cursor floor. Subsequent cycles call
        ``users.history.list(startHistoryId=...)`` and fetch each added
        message via ``users.messages.get(format=full)``.
        """
        if self._service is None:
            raise RuntimeError("Gmail service not initialized; call start() first")
        if self._cursor is None:
            self._cursor = self._bootstrap_cursor()
            return 0

        new_history_id, history_records = self._fetch_history(self._cursor)
        message_ids = self._extract_added_message_ids(history_records)
        emitted = 0
        for msg_id in message_ids:
            full_msg = self._fetch_message(msg_id)
            await self._emit_messaging_received(ctx=ctx, message=full_msg)
            emitted += 1
            self._events_since_advance += 1

        if new_history_id and new_history_id != self._cursor:
            self._cursor = new_history_id
        await self._maybe_emit_cursor_advance(ctx=ctx, cursor_value=self._cursor)
        return emitted

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
        ``messaging.sent@v1`` event with field-mapping from Gmail
        semantics to the existing schema (registered at
        ``adminme/events/schemas/ingest.py`` since phase 04-3); on
        observation-suppress, the same event is emitted with
        ``delivery_status="queued"`` and ``thread_id=None``. The
        ``messaging.sent@v1`` registration is reused unchanged per [D7].

        Errors classify per :meth:`_classify_gmail_error` into the
        ``AdapterErrorV1`` taxonomy (4xx auth → fatal ``auth_failed``,
        5xx → transient ``network``, 429 → transient ``rate_limited``),
        emit one ``adapter.error@v1`` event, and re-raise so the
        supervisor's backoff handles retry. ``messaging.sent@v1`` is
        NOT emitted on the error path — the action did not, in fact,
        send.
        """
        # [§7.3, D17]: defense-in-depth input check; the Cat-A ABC's
        # OutboundMessage is frozen but does not validate non-emptiness.
        if not message.recipient:
            raise ValueError("OutboundMessage.recipient must be a non-empty string")
        ctx = self._ctx
        if self._service is None:
            raise RuntimeError("Gmail service not initialized; call start() first")

        mime_b64 = self._build_mime_message(message)
        service = self._service

        async def _gmail_api_send() -> dict[str, Any]:
            # Wrapped in a closure so :func:`outbound` can suppress it
            # under observation mode. ``execute()`` is sync; the
            # observation seam awaits the closure.
            return (
                service.users()
                .messages()
                .send(userId="me", body={"raw": mime_b64})
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

        # [D7] map onto the EXISTING ``messaging.sent@v1`` field set.
        # No new schema; field names match
        # ``MessagingSentV1`` byte-for-byte.
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
        }
        if ctx is not None:
            await self._append(
                ctx=ctx,
                event_type="messaging.sent",
                payload=sent_payload,
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
        mime["Date"] = formatdate(localtime=False, usegmt=True)
        return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")

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
            "scopes": list(self.SCOPES),
        }
        await self._append(ctx=ctx, event_type="adapter.authenticated", payload=payload)

    async def _emit_messaging_received(
        self, *, ctx: AdapterContext, message: dict[str, Any]
    ) -> None:
        """Translate a Gmail ``users.messages.get`` response into a
        ``messaging.received`` v1 event. The pack's ``user_email`` is the
        ``to_identifier``; ``from_identifier`` comes from the From header.
        """
        payload_dict = message.get("payload") or {}
        headers = self._headers_to_map(payload_dict.get("headers"))
        body_text, body_html = self._extract_bodies(payload_dict)
        received_iso = self._epoch_ms_to_iso(message.get("internalDate"))
        configured_to = self._config.get("user_email") or ""
        event_payload: dict[str, Any] = {
            "source_channel": "gmail",
            "from_identifier": headers.get("from") or "unknown@unknown",
            "to_identifier": headers.get("to") or configured_to or "unknown@unknown",
            "thread_id": message.get("threadId"),
            "subject": headers.get("subject"),
            "body_text": body_text,
            "body_html": body_html,
            "received_at": received_iso,
            "attachments": [],
        }
        await self._append(
            ctx=ctx, event_type="messaging.received", payload=event_payload
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
    ) -> None:
        """Append a typed event via the supervisor-provided event log.

        [§7.3, D17] adapters never write to projections — append-only
        event log is the sole write surface.
        """
        try:
            await write_adapter_event(
                ctx,
                adapter_name=self.name,
                sensitivity_floor=self.sensitivity_floor,
                config=self._config,
                event_type=event_type,
                payload=payload,
            )
        except Exception:  # noqa: BLE001 — best-effort emission
            _log.exception("gmail adapter failed to emit %s event", event_type)

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
    "GMAIL_READONLY_SCOPE",
    "CredentialsResolver",
    "GmailAdapter",
    "ServiceFactory",
]
