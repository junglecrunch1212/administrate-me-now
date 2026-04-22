# Prompt 08: Session + scope + governance + observation mode

**Phase:** BUILD.md "L3 CONTINUED: THE SESSION & SCOPE ENFORCEMENT" + "AUTHORITY, OBSERVATION, GOVERNANCE".
**Depends on:** Prompts 05, 06, 07 — all 11 projections exist.
**Estimated duration:** 3-4 hours.
**Stop condition:** Session objects correctly filter reads; writes route through guardedWrite with three layers; observation mode wraps every outbound; all security tests pass.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md`:
   - **"L3 CONTINUED: THE SESSION & SCOPE ENFORCEMENT"** — the session model, scope tags, query-time enforcement.
   - **"AUTHORITY, OBSERVATION, GOVERNANCE"** — authMember/viewMember, action gates, observation wrapper.
2. `ADMINISTRATEME_CONSOLE_PATTERNS.md` §2 (session model), §3 (guardedWrite three-layer check), §4 (RateLimiter), §6 (privacy filter), §11 (observation enforcement). The Node patterns are parallel implementations; the Python-side constructs in this prompt must compose cleanly with them.
3. `ADMINISTRATEME_DIAGRAMS.md` §3 (guardedWrite), §4 (authMember/viewMember split), §5 (session+scope matrix), §9 (observation mode fire/suppress).

## Operating context

This is the security backbone. Everything that reads or writes the event log or projections must go through a `Session` object that carries:
- `auth_member_id` — from Tailscale identity (in the Node console) or from the calling context (for internal Python code).
- `auth_role` — principal, child, ambient, coach-session (external-context).
- `view_member_id` — who's data I'm looking at (equals auth_member_id in the fast path; differs when a principal is viewing-as another principal).
- `dm_scope` — per-channel-peer or shared (from OpenClaw when invoked via chat).
- `tenant_id` — the household.

Scope enforcement happens at three layers in reads (session construction, projection query wrappers, privacy filter at response time) and at three layers in writes (agent allowlist, governance action_gate, rate limit) per DIAGRAMS.md §3.

Observation mode is a single enforcement point (see DIAGRAMS.md §9): `outbound()` wraps any external side effect and short-circuits it when observation is on, logging an `observation.suppressed` event instead.

## Objective

Implement four modules:

- `platform/lib/session.py` — Session dataclass + constructor from various inputs; view-as validation.
- `platform/lib/scope.py` — query-time scope filter helpers; privacy filter for privileged content.
- `platform/lib/governance.py` — guardedWrite three-layer check; action_gates loader; RateLimiter.
- `platform/lib/observation.py` — observation wrapper; suppressed-event emission; configuration.

## Out of scope

- Do NOT implement the Node console's guardedWrite (that's prompt 14a). This prompt is the Python counterpart that the Python product APIs (prompt 13a/b) will call.
- Do NOT tie to OpenClaw's exec-approvals yet — document the composition seam in comments; integration is prompt 15.
- Do NOT define the specific action_gates for the household (those come from config in the bootstrap wizard, prompt 16). Define the mechanism; feed it a fixture config for tests.

## Deliverables

### `platform/lib/session.py`

```python
@dataclass(frozen=True)
class Session:
    tenant_id: str
    auth_member_id: str
    auth_role: Literal["principal", "child", "ambient", "coach_session", "device"]
    view_member_id: str  # equals auth_member_id unless view-as is active
    view_role: Literal["principal", "child", "ambient", "device"]
    dm_scope: Literal["per_channel_peer", "shared"]
    source: Literal["node_console", "product_api_internal", "openclaw_slash_command",
                    "openclaw_standing_order", "bootstrap_wizard"]
    correlation_id: str | None = None

    @property
    def is_view_as(self) -> bool: ...
    @property
    def allowed_scopes(self) -> frozenset[str]: ...
```

`build_session_from_node(req, config)` — used by the console bridge; returns Session or raises auth error.

`build_session_from_openclaw(request, config)` — used by slash-command/standing-order dispatch.

`build_internal_session(actor, role, tenant_id)` — for bootstrap wizard, migrations, CLI commands.

View-as validation (see DIAGRAMS.md §4 blocking table):
- Only principals can view-as.
- Target must be in same household.
- Target cannot be ambient (no surface).
- Child sessions cannot view-as anyone.

### `platform/lib/scope.py`

```python
def allowed_read(session: Session, sensitivity: str, owner_scope: str) -> bool: ...

def privacy_filter(session: Session, row: dict) -> dict:
    """Apply redaction if session.auth_member_id doesn't own the privileged content."""
    # privileged + scope != session's scope + view-as case: keep time/duration, drop title/body

def coach_column_strip(row: dict) -> dict:
    """For coach-session, strip financial_* and health_* columns."""

def child_hidden_tag_filter(session: Session, row: dict) -> bool:
    """Returns False (filter out) if row has a tag in child_forbidden_tags for a child session."""
```

Integrate `privacy_filter` and `child_hidden_tag_filter` with each projection's query functions. Projections accept a `session` parameter and filter accordingly. (Modify prompts 05/06/07's query functions to accept `session: Session` — this is the integration work for this prompt.)

### `platform/lib/governance.py`

```python
@dataclass
class ActionGateConfig:
    """Loaded from config/governance.yaml at instance level."""
    action_gates: dict[str, Literal["allow", "review", "deny", "hard_refuse"]]
    rate_limits: dict[str, RateLimit]
    forbidden_outbound_parties: list[dict]  # [{tag: "opposing_counsel"}]

class RateLimiter:
    """Sliding window per (tenant_id, scope, action) key. Per CONSOLE_PATTERNS.md §4."""
    def check_and_record(self, key: str, window_s: int, max_n: int) -> bool: ...

class GuardedWrite:
    """Three-layer check. Per DIAGRAMS.md §3 and CONSOLE_PATTERNS.md §3."""

    def __init__(self, config: ActionGateConfig, limiter: RateLimiter, allowlist: AgentAllowlist): ...

    async def check(self, session: Session, action: str, payload: dict) -> GuardedWriteResult:
        """Returns {pass: bool, layer_failed: str|None, reason: str|None, review_id: str|None,
                    retry_after_s: int|None}. Emits write.denied events on failure."""
```

`AgentAllowlist` — simple lookup: given `(agent_id, action)` (possibly glob), returns allow/deny. Config-driven.

Contract for callers: all product API routes that mutate state call `await guarded_write.check(session, action, payload)` first. On fail, return the corresponding HTTP status (403, 202, or 429); on pass, proceed.

### `platform/lib/observation.py`

```python
@dataclass
class ObservationState:
    active: bool
    enabled_at: datetime | None
    enabled_by: str | None

class ObservationManager:
    """Per DIAGRAMS.md §9 and CONSOLE_PATTERNS.md §11."""

    def __init__(self, event_log: EventLog, config_path: Path): ...

    async def is_active(self) -> bool: ...
    async def enable(self, actor: str, reason: str) -> None: ...
    async def disable(self, actor: str, reason: str) -> None: ...

async def outbound(session: Session, action: str, payload: dict, action_fn: Callable) -> Any:
    """
    Wrapper around external side effects (messaging.send, email.send, push.send, reminder.write, etc.)
    If observation mode is active: emits observation.suppressed, does NOT call action_fn.
    Otherwise: calls action_fn, emits external.sent on success.
    """
```

Every L5 surface that sends externally must call `outbound()`. Every standing-order pipeline that wants to deliver externally must too.

### Tests

`tests/unit/test_session.py` — all the view-as blocking cases from DIAGRAMS.md §4.

`tests/unit/test_scope.py` — the matrix from DIAGRAMS.md §5. Every (auth_role × sensitivity × owner_scope) combination has an expected outcome; test all of them.

`tests/unit/test_governance.py` — action_gate allow/review/deny/hard_refuse paths; rate limit exhaustion; forbidden-party hard refusal.

`tests/unit/test_observation.py` — outbound() suppresses when active; suppressed event has full payload; external.sent emitted when active == false.

Integration test `tests/integration/test_security_end_to_end.py` — a realistic flow: James (principal) view-as Laura, tries to complete a task that Laura "owns", verify task.completed has `actor: james, owner: laura`, verify privileged event in Laura's scope is redacted in James's read.

## Verification

```bash
poetry run pytest tests/unit/test_session.py tests/unit/test_scope.py tests/unit/test_governance.py tests/unit/test_observation.py tests/integration/test_security_end_to_end.py -v

# Previous tests still pass
poetry run pytest -v

git add platform/lib/ tests/
git commit -m "phase 08: session + scope + governance + observation"
```

## Stop

**Explicit stop message:**

> Security backbone in. Sessions carry identity; scope enforcement runs at query time; guardedWrite gates writes in three layers; observation mode can suppress any outbound. Projections now require a Session to query. Ready for prompt 09a (skill runner wrapper around OpenClaw).
