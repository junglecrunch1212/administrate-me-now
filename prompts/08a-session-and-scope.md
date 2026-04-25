# Prompt 08a — Session model + scope enforcement (read side)

**Phase A. Tier B. Pattern: Introduction.**
**Depends on:** 07c-β merged (xlsx round-trip closed; UT-6 RESOLVED). All 11 projections live with `# TODO(prompt-08)` markers in their `queries.py` files.
**Estimated duration:** 3–4 hours, four commits.
**Stop condition:** `Session` constructs from three entry points; `scope` filtering runs at projection-query time; privileged-content redaction works for view-as and coach roles; child HIDDEN_FOR_CHILD filter rejects forbidden tags. All 48 `# TODO(prompt-08)` markers across `adminme/projections/*/queries.py` are gone.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md` lines 1074–1106 — `## L3 CONTINUED: THE SESSION & SCOPE ENFORCEMENT` (34 lines: Session class shape, scope predicates, privileged-channel rules).
2. `ADMINISTRATEME_CONSOLE_PATTERNS.md`:
   - **§1** Tailscale identity resolution (lines 52–144). Console-side identity construction; Session inputs from the console come from this.
   - **§2** Session model and the authMember/viewMember split (lines 145–291). The canonical model 08a implements in Python.
   - **§6** Calendar privacy filtering (lines 860–993). Specifies the `busy` vs `redacted` modes for privileged events; informs `privacy_filter` shape.
   - **§7** HIDDEN_FOR_CHILD navigation filter (lines 994–1097). Specifies the child-tag filter shape.
3. `ADMINISTRATEME_DIAGRAMS.md`:
   - **§4** authMember / viewMember split (lines 340–420). View-as blocking rules.
   - **§5** Session and scope enforcement (lines 421–489). The (auth_role × sensitivity × owner_scope) matrix.
4. `docs/SYSTEM_INVARIANTS.md` §6.1–4 (no global DB connection; authMember/viewMember; writes use authMember; auto-appended scope predicates), §6.9–12 (privileged read rules), §6.17–18 (HIDDEN_FOR_CHILD nav), §3.4 (member_id keying), §9.5 (Tailscale-User-Login resolution), §12 (tenant isolation).
5. `adminme/lib/session.py` — existing 21-line docstring stub from prompt 02. Read in full; the docstring sets the contract this prompt populates.
6. `adminme/lib/scope.py` — existing 14-line docstring stub from prompt 02. Read in full; same.
7. All 10 `adminme/projections/*/queries.py` files. Read enough of each to see `# TODO(prompt-08)` marker context; you will edit every public query function in Commits 2–3.

## Operating context

This is the read-side half of the security backbone. 08a lands Session + scope filtering at read time. 08b will land guardedWrite at write time + observation mode + close UT-7 (reverse-daemon attribution).

**Stub disposition (PM-10): REPURPOSE.** `adminme/lib/session.py` and `adminme/lib/scope.py` exist as docstring-only stubs from prompt 02. Their docstrings define the contract — populate the modules in place. Do NOT delete and recreate; do NOT shadow with new files.

Every read of a projection now requires a Session. The 10 projection `queries.py` files have `# TODO(prompt-08)` markers at every public query function — these markers are the contract 08a fulfills. After 08a merges, none remain.

**Carry-forwards:**
- 07c-β's `_append` helper at `adminme/daemons/xlsx_sync/reverse.py:821` still uses `actor_identity=_ACTOR` literal. **Do NOT modify in 08a.** That fix is 08b's UT-7 closure.
- The forward xlsx daemon (`adminme/projections/xlsx_workbooks/`) reads other projections' query functions to build sheets. After 08a, those builders pass a Session — construct via `build_internal_session("xlsx_workbooks", "device", config.tenant_id)` at builder entry.

## Objective

Populate two stub modules + integrate scope across 10 projection query files + two unit-test files.

## Out of scope

- `adminme/lib/governance.py`, `adminme/lib/observation.py` — both exist as docstring stubs; 08b populates them. Do not touch in 08a.
- `guardedWrite` — writes through projections still use the existing direct-handler path. 08b adds the gate.
- Reverse-daemon UT-7 fix — 08b.
- `tests/integration/test_security_end_to_end.py` — 08b lands it (depends on `guardedWrite`).
- Authority config (`config/authority.yaml`), governance config (`config/governance.yaml`) — 08b loads these.
- Server-side `CHILD_BLOCKED_API_PREFIXES` middleware — 14a per existing 08 draft's out-of-scope clause. 08a names the blocklist constant only.

## Deliverables

### Commit 1 — populate `adminme/lib/session.py` + `tests/unit/test_session.py`

`Session` is a frozen dataclass per BUILD.md 1078–1090:

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
                    "openclaw_standing_order", "bootstrap_wizard", "xlsx_workbooks",
                    "xlsx_reverse_daemon"]
    correlation_id: str | None = None

    @property
    def is_view_as(self) -> bool: ...
    @property
    def allowed_scopes(self) -> frozenset[str]: ...
```

Cite `[§5]`, `[§12]`, `[arch §6]` in module docstring (preserve existing stub docstring content; extend, don't replace).

`ScopeViolation` exception lives in `scope.py` (module-level class). Do not duplicate.

View-as validation per DIAGRAMS §4 blocking table — only principals can view-as; target must be in same household; target cannot be ambient; child sessions cannot view-as.

Three constructors:
- `build_session_from_node(req, config)` — console bridge (consumes Tailscale identity per CONSOLE_PATTERNS §1). Raises `AuthError` on missing/invalid identity.
- `build_session_from_openclaw(request, config)` — slash-command/standing-order dispatch.
- `build_internal_session(actor: str, role: str, tenant_id: str)` — bootstrap, migrations, CLI, daemons, forward xlsx workbook builders.

`tests/unit/test_session.py` — every row of the DIAGRAMS §4 view-as blocking matrix. ≥ 12 tests covering: principal-views-self (allowed), principal-views-other-principal (allowed), principal-views-child (allowed), principal-views-ambient (rejected), child-views-anyone (rejected), ambient-anything (rejected), coach-anything (rejected), construction-from-malformed-input (raises AuthError), tenant_id mismatch (rejected), correlation_id propagation through view-as.

### Commit 2 — populate `adminme/lib/scope.py` + `tests/unit/test_scope.py` + wrap first 5 projection query files

Functions in `scope.py`:
- `class ScopeViolation(Exception)` — raised when a query is called with an out-of-scope Session.
- `allowed_read(session: Session, sensitivity: str, owner_scope: str) -> bool`
- `privacy_filter(session: Session, row: dict) -> dict` — privileged redaction per CONSOLE_PATTERNS §6 (privileged calendar events keep time/duration, drop title/body when viewer doesn't own the privileged content).
- `coach_column_strip(row: dict) -> dict` — strip `financial_*` and `health_*` columns for coach-role per [§13].
- `child_hidden_tag_filter(session: Session, row: dict) -> bool` — return False (drop) if row has a tag in `child_forbidden_tags` for child session per CONSOLE_PATTERNS §7.
- `CHILD_FORBIDDEN_TAGS: frozenset[str]` — module-level constant (the blocklist 14a's middleware will also reference).

Wrap query functions in 5 of the 10 projection `queries.py` files:
- `adminme/projections/parties/queries.py`
- `adminme/projections/interactions/queries.py`
- `adminme/projections/artifacts/queries.py`
- `adminme/projections/commitments/queries.py`
- `adminme/projections/tasks/queries.py`

Every public query function (anything not prefixed `_`) grows a leading `session: Session` parameter. SQL WHERE clauses gain a scope predicate per BUILD.md 1092: `WHERE visibility_scope IN (allowed_scopes) AND (sensitivity != 'privileged' OR owner_scope = current_user)`. Result rows pass through `scope.privacy_filter` and are dropped by `scope.child_hidden_tag_filter` before return. The `# TODO(prompt-08)` markers go away.

Update each file's existing test file for the new signature.

`tests/unit/test_scope.py` — every cell of the DIAGRAMS §5 matrix. Auth roles `{principal, child, ambient, coach_session, device}` × sensitivity `{normal, sensitive, privileged}` × owner_scope `{self, other_principal, shared:household, org:*}`. ≥ 30 tests including ScopeViolation canaries.

### Commit 3 — wrap remaining 5 projection query files

Same wrap pattern, applied to:
- `adminme/projections/recurrences/queries.py`
- `adminme/projections/calendars/queries.py`
- `adminme/projections/places_assets_accounts/queries.py`
- `adminme/projections/money/queries.py`
- `adminme/projections/vector_search/queries.py` — **special case per [§6.10] / [§13.9]:** privileged events never enter `vector_search` at all. Add an entry-point assertion that the session's `allowed_scopes` would even permit; drop any rows with `sensitivity='privileged'` before vector matching; if a query asks for privileged-owned content, return empty (do NOT raise). Note this in a comment cross-referencing `[§13.9]` — this is the UT-8 carve-out.

After Commit 3, `grep -rn 'TODO(prompt-08)' adminme/projections/` returns 0 lines.

Update each file's test file for the new signature.

The `xlsx_workbooks` builders (`adminme/projections/xlsx_workbooks/sheets/*.py`) call other projections' queries. Update each builder's call site to construct an internal Session via `build_internal_session("xlsx_workbooks", "device", config.tenant_id)` once at builder entry; pass that Session to every query call.

### Commit 4 — verification + BUILD_LOG + push + PR

```bash
poetry run ruff check adminme/lib/ adminme/projections/ tests/unit/test_session.py tests/unit/test_scope.py
poetry run mypy adminme/lib/ adminme/projections/ 2>&1 | tail -10
poetry run pytest tests/unit/test_session.py tests/unit/test_scope.py -v
poetry run pytest -v  # full suite still passes

# Confirm zero TODO(prompt-08) markers remain
test "$(grep -rn 'TODO(prompt-08)' adminme/projections/ | wc -l)" = "0" || (echo "TODO(prompt-08) markers remain"; exit 1)

# Cross-cutting invariants
bash scripts/verify_invariants.sh
```

Append to `docs/build_log.md` per template in `docs/qc_rubric.md`. `Outcome: IN FLIGHT (PR open)` until Partner housekeeping fills placeholders.

Carry-forward block:
```markdown
- **Carry-forward for prompt 08b**:
  - Session API surface frozen; 08b imports `Session`, `allowed_read`, `privacy_filter`, `ScopeViolation`, `CHILD_FORBIDDEN_TAGS`.
  - The 10 projection `queries.py` signatures now accept `session: Session`; 08b's guardedWrite consumers can rely on Session being available.
  - UT-7 carries: `adminme/daemons/xlsx_sync/reverse.py` still uses `_ACTOR = "xlsx_reverse"` literal; 08b's surgical edit closes it.
- **Carry-forward for prompt 09a**:
  - Skill runner constructs sessions via `build_internal_session("skill_runner", "device", ...)` for skill-call provenance.
- **Carry-forward for prompt 13a/b**:
  - Product API endpoints construct sessions via `build_session_from_node(req, config)` for every authenticated request.
```

Then `git push`, `gh pr create` (MCP fallback per universal preamble), **stop**.

## Tests

Total ≥ 42 (12 session + 30 scope, both estimates). Plus signature updates across ~10 existing projection test files. Overshoot welcome; undershoot must be justified in PR description.

## Stop

> Session model + scope enforcement landed. All 11 projections (10 sqlite + xlsx) now require a Session for query. Privileged content redacts per role; vector_search excludes privileged outright per [§13.9]. Child HIDDEN_FOR_CHILD filter active. The 48 TODO(prompt-08) markers are gone. Ready for prompt 08b (governance + observation + reverse-daemon UT-7 closure).
