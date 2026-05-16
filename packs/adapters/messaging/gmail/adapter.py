"""Gmail adapter pack — L1 standalone adapter for messaging (Gmail v1 API).

Implements the ``Adapter`` Protocol from ``adminme.adapters.base``. C2
ships the manifest + service-account authentication path; the polling
loop, cursor-advance milestone emission, and defensive error catch are
added in C3 (currently stubbed with ``NotImplementedError``).

Per ADMINISTRATEME_BUILD.md §L1 (adapter contract), DECISIONS.md §D6
(no LLM SDK), §D7 (schemas at v1), §D8 (correlation_id propagation),
SYSTEM_INVARIANTS.md §6 invariant 10 (sensitivity-floor downgrade
forbidden), §7.3 + §D17 (adapters never write projections), §8
invariant 3 (channel transport is OpenClaw's territory — adapters emit
events only).

Gmail API endpoints used (see ``docs/reference/google-gmail/gmail-v1.ts``):

  - ``users.history.list``  — incremental sync (gmail-v1.ts line 2603)
  - ``users.messages.list`` — initial backfill (gmail-v1.ts line 4736)
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

import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable

from adminme.adapters.base import (
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)

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
                "-----BEGIN PRIVATE KEY-----\nTESTKEY\n"
                "-----END PRIVATE KEY-----\n"
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


class GmailAdapter:
    """Gmail L1 standalone adapter.

    Polls the Gmail history API (``users.history.list``) every 2 minutes
    per ``pack.yaml`` ``triggers.schedule`` and emits ``messaging.received``
    v1 per fetched message. Cursor-advance emission is milestone-only
    (every 100 messages OR every 5 minutes — see
    ``AdapterCursorAdvancedV1`` docstring) per anti-vision §"Quiet by
    default". The polling implementation lands in C3; C2 is the auth
    path + stub.
    """

    pack_id: str = "adapter:messaging-gmail-api"
    name: str = "messaging-gmail-api"
    family: str = "messaging"
    version: str = "1.0.0"

    HARDCODED_FLOOR: SensitivityFloor = "normal"
    """Class-level minimum sensitivity_floor. Config may RAISE the floor
    (e.g. to ``privileged`` for a law-practice Gmail) but cannot lower
    it. [§6 invariant 10]"""

    SCOPES: list[str] = [GMAIL_READONLY_SCOPE]

    def __init__(
        self,
        *,
        config: dict[str, Any],
        credentials_resolver: CredentialsResolver | None = None,
        service_factory: ServiceFactory | None = None,
    ) -> None:
        self._config = dict(config)
        self._credentials_resolver = (
            credentials_resolver or _default_credentials_resolver
        )
        self._service_factory = service_factory or _default_service_factory
        # [§6 invariant 10] resolve floor at construction; raise on downgrade.
        self.sensitivity_floor: SensitivityFloor = self._resolve_sensitivity_floor()
        self._status = AdapterStatus()
        self._service: Any = None

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
        polling loop. In C2 the polling loop raises ``NotImplementedError``
        — C3 fills it in with the history-API sync."""
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
        self._status.started_at = ctx.now_factory()
        self._status.state = "running"
        # [D7] adapter.authenticated registered at v1 in events/schemas/adapters.py.
        # [D8] correlation_id minted by the supervisor-provided factory.
        self._emit_authenticated(ctx=ctx, op_ref=op_ref)

        await self._poll_loop(ctx=ctx)

    async def _poll_loop(self, *, ctx: AdapterContext) -> None:
        """Polling loop. Stub in C2; replaced in C3 with the history-API
        sync + ``messages.get`` fetch + milestone cursor advances +
        defensive error catch."""
        raise NotImplementedError("Gmail polling loop ships in commit 3")

    async def stop(self) -> None:
        """Release the Gmail service handle. Idempotent."""
        self._service = None
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    # ------------------------------------------------------------------
    # emission helpers
    # ------------------------------------------------------------------
    def _emit_authenticated(
        self, *, ctx: AdapterContext, op_ref: str
    ) -> None:
        """Append an ``adapter.authenticated`` v1 event to the event
        log. Carries the 1Password reference, NEVER the secret itself."""
        # [§7.3, D17] adapters write to the event log only — never a projection.
        appender = getattr(ctx.event_log, "append_sync", None) or getattr(
            ctx.event_log, "append", None
        )
        if appender is None:
            return
        payload = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "token_storage_ref": op_ref,
            "authenticated_at": datetime.now(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scopes": list(self.SCOPES),
        }
        try:
            appender(
                type="adapter.authenticated",
                schema_version=1,
                payload=payload,
                correlation_id=ctx.correlation_id_factory(),
            )
        except Exception:  # noqa: BLE001 — best-effort emission
            _log.exception(
                "gmail adapter failed to emit adapter.authenticated"
            )


__all__ = [
    "GMAIL_READONLY_SCOPE",
    "CredentialsResolver",
    "GmailAdapter",
    "ServiceFactory",
]
