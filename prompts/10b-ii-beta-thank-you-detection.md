# Prompt 10b-ii-β: Reactive pipelines — thank_you_detection + extract_thank_you_fields

**Phase:** `BUILD.md` §L4 (reactive pipelines, second half — second wave).
**Depends on:**
- Prompt 10b-ii-α merged (PR #41). Parties-DB seam through `PipelineContext.parties_conn_factory` + `PipelineRunner.__init__` is on main. `commitment_extraction` pipeline pack is the canonical reference for two-skill-chain pipelines. `commitment.suppressed` event type is registered at v1.
- Prompt 09b merged. `classify_thank_you_candidate@1.3.0` is on main at `packs/skills/classify_thank_you_candidate/`. This pipeline's binary classify step uses the existing 09b skill; only the second skill (`extract_thank_you_fields`) is new.
- The `sequence-update-10b-ii-split` PR (#39) merged 2026-04-27. This prompt's row exists in `prompts/PROMPT_SEQUENCE.md`'s sequence table.

**Quality bar:** smaller and simpler than 10b-ii-α. Reuse the 10b-ii-α pipeline-pack shape literally. Reuse the 09b skill-pack shape literally. No new infrastructure. No new event types.

**Stop condition:** `thank_you_detection` pipeline pack registered and discoverable; `extract_thank_you_fields` skill pack registered and loadable; one round-trip integration test against the live runner; suite green; `verify_invariants.sh` exit 0; ≥15 new tests across 10b-ii-β.

---

## Universal preamble

> **Phase + repository + documentation + sandbox discipline.**
>
> You are in **Phase A**: generating code in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live OpenClaw, live BlueBubbles, live Plaid, or any other external service. Tests that require those are marked `@pytest.mark.requires_live_services` and skipped. If a prompt is ambiguous about which phase it belongs to, the answer is always Phase A.
>
> **Sandbox egress is allowlisted.** `github.com` and `raw.githubusercontent.com` work. Most other hosts return HTTP 403 `x-deny-reason: host_not_allowed` from Anthropic's proxy. A 403 does not mean the site is down — it means the sandbox won't reach it. If a prompt tells you to WebFetch a non-GitHub URL and you get 403, that's expected; document the gap and move on per prompt 00.5's pattern.
>
> **Session-start sequence (required):**
>
> ```
> git checkout main
> git pull origin main
> git checkout -b phase-10b-ii-beta-thank-you-detection
> ```
>
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
> **Cross-cutting invariant verification** is `bash scripts/verify_invariants.sh` — one line in Commit 4's verification block. It checks `[§8]`/`[D6]` (no LLM/embedding SDK imports in `adminme/`), `[§15]`/`[D15]` (no hardcoded instance paths), `[§12.4]` (no tenant identity in platform code), `[§2.2]` (projections emit only allowed system events, only from allowed files), and pipeline → projection direct writes. Exits non-zero on any violation and prints the offending lines. Do not duplicate its checks inline in the prompt.
>
> **Schema conventions.** New event types register at `v1` per `[D7]`. Migrations (and upcasters when the schema shape changes) compose forward only. Projection schemas use SQLite `CHECK` constraints on closed-enum columns and NOT on open columns. Composite PK `(tenant_id, <entity_id>)` for multi-tenant projection tables. Cross-DB FK references are documentation-only comments; SQLite cannot enforce FKs across separate projection DBs per `[§2.3]`.
>
> **Tenant-identity firewall.** Platform code under `adminme/` must not reference "James", "Laura", "Charlie", "Stice", "Morningside", or any other specific tenant name per `[§12.4]`. These names live in `tests/fixtures/` only, with `# fixture:tenant_data:ok` on the relevant line if ambiguity warrants. If a prompt's illustrative example uses a name, it's illustrative — the shipped code stays tenant-agnostic. The verify script catches violations.
>
> **Citation discipline.** When a decision or invariant shapes code, cite it in the code comment or docstring. Formats: `[§N]` for `SYSTEM_INVARIANTS.md` section N, `[DN]` for `DECISIONS.md` entry N, `[arch §N]` for `architecture-summary.md` section N, `[cheatsheet Qn]` for `openclaw-cheatsheet.md` question n. BUILD_LOG entries under Evidence cite the invariants that shaped the implementation.
>
> **Async-subscriber test discipline.** When a test appends an event and then reads a projection, it MUST call `notify(event_id)` on the bus and then `_wait_for_checkpoint(bus, subscriber_id, event_id)` before the read assertion. For "event NOT landing" tests, append a follow-up innocuous event after the one under test, notify + wait-for-checkpoint on the follow-up, THEN assert the original's absence.
>
> **Failure-mode handler-direct discipline.** When a test wants to assert "a malformed write does not land" (CHECK-constraint failure, IntegrityError, schema-validation reject), call the handler or sheet-builder function directly with a test connection — do not route through the bus + subscriber.
>
> **Mypy preflight for new libraries.** If a prompt adds an import from a library not already in the codebase, run `poetry run mypy adminme/ 2>&1 | tail -10` before Commit 1. If mypy complains about missing stubs, add the library to the `[[tool.mypy.overrides]]` block in `pyproject.toml` with `ignore_missing_imports = true` as part of Commit 1.
>
> **PR creation with gh/MCP fallback.** After pushing your branch, try `gh pr create` first. If `gh` returns `command not found` or a GitHub API permission error, fall back to `mcp__github__create_pull_request` with `base=main`, `head=<your branch>`, `owner=junglecrunch1212`, `repo=administrate-me-now`, title + body from the prompt's template. If the MCP tool also fails: report the exact error and stop.
>
> **Post-PR: one check, then stop.** After the PR opens, the MCP tool returns a webhook-subscription message. Do ONE round of `mcp__github__pull_request_read` with `method=get_status`, `get_reviews`, and `get_comments`. Report whatever is returned. Then STOP. Do not poll again. Do not respond to webhook events that arrive after the stop message. Do not merge the PR yourself.

---

## Read first (required)

Use `sed -n '<start>,<end>p'` for the `BUILD.md` / `REFERENCE_EXAMPLES.md` ranges. Do NOT full-read either file. Read every section listed before producing Commit 1.

1. **`docs/02-split-memo-10b-ii.md`** — full read, focus on §"10b-ii-β". This pins the scope: `extract_thank_you_fields` skill pack + `thank_you_detection` pipeline pack. No new infrastructure. Reuses 10b-ii-α's parties-DB seam, defensive-default pattern, per-member-overrides config shape.
2. **`prompts/10b-ii-alpha-commitment-extraction.md`** — full read. This is your quality bar AND your literal copy-the-shape reference. Replicate its section ordering, four-commit decomposition, Read-first discipline, Operating Context, BUILD_LOG template, PR title/body shape.
3. **`packs/pipelines/commitment_extraction/{pipeline.yaml,handler.py,config.example.yaml,config.schema.json}`** — full read all four. This is the on-main shape `thank_you_detection` clones. The handler's `_classify_identifier` helper, `_load_config` helper, `_thresholds_for_member` helper, defensive-default `except` tuple, sender-resolution path, and `_emit_suppressed` method — all reusable patterns. **`thank_you_detection` is structurally simpler:** one binary classifier upstream + one extractor downstream, vs. `commitment_extraction`'s classify-then-extract. The skill chain is the same shape; the kind/urgency mapping at the end differs.
4. **`packs/pipelines/commitment_extraction/tests/test_pack_load.py`** — full read. Pack-load canary template.
5. **`tests/unit/test_pipeline_commitment_extraction.py`** — full read. Handler-direct unit-test patterns: fake event log, factory closure, seeded SQLCipher parties DB, `_config_override` test seam, threshold-path / suppression-reason / F-2 widening / per-member-overrides cases. Clone the structure literally.
6. **`tests/integration/test_pipeline_10b_ii_alpha_integration.py`** — full read. Integration test pattern: tmp `InstanceConfig`, seeded parties DB via `_seed_parties_db`, monkeypatched `adminme.pipelines.runner.run_skill`. Clone literally.
7. **`packs/skills/classify_thank_you_candidate/{pack.yaml,SKILL.md,schemas/input.schema.json,schemas/output.schema.json,handler.py,prompt.jinja2,tests/test_skill.py}`** — full read all seven. **This is the upstream classifier 10b-ii-β consumes — it is NOT changed by this prompt.** Confirms output shape your pipeline will receive: `{is_candidate: bool, confidence: float, urgency?: str, suggested_medium?: str, reasons: list[str]}`. Note: `urgency` and `suggested_medium` are present only when `is_candidate=true` (per the JSON Schema's `if/then`). The pipeline must handle the absent-on-false case.
8. **`packs/skills/extract_commitment_fields/`** — full directory read. The 10b-ii-α extract-skill shape; `extract_thank_you_fields` clones it with substitutions.
9. **`adminme/pipelines/base.py`** — full read. Confirms `PipelineContext.parties_conn_factory` is on main from 10b-ii-α. No changes here.
10. **`adminme/pipelines/runner.py`** — read only `__init__` signature + `_make_callback`. Confirms `parties_conn_factory` threading is on main from 10b-ii-α. No changes here.
11. **`adminme/events/schemas/domain.py`** — full read. Confirms `CommitmentProposedV1` shape (`kind` Literal, `urgency` Literal, etc.) AND `CommitmentSuppressedV1` shape (`reason` Literal includes `below_confidence_threshold`, `dedupe_hit`, `skill_failure_defensive_default`). **No new event types in this prompt.** `thank_you_detection` emits `commitment.proposed` with `kind="other"` (default path) OR `commitment.suppressed` (below-confidence / skill-failure path), reusing the schemas already on main.
12. **`adminme/events/schemas/ingest.py`** — already loaded if you read 10b-ii-α. Confirms `MessagingReceivedV1` payload shape (`from_identifier`, `to_identifier`, `source_channel`, `body_text`).
13. **`adminme/lib/skill_runner/__init__.py`** — full read. Confirms the seven exception types the defensive-default `except` tuple catches: `OpenClawTimeout`, `OpenClawUnreachable`, `OpenClawResponseMalformed`, `SkillInputInvalid`, `SkillOutputInvalid`, `SkillSensitivityRefused`, `SkillScopeInsufficient`. Same tuple as `commitment_extraction`.
14. **`ADMINISTRATEME_BUILD.md` §L4 — `thank_you_detection` definition.**
   - `sed -n '1148,1160p' ADMINISTRATEME_BUILD.md` — verify the start line by `grep -n "^#### \`thank_you_detection\`" ADMINISTRATEME_BUILD.md` first. The section is short (~10 lines) — reads "Specialization of commitment extraction for gratitude … Owner-scoped (James's thank-yous are James's; Laura's are Laura's …)." This is the **owner-scope clue.** **Open architectural question (resolve at depth-read time):** does §1150 imply `thank_you` should be its own kind in `CommitmentProposedV1.kind`'s Literal, or does `kind="other"` suffice with a thank-you-decision reason string in `classify_reasons`? **Default disposition: ship `kind="other"` and DO NOT silently extend the enum.** Extending `CommitmentProposedV1.kind` is a forward-only Literal-extension migration per `[D7]` and is OUT OF SCOPE for this prompt (would require its own upcaster, projection schema review, downstream consumer audit). If §1150 reads as strictly requiring the enum extension, stop and report — James will scope a follow-up. The 10b-ii-α build_log carry-forward explicitly cautioned: "do NOT silently extend the enum."
15. **`ADMINISTRATEME_REFERENCE_EXAMPLES.md` §3** — the `classify_thank_you_candidate` worked example. The §3 header is around line 1029 (verify by `grep -n "^## 3\." ADMINISTRATEME_REFERENCE_EXAMPLES.md`). Read sections in chunks of ≤200 lines: `sed -n '1029,1200p'` for the skill pack, then `sed -n '1201,1380p'` for tests/fixtures. This pre-dates the current pipeline shape; use it for **structure** only — your authoritative shape is `commitment_extraction`'s on-main code.
16. **`docs/build_log.md`** — read the **prompt 10b-ii-α entry** (search for `### Prompt 10b-ii-α`) end to end. Carry-forwards into 10b-ii-β are listed there. Especially the F-5 carry-forward: outbound `messaging.sent` defensive-default emits audit-trail noise; address in this prompt by an early-return at the top of `handle()` for `messaging.sent`.
17. **`docs/SYSTEM_INVARIANTS.md`** — §7 (pipelines) and §8 (no LLM SDKs). Targeted reads only. Already read for 10b-ii-α; refresh if needed.

After all reads complete, BEFORE running mypy preflight, write a one-paragraph orientation comment to your scratchpad confirming: (a) you understand the existing parties-DB seam from 10b-ii-α and that you are NOT modifying `adminme/pipelines/`, (b) you understand `classify_thank_you_candidate`'s output shape including the `urgency`/`suggested_medium` conditional presence, (c) you understand the 09b skill-pack shape and will clone it for `extract_thank_you_fields`, (d) you have NOT silently extended `CommitmentProposedV1.kind`'s Literal — `kind="other"` is the v1 path, (e) you understand the F-5 carry-forward (early-return on `messaging.sent` at the top of `handle()`). If any of (a)–(e) is shaky, re-read the relevant file before proceeding.

---

## Operating context

- **No new infrastructure.** `PipelineContext.parties_conn_factory` is on main from 10b-ii-α. The pipeline opens its own per-call connection inside `handle()` via `with ctx.parties_conn_factory() as conn:`. Runner-side wiring is unchanged.
- **No new event types.** Reuse `commitment.proposed` (existing v1) for the success path with `kind="other"`. Reuse `commitment.suppressed` (existing v1, registered in 10b-ii-α) for below-threshold and defensive-default paths.
- **The skill chain is asymmetric:** the binary classifier (`classify_thank_you_candidate@^1.3.0`) already exists on main from 09b. This prompt only ships the second-stage `extract_thank_you_fields@^1.0.0` skill pack. The pipeline calls classify → if `is_candidate=true` and confidence ≥ threshold → calls extract → emits `commitment.proposed`. Below threshold or `is_candidate=false` → emits `commitment.suppressed`.
- **`classify_thank_you_candidate` output shape (from 09b):** `is_candidate: bool`, `confidence: float`, `reasons: list[str]`, plus `urgency: 'within_24h'|'this_week'|'within_month'|'no_rush'` and `suggested_medium: 'text'|'email'|'handwritten_card'|'small_gift'` **only when `is_candidate=true`** (per the JSON Schema `if/then`). The pipeline must handle the absent-on-false case — checking `is_candidate` before reading `urgency` / `suggested_medium`.
- **`extract_thank_you_fields` output shape (this prompt's new skill):**
  - `recipient_party_id: str` (min_length=1) — typically the upstream sender.
  - `suggested_text: str` (min_length=1, max_length=500) — the proposed thank-you note body.
  - `urgency: Literal["today", "this_week", "this_month", "no_rush"]` — matches `CommitmentProposedV1.urgency`'s Literal exactly.
  - `confidence: float` (ge=0.0, le=1.0).
  - **Note the urgency-vocabulary alignment:** `classify_thank_you_candidate`'s urgency Literal (`within_24h | this_week | within_month | no_rush`) does NOT match `CommitmentProposedV1.urgency`'s Literal (`today | this_week | this_month | no_rush`). The pipeline does NOT round-trip the classify-side urgency directly — it lets the extractor produce the canonical urgency value, which round-trips into `CommitmentProposedV1` cleanly. Do NOT attempt to coerce/translate the classify-side urgency at the pipeline layer; it's a hint to the extractor only (and is not even required to be passed through, since the extractor sees the same message text).
- **`commitment.proposed` shape for thank-you path:**
  - `commitment_id: str` — generated as `cmt_<token>` per the 10b-ii-α pattern.
  - `kind: "other"` — **default v1 disposition per the BUILD.md §1150 open question. Do NOT extend the Literal.**
  - `owed_by_member_id: str` — the receiving member from the inbound (the household member who received the message and would be sending the thank-you).
  - `owed_to_party_id: str` — the recipient party (typically the message sender, propagated from extractor output).
  - `text_summary: str` — the extractor's `suggested_text`, truncated to 500 chars.
  - `urgency: Literal[...]` — from extractor output.
  - `confidence: float` — from classifier output (the binary classifier's confidence, NOT the extractor's; same convention as `commitment_extraction`).
  - `strength: "confident" | "weak"` — `"confident"` if classifier confidence ≥ `review_threshold`, else `"weak"`.
  - `classify_reasons: list[str]` — from classifier output.
  - `source_message_preview: str | None` — first 240 chars of the inbound's `body_text`.
  - `source_interaction_id: str | None` — the inbound's `thread_id`.
- **F-5 carry-forward (outbound audit-trail noise):** the handler's first action after the type guard is `if event_type == "messaging.sent": return`. This is a behavioral departure from `commitment_extraction`'s outbound-defensive-suppress path; document the rationale in the docstring with a citation to the F-5 build_log entry. The reasoning: thank-yous are extracted from inbound only — what the household received, not what they sent.
- **Per-member overrides** ride on `config.example.yaml` per the same shape `commitment_extraction` uses. Same keys: `min_confidence`, `review_threshold`, `dedupe_window_hours`, `per_member_overrides`, `skip_party_tags`. Reuse the placeholder member-id pattern (`member_id_example_*`) per `[§12.4]`.
- **Dedupe is deferred** to the same future projection-side prompt as `commitment_extraction`'s. Insert a `# TODO(prompt-XX): dedupe against open thank-you commitments referencing this thread within dedupe_window_hours; currently always emits.` comment before the emit. Do NOT pre-implement dedupe.
- **`[ADR-0002]`:** skills invoked via `await ctx.run_skill_fn(skill_id, inputs, SkillContext(session=ctx.session, correlation_id=ctx.correlation_id))`.
- **`[§7.3]` / `[§7.4]` / `[§7.7]` / `[D6]` / `[D7]`** all enforced as in 10b-ii-α. Defensive-default exception tuple is the same 7 types.

### What's NOT in scope for this prompt

- Extending `CommitmentProposedV1.kind`'s Literal to include `"thank_you"`. **Default disposition: ship `kind="other"`. If §1150 depth-read reveals strict requirement, STOP and report — James scopes the migration as a follow-up prompt.**
- Owner-scope enforcement at the pipeline layer. The pipeline emits `owed_by_member_id` correctly; downstream projections (and the inbox surface in 14b) handle owner-scope filtering. Pattern matches `commitment_extraction`.
- Dedupe (deferred to a future projection-side prompt).
- Auto-confirming `commitment.proposed` events. Approval comes from the inbox surface (prompt 14b).
- Proactive pipelines. Prompt 10c.
- Aggregating `messaging.classified` into `interactions` rows. The TODO at `adminme/projections/interactions/handlers.py:15` stays.
- Subscribing to `telephony.voicemail_transcribed`, `calendar.event.concluded`, `capture.note_created`, `financial.money_flow_recorded`. These event types are not yet registered. Per PM-9, add as TODO comments inside the `triggers.events:` block.

---

## Deliverables

### Commit 1 — `extract_thank_you_fields` skill pack

**File: `packs/skills/extract_thank_you_fields/`** — full 09b-shape skill pack. Files mirror `packs/skills/extract_commitment_fields/`:

- `pack.yaml` — `pack.id: skill:extract_thank_you_fields`, `version: 1.0.0`, `kind: skill`. Documentation-only `model:` block.
- `SKILL.md` — frontmatter: `name: extract_thank_you_fields`, `namespace: adminme`, `version: 1.0.0`, `description:` (one line — "Given a message classified as a thank-you candidate, extract structured fields suitable for round-tripping into a `commitment.proposed` event with `kind=other`."), `input_schema: ./schemas/input.schema.json`, `output_schema: ./schemas/output.schema.json`, `provider_preferences: [anthropic/claude-haiku-4-5, anthropic/claude-opus-4-7]`, `max_tokens: 400`, `temperature: 0.1`, `sensitivity_required: normal`, `context_scopes_required: []`, `timeout_seconds: 8`, `outbound_affecting: false`, `on_failure: { recipient_party_id: "", suggested_text: "", urgency: "no_rush", confidence: 0.0, reasons: [skill_failure_defensive_default] }`. After frontmatter, a 2-paragraph "What good output looks like" prose section.
- `schemas/input.schema.json` — JSON Schema: required `{message_text: string, sender_party_id: string, receiving_member_id: string, classify_reasons: array}`. `additionalProperties: false`.
- `schemas/output.schema.json` — JSON Schema: required `{recipient_party_id, suggested_text, urgency, confidence}`. `urgency` enum matches `CommitmentProposedV1.urgency`'s Literal: `["today", "this_week", "this_month", "no_rush"]`. `suggested_text` `minLength: 1, maxLength: 500`. `confidence` `minimum: 0.0, maximum: 1.0`. `additionalProperties: false`.
- `prompt.jinja2` — short Jinja template; mirrors `extract_commitment_fields/prompt.jinja2`'s shape but tuned for thank-you extraction (instruct the model to draft a short note rather than describe an obligation).
- `handler.py` — `post_process(raw, inputs, ctx)`: defensive coercion. Non-dict input → on_failure shape. Unknown urgency → coerce to `no_rush` with a `rejected_urgency:<value>` reason appended.
- `tests/test_skill.py` — pack-loader canary (1 test) + 3–4 handler-direct cases (well-formed pass-through, urgency coercion, non-dict-input). Mirror `packs/skills/extract_commitment_fields/tests/test_skill.py`.

**Commit 1 verification:**

```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest packs/skills/extract_thank_you_fields/tests/ -v
git add packs/skills/extract_thank_you_fields/
git commit -m "phase 10b-ii-beta commit 1: extract_thank_you_fields skill pack"
```

Expected: mypy clean, 4 tests pass.

### Commit 2 — `thank_you_detection` pipeline pack

**File: `packs/pipelines/thank_you_detection/pipeline.yaml`** — pack manifest. Same shape as `commitment_extraction`'s manifest:

```yaml
pack:
  id: pipeline:thank_you_detection
  name: Thank-you detection
  version: 1.0.0
  kind: pipeline
runtime:
  language: python
  python_version: ">=3.11"
  entrypoint: handler.py
  class: ThankYouDetectionPipeline
triggers:
  events:
    - messaging.received
    # NOTE: messaging.sent intentionally excluded — thank-yous are
    # extracted from inbound only (per F-5 build_log carry-forward
    # from prompt 10b-ii-α). Outbound is what the household sent;
    # there's no thank-you to extract from it.
    # TODO(prompt-XX): subscribe to telephony.voicemail_transcribed,
    # calendar.event.concluded, capture.note_created,
    # financial.money_flow_recorded once those event types register.
depends_on:
  skills:
    - classify_thank_you_candidate@^1.3.0
    - extract_thank_you_fields@^1.0.0
  projections:
    - parties              # for find_party_by_identifier
    - commitments          # for future dedupe (TODO)
events_emitted:
  - commitment.proposed
  - commitment.suppressed
config:
  schema: config.schema.json
  example: config.example.yaml
```

**File: `packs/pipelines/thank_you_detection/config.example.yaml`** — copy `commitment_extraction/config.example.yaml` literally. Same defaults (`min_confidence: 0.55`, `review_threshold: 0.75`, `dedupe_window_hours: 72`, empty `per_member_overrides: {}`, same `skip_party_tags`).

**File: `packs/pipelines/thank_you_detection/config.schema.json`** — copy `commitment_extraction/config.schema.json` literally; only `title` differs.

**File: `packs/pipelines/thank_you_detection/handler.py`** — class `ThankYouDetectionPipeline`. Logic:

1. Class attributes: `pack_id = "pipeline:thank_you_detection"`, `version = "1.0.0"`, `triggers: Triggers = {"events": ["messaging.received"]}`.
2. `async def handle(event, ctx)`:
   a. **F-5 early-return:** if `event_type != "messaging.received"` return. Document the F-5 rationale in the method docstring (no outbound suppress-noise — thank-yous are inbound-only).
   b. Load config via `_load_config()` helper. Same shape as `commitment_extraction._load_config`; either factor out the helper into a shared module (NOT recommended for 10b-ii-β — the split memo says "no shared helper yet, that's a future refactor") or copy the body inline. **Recommended: copy inline, with a docstring comment noting the duplication is intentional pending a future shared-helper refactor.**
   c. Defense-in-depth: if `ctx.parties_conn_factory is None`, emit `commitment.suppressed` with `reason="skill_failure_defensive_default"` and return. Same pattern as `commitment_extraction`.
   d. Open the parties-DB connection inside `with ctx.parties_conn_factory() as conn:`. Derive `kind` and `value_normalized` from `from_identifier` (copy `_classify_identifier` from `commitment_extraction/handler.py` inline — same pattern as the docstring's no-shared-helper note). Call `find_party_by_identifier(conn, ctx.session, kind=kind, value_normalized=value_normalized)`. If miss, emit `commitment.suppressed` with `reason="skill_failure_defensive_default"` and return.
   e. Resolve `receiving_member_id` from `to_identifier`. If missing, suppress + return.
   f. Skip rules: if `event["payload"].get("party_tags", [])` intersects `config["skip_party_tags"]`, return silently.
   g. Resolve `(min_confidence, review_threshold)` via `_thresholds_for_member` (copy inline).
   h. Call `await ctx.run_skill_fn("skill:classify_thank_you_candidate", classify_inputs, skill_ctx)`. Wrap in `try/except _SKILL_FAILURE_TYPES` and on raise emit `commitment.suppressed` with `reason="skill_failure_defensive_default"`.
   i. Read `is_candidate` and `confidence` from classifier output. **Two-axis early-suppress:** if NOT `is_candidate` (boolean false) OR `confidence < min_confidence`, emit `commitment.suppressed` with `reason="below_confidence_threshold"` and return. The reason-string overload is intentional — both axes represent the same "this is not a thank-you candidate worth proposing" decision; the audit trail captures the confidence value either way.
   j. Determine `strength`: `"confident"` if `confidence >= review_threshold`, else `"weak"`.
   k. Call `await ctx.run_skill_fn("skill:extract_thank_you_fields", extract_inputs, skill_ctx)`. Same try/except + suppress-on-failure pattern.
   l. Build `commitment.proposed` payload per the Operating Context spec above. **`kind="other"` is the literal value.** Generate `commitment_id` via `_new_commitment_id()` (copy inline; mirrors `commitment_extraction`).
   m. **Dedupe TODO** comment immediately before the emit: `# TODO(prompt-XX): dedupe against open thank-you commitments referencing this thread within dedupe_window_hours; currently always emits.`
   n. Emit `commitment.proposed` via `ctx.event_log.append(envelope, correlation_id=ctx.correlation_id, causation_id=ctx.triggering_event_id)`.
3. `_emit_suppressed` helper — copy from `commitment_extraction/handler.py` inline, identical signature and behavior.

**Inline-code budget note (PM-8):** the handler will exceed `commitment_extraction`'s line count slightly because the inlined helpers (`_classify_identifier`, `_load_config`, `_thresholds_for_member`, `_new_commitment_id`, `_emit_suppressed`) are duplicated rather than shared. This is accepted per the split memo's "no shared helper yet" guidance. Total handler size should be ~280–320 lines, comparable to `commitment_extraction`'s ~420 lines minus the second-skill-call branch which thank-you-detection still has but the binary-classify-up-front shape is simpler.

**File: `packs/pipelines/thank_you_detection/tests/test_pack_load.py`** — pack-loader canary (1 test). Mirror `commitment_extraction/tests/test_pack_load.py`.

**File: `tests/unit/test_pipeline_thank_you_detection.py`** — handler-direct unit cases. Mirror `tests/unit/test_pipeline_commitment_extraction.py`'s structure (fake event log, factory closure, seeded SQLCipher parties DB, `_config_override` test seam). Cases:

- `test_above_review_threshold_emits_proposed_confident` — classify returns `{is_candidate: true, confidence: 0.85, ...}`; assert `commitment.proposed` with `strength="confident"` and `kind="other"`.
- `test_between_min_and_review_emits_proposed_weak` — `confidence: 0.62`; assert `strength="weak"`.
- `test_is_candidate_false_emits_suppressed` — classify returns `{is_candidate: false, confidence: 0.95, reasons: [...]}` (high confidence in the negative); assert `commitment.suppressed` with `reason="below_confidence_threshold"`.
- `test_below_min_threshold_emits_suppressed` — classify `{is_candidate: true, confidence: 0.30}`; assert suppressed.
- `test_messaging_sent_returns_silently` — F-5 carry-forward: append a `messaging.sent` event, assert no emit.
- `test_unresolvable_sender_emits_suppressed` — same pattern as `commitment_extraction`.
- `test_no_parties_factory_emits_suppressed` — defense-in-depth.
- `test_skip_party_tag_returns_silently` — skip rule.
- `test_classify_timeout_emits_suppressed` — F-2 path.
- `test_extract_failure_emits_suppressed` — second-skill failure path.

**Commit 2 verification:**

```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest packs/pipelines/thank_you_detection/tests/ -v
poetry run pytest tests/unit/test_pipeline_thank_you_detection.py -v
git add packs/pipelines/thank_you_detection/ tests/unit/test_pipeline_thank_you_detection.py
git commit -m "phase 10b-ii-beta commit 2: thank_you_detection pipeline pack"
```

Expected: mypy clean, pack-load canary passes, 10 handler-direct unit tests pass.

### Commit 3 — Per-member-overrides coverage + classify-output-shape edge case

**File: `tests/unit/test_pipeline_thank_you_detection.py`** (extend) — add 2–3 cases:

1. `test_per_member_override_lowers_threshold` — config has `per_member_overrides["member_alpha"] = {"min_confidence": 0.40, "review_threshold": 0.70}`; classify returns `{is_candidate: true, confidence: 0.45}` from `member_alpha`; assert `commitment.proposed` (would be suppressed under global 0.55).
2. `test_per_member_override_disables_via_high_threshold` — config has `per_member_overrides["member_omega"] = {"min_confidence": 1.1}`; any classify with confidence < 1.1 emits suppressed. Cite `REFERENCE_EXAMPLES.md §2 line 666` for the disable-via-1.1 pattern.
3. `test_classify_output_without_urgency_when_is_candidate_false` — confirm the pipeline does NOT crash when classify returns `is_candidate: false` and the `urgency`/`suggested_medium` fields are absent (per `classify_thank_you_candidate`'s JSON Schema `if/then`). The pipeline never reads those fields on the suppressed path — this test asserts robustness against the conditional output.

**Commit 3 verification:**

```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest tests/unit/test_pipeline_thank_you_detection.py -v
git add tests/unit/test_pipeline_thank_you_detection.py
git commit -m "phase 10b-ii-beta commit 3: per-member-overrides + classify-shape edge cases"
```

Expected: mypy clean, unit tests at 13 total.

### Commit 4 — Integration round-trip + verification + BUILD_LOG + push

**File: `tests/integration/test_pipeline_10b_ii_beta_integration.py`** — round-trip integration tests against the live runner (mirror `tests/integration/test_pipeline_10b_ii_alpha_integration.py`). Cases:

1. `test_thank_you_detection_emits_proposed_for_above_threshold_candidate` — seed parties DB; register pipeline; `parties_conn_factory` opens seeded DB; monkeypatch `adminme.pipelines.runner.run_skill` to return classify (`{is_candidate: true, confidence: 0.85, urgency: "this_week", suggested_medium: "handwritten_card", reasons: [...]}`) and extract (`{recipient_party_id: "party_seed", suggested_text: "...", urgency: "this_week", confidence: 0.9}`). Append `messaging.received`. Notify + wait-for-checkpoint. Assert event log contains `commitment.proposed` with `causation_id == messaging.received.event_id`, `kind == "other"`, `strength == "confident"`.
2. `test_thank_you_detection_emits_suppressed_for_is_candidate_false` — classify returns `{is_candidate: false, confidence: 0.95, reasons: [...]}` (no urgency / suggested_medium fields — JSON-Schema-conditional). Assert `commitment.suppressed` with `reason == "below_confidence_threshold"`.
3. `test_thank_you_detection_skips_messaging_sent` — F-5 carry-forward, integration version. Append `messaging.sent`, append a follow-up `messaging.received` (so the subscriber checkpoint advances), wait-for-checkpoint on the follow-up, assert NO event was derived from the original `messaging.sent` (its `causation_id` does not appear in the log). Per the universal preamble's "absence assertion" pattern.

**File: `docs/build_log.md`** — append entry under `## Build prompts`, AFTER the prompt 10b-ii-α entry. Template:

```markdown
### Prompt 10b-ii-β — reactive pipelines (thank_you_detection + extract_thank_you_fields)
- **Refactored**: by Partner in Claude Chat, <YYYY-MM-DD>. Prompt file: prompts/10b-ii-beta-thank-you-detection.md (~<NNN> lines, quality bar = 10b-ii-α + reuse).
- **Session merged**: PR #<N>, commits <sha1> / <sha2> / <sha3> / <sha4>, merged <merge-date>.
- **Outcome**: IN FLIGHT (PR open).
- **Evidence**:
  - packs/skills/extract_thank_you_fields/ — full 09b-shape skill pack at v1.0.0. 4 unit tests via pack-loader canary + handler-direct.
  - packs/pipelines/thank_you_detection/{pipeline.yaml,config.example.yaml,config.schema.json,handler.py,tests/test_pack_load.py} — ThankYouDetectionPipeline; reuses 10b-ii-α's parties-DB seam through ctx.parties_conn_factory; calls classify_thank_you_candidate@^1.3.0 (already on main from 09b) → extract_thank_you_fields@^1.0.0 → emits commitment.proposed with kind="other" or commitment.suppressed.
  - tests/unit/test_pipeline_thank_you_detection.py — 12-13 handler-direct unit tests.
  - tests/integration/test_pipeline_10b_ii_beta_integration.py — 3 round-trip tests against the live runner.
  - Total new tests: <count>; suite tally <before> → <after> passed.
  - F-5 carry-forward CLOSED: thank_you_detection early-returns on messaging.sent at the top of handle() (vs. commitment_extraction's defensive-default-suppress path); no audit-trail noise from outbound.
  - kind="other" v1 disposition CONFIRMED: BUILD.md §1150 depth-read concluded kind="other" plus thank-you-decision reasons in classify_reasons is sufficient. CommitmentProposedV1.kind Literal NOT extended.
  - [§7.3] / [§7.4] / [§7.7] / [§12.4] / [§15] / [D6] / [D7] all clean — same pattern as 10b-ii-α.
  - Causation-id wiring: every emit uses causation_id=ctx.triggering_event_id.
  - verify_invariants.sh exit 0.
- **Carry-forward for prompt 10c** (proactive pipelines):
  - Two-skill-chain pattern with binary-classifier-first is now established (commitment_extraction: classify-then-extract with same skill pack family; thank_you_detection: classify-then-extract with cross-prompt classifier).
  - The early-return-on-messaging.sent pattern for inbound-only pipelines is established. Future inbound-only pipelines reuse it.
- **Carry-forward for prompt 14b** (inbox surface):
  - commitment.proposed events with kind="other" carrying a reasons array including thank-you signals are visually distinct in the inbox from kind="reply"/"task"/etc. The 14b inbox surface UI may want to filter or group thank-you proposals separately. This is presentation, not pipeline.
- **Carry-forward for prompt 16** (bootstrap wizard):
  - Bootstrap §3 collects per-member overrides for thank_you_detection alongside commitment_extraction. The config.example.yaml shape is identical between the two pipelines.
- **Carry-forward for prompt 19** (Phase B smoke test):
  - Confirm thank_you_detection correctly proposes a thank-you commitment (kind="other") from a seeded test message containing hosting hospitality language.
- **Soft-watch for future:**
  - The four inlined helpers (_classify_identifier, _load_config, _thresholds_for_member, _new_commitment_id, _emit_suppressed) are now duplicated across two pipeline handlers. A future refactor (no specific prompt assigned) should extract them to adminme/pipelines/_helpers.py once a third pipeline needs them. Until then, the duplication is accepted per the 10b-ii-α split-memo guidance.
```

**Commit 4 verification:**

```
poetry run ruff check adminme/ packs/ tests/ 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -20
bash scripts/verify_invariants.sh
git add docs/build_log.md tests/integration/test_pipeline_10b_ii_beta_integration.py
git commit -m "phase 10b-ii-beta commit 4: integration tests + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean (on adminme/packs/tests scope; 2 pre-existing F401 in `docs/reference/plaid/` carry forward from prior merges and are out-of-scope for this prompt); mypy clean; full suite green with ≥15 new tests across 10b-ii-β; `verify_invariants.sh` exit 0.

PR title: `phase 10b-ii-β: thank_you_detection pipeline + extract_thank_you_fields skill pack`

PR body: copy the BUILD_LOG `Evidence` section verbatim. Add a top line: "Closes the second half of the 10b-ii secondary split per `docs/02-split-memo-10b-ii.md`. Reuses 10b-ii-α's parties-DB seam, defensive-default exception tuple, and per-member-overrides config shape literally. No new infrastructure, no new event types."

After PR opens: one round of `mcp__github__pull_request_read` (status, reviews, comments), report, stop.

---

## Stop

`thank_you_detection` pipeline pack registered. `extract_thank_you_fields` skill pack at v1.0.0 live. Two-skill chain reusing existing 09b `classify_thank_you_candidate@1.3.0` is the new architectural pattern. F-5 outbound-noise carry-forward closed. UT-12 already closed by 10b-ii-α. Ready for 10c (proactive pipelines).
