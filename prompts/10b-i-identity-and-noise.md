# Prompt 10b-i: Reactive pipelines — identity_resolution + noise_filtering

**Phase:** BUILD.md §L4 (reactive pipelines, first half).
**Depends on:** Prompt 10a merged (PR #33). `Pipeline` Protocol, `PipelineContext`, `PipelinePackLoadError`, `LoadedPipelinePack`, `load_pipeline_pack()`, `PipelineRunner.{register,discover,start,stop,status}()` all live on main.
**Quality bar:** 09b (single skill pack) + 10a (pipeline subsystem). Reuse the 09b skill-pack shape and the 10a fixture-pipeline shape literally.
**Stop condition:** Two reactive pipeline packs registered and discoverable; one new skill pack registered and loadable; two new event types at v1; one round-trip integration test per pipeline against the live runner; suite green; `verify_invariants.sh` exit 0.

---

## Universal preamble

> **Phase + repository + documentation + sandbox discipline.**
>
> You are in **Phase A**: generating code in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live OpenClaw, live BlueBubbles, live Plaid, or any other external service. Tests that require those are marked `@pytest.mark.requires_live_services` and skipped. If a prompt is ambiguous about which phase it belongs to, the answer is always Phase A.
>
> **Sandbox egress is allowlisted.** `github.com` and `raw.githubusercontent.com` work. Most other hosts return HTTP 403 `x-deny-reason: host_not_allowed` from Anthropic's proxy. A 403 does not mean the site is down — it means the sandbox won't reach it. If a prompt tells you to WebFetch a non-GitHub URL and you get 403, that's expected; document the gap and move on per prompt 00.5's pattern.
>
> **Session-start sequence (required):**
> ```
> git checkout main
> git pull origin main
> git checkout -b phase-10b-i-identity-and-noise
> ```
> The harness may auto-reassign you to `claude/<random>` regardless of the `-b` name. Work on whatever branch you actually get — do not fight it. Do NOT `git pull` again during the session. Do NOT push to `main`. You open a PR at the end; James reviews and merges.
>
> **Poetry install as needed.** If `pytest` fails with `ModuleNotFoundError: No module named 'sqlcipher3'` (or similar), run `poetry install 2>&1 | tail -5` and retry. Sandbox warm-state quirk; do not fix in code.
>
> **Read before acting.** When a prompt tells you to read something, READ IT — do not skim, do not assume, do not infer from training. Use targeted line ranges (`sed -n '<start>,<end>p'`) for large files; never full-read `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_CONSOLE_PATTERNS.md`, `ADMINISTRATEME_REFERENCE_EXAMPLES.md`, `ADMINISTRATEME_DIAGRAMS.md`, or `ADMINISTRATEME_CONSOLE_REFERENCE.html`. If a file or range listed in a prompt's "Read first" block does not exist in the repo, stop and report — do not proceed.
>
> **External documentation is mirrored.** When a prompt says "per OpenClaw docs" or "per Plaid docs" or any external-doc reference, read from `docs/reference/<section>/` (the local mirror populated by prompt 00.5). Do NOT use WebFetch to pull these docs live. If a referenced file is missing, either the mirror is incomplete (stop and finish prompt 00.5) or the content is a documented gap (check `docs/reference/_gaps.md`).
>
> **Four-commit discipline.** Every prompt structures its work as four incremental commits — typically schema/plumbing, first-module build, second-module build, then integration + verification + BUILD_LOG append + push. If a turn times out mid-commit, stop; James re-launches. Commit 4 includes appending the structured entry to `docs/build_log.md` (template in the prompt's Commit 4 block) — this is not a separate PR, it's part of Commit 4's changeset.
>
> **Cross-cutting invariant verification** is `bash scripts/verify_invariants.sh` — one line in Commit 4's verification block. It checks [§8]/[D6] (no LLM/embedding SDK imports in adminme/), [§15]/[D15] (no hardcoded instance paths), [§12.4] (no tenant identity in platform code), [§2.2] (projections emit only allowed system events, only from allowed files), and pipeline → projection direct writes. Exits non-zero on any violation and prints the offending lines. Do not duplicate its checks inline in the prompt.
>
> **Schema conventions.** New event types register at `v1` per [D7]. Migrations (and upcasters when the schema shape changes) compose forward only. Projection schemas use SQLite `CHECK` constraints on closed-enum columns (e.g. `kind IN (...)`, `status IN (...)`, `sensitivity IN ('normal','sensitive','privileged')`) and NOT on open columns (display_name, category, notes). Composite PK `(tenant_id, <entity_id>)` for multi-tenant projection tables. Cross-DB FK references are documentation-only comments; SQLite cannot enforce FKs across separate projection DBs per [§2.3].
>
> **Tenant-identity firewall.** Platform code under `adminme/` must not reference "James", "Laura", "Charlie", "Stice", "Morningside", or any other specific tenant name per [§12.4]. These names live in `tests/fixtures/` only, with `# fixture:tenant_data:ok` on the relevant line if ambiguity warrants. If a prompt's illustrative example uses a name, it's illustrative — the shipped code stays tenant-agnostic. The verify script catches violations.
>
> **Citation discipline.** When a decision or invariant shapes code, cite it in the code comment or docstring. Formats: `[§N]` for SYSTEM_INVARIANTS.md section N, `[DN]` for DECISIONS.md entry N, `[arch §N]` for architecture-summary.md section N, `[cheatsheet Qn]` for openclaw-cheatsheet.md question n. BUILD_LOG entries under Evidence cite the invariants that shaped the implementation.
>
> **Async-subscriber test discipline.** When a test appends an event and then reads a projection, it MUST call `notify(event_id)` on the bus and then `_wait_for_checkpoint(bus, subscriber_id, event_id)` before the read assertion. For "event NOT landing" tests (privileged-skipped, filter rejected, etc.), append a follow-up innocuous event after the one under test, notify + wait-for-checkpoint on the follow-up, THEN assert the original's absence. Without the follow-up, the subscriber may not have processed the earlier event yet and your "absence" assertion is just a timing artifact.
>
> **Failure-mode handler-direct discipline.** When a test wants to assert "a malformed write does not land" (CHECK-constraint failure, IntegrityError, schema-validation reject), call the handler or sheet-builder function directly with a test connection — do not route through the bus + subscriber. Routing a deliberately-bad event through the bus puts the subscriber in a degraded state and wrecks subsequent tests.
>
> **Mypy preflight for new libraries.** If a prompt adds an import from a library not already in the codebase, run `poetry run mypy adminme/ 2>&1 | tail -10` before Commit 1. If mypy complains about missing stubs, add the library to the `[[tool.mypy.overrides]]` block in `pyproject.toml` with `ignore_missing_imports = true` as part of Commit 1.
>
> **PR creation with gh/MCP fallback.** After pushing your branch, try `gh pr create` first. If `gh` returns `command not found` or a GitHub API permission error, fall back to `mcp__github__create_pull_request` with `base=main`, `head=<your branch>`, `owner=junglecrunch1212`, `repo=administrate-me-now`, title + body from the prompt's template. If the MCP tool also fails: report the exact error and stop. Do not retry with modified flags — James decides next step.
>
> **Post-PR: one check, then stop.** After the PR opens, the MCP tool returns a webhook-subscription message. Do ONE round of `mcp__github__pull_request_read` with `method=get_status`, `get_reviews`, and `get_comments`. Report whatever is returned. Then STOP. Do not poll again. Do not respond to webhook events that arrive after the stop message. Do not merge the PR yourself.

---

## Read first (required)

Use `sed -n '<start>,<end>p'` for the BUILD.md ranges. Do NOT full-read BUILD.md. Read every section listed before producing Commit 1.

1. **`ADMINISTRATEME_BUILD.md` §L4 — pipeline definitions for the two pipelines this prompt ships.**
   - `sed -n '1107,1140p' ADMINISTRATEME_BUILD.md` — section header + `identity_resolution` + `noise_filtering` definitions.
2. **`ADMINISTRATEME_REFERENCE_EXAMPLES.md` §3 — skill-pack shape (canonical reference; this prompt ships one new skill pack in this shape).**
   - The §3 header is at line ~1200 (verify by `grep -n "^## 3\." ADMINISTRATEME_REFERENCE_EXAMPLES.md`); read that section through its end. The 09b prompt shipped `classify_thank_you_candidate` against this shape — read `packs/skills/classify_thank_you_candidate/` end-to-end as the live reference (it's smaller than reading §3 in full and matches what's actually on main).
3. **`adminme/pipelines/base.py`, `adminme/pipelines/pack_loader.py`, `adminme/pipelines/runner.py`** — full read. These are the seams 10b-i's pipelines plug into. The `Pipeline` Protocol in `base.py` is the contract every pipeline class must satisfy. `PipelineContext` is what `handle()` receives.
4. **`tests/fixtures/pipelines/echo_emitter/pipeline.yaml`** and **`tests/fixtures/pipelines/echo_emitter/handler.py`** — full read. These are the smallest possible reference for the pack shape. 10b-i's two pipelines will look like longer versions of this.
5. **`adminme/projections/parties/queries.py`** — read `find_party_by_identifier()` (lines 47–65) in full. `identity_resolution` calls this; the signature is `(conn, session, *, kind, value_normalized) -> dict | None`.
6. **`adminme/events/schemas/ingest.py`** — full read (~100 lines). Confirms the `MessagingReceivedV1` payload uses `from_identifier` / `to_identifier` / `source_channel` (NOT the `from_handle` / `from_handle_kind` shape shown in REFERENCE_EXAMPLES.md §2 — that example pre-dates this schema).
7. **`adminme/events/schemas/crm.py`** — full read (~85 lines). Confirms `party.created` and `identifier.added` v1 shapes; `identity.merge_suggested` will be appended next to `party.merged`.
8. **`adminme/lib/skill_runner/__init__.py` + `wrapper.py`** — `__init__.py` in full; for `wrapper.py` read `class SkillContext` (lines 99–108), `class SkillResult` (lines 109–118), and the `async def run_skill(...)` signature (lines 511–520 — just the signature, not the body). This is the seam `noise_filtering`'s pipeline calls.
9. **`packs/skills/classify_thank_you_candidate/`** — full directory listing + read `pack.yaml`, `SKILL.md` (frontmatter only — first 25 lines), `schemas/input.schema.json`, `schemas/output.schema.json`, `handler.py`, `tests/test_skill.py`. This is the canonical 09b skill pack you're cloning the shape of for `classify_message_nature`.
10. **`docs/build_log.md`** — read the **prompt 10a entry** (search for `### Prompt 10a`) end to end. Carry-forwards into 10b live there.
11. **`docs/SYSTEM_INVARIANTS.md`** — §7 (pipelines) and §8 (no LLM SDKs). Targeted reads only — open the file and find these sections by header.

After all reads complete, BEFORE running mypy preflight, write a one-paragraph orientation comment to your scratchpad confirming: (a) you understand the `Pipeline` Protocol, (b) you understand `find_party_by_identifier`'s signature, (c) you understand the 09b skill-pack shape, (d) you have not assumed any field on `messaging.received` or `telephony.sms_received` that isn't in `ingest.py`'s actual schema. If any of (a)–(d) is shaky, re-read the relevant file before proceeding.

---

## Operating context

- Reactive pipelines run inside the `PipelineRunner` from 10a. Each pipeline is a directory under `packs/pipelines/<name>/` with `pipeline.yaml` + `handler.py`. The runner discovers them via `discover(builtin_root, installed_root)`. 10b-i ships two such directories.
- `packs/pipelines/` does not yet exist in the repo — Commit 2 creates it. (The directory is not auto-created by 10a's discover call; missing dirs are tolerated per `runner.py:101`.)
- `find_party_by_identifier(conn, session, *, kind, value_normalized)` returns a dict on hit, None on miss.
  - `kind` mapping for 10b-i: derive from `MessagingReceivedV1.source_channel`. Imessage → `"imessage_handle"`; gmail / icloud / hotmail / etc. → `"email"`. For `TelephonySmsReceivedV1`, kind = `"phone"`. Use a small mapping helper in the pipeline; do NOT hardcode tenants or providers.
  - `value_normalized` for 10b-i: `from_identifier.lower().strip()` for emails and handles; for phones, strip non-digit characters. Real normalization will get richer later — keep this simple and document the simplification.
- `[ADR-0002]` says skills are invoked via `await ctx.run_skill_fn(skill_id, inputs, SkillContext(session=ctx.session, correlation_id=ctx.correlation_id))`. `noise_filtering` calls `classify_message_nature` exactly once per inbound event.
- `[§7.3]` — pipelines NEVER write a projection row directly. They emit events; projections consume them. `verify_invariants.sh`'s pipeline→projection canary will catch violations.
- `[§7.4]` / `[§8]` / `[D6]` — pipelines NEVER import provider SDKs. `noise_filtering` calls `classify_message_nature` through `ctx.run_skill_fn` only.
- Pipelines emit events through `ctx.event_log.append(envelope, correlation_id=..., causation_id=ctx.triggering_event_id)`. The `causation_id=ctx.triggering_event_id` wiring is the 09a/10a carry-forward.
- The pipeline's class name in `runtime.class` (per `pipeline.yaml`) must match a class in `handler.py` that satisfies the `Pipeline` Protocol — `pack_id: str`, `version: str`, `triggers: Triggers`, `async def handle(event, ctx)`. The `LoadedPipelinePack` cache keys on `(pack_id, version)`; if you re-run the runner in a test, call `invalidate_cache()`.

### What's NOT in scope for this prompt

- `commitment_extraction`, `thank_you_detection` — prompt 10b-ii.
- `extract_thank_you_fields`, `classify_commitment_candidate`, `extract_commitment_fields` skills — prompt 10b-ii.
- `commitment.suppressed` event type — prompt 10b-ii.
- Aggregating multiple `messaging.classified` events into one `interactions` row — that's a projection-side change, not pipeline. The TODO at `adminme/projections/interactions/handlers.py:15` stays.
- Auto-merging at any threshold. `identity_resolution` ALWAYS emits `identity.merge_suggested` (above threshold) or `party.created` + `identifier.added` (below threshold). Human approval handled later (no event to emit on approval yet — that's prompt 14b inbox view + a sidecar that subscribes to approval clicks).
- Per-member tuning / config files / dedupe windows. Those land in 10b-ii's `commitment_extraction` (which has the per-member-thresholds reference example to follow).
- Proactive pipelines. Prompt 10c.
- OpenClaw standing-order registration. Prompt 10c.

---

## Deliverables

### Commit 1 — schemas, plumbing, and `classify_message_nature` skill pack

**File: `adminme/events/schemas/crm.py`** — append `IdentityMergeSuggestedV1` model + `registry.register("identity.merge_suggested", 1, IdentityMergeSuggestedV1)`. Schema:

- `surviving_party_id: str` (min_length=1) — the existing party the new identifier might belong to.
- `candidate_value: str` (min_length=1) — the unresolved `from_identifier` from the inbound event.
- `candidate_kind: str` (min_length=1) — `email | phone | imessage_handle` (Literal type — closed enum).
- `candidate_value_normalized: str` (min_length=1).
- `confidence: float` (ge=0.0, le=1.0).
- `heuristic: str` — short label: `levenshtein_display_name | email_domain_match | phone_prefix_match`.
- `source_event_id: str` (min_length=1) — the `messaging.received` / `telephony.sms_received` that triggered this suggestion.
- `model_config = {"extra": "forbid"}`.

**File: `adminme/events/schemas/ingest.py`** — append `MessagingClassifiedV1` model + `registry.register("messaging.classified", 1, MessagingClassifiedV1)`. Schema:

- `source_event_id: str` (min_length=1) — the `messaging.received` / `telephony.sms_received` this classification refers to.
- `classification: Literal["noise", "transactional", "personal", "professional", "promotional"]` (BUILD.md §1136).
- `confidence: float` (ge=0.0, le=1.0).
- `skill_name: str` (min_length=1) — e.g., `"classify_message_nature"`.
- `skill_version: str` (min_length=1).
- `model_config = {"extra": "forbid"}`.

**File: `packs/skills/classify_message_nature/`** — full 09b-shape skill pack. Files (clone shape from `packs/skills/classify_thank_you_candidate/`):

- `pack.yaml` — `pack.id: skill:classify_message_nature`, `version: 2.0.0` (BUILD.md §1136 names it `@v2`), `kind: skill`. Documentation-only `model:` block (the loader reads frontmatter, not pack.yaml's model section per the 09b precedent).
- `SKILL.md` — frontmatter: `name: classify_message_nature`, `namespace: adminme`, `version: 2.0.0`, `description:` (one-line — "Classify an inbound message as noise, transactional, personal, professional, or promotional."), `input_schema: ./schemas/input.schema.json`, `output_schema: ./schemas/output.schema.json`, `provider_preferences: [anthropic/claude-haiku-4-5, anthropic/claude-opus-4-7]`, `max_tokens: 200`, `temperature: 0.1`, `sensitivity_required: normal`, `context_scopes_required: []`, `timeout_seconds: 8`, `outbound_affecting: false`, `on_failure: { classification: "personal", confidence: 0.0, reasons: [skill_failure_defensive_default] }`. The `on_failure` defaults to `personal` rather than `noise` because mis-classifying real messages as `noise` (and dropping them from the inbox) is worse than mis-classifying noise as `personal`. Add a 3-paragraph "What classifies as each" section after frontmatter — one paragraph per category, plain prose.
- `schemas/input.schema.json` — JSON Schema Draft 2020-12. Required: `body_text` (string), `source_channel` (string), `from_identifier` (string). Optional: `subject` (string), `from_party_known` (boolean — has a Party already been resolved for this sender). All other fields rejected (`additionalProperties: false`).
- `schemas/output.schema.json` — Required: `classification` (enum: `noise | transactional | personal | professional | promotional`), `confidence` (number, 0–1), `reasons` (array of strings). `additionalProperties: false`.
- `prompt.jinja2` — instruct the model to output JSON matching the output schema. Be brief; include the five categories with one-line descriptions; instruct that low-confidence outputs default to `personal`.
- `handler.py` — `def post_process(raw, inputs, ctx) -> dict`: defensive coercion mirroring `classify_thank_you_candidate/handler.py`. If `raw` is not a dict, return `{"classification": "personal", "confidence": 0.0, "reasons": ["skill_post_process_non_dict_input"]}`. If `classification` is missing or not in the enum, coerce to `personal` with confidence 0.0 and a `reason` documenting which value was rejected.
- `tests/test_skill.py` — clone the 09b test pattern. Three tests: `test_pack_loads_cleanly`, `test_handler_passes_through_well_formed_output`, `test_handler_coerces_when_classification_missing`. Use `from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack` and `PACK_ROOT = Path(__file__).resolve().parents[1]` per 09b. (Do NOT route through the wrapper — handler-direct discipline.)
- No `tests/fixtures/` subdir on the skill pack. The 09b pattern includes fixture YAMLs but only for fully-mocked OpenClaw integration tests; 10b-i's integration tests live in `tests/integration/` and don't read skill-pack fixtures.

**Commit 1 verification:**
```
poetry run mypy adminme/ 2>&1 | tail -10   # mypy preflight per universal preamble
poetry run pytest tests/unit/test_event_registry.py -v   # confirms two new types register at v1
poetry run pytest packs/skills/classify_message_nature/tests/ -v   # 3 tests pass
git add adminme/events/schemas/crm.py adminme/events/schemas/ingest.py packs/skills/classify_message_nature/
git commit -m "phase 10b-i commit 1: schemas (identity.merge_suggested, messaging.classified) + classify_message_nature@2.0.0 skill pack"
```

### Commit 2 — `identity_resolution` pipeline pack

**File: `packs/pipelines/identity_resolution/pipeline.yaml`** — pack manifest. Required keys per `adminme/pipelines/pack_loader.py`:

```yaml
pack:
  id: pipeline:identity_resolution
  name: Identity resolution
  version: 1.0.0
  kind: pipeline
runtime:
  language: python
  python_version: ">=3.11"
  entrypoint: handler.py
  class: IdentityResolutionPipeline
triggers:
  events:
    - messaging.received
    - messaging.sent
    - telephony.sms_received
    # TODO(prompt-XX): subscribe to telephony.voicemail_transcribed, telephony.call_received
    # once those event types register. The pipeline's resolve-identifier logic
    # is identical for those events — the trigger list is the only addition.
depends_on:
  skills: []           # heuristic-only; no skill calls
  projections:
    - parties          # for find_party_by_identifier
events_emitted:
  - identity.merge_suggested
  - party.created
  - identifier.added
```

**File: `packs/pipelines/identity_resolution/handler.py`** — class `IdentityResolutionPipeline` implementing `Pipeline`. Logic per BUILD.md §1120–1130:

1. Class attributes: `pack_id = "pipeline:identity_resolution"`, `version = "1.0.0"`, `triggers: Triggers = {"events": ["messaging.received", "messaging.sent", "telephony.sms_received"]}`.
2. `async def handle(event, ctx)`:
   a. Extract `from_identifier` from event payload. For `messaging.received` / `messaging.sent`, the field is `payload["from_identifier"]` (or `payload["to_identifier"]` for `messaging.sent`'s outbound — but per BUILD.md §1120 only inbound resolves; if `event["type"] == "messaging.sent"`, return without action — outbound resolution is the recipient's responsibility, deferred). For `telephony.sms_received`, `payload["from_number"]`.
   b. Determine `kind` and `value_normalized` from a small `_classify_identifier(event)` helper (see below).
   c. Open a SQLCipher connection to the parties DB via `ctx.session.tenant_id` and `InstanceConfig`-resolved path. **Do not** hardcode the path — pull from the `instance_config` that was used to build the runner. (See "Open question for orientation" below for the path-resolution seam.)
   d. Call `find_party_by_identifier(conn, ctx.session, kind=kind, value_normalized=value_normalized)`. If hit → return; nothing to emit.
   e. If miss: compute similarity to all existing identifiers of the same `kind` (load via a small read query — keep ≤100 most recent for safety). Use one heuristic per `kind`:
      - `email`: domain-tail equality + Levenshtein on local part. Score = 1.0 - (lev / max(len(local_a), len(local_b))). Keep only candidates with same domain tail.
      - `phone`: last-7-digit prefix match → 1.0 if exact prefix; else 0.0.
      - `imessage_handle`: Levenshtein on full handle string normalized.
   f. If best candidate score ≥ 0.85: emit `identity.merge_suggested` with `surviving_party_id = best.party_id`, `candidate_value = from_identifier`, `candidate_kind = kind`, `candidate_value_normalized = value_normalized`, `confidence = score`, `heuristic = <heuristic name>`, `source_event_id = event["event_id"]`. Use `causation_id = ctx.triggering_event_id` and `correlation_id = ctx.correlation_id` per the 10a carry-forward.
   g. If best score < 0.85 (or no candidates): emit `party.created` with a fresh `party_id = "party_" + ulid()` (use `ulid_new()` from `adminme.events.envelope`), `kind = "person"`, `display_name = from_identifier`, `sort_name = from_identifier.lower()`. Then immediately emit `identifier.added` with `identifier_id = "ident_" + ulid()`, `party_id` (matching), `kind = <derived kind>`, `value = from_identifier`, `value_normalized`, `verified = False`, `primary_for_kind = True`. Both events use `causation_id = ctx.triggering_event_id`.
   h. Never auto-merge above threshold. The 0.85 case emits a `merge_suggested` event for the human; it does NOT modify any party.

3. Module-level helper `_classify_identifier(event_type: str, payload: dict) -> tuple[str, str, str]` — returns `(from_identifier, kind, value_normalized)`. Source-channel-to-kind mapping uses `payload.get("source_channel", "")` for messaging events; for `telephony.sms_received`, kind is unconditionally `"phone"`. Channel-name mapping: contains `"sms"` → `"phone"`; contains `"imessage"` → `"imessage_handle"`; otherwise → `"email"`. Document this mapping in a docstring; cite BUILD.md §3.2 (CRM identifiers) so readers know it's a placeholder until a real adapter ships and richer normalization is needed.

**Connection-management note (carry-forward from 10a):** the runner does not own per-pipeline DB connections. The pipeline opens its read-only connection inside `handle()` and closes via context manager. There's a small overhead per event but it's correct — connection pooling lands later. Use `with sqlcipher3.connect(...) as conn:` with the SQLCipher key from `ctx.session.encryption_key` if exposed, else from `instance_config.encryption_key`. **(See "Open question for orientation" — if neither path is exposed yet, the pipeline's `handle()` becomes a no-op for events whose tenant the runner cannot reach; emit a single warning log per session and move on. Do not fail. The integration test will assert only the schema-validation path; identity-resolution full round-trip is in 10b-ii's responsibility set if it requires a deeper plumbing pass.)**

**Open question for orientation:** verify in `adminme/lib/instance_config.py` and `adminme/lib/session.py` whether the parties projection's connection-key + path is reachable from `PipelineContext`. If yes, code the full round-trip. If no — surface this in your scratchpad after Reads but BEFORE Commit 1, **stop and report to James**. Do NOT silently downgrade the pipeline to a no-op without confirmation; that's a contract change. Acceptable resolutions: (1) extend `PipelineContext` with a `parties_conn_factory` callable threaded from runner construction; (2) defer DB read to 10b-ii and have `identity_resolution` here only emit `identity.merge_suggested` based on a stub-empty candidate list (degenerate but architecturally clean — every miss creates a new party). Pick whichever the depth-read supports cleanly. Document the choice in the BUILD_LOG entry.

**File: `packs/pipelines/identity_resolution/tests/test_pack_load.py`** — pack-loader canary. One test:
- `test_pack_loads_cleanly`: invalidate pipeline cache, `load_pipeline_pack(PACK_ROOT)`, assert `pack_id == "pipeline:identity_resolution"`, `version == "1.0.0"`, `triggers["events"] == [...]`, instance is a `Pipeline` (Protocol check via `isinstance` with `runtime_checkable`). Mirrors `tests/unit/test_pipeline_pack_loader.py` style.

**File: `tests/unit/test_pipeline_identity_resolution.py`** — handler-direct unit tests against the class. Construct a fake `PipelineContext` (frozen dataclass; provide stubs for `event_log` / `run_skill_fn` / `outbound_fn` / `guarded_write` / `observation_manager`). Tests:
- `test_messaging_sent_returns_without_emit` — outbound events skip per logic step (a/b).
- `test_email_classification` — `_classify_identifier` returns `("ada@example.com", "email", "ada@example.com")` for a `messaging.received` event with `source_channel="gmail"`.
- `test_imessage_classification` — same helper returns `kind="imessage_handle"` for `source_channel="imessage_adminme"`.
- `test_phone_classification` — for `telephony.sms_received` payload, returns `kind="phone"` and digits-only normalized.
- `test_unknown_sender_emits_party_and_identifier` — feed an event whose sender is unresolved (`find_party_by_identifier` returns None and similarity yields no above-threshold candidate). Capture `event_log.append` calls. Assert exactly two emits: `party.created` then `identifier.added`. Both with `causation_id=triggering_event_id`.
- `test_above_threshold_emits_merge_suggested` — feed an event whose sender's identifier scores 0.92 via `_email_score` against an existing party. Assert exactly one emit: `identity.merge_suggested`. Confidence rounded to 0.92. Heuristic = `email_domain_match` (or whichever applies).
- `test_below_threshold_emits_party_created` — sender scores 0.4 against best candidate. Two emits: party + identifier (no merge_suggested). This is the safety-net path that prevents auto-merge.

**Commit 2 verification:**
```
poetry run pytest packs/pipelines/identity_resolution/tests/ tests/unit/test_pipeline_identity_resolution.py -v   # ≥ 7 tests pass
git add packs/pipelines/identity_resolution/ tests/unit/test_pipeline_identity_resolution.py
git commit -m "phase 10b-i commit 2: identity_resolution pipeline pack + unit tests"
```

### Commit 3 — `noise_filtering` pipeline pack

**File: `packs/pipelines/noise_filtering/pipeline.yaml`** — same shape as identity_resolution. Differences:
- `pack.id: pipeline:noise_filtering`
- `runtime.class: NoiseFilteringPipeline`
- `triggers.events: [messaging.received, telephony.sms_received]`
- `depends_on.skills: [classify_message_nature@^2.0.0]`
- `events_emitted: [messaging.classified]`

**File: `packs/pipelines/noise_filtering/handler.py`** — class `NoiseFilteringPipeline`. Logic per BUILD.md §1132–1138:

1. Class attributes mirror identity_resolution's pattern.
2. `async def handle(event, ctx)`:
   a. Extract message text. For `messaging.received` → `payload["body_text"]` (may be None — short-circuit return if absent or empty after strip; emit nothing). For `telephony.sms_received` → `payload["body"]`.
   b. Build skill inputs: `{"body_text": <text>, "source_channel": <derived>, "from_identifier": <derived>, "subject": <if present>, "from_party_known": <bool>}`. For `from_party_known`, do NOT call into the parties DB here — pass `False` if you can't determine cheaply. (The skill prompt is robust to this hint being conservative.)
   c. Call `result = await ctx.run_skill_fn("skill:classify_message_nature", inputs, SkillContext(session=ctx.session, correlation_id=ctx.correlation_id))`. Import `SkillContext` from `adminme.lib.skill_runner`.
   d. On `SkillInputInvalid` / `SkillOutputInvalid` / `OpenClawTimeout` / `OpenClawUnreachable`: emit a `messaging.classified` with classification = the SKILL.md `on_failure.classification` value (`"personal"`), confidence = 0.0, `skill_version = "2.0.0"`, `skill_name = "classify_message_nature"`, source_event_id = triggering id. **Do not let exceptions propagate up to the runner** — that would non-advance the bus checkpoint per `[§7.7]`, blocking subsequent events on every classification failure. The pipeline's contract is "always emit `messaging.classified` on a triggered event"; defensive-default classification is the failure mode, not exception propagation.
   e. On success: emit `messaging.classified` with `classification = result.output["classification"]`, `confidence = result.output["confidence"]`, `skill_name = "classify_message_nature"`, `skill_version = result.output.get("skill_version", "2.0.0")` — the wrapper response shape is in `wrapper.py`'s `SkillResult`; use that contract literally rather than inventing fields. `causation_id = ctx.triggering_event_id`.
   f. The pipeline does NOT delete or filter anything itself. It only tags via the emitted classification event. Downstream (a future `interactions` projection update) decides whether to suppress the row.

**File: `packs/pipelines/noise_filtering/tests/test_pack_load.py`** — pack-loader canary, same shape as identity_resolution's.

**File: `tests/unit/test_pipeline_noise_filtering.py`** — handler-direct unit tests:
- `test_empty_body_returns_without_emit` — `messaging.received` with `body_text=None`, no emit.
- `test_skill_success_emits_classified` — stub `run_skill_fn` returning `SkillResult(output={"classification": "transactional", "confidence": 0.91, "reasons": [...]}, ...)`. Assert one emit: `messaging.classified` with classification=`transactional`, confidence=0.91.
- `test_skill_failure_emits_defensive_default` — stub `run_skill_fn` raising `OpenClawTimeout`. Assert one emit: `messaging.classified` with classification=`personal`, confidence=0.0.
- `test_skill_input_invalid_emits_defensive_default` — same but raising `SkillInputInvalid`.
- `test_telephony_sms_uses_body_field` — feed a `telephony.sms_received` payload; assert the skill_inputs passed to `run_skill_fn` include `body_text=<sms body>`.

**Commit 3 verification:**
```
poetry run pytest packs/pipelines/noise_filtering/tests/ tests/unit/test_pipeline_noise_filtering.py -v   # ≥ 6 tests pass
git add packs/pipelines/noise_filtering/ tests/unit/test_pipeline_noise_filtering.py
git commit -m "phase 10b-i commit 3: noise_filtering pipeline pack + unit tests"
```

### Commit 4 — integration tests, BUILD_LOG, push

**File: `tests/integration/test_pipeline_10b_i_integration.py`** — round-trip tests against the live `PipelineRunner`. Use the same harness pattern as `tests/integration/test_pipeline_runner_integration.py` (the 10a integration file): construct an in-memory `EventBus` + `EventLog` + `InstanceConfig` (tmp_path fixture), instantiate the runner, register the pipeline pack via `register()` directly (bypass `discover()` to avoid filesystem walk overhead in tests; `discover()` is exercised by 10a's integration suite already). Tests:

1. `test_identity_resolution_emits_party_created_for_unknown_sender` — append a `messaging.received` from `unknown@example.com`, notify, wait-for-checkpoint. Assert event log contains a subsequent `party.created` and `identifier.added`, both with `causation_id == messaging.received.event_id`.
2. `test_identity_resolution_skips_messaging_sent` — append `messaging.sent`, notify + wait + follow-up + wait-for-checkpoint on follow-up (per universal preamble's "absence" pattern). Assert no `party.created` or `identity.merge_suggested` in the log.
3. `test_noise_filtering_emits_classified_with_stubbed_skill` — register the noise_filtering pipeline; monkeypatch `adminme.pipelines.runner.run_skill` (the import the runner uses) to return a deterministic `SkillResult(output={"classification":"transactional", "confidence":0.93, "reasons":[]}, openclaw_invocation_id="stub", provider="stub", input_tokens=10, output_tokens=5, cost_usd=0.0001, duration_ms=42)`. Append a `messaging.received` event. Notify + wait. Assert a `messaging.classified` event lands with `classification == "transactional"` and `causation_id == triggering id`.
4. `test_noise_filtering_skill_failure_lands_defensive_default` — same harness; monkeypatch `run_skill` to raise `OpenClawTimeout`. Assert a `messaging.classified` lands with `classification == "personal"` and `confidence == 0.0`.

For monkeypatching the skill runner, use `monkeypatch.setattr("packs.pipelines.noise_filtering.handler.run_skill", stub)` if the handler imports the function directly, OR pass `ctx.run_skill_fn = stub` if the test constructs `PipelineContext` manually. Pick one approach during depth-read and apply consistently. The test must NOT call OpenClaw — there's no live OpenClaw in Phase A (universal preamble).

**File: `docs/build_log.md`** — append entry under the existing `## Build prompts` section, AFTER the prompt-10a entry. Template (fill placeholders BEFORE pushing — Partner fills `<PR-N>` / `<sha1>...<sha4>` / `<merge-date>` post-merge):

```markdown
### Prompt 10b-i — reactive pipelines (identity_resolution + noise_filtering)
- **Refactored**: by Partner in Claude Chat, 2026-04-26. Prompt file: `prompts/10b-i-identity-and-noise.md` (~<NNN> lines, quality bar = 09b + 10a). Pre-split memo at `docs/2026-04-26-prompt-10b-split.md` (single-purpose PR landed before this prompt).
- **Session merged**: PR #<N>, commits <sha1> / <sha2> / <sha3> / <sha4>, merged <merge-date>.
- **Outcome**: IN FLIGHT (PR open).
- **Evidence**:
  - `packs/pipelines/identity_resolution/{pipeline.yaml,handler.py,tests/test_pack_load.py}` — `IdentityResolutionPipeline` heuristic-only resolver; emits `party.created` + `identifier.added` on miss; `identity.merge_suggested` above 0.85 threshold; never auto-merges per BUILD.md §1130.
  - `packs/pipelines/noise_filtering/{pipeline.yaml,handler.py,tests/test_pack_load.py}` — `NoiseFilteringPipeline` calls `classify_message_nature` once per inbound; emits `messaging.classified` with full skill provenance; defensive-default = "personal" / confidence 0.0 on skill failure (does NOT propagate exceptions per [§7.7]).
  - `packs/skills/classify_message_nature/` — full 09b-shape skill pack at v2.0.0 (BUILD.md §1136 names it `classify_message_nature@v2`). 3 unit tests via pack-loader canary + handler-direct.
  - `adminme/events/schemas/crm.py` — appended `IdentityMergeSuggestedV1` registered at v1.
  - `adminme/events/schemas/ingest.py` — appended `MessagingClassifiedV1` registered at v1.
  - Integration tests at `tests/integration/test_pipeline_10b_i_integration.py` — 4 round-trip tests against the live runner.
  - Total new tests: <count by file>; suite tally <before> → <after> passed.
  - `[§7.3]` (no projection direct writes): pipelines emit only via `ctx.event_log.append`; pipeline→projection canary in `verify_invariants.sh` clean.
  - `[§7.4]` / `[§8]` / `[D6]`: zero new SDK imports (heuristics are pure-Python; the one skill call goes through `ctx.run_skill_fn` per [ADR-0002]).
  - `[D7]`: both new event types register at v1.
  - `verify_invariants.sh` exit 0.
- **Carry-forward for prompt 10b-ii** (commitment_extraction + thank_you_detection):
  - `find_party_by_identifier` is now backed by parties auto-created by `identity_resolution`; commitment_extraction's sender-resolution step has a non-empty hit rate.
  - `messaging.classified` events let commitment_extraction skip noise/transactional classifications cheaply (subscribe to the classification, not the raw inbound).
  - The pipeline-pack shape pattern (yaml + handler + test_pack_load) is now duplicated; 10b-ii continues this exact shape.
  - The defensive-default-on-skill-failure pattern from `noise_filtering` carries forward; commitment_extraction does the same on `classify_commitment_candidate` failure (suppress the proposal rather than crash).
- **Carry-forward for prompt 10c** (proactive pipelines):
  - The `triggers.events: []` + `triggers.proactive: true` shape is the unfilled half of the manifest contract; 10b-i's two manifests use only `triggers.events`.
- **Carry-forward for prompt 16** (bootstrap wizard):
  - `bootstrap §8` runs `PipelineRunner.discover(builtin_root=adminme/pipelines/pipeline_packs, installed_root=instance_config.packs_dir/"pipelines")`. The path layout is `packs/pipelines/<name>/pipeline.yaml`. Bootstrap copies builtin packs into the instance dir on first run.
- **Carry-forward for prompt 19** (Phase B smoke test):
  - Confirm `identity_resolution` correctly resolves a test sender against a seeded party, and `noise_filtering` calls real OpenClaw and classifies a transactional receipt as `transactional`.
```

**Commit 4 verification:**
```
poetry run ruff check . 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -20   # full suite green; ≥ 25 new tests across 10b-i
bash scripts/verify_invariants.sh   # exit 0
git add docs/build_log.md tests/integration/test_pipeline_10b_i_integration.py
git commit -m "phase 10b-i commit 4: integration tests + BUILD_LOG"
git push origin HEAD
```

PR title: `phase 10b-i: reactive pipelines (identity_resolution + noise_filtering)`

PR body: copy the BUILD_LOG `Evidence` section verbatim. Add a top line: "Closes the first half of the prompt-10b split per `docs/2026-04-26-prompt-10b-split.md`. 10b-ii (commitment_extraction + thank_you_detection) ships separately."

After PR opens: one round of `mcp__github__pull_request_read` (status, reviews, comments), report, stop.

---

## Stop

Two reactive pipeline packs registered. `classify_message_nature@2.0.0` skill pack live. Two new event types at v1. Round-trip integration verified. Ready for 10b-ii (commitment_extraction + thank_you_detection).
