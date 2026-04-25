"""
Authority gate + action_gates — the governance layer of guardedWrite.

Per ADMINISTRATEME_BUILD.md §AUTHORITY/§GOVERNANCE (lines 2053-2168),
SYSTEM_INVARIANTS.md §6.5-6.8, CONSOLE_PATTERNS.md §3 (canonical algorithm)
and §4 (sliding-window mechanics), DIAGRAMS.md §3 (control-flow diagram).

GuardedWrite is the canonical write gate: every product API route that
mutates state will eventually pass through it. Three layers, in this order:

    agent allowlist  →  governance action_gate  →  rate limit

The first refusal short-circuits; the denial event records ``layer_failed``
so audit can attribute causes unambiguously [§6.6]. ``hard_refuse`` gates
are NEVER overridable [§6.7]. ``review`` gates emit a ``review_request``
event and return ``held_for_review`` instead of firing [§6.8].

OpenClaw's approval gates (tool-execution boundary, host-local) and this
AdministrateMe governance gate (HTTP API boundary) are INDEPENDENT — both
must pass; neither substitutes for the other [§8.7].

OpenClaw's exec-approval composition seam: OpenClaw runs its own approval
machinery for tool execution; AdministrateMe's guardedWrite runs at the HTTP
API boundary BEFORE the openclaw skill_runner is even invoked. Prompt 15
defines the composed flow; here we provide the AdministrateMe half only.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from adminme.events.envelope import EventEnvelope

if TYPE_CHECKING:
    from adminme.events.log import EventLog
    from adminme.lib.session import Session

_log = logging.getLogger(__name__)

GateValue = Literal["allow", "review", "deny", "hard_refuse"]
LayerName = Literal["allowlist", "governance", "rate_limit"]


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimit:
    """Single (window_s, max_n) policy. Per CONSOLE_PATTERNS.md §4."""

    window_s: int
    max_n: int


@dataclass(frozen=True)
class ActionGateConfig:
    """Loaded from ``config/governance.yaml`` per ADMINISTRATEME_BUILD.md
    lines 2120-2145. Carries the three pieces guardedWrite needs:

    - ``action_gates`` — per-action policy enum
    - ``rate_limits`` — per-action sliding-window policy plus a ``__default__``
      fallback for any action without an explicit entry
    - ``forbidden_outbound_parties`` — opaque dicts from the ``never:`` block
      in authority.yaml; consulted by callers wanting to perform an outbound
      send (the gate value for the action is still authoritative; this is
      additional context for descriptive audit reasons)
    """

    action_gates: dict[str, GateValue]
    rate_limits: dict[str, RateLimit]
    forbidden_outbound_parties: list[dict[str, Any]] = field(default_factory=list)

    def gate(self, action: str) -> GateValue:
        """Return the gate value for ``action``. Default ``allow`` when the
        action is unknown — keeps existing routes working as new gates land
        per CONSOLE_PATTERNS.md §3 ('actions default to allow if absent')."""
        return self.action_gates.get(action, "allow")

    def rate_limit_for(self, action: str) -> RateLimit:
        """Return the RateLimit for ``action`` or the configured default
        (60/60s if neither is set)."""
        if action in self.rate_limits:
            return self.rate_limits[action]
        if "__default__" in self.rate_limits:
            return self.rate_limits["__default__"]
        return RateLimit(window_s=60, max_n=60)


@dataclass(frozen=True)
class GuardedWriteResult:
    """Outcome of ``GuardedWrite.check``. See DIAGRAMS.md §3.

    - ``pass_=True``  — all three layers passed; caller may write.
    - ``pass_=False`` with ``layer_failed`` set — refused at that layer; a
      ``write.denied`` event was emitted, OR (when ``review_id`` is set) a
      ``review_request`` event was emitted at the governance layer.

    ``pass_`` is named with a trailing underscore because ``pass`` is a
    Python reserved word.
    """

    pass_: bool
    layer_failed: LayerName | None = None
    reason: str | None = None
    review_id: str | None = None
    retry_after_s: int | None = None
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# RateLimiter (sliding window per CONSOLE_PATTERNS.md §4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitDecision:
    """Result of a single rate-limit check.

    ``allowed`` mirrors the JS implementation's boolean; ``retry_after_s``
    is populated only on denial per CONSOLE_PATTERNS.md §4 ('retry_after_s
    is rounded up')."""

    allowed: bool
    retry_after_s: int | None = None


class RateLimiter:
    """In-memory sliding-log rate limiter, per-key. Per CONSOLE_PATTERNS.md
    §4. The console is single-process per household so an in-memory bucket
    set is sufficient; if a future deployment shards the console, this
    moves to Redis."""

    def __init__(self, *, time_fn: Any = None) -> None:
        # Each key maps to a list of timestamps (seconds since epoch). The
        # list stays small (<= max_n at any moment) so an in-place shift is
        # cheaper than an allocating filter [CONSOLE_PATTERNS.md §4 'shift
        # vs filter'].
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._time_fn = time_fn or time.monotonic

    def check_and_record(self, key: str, window_s: int, max_n: int) -> bool:
        """Sliding-window check. Returns True if the call is admitted
        (and records the timestamp); False if rejected. Use ``decide`` if
        you also want ``retry_after_s``.

        Pure side effect: on success, appends now to the bucket; on failure,
        leaves the bucket untouched."""
        return self.decide(key, window_s, max_n).allowed

    def decide(self, key: str, window_s: int, max_n: int) -> RateLimitDecision:
        """Sliding-window check that returns full ``RateLimitDecision``."""
        now = self._time_fn()
        cutoff = now - window_s
        bucket = self._buckets[key]
        # In-place prune. Buckets stay small per §4.
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) < max_n:
            bucket.append(now)
            return RateLimitDecision(allowed=True)
        # Denied: oldest timestamp + window_s is when the next slot frees up.
        retry_ms = (bucket[0] + window_s - now) * 1000
        # Round up per CONSOLE_PATTERNS.md §4 ('retry_after_s rounded up').
        retry_s = max(1, int(-(-retry_ms // 1000)))
        return RateLimitDecision(allowed=False, retry_after_s=retry_s)


# ---------------------------------------------------------------------------
# AgentAllowlist (per CONSOLE_PATTERNS.md §3 layer 1)
# ---------------------------------------------------------------------------


class AgentAllowlist:
    """Config-driven (agent_id, action) lookup. Patterns support glob via
    trailing ``*`` per CONSOLE_PATTERNS.md §3 ('matchPattern: full regex
    too wide; trailing-* glob only').

    Two-axis match: the agent_id pattern AND the action pattern must match.
    Either axis accepts ``*`` as a sole wildcard to match any value, or a
    ``prefix.*`` form to match any value starting with ``prefix.``."""

    def __init__(self, rules: list[tuple[str, list[str]]]) -> None:
        # Each rule is (agent_pattern, [action_patterns]).
        self._rules: list[tuple[str, list[str]]] = rules

    def is_allowed(self, agent_id: str, action: str) -> bool:
        for agent_pat, action_pats in self._rules:
            if not _match_pattern(agent_pat, agent_id):
                continue
            for ap in action_pats:
                if _match_pattern(ap, action):
                    return True
        return False


def _match_pattern(pattern: str, value: str) -> bool:
    """Glob match per CONSOLE_PATTERNS.md §3.

    - ``*``           → match anything
    - ``prefix.*``    → match anything that begins with ``prefix.``
                        (segment-aware, so ``task.*`` does NOT match
                        ``tasks.created``)
    - ``prefix:*``    → match anything that begins with ``prefix:``
                        (used for the ``user:`` / ``openclaw:`` /
                        ``daemon:`` agent-id namespaces)
    - exact literal   → match only that literal

    Full regex is deliberately not supported; the surface area is too wide
    and pack authors over-match by accident."""
    if pattern == value:
        return True
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return value.startswith(pattern[:-2] + ".")
    if pattern.endswith(":*"):
        return value.startswith(pattern[:-2] + ":")
    return False


# ---------------------------------------------------------------------------
# Config loaders
# ---------------------------------------------------------------------------


def _coerce_gate_value(raw: Any) -> GateValue:
    """Map config strings to the closed gate enum.

    Accepts BUILD.md §GOVERNANCE's ``confirm`` as an alias for ``review``
    (the prompt's vocabulary uses ``review``; BUILD.md uses ``confirm``)."""
    if not isinstance(raw, str):
        raise ValueError(f"gate value must be a string, got {type(raw).__name__}")
    s = raw.strip().lower()
    if s in ("allow", "review", "deny", "hard_refuse"):
        return s  # type: ignore[return-value]
    if s == "confirm":
        return "review"
    raise ValueError(f"unknown gate value: {raw!r}")


def load_governance_config(path: Path) -> ActionGateConfig:
    """Load an ActionGateConfig from a yaml file. Schema mirrors
    BUILD.md §GOVERNANCE plus the ``never:`` block from authority.yaml."""
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    raw_gates = data.get("action_gates") or {}
    action_gates: dict[str, GateValue] = {
        str(k): _coerce_gate_value(v) for k, v in raw_gates.items()
    }
    raw_limits = data.get("rate_limits") or {}
    rate_limits: dict[str, RateLimit] = {}
    for k, v in raw_limits.items():
        # Accept either {window_sec, max_calls} (BUILD.md vocabulary) or
        # {window_s, max_n} (this module's vocabulary).
        window = v.get("window_s") or v.get("window_sec")
        cap = v.get("max_n") or v.get("max_calls")
        if window is None or cap is None:
            raise ValueError(f"rate_limit {k!r} missing window/max")
        rate_limits[str(k)] = RateLimit(window_s=int(window), max_n=int(cap))
    forbidden = data.get("forbidden_outbound_parties") or data.get("never") or []
    if not isinstance(forbidden, list):
        raise ValueError("forbidden_outbound_parties must be a list")
    return ActionGateConfig(
        action_gates=action_gates,
        rate_limits=rate_limits,
        forbidden_outbound_parties=list(forbidden),
    )


def load_agent_allowlist(path: Path) -> AgentAllowlist:
    """Load an AgentAllowlist from a yaml file. Expected shape::

        agent_allowlist:
          - agent_id: "daemon:xlsx_reverse"
            actions: ["task.*", "commitment.*", ...]
          - agent_id: "user:*"
            actions: ["task.*"]

    The list is order-preserving: rules are scanned in order; first match
    wins. Per CONSOLE_PATTERNS.md §3 the JS version uses a flat
    (agent_id, action_pattern) row table — same semantics, denormalized."""
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    rows = data.get("agent_allowlist") or []
    if not isinstance(rows, list):
        raise ValueError("agent_allowlist must be a list")
    rules: list[tuple[str, list[str]]] = []
    for row in rows:
        agent_id = row.get("agent_id")
        actions = row.get("actions") or []
        if not agent_id:
            raise ValueError("agent_allowlist row missing agent_id")
        if not isinstance(actions, list):
            raise ValueError(f"agent_allowlist actions for {agent_id!r} not a list")
        rules.append((str(agent_id), [str(a) for a in actions]))
    return AgentAllowlist(rules)


# ---------------------------------------------------------------------------
# Session → agent_id derivation
# ---------------------------------------------------------------------------


def derive_agent_id(session: "Session") -> str:
    """Map a Session's (source, auth_member_id) to an agent_id used as the
    allowlist key. Convention mirrors CONSOLE_PATTERNS.md §3's ``user:`` /
    ``skill:`` / ``pipeline:`` prefixes; AdministrateMe additions:

    - ``daemon:xlsx_reverse``      — xlsx reverse daemon (UT-7 attribution)
    - ``daemon:xlsx_workbooks``    — xlsx forward daemon
    - ``openclaw:<auth_member_id>`` — slash command or standing order
    - ``system:bootstrap``          — bootstrap wizard
    - ``system:internal``           — internal product API caller
    - ``user:<auth_member_id>``     — node console (default)
    """
    source = session.source
    if source == "xlsx_reverse_daemon":
        return "daemon:xlsx_reverse"
    if source == "xlsx_workbooks":
        return "daemon:xlsx_workbooks"
    if source in ("openclaw_slash_command", "openclaw_standing_order"):
        return f"openclaw:{session.auth_member_id}"
    if source == "bootstrap_wizard":
        return "system:bootstrap"
    if source == "product_api_internal":
        return "system:internal"
    return f"user:{session.auth_member_id}"


# ---------------------------------------------------------------------------
# GuardedWrite — the three-layer check
# ---------------------------------------------------------------------------


def _correlation_id() -> str:
    return f"w_{int(time.time() * 1000):x}_{secrets.token_hex(3)}"


class GuardedWrite:
    """Three-layer write check per CONSOLE_PATTERNS.md §3 + DIAGRAMS.md §3.

    Layers run in strict order; first refusal short-circuits. ``check`` is
    pure-decision: it returns a result and the caller decides whether to
    perform the actual write. (Unlike the JS version which takes a
    ``writeFn``, the Python product splits the responsibilities so a caller
    can issue more than one write per check or stage payload mutations
    between gating and the actual append.)

    The denial-event side effect happens INSIDE ``check`` so callers cannot
    forget to record it. Audit invariant per DIAGRAMS.md §3: every check
    produces exactly one terminal event."""

    def __init__(
        self,
        config: ActionGateConfig,
        limiter: RateLimiter,
        allowlist: AgentAllowlist,
        event_log: "EventLog",
    ) -> None:
        self._config = config
        self._limiter = limiter
        self._allowlist = allowlist
        self._log = event_log

    async def check(
        self,
        session: "Session",
        action: str,
        payload: dict[str, Any],
    ) -> GuardedWriteResult:
        agent_id = derive_agent_id(session)
        correlation = session.correlation_id or _correlation_id()

        # ---- Layer 1: agent allowlist ----
        if not self._allowlist.is_allowed(agent_id, action):
            await self._emit_denied(
                session,
                agent_id=agent_id,
                action=action,
                payload=payload,
                layer="allowlist",
                reason="agent_not_permitted_for_action",
                correlation=correlation,
            )
            return GuardedWriteResult(
                pass_=False,
                layer_failed="allowlist",
                reason="agent_not_permitted_for_action",
                correlation_id=correlation,
            )

        # ---- Layer 2: governance action_gate ----
        gate = self._config.gate(action)
        if gate == "hard_refuse":
            await self._emit_denied(
                session,
                agent_id=agent_id,
                action=action,
                payload=payload,
                layer="governance",
                reason="hard_refuse_by_governance",
                correlation=correlation,
            )
            return GuardedWriteResult(
                pass_=False,
                layer_failed="governance",
                reason="hard_refuse",
                correlation_id=correlation,
            )

        if gate == "deny":
            await self._emit_denied(
                session,
                agent_id=agent_id,
                action=action,
                payload=payload,
                layer="governance",
                reason="denied_by_governance",
                correlation=correlation,
            )
            return GuardedWriteResult(
                pass_=False,
                layer_failed="governance",
                reason="deny",
                correlation_id=correlation,
            )

        if gate == "review":
            review_id = correlation  # JS version reuses the correlation id
            await self._emit_review_request(
                session,
                agent_id=agent_id,
                action=action,
                payload=payload,
                review_id=review_id,
                correlation=correlation,
            )
            return GuardedWriteResult(
                pass_=False,
                layer_failed="governance",
                reason="held_for_review",
                review_id=review_id,
                correlation_id=correlation,
            )

        # ---- Layer 3: rate limit ----
        rl = self._config.rate_limit_for(action)
        # Key follows CONSOLE_PATTERNS.md §3: "${tenantId}:${scope}:${action}".
        # ``scope`` is the auth_member_id — per-principal budgets so one
        # principal's runaway loop cannot exhaust another principal's quota.
        rl_key = f"{session.tenant_id}:{session.auth_member_id}:{action}"
        decision = self._limiter.decide(rl_key, rl.window_s, rl.max_n)
        if not decision.allowed:
            await self._emit_denied(
                session,
                agent_id=agent_id,
                action=action,
                payload=payload,
                layer="rate_limit",
                reason="rate_limit_exceeded",
                correlation=correlation,
                retry_after_s=decision.retry_after_s,
            )
            return GuardedWriteResult(
                pass_=False,
                layer_failed="rate_limit",
                reason="rate_limit_exceeded",
                retry_after_s=decision.retry_after_s,
                correlation_id=correlation,
            )

        # ---- All three passed ----
        return GuardedWriteResult(pass_=True, correlation_id=correlation)

    # ------------------------------------------------------------------
    # event emission
    # ------------------------------------------------------------------
    async def _emit_denied(
        self,
        session: "Session",
        *,
        agent_id: str,
        action: str,
        payload: dict[str, Any],
        layer: LayerName,
        reason: str,
        correlation: str,
        retry_after_s: int | None = None,
    ) -> None:
        envelope = EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="write.denied",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="guarded_write",
            source_account_id="system",
            owner_scope="shared:household",
            visibility_scope="shared:household",
            sensitivity="normal",
            actor_identity=session.auth_member_id,
            payload={
                "layer_failed": layer,
                "reason": reason,
                "agent_id": agent_id,
                "action": action,
                "payload_echo": payload,
                "review_id": None,
                "retry_after_s": retry_after_s,
                "actor_identity": session.auth_member_id,
            },
        )
        await self._log.append(envelope, correlation_id=correlation)

    async def _emit_review_request(
        self,
        session: "Session",
        *,
        agent_id: str,
        action: str,
        payload: dict[str, Any],
        review_id: str,
        correlation: str,
    ) -> None:
        envelope = EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="review_request",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="guarded_write",
            source_account_id="system",
            owner_scope="shared:household",
            visibility_scope="shared:household",
            sensitivity="normal",
            actor_identity=session.auth_member_id,
            payload={
                "review_id": review_id,
                "agent_id": agent_id,
                "action": action,
                "payload": payload,
                "requested_at": EventEnvelope.now_utc_iso(),
                "actor_identity": session.auth_member_id,
            },
        )
        await self._log.append(envelope, correlation_id=correlation)


__all__ = [
    "ActionGateConfig",
    "AgentAllowlist",
    "GateValue",
    "GuardedWrite",
    "GuardedWriteResult",
    "LayerName",
    "RateLimit",
    "RateLimitDecision",
    "RateLimiter",
    "derive_agent_id",
    "load_agent_allowlist",
    "load_governance_config",
]
