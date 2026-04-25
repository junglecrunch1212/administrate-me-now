# Prompt 08b — Governance + observation mode + reverse-daemon attribution (UT-7)

**Phase A. Tier B. Pattern: Introduction.**
**Depends on:** 08a merged (Session and scope live; all 10 projection `queries.py` accept `Session`).
**Estimated duration:** 3–4 hours, four commits.
**Stop condition:** `guardedWrite.check` returns the four-layer result; `outbound()` short-circuits when observation is active; reverse-daemon emits attribute `principal_member_id` from a Session; `tests/integration/test_security_end_to_end.py` passes; UT-7 closes (or splits to 08.5 sidecar — see Commit 3 hedge).

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md` lines 2053–2168 — `## AUTHORITY, OBSERVATION, GOVERNANCE` (115 lines: authority.yaml shape, governance.yaml shape, observation env var + runtime override, privileged-access log).
2. `ADMINISTRATEME_CONSOLE_PATTERNS.md`:
   - **§3** guardedWrite three-layer write check (lines 292–560). Canonical algorithm.
   - **§4** RateLimiter sliding window (lines 561–662). Exact window mechanics.
   - **§11** Observation mode enforcement (lines 1576–1689). `outbound()` wrapper contract.
3. `ADMINISTRATEME_DIAGRAMS.md`:
   - **§3** guardedWrite three-layer check (lines 233–339).
   - **§9** Observation mode fire/suppress (lines 787–910).
4. `docs/SYSTEM_INVARIANTS.md` §6.5–8 (guardedWrite layer order, hard_refuse non-overridable, review-gate semantics), §6.13–16 (observation enforcement at final outbound only, default-on, per-tenant scope, suppressed-event payload completeness), §13 (privileged-access log items 1–12), §14 (proactive-behavior scheduling boundary).
5. `docs/DECISIONS.md` D5 (governance config schema, if present), D8 (observation default-on for new instances, if present). If either undecided, append a new D entry as part of Commit 1.
6. `adminme/lib/session.py`, `adminme/lib/scope.py` — both populated by 08a. Verify before starting Commit 1.
7. `adminme/lib/governance.py` — existing 25-line docstring stub from prompt 02. Read in full; docstring defines the contract this prompt populates.
8. `adminme/lib/observation.py` — existing 23-line docstring stub from prompt 02. Read in full; same.
9. `adminme/daemons/xlsx_sync/reverse.py`:
   - **Line 91** (`_ACTOR = "xlsx_reverse"` literal).
   - **Lines ~407, ~412, ~431, ~436, ~843, ~848** (the `actor_identity=_ACTOR` and `source_adapter="xlsx_reverse"` sites — line numbers may shift ±2; verify on merged main).
   - **Lines ~820–852** (the `_append` envelope-construction helper). Single seam where attribution gets written. UT-7 closure modifies this helper.
10. `adminme/events/schemas/system.py` — read to understand existing system-event registration pattern; you add five new event types in Commit 1.

## Operating context

This is the write-side half of the security backbone. 08a landed read-side (Session + scope at query time). 08b lands write-side (`guardedWrite` three-layer + observation `outbound()`) and closes UT-7 by routing reverse-daemon emits through Session/`guardedWrite`.

**Stub disposition (PM-10): REPURPOSE.** `governance.py` and `observation.py` exist as docstring-only stubs from prompt 02. Their docstrings define the contract — populate in place. Do NOT delete and recreate.

`guardedWrite` is the canonical write gate. Every product API route that mutates state will eventually call it (prompts 13a/b consume). Three layers: agent allowlist → governance action_gate → rate limit. First refusal short-circuits; denial event records `layer_failed`.

`observation_mode` is enforced at the **final outbound filter** per [§6.13]. All internal logic runs normally; only external side effects suppressed. `outbound()` is the single enforcement point.

UT-7 carry-forward from 07c-β: reverse daemon emits with `actor_identity = "xlsx_reverse"` (literal), bypassing principal attribution. After 08b: daemon constructs Session from detected workbook editor; routes `_append` through `guardedWrite.check` for write authorization plus Session-derived `actor_identity`.

**Carry-forwards:**
- After 08b, all writes through `_append` helpers in daemons must construct a Session. Pattern lands here for xlsx reverse; later prompts (11+) replicate for Gmail/Plaid.
- `config/governance.yaml` and `config/authority.yaml` are loaded but not yet authored for the household — the bootstrap wizard (prompt 16) populates production. 08b ships fixtures in `tests/fixtures/governance/` and `tests/fixtures/authority/` for tests.

## Objective

Populate two stub modules + register five new event types + populate two fixture configs + one surgical edit to the reverse daemon (or split to 08.5 sidecar) + three test files (two unit, one integration) + UT-7 closure case.

## Out of scope

- Authoring household-specific authority/governance configs (bootstrap wizard prompt 16).
- Node console's parallel guardedWrite (14a).
- OpenClaw `exec-approvals` integration (15 — document composition seam in module docstring only).
- Plaid `observation_mode_active` payload field on `xlsx.regenerated` — already shipped in 07c-α.
- Adapter-specific `outbound()` callers (Gmail/Plaid) — land with each adapter prompt.

## Deliverables

### Commit 1 — populate `adminme/lib/governance.py` + register 5 new event types + fixtures + `tests/unit/test_governance.py`

**Five new event types, all v1 per [D7], registered in `adminme/events/schemas/system.py`:**

1. `write.denied` — governance attribution: `layer_failed ∈ {"allowlist", "governance", "rate_limit"}`, `reason: str`, `payload_echo: dict`.
2. `review_request` — held-for-review; emitted on review gate. Includes pending action + payload + reviewer-target identity.
3. `observation.suppressed` — full would-have-sent payload + `observation_mode_active: bool`.
4. `observation.enabled` / `observation.disabled` — state-change audit. Includes `actor: str`, `reason: str`, `prior_state: bool`.
5. `external.sent` — success-path companion to `observation.suppressed`. Same payload shape; emitted only when observation is inactive.

**`scripts/verify_invariants.sh` `ALLOWED_EMITS` does NOT need updating.** These are emitted from product code / outbound wrappers, not from projections. The `[§2.2]` projection-emit canary is unaffected. Confirm none of the new emits ends up wired to `adminme/projections/*`.

`adminme/lib/governance.py` populates per CONSOLE_PATTERNS §3 + DIAGRAMS §3:

```python
@dataclass
class ActionGateConfig:
    """Loaded from config/governance.yaml. Per BUILD.md 2120-2145."""
    action_gates: dict[str, Literal["allow", "review", "deny", "hard_refuse"]]
    rate_limits: dict[str, "RateLimit"]
    forbidden_outbound_parties: list[dict]

@dataclass(frozen=True)
class GuardedWriteResult:
    pass_: bool
    layer_failed: str | None  # "allowlist" / "governance" / "rate_limit" / None
    reason: str | None
    review_id: str | None
    retry_after_s: int | None

class RateLimiter:
    """Sliding window per (tenant_id, scope, action). Per CONSOLE_PATTERNS §4."""
    def check_and_record(self, key: str, window_s: int, max_n: int) -> bool: ...

class AgentAllowlist:
    """Config-driven (agent_id, action) lookup; supports glob patterns."""
    def is_allowed(self, agent_id: str, action: str) -> bool: ...

class GuardedWrite:
    """Three-layer check. Per DIAGRAMS §3 + CONSOLE_PATTERNS §3."""
    def __init__(self, config, limiter, allowlist, event_log): ...
    async def check(self, session, action, payload) -> GuardedWriteResult:
        """allowlist → action_gate → rate_limit. First refusal short-circuits.
        Emits write.denied on failure with layer_failed attribution."""
```

Fixture configs:
- `tests/fixtures/governance/sample_governance.yaml`
- `tests/fixtures/authority/sample_authority.yaml`

Each marked `# fixture:tenant_data:ok` per [§12.4]. Use BUILD.md's `<persona.handle>` placeholder pattern — no tenant identity bleed.

`tests/unit/test_governance.py` — action_gate paths (allow / review / deny / hard_refuse, including hard_refuse non-overridable verification with admin-equivalent session); rate-limit exhaustion (sliding-window correctness); short-circuit-on-first-refusal ordering; `write.denied.layer_failed` correctness for each layer; forbidden-party hard refusal; `review_request` event structure; glob pattern matching in `AgentAllowlist`. ≥ 18 tests.

### Commit 2 — populate `adminme/lib/observation.py` + `tests/unit/test_observation.py`

Per CONSOLE_PATTERNS §11 + DIAGRAMS §9:

```python
@dataclass(frozen=True)
class ObservationState:
    active: bool
    enabled_at: datetime | None
    enabled_by: str | None

class ObservationManager:
    def __init__(self, event_log: EventLog, runtime_config_path: Path): ...
    async def is_active(self) -> bool: ...
    async def enable(self, actor: str, reason: str) -> None: ...
    async def disable(self, actor: str, reason: str) -> None: ...

async def outbound(session: Session, action: str, payload: dict,
                   action_fn: Callable) -> Any:
    """If active: emits observation.suppressed; does NOT call action_fn.
    If inactive: calls action_fn, emits external.sent on success."""
```

State persisted to `config/runtime.yaml` per BUILD.md 2149. Default-on for new instances per [§6.16] / D8 (or new D entry if undecided).

`tests/unit/test_observation.py` — `outbound()` suppresses when active; `observation.suppressed` has full would-have-sent payload (target, channel, payload preview); `external.sent` emitted when active==false; toggle persistence (round-trip through `runtime.yaml`); default-on at fresh-instance bootstrap (no `runtime.yaml`); `enable`/`disable` emit `observation.enabled` / `observation.disabled` events. ≥ 12 tests.

### Commit 3 — UT-7 closure + integration test (with sidecar hedge)

**Sidecar hedge: probe `adminme/daemons/xlsx_sync/reverse.py` first.** If the rewrite is mechanical (wrap each emit site with `outbound()` and route through Session) → stays in this commit. If it touches more than one structural seam (e.g., the `_append` helper signature changes plus per-pathway plumbing in 4+ `_emit_*` methods plus the `_ACTOR` constant removal triggers ≥100 lines of net change) → **stop and write `prompts/08.5-reverse-daemon-outbound-rewrite.md` as a 15–25 minute sidecar memo**, defer the rewrite to a 08.5 standalone PR after 08b ships, and ship 08b without UT-7 closure (carry UT-7 forward; close it when 08.5 merges).

Surgical edits if rewrite stays in 08b:

1. **Extend `adminme/lib/session.py`** with `build_session_from_xlsx_reverse_daemon(detected_member_id: str | None, config: InstanceConfig) -> Session`. If `detected_member_id` is `None`: system-internal `device`-role session. If detected: Session attributing that principal as actor with role from household config.

2. **Edit `adminme/daemons/xlsx_sync/reverse.py`:**
   - Remove `_ACTOR = "xlsx_reverse"` literal at line 91.
   - The `_append` helper (~lines 820–852) takes `session: Session`; derives `actor_identity` from `session.auth_member_id`. `source_adapter` stays `"xlsx_reverse"` (it is the *adapter* identity per [arch §3] row schema — that's correct). Only `actor_identity` changes.
   - Each `_emit_*` method (`_emit_skip` ~423, `_emit_diff` ~448, `_emit_tasks` ~479, `_emit_task_deleted` ~543, `_emit_commitments` ~560, `_emit_recurrences` ~600, `_emit_raw_data` ~664, `_emit_money_flow_deleted` ~733) constructs/receives a Session via `build_session_from_xlsx_reverse_daemon` and passes to `_append`.
   - Each emit routes through `guarded_write.check(session, "<event_type>", payload)`. On `pass=False`: emit `write.denied`, skip the `_append`.

3. **`tests/integration/test_security_end_to_end.py`** — realistic flow: principal_a view-as principal_b, completes a task that principal_b owns, verify `task.completed` carries `actor: <principal_a>, owner: <principal_b>`, verify privileged event in B's scope is redacted in A's read.

4. **Extend `tests/integration/test_xlsx_round_trip.py`** with UT-7 closure case: simulate workbook edit detected to specific principal_member_id; assert resulting `task.completed` event's `actor_identity` equals that principal_member_id (NOT literal `"xlsx_reverse"`).

### Commit 4 — verification + BUILD_LOG + push + PR

```bash
poetry run ruff check adminme/lib/ adminme/daemons/ adminme/events/schemas/ tests/unit/test_governance.py tests/unit/test_observation.py tests/integration/test_security_end_to_end.py
poetry run mypy adminme/lib/ adminme/daemons/ 2>&1 | tail -10
poetry run pytest tests/unit/test_governance.py tests/unit/test_observation.py tests/integration/test_security_end_to_end.py tests/integration/test_xlsx_round_trip.py -v
poetry run pytest -v  # full suite still passes

# Confirm UT-7 closure (skip if sidecar hedge activated)
test "$(grep -nE '_ACTOR\s*=' adminme/daemons/xlsx_sync/reverse.py | wc -l)" = "0" \
  || (echo "_ACTOR literal still present in reverse.py — UT-7 not closed; expected if 08.5 sidecar hedge activated"; exit 0)

# Cross-cutting invariants
bash scripts/verify_invariants.sh
```

Append to `docs/build_log.md` per template. If sidecar hedge activated: mark UT-7 as carrying forward to 08.5; otherwise mark UT-7 RESOLVED.

```markdown
- **Carry-forward for prompt 09a**:
  - Skill runner outbound calls go through `outbound(session, action, payload, action_fn)` from observation.py.
  - Skill calls go through `guarded_write.check(session, "skill.invoke", ...)`.
- **Carry-forward for prompt 11+**:
  - All adapters that emit domain events on external authority follow the reverse-daemon pattern: build Session attributing detected principal, pass to `_append`, route through `guarded_write.check`.
- **UT-7 status**:
  - If rewrite landed: RESOLVED 2026-MM-DD.
  - If sidecar hedge activated: carries to 08.5; close on 08.5 merge.
```

Then `git push`, `gh pr create` (MCP fallback), **stop**.

## Tests

Total ≥ 30 (18 governance + 12 observation, both estimates) plus 1 new integration test plus UT-7 closure case in xlsx round-trip integration test (or deferred to 08.5).

## Stop

> Security backbone complete (write side). Writes route through `guardedWrite` three-layer; observation mode wraps every outbound; five new event types registered. UT-7 status: RESOLVED if reverse-daemon rewrite stayed in this prompt; otherwise carries to 08.5 sidecar. Ready for prompt 09a (skill runner wrapper around OpenClaw).
