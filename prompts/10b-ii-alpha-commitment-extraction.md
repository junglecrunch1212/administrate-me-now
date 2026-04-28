# Prompt 10b-ii-α: Reactive pipelines — parties-DB seam + commitment_extraction

**Phase:** `BUILD.md` §L4 (reactive pipelines, second half — first wave).
**Depends on:**
- Prompt 10a merged (PR #33). `Pipeline` Protocol, `PipelineContext`, `PipelineRunner`, `LoadedPipelinePack`, `load_pipeline_pack()` all on main.
- Prompt 10b-i merged (PR #38). `identity_resolution` is live; auto-created parties accumulate in the `parties` projection so `find_party_by_identifier` has hits.
- Prompt 09b merged. The 09b skill-pack shape is the canonical reference; `classify_thank_you_candidate@1.3.0` lives at `packs/skills/classify_thank_you_candidate/`.
- The `sequence-update-10b-ii-split` PR is merged (this prompt's row exists in `prompts/PROMPT_SEQUENCE.md`'s sequence table; `docs/02-split-memo-10b-ii.md` is on disk as the authoritative scope spec).

**Quality bar:** 10b-i (320 lines, 22 tests, 4 commits) plus one infrastructure extension and one extra skill pack. Reuse the 10b-i pipeline-pack shape and the 09b skill-pack shape literally.

**Stop condition:** Parties-DB seam wired through `PipelineContext` + `PipelineRunner` + `_make_callback`; `commitment_extraction` pipeline pack registered and discoverable; two new skill packs registered and loadable; one new event type at v1 (`commitment.suppressed`); one round-trip integration test against the live runner; suite green; `verify_invariants.sh` exit 0.

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
> git checkout -b phase-10b-ii-alpha-commitment-extraction
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
> **Schema conventions.** New event types register at `v1` per `[D7]`. Migrations (and upcasters when the schema shape changes) compose forward only. Projection schemas use SQLite `CHECK` constraints on closed-enum columns (e.g. `kind IN (...)`, `status IN (...)`, `sensitivity IN ('normal','sensitive','privileged')`) and NOT on open columns (display_name, category, notes). Composite PK `(tenant_id, <entity_id>)` for multi-tenant projection tables. Cross-DB FK references are documentation-only comments; SQLite cannot enforce FKs across separate projection DBs per `[§2.3]`.
>
> **Tenant-identity firewall.** Platform code under `adminme/` must not reference "James", "Laura", "Charlie", "Stice", "Morningside", or any other specific tenant name per `[§12.4]`. These names live in `tests/fixtures/` only, with `# fixture:tenant_data:ok` on the relevant line if ambiguity warrants. If a prompt's illustrative example uses a name, it's illustrative — the shipped code stays tenant-agnostic. The verify script catches violations.
>
> **Citation discipline.** When a decision or invariant shapes code, cite it in the code comment or docstring. Formats: `[§N]` for `SYSTEM_INVARIANTS.md` section N, `[DN]` for `DECISIONS.md` entry N, `[arch §N]` for `architecture-summary.md` section N, `[cheatsheet Qn]` for `openclaw-cheatsheet.md` question n. BUILD_LOG entries under Evidence cite the invariants that shaped the implementation.
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

Use `sed -n '<start>,<end>p'` for the `BUILD.md` / `REFERENCE_EXAMPLES.md` ranges. Do NOT full-read either file. Read every section listed before producing Commit 1.

1. **`docs/02-split-memo-10b-ii.md`** — full read. This is the authoritative scope spec for this prompt. §"10b-ii-α" pins schema fields, infrastructure extension shape, the 4-commit decomposition, and the F-2 carry-forward verification requirement.
2. **`ADMINISTRATEME_BUILD.md` §L4 — `commitment_extraction` pipeline definition.**
   - `sed -n '1140,1200p' ADMINISTRATEME_BUILD.md` — covers commitment-extraction pipeline + its skill-call chain. Verify the start line by `grep -n "^### commitment_extraction\|commitment_extraction" ADMINISTRATEME_BUILD.md | head -5` first; the section header is the anchor.
3. **`ADMINISTRATEME_REFERENCE_EXAMPLES.md` §2 — full `commitment_extraction` worked example.**
   - The §2 header is at line 584 (verify by `grep -n "^## 2\." ADMINISTRATEME_REFERENCE_EXAMPLES.md`). Read lines `584,1029` in chunks of ≤200 lines: `sed -n '584,780p'`, `sed -n '781,920p'`, `sed -n '921,1029p'`. The section runs ~445 lines including yaml/python blocks; do not full-read the whole file. The example pre-dates current schemas in places — your authoritative shape comes from `docs/02-split-memo-10b-ii.md` §10b-ii-α and `adminme/events/schemas/domain.py`'s actual `CommitmentProposedV1`, NOT from the example's `tenant_id` / `payload` / `kind` field names. Use the example for **structure** (per-member overrides config shape, dedupe semantics, observation-mode awareness, defensive-default discipline) and for the **architecture** (resolve sender → skip rules → classify → extract → emit), but cross-reference every field name against the actual schema in `domain.py` before writing it.
4. **`prompts/10b-i-identity-and-noise.md`** — full read. This is your quality bar. Replicate its section ordering, its level of detail, its 4-commit decomposition, its Read-first discipline, its Operating Context shape, and its BUILD_LOG template.
5. **`packs/pipelines/identity_resolution/{pipeline.yaml,handler.py}`** and **`packs/pipelines/noise_filtering/{pipeline.yaml,handler.py}`** — full read both. These are the on-main pipeline-pack shape you are cloning. `noise_filtering`'s defensive-default-on-skill-failure pattern is the model for `commitment_extraction`'s suppress-on-failure pattern.
6. **`adminme/pipelines/base.py`** — full read. `PipelineContext` is the dataclass you extend with `parties_conn_factory: Callable[[], "sqlcipher3.Connection"] | None = None`.
7. **`adminme/pipelines/runner.py`** — full read. Two changes: `__init__` gains an optional `parties_conn_factory` keyword arg (default `None` for backward compat with all five existing PipelineRunner construction sites in `tests/integration/test_pipeline_runner_integration.py`); `_make_callback` threads it into the `PipelineContext`. Reading this in full also confirms the bus / log / config / observation / guarded_write wiring you must NOT touch.
8. **`adminme/projections/runner.py` lines 40–80** — read for the `encryption_key: bytes` kwarg + `_format_key()` PRAGMA-key idiom. The pipeline-runner extension does **not** need its own encryption-key handling for 10b-ii-α; the parties-DB factory closure can accept a `(path, key_pragma)` pair captured at runner-construction or, equivalently, a `Callable[[], sqlcipher3.Connection]` constructed by the test/bootstrap caller. Pick whichever shape preserves test isolation; the split memo's "Callable" framing is the floor.
9. **`adminme/projections/parties/queries.py` lines 47–65** — full read. `find_party_by_identifier(conn, session, *, kind, value_normalized) -> dict | None`. This is the seam consumer.
10. **`adminme/events/schemas/domain.py`** — full read (~210 lines). Confirms `CommitmentProposedV1` shape (line 26+) and the `registry.register("commitment.proposed", 1, ...)` pattern at line 194. Your `CommitmentSuppressedV1` lands directly after `CommitmentProposedV1` and registers next to it.
11. **`adminme/events/schemas/ingest.py`** — full read (~120 lines). Confirms `MessagingReceivedV1` payload shape (`from_identifier`, `to_identifier`, `source_channel`, etc. — NOT the `REFERENCE_EXAMPLES.md` §2 `payload[type]` framing).
12. **`adminme/lib/skill_runner/__init__.py`** — full read. Confirms the seven exception types this pipeline's defensive-default `except` list catches: `OpenClawTimeout`, `OpenClawUnreachable`, `OpenClawResponseMalformed`, `SkillInputInvalid`, `SkillOutputInvalid`, **`SkillSensitivityRefused`**, **`SkillScopeInsufficient`** (the bolded two are the F-2 widening per 10b-i build_log carry-forward).
13. **`packs/skills/classify_thank_you_candidate/`** — full directory listing + read `pack.yaml`, `SKILL.md` (frontmatter only, first 25 lines), `schemas/input.schema.json`, `schemas/output.schema.json`, `handler.py`, `tests/test_skill.py`. Canonical 09b skill-pack shape.
14. **`packs/skills/classify_message_nature/`** — full directory listing + read the same files (frontmatter-only for `SKILL.md`). 10b-i's clone of the 09b shape; lower-friction reference if the `classify_thank_you_candidate` shape is unclear.
15. **`adminme/lib/instance_config.py`** — full read (~120 lines). Confirms `InstanceConfig.projection_db_path("parties")` resolves the parties DB path. The seam factory uses this; do NOT hardcode `~/.adminme/projections/parties.db` per `[§15]`/`[D15]`.
16. **`tests/integration/test_pipeline_runner_integration.py`** — full read. Confirms five `PipelineRunner(bus, log, config)` construction sites that must stay green when `parties_conn_factory: None` is added with a default.
17. **`tests/integration/test_pipeline_10b_i_integration.py`** — full read. Pattern reference for round-trip integration tests; this prompt adds one such file.
18. **`docs/build_log.md`** — read the **prompt 10b-i entry** (search for `### Prompt 10b-i`) end to end. Carry-forwards into 10b-ii-α live there. Especially: F-2 (defensive-`except` widening), `find_party_by_identifier` hit-rate guidance, `messaging.classified` skip-on-noise opportunity, and the per-member-overrides config shape carryforward.
19. **`docs/SYSTEM_INVARIANTS.md`** — §7 (pipelines) and §8 (no LLM SDKs). Targeted reads only.

After all reads complete, BEFORE running mypy preflight, write a one-paragraph orientation comment to your scratchpad confirming: (a) you understand the `PipelineContext` extension shape and the runner's `_make_callback` wiring, (b) you understand `find_party_by_identifier`'s signature and return shape, (c) you understand the 09b skill-pack shape including the F-2 frontmatter check (`sensitivity_required` + `context_scopes_required`), (d) you have not assumed any field on `CommitmentProposedV1` that isn't in `domain.py`'s actual schema, (e) your `commitment.suppressed` field list matches what `docs/02-split-memo-10b-ii.md` §10b-ii-α specifies. If any of (a)–(e) is shaky, re-read the relevant file before proceeding.

---

## Operating context

- The parties-DB seam is the load-bearing infrastructure piece. `PipelineContext` gains `parties_conn_factory: Callable[[], "sqlcipher3.Connection"] | None`, default `None`. The runner's `__init__` gains an optional `parties_conn_factory` kwarg; `_make_callback` passes it into the `PipelineContext` it constructs per dispatched event. The pipeline opens the connection inside `handle()` and closes via context manager — runner does NOT own per-pipeline connections (10a connection-management note).
- **Backward compatibility is mandatory.** All five existing `PipelineRunner(bus, log, config)` construction sites in `tests/integration/test_pipeline_runner_integration.py` must stay green without modification. They get `parties_conn_factory=None` implicitly; pipelines that depend on it must check for `None` and degrade cleanly (for `commitment_extraction`, "factory is None" means "skip sender resolution; emit `commitment.suppressed` with `reason=skill_failure_defensive_default` because we cannot determine the receiving member" — but in practice the test that exercises `commitment_extraction` will provide a factory, so this is a defense-in-depth path).
- `find_party_by_identifier(conn, session, *, kind, value_normalized) -> dict | None` returns a dict on hit (containing `party_id`, `display_name`, `sort_name`, etc.), `None` on miss. `kind` derivation mirrors 10b-i: `"email"` for email-shaped, `"phone"` for phone-shaped, `"imessage_handle"` for iMessage handles. `value_normalized` mirrors 10b-i's normalization (lowercase + strip for emails/handles; digit-only for phones).
- `CommitmentProposedV1` (already on main, registered at v1 in `adminme/events/schemas/domain.py:194`) is the success-path emit. Field shape — confirmed against `domain.py`:
  - `commitment_id: str` (min_length=1)
  - `kind: Literal["reply", "task", "appointment", "payment", "document_return", "visit", "other"]`
  - `owed_by_member_id: str` (min_length=1)
  - `owed_to_party_id: str` (min_length=1)
  - `text_summary: str` (min_length=1, max_length=500)
  - `suggested_due: datetime | None`
  - `urgency: Literal["today", "this_week", "this_month", "no_rush"]`
  - `confidence: float` (ge=0.0, le=1.0)
  - `strength: Literal["confident", "weak"]`
  - `source_interaction_id: str | None`
  - `source_message_preview: str | None` (max_length=240)
  - `classify_reasons: list[str]`
- `CommitmentSuppressedV1` (this prompt's new schema — registered at v1 per `[D7]`) is the suppress-path emit. Field shape per `docs/02-split-memo-10b-ii.md` §10b-ii-α:
  - `reason: Literal["below_confidence_threshold", "dedupe_hit", "skill_failure_defensive_default"]`
  - `confidence: float` (ge=0.0, le=1.0) — the confidence the classify skill returned, or 0.0 on skill failure
  - `threshold: float` (ge=0.0, le=1.0) — the threshold this confidence was compared against
  - `source_event_id: str` (min_length=1) — the `messaging.received` / `messaging.sent` that triggered evaluation
  - `model_config = {"extra": "forbid"}`
- `[ADR-0002]`: skills are invoked via `await ctx.run_skill_fn(skill_id, inputs, SkillContext(session=ctx.session, correlation_id=ctx.correlation_id))`. `commitment_extraction` calls two skills serially: `classify_commitment_candidate` first, then `extract_commitment_fields` only when classification confidence meets threshold.
- `[§7.3]` — pipelines NEVER write a projection row directly. They emit events; projections consume them. `verify_invariants.sh`'s pipeline→projection canary will catch violations.
- `[§7.4]` / `[§8]` / `[D6]` — pipelines NEVER import provider SDKs. All skill calls go through `ctx.run_skill_fn`.
- `[§7.7]` — pipeline failure does not halt the bus. The bus owns checkpoint advancement on success / non-advancement on raise. **But** `commitment_extraction` does NOT raise on skill failure — it catches the seven listed exception types and emits `commitment.suppressed` with `reason="skill_failure_defensive_default"`. Bus-level checkpoint advances. This mirrors `noise_filtering`'s pattern but with the F-2 widening.
- Pipelines emit events through `ctx.event_log.append(envelope, correlation_id=..., causation_id=ctx.triggering_event_id)`. Causation wiring is non-negotiable (10a echo_emitter canary contract).
- **Per-member overrides** ride on `config.example.yaml` per `REFERENCE_EXAMPLES.md` §2 lines 638–663. Keep the **structure** (top-level `min_confidence`, `review_threshold`, `dedupe_window_hours`, `per_member_overrides:`, `skip_party_tags:`); **omit tenant-specific values** ("stice-james", "stice-laura", "stice-charlie") because `[§12.4]` forbids tenant identity in platform code. The example file ships with placeholders like `"member_id_example_1"` and a comment block explaining the per-member-override mechanism. Real values populate post-bootstrap.
- **Dedupe is deferred to a future projection-side prompt.** Per `docs/02-split-memo-10b-ii.md` §10b-ii-α: "Dedupe deferred to a future projection-side prompt (TODO)." Insert a `# TODO(prompt-XX): dedupe against open commitments referencing this thread within dedupe_window_hours; currently always emits.` comment in the handler. Do NOT pre-implement dedupe; it requires reading the `commitments` projection, which has its own seam considerations beyond this prompt's scope.

### What's NOT in scope for this prompt

- `thank_you_detection` pipeline pack — prompt 10b-ii-β.
- `extract_thank_you_fields` skill pack — prompt 10b-ii-β.
- Dedupe against open commitments (projection-side concern; future prompt).
- Auto-confirming `commitment.proposed` events. Approval comes from the inbox surface (prompt 14b).
- Proactive pipelines. Prompt 10c.
- OpenClaw standing-order registration. Prompt 10c.
- Aggregating `messaging.classified` events into `interactions` rows — projection-side, separate prompt. The TODO at `adminme/projections/interactions/handlers.py:15` stays.
- Subscribing to `telephony.voicemail_transcribed`, `calendar.event.concluded`, `capture.note_created`. These event types are not yet registered. Per PM-9, add as TODO comments inside the `triggers.events:` block of the pipeline manifest; do NOT fragment into more sub-prompts.

---

## Deliverables

### Commit 1 — Parties-DB seam + `commitment.suppressed` schema + two skill packs

**File: `adminme/pipelines/base.py`** — add `parties_conn_factory: Callable[[], "sqlcipher3.Connection"] | None = None` to the `PipelineContext` dataclass, ordered AFTER `correlation_id` so the field order remains deterministic. Update the docstring to mention the new seam: "`parties_conn_factory`, when present, opens a SQLCipher connection to the parties projection DB; pipelines that resolve identifiers (e.g. `commitment_extraction`) call it inside `handle()` and close via context manager. None for pipelines that don't need it." Add `import sqlcipher3` to the `TYPE_CHECKING` block.

**File: `adminme/pipelines/runner.py`** — `PipelineRunner.__init__` gains a new keyword-only argument: `parties_conn_factory: Callable[[], "sqlcipher3.Connection"] | None = None`. Store as `self._parties_conn_factory`. Update `_make_callback` to thread it into the constructed `PipelineContext`. Update the class docstring with one sentence on the new seam.

**File: `adminme/events/schemas/domain.py`** — append `CommitmentSuppressedV1` IMMEDIATELY after `CommitmentProposedV1` (line 42 vicinity) + `registry.register("commitment.suppressed", 1, CommitmentSuppressedV1)` IMMEDIATELY after the existing `commitment.proposed` registration on line 194. Schema fields per Operating Context above. `model_config = {"extra": "forbid"}`. Use `Literal` for `reason` (closed enum).

**File: `packs/skills/classify_commitment_candidate/`** — full 09b-shape skill pack. Files (clone shape from `packs/skills/classify_message_nature/` — that's the 10b-i clone of the 09b precedent):

- `pack.yaml` — `pack.id: skill:classify_commitment_candidate`, `version: 3.0.0` (`BUILD.md` §L4 names it `@^3.0.0`), `kind: skill`. Documentation-only `model:` block.
- `SKILL.md` — frontmatter: `name: classify_commitment_candidate`, `namespace: adminme`, `version: 3.0.0`, `description:` (one-line — "Classify whether an inbound message contains a commitment a household member has made or been asked to make."), `input_schema: ./schemas/input.schema.json`, `output_schema: ./schemas/output.schema.json`, `provider_preferences: [anthropic/claude-haiku-4-5, anthropic/claude-opus-4-7]`, `max_tokens: 200`, `temperature: 0.1`, **`sensitivity_required: normal`**, **`context_scopes_required: []`**, `timeout_seconds: 8`, `outbound_affecting: false`, `on_failure: { is_candidate: false, confidence: 0.0, reasons: [skill_failure_defensive_default] }`. After frontmatter, add a 3-paragraph "What counts as a commitment" prose section.
  - **F-2 verification (mandatory):** the values bolded above (`sensitivity_required: normal`, `context_scopes_required: []`) match 10b-i's `classify_message_nature` shape, so the `commitment_extraction` pipeline's `except` list does NOT need widening to catch `SkillSensitivityRefused` / `SkillScopeInsufficient`. **However**, defense-in-depth: include those two exceptions in the `except` list anyway with a comment explaining "today both skill packs are normal-empty, but widening the catch list now eliminates the F-2 carry-forward as a future-prompt risk and matches 10b-i build_log §F-2 guidance." The defensive-default branch is the same code path either way.
- `schemas/input.schema.json` — JSON Schema: required `{message_text: string, sender_party_id: string, receiving_member_id: string, thread_context: array}` per `docs/02-split-memo-10b-ii.md` §10b-ii-α. `thread_context` is an array of `{role: string, text: string}` for prior turns in the thread (empty array if no prior thread).
- `schemas/output.schema.json` — JSON Schema: required `{is_candidate: boolean, confidence: number, reasons: array}`.
- `handler.py` — exposes the conventional `process(input_data) -> dict` entrypoint per the 09b shape; defensive coercion (handles missing keys, returns the `on_failure` shape on input-shape failure).
- `tests/test_skill.py` — pack-loader canary (1 test) + 3–4 handler-direct unit cases mirroring `packs/skills/classify_message_nature/tests/test_skill.py`.

**File: `packs/skills/extract_commitment_fields/`** — full 09b-shape skill pack. Files mirror the above with these changes:

- `pack.yaml` — `pack.id: skill:extract_commitment_fields`, `version: 2.1.0` (`BUILD.md` §L4 names it `@^2.1.0`).
- `SKILL.md` — frontmatter mirrors `classify_commitment_candidate` with these substitutions: `name: extract_commitment_fields`, `version: 2.1.0`, `description:` ("Given a message classified as a commitment candidate, extract structured commitment fields suitable for round-tripping into a `commitment.proposed` event."), `max_tokens: 400`, `on_failure: { kind: "other", confidence: 0.0, reasons: [skill_failure_defensive_default] }`. Same `sensitivity_required: normal` and `context_scopes_required: []` (verify at depth-read time).
- `schemas/input.schema.json` — required `{message_text: string, sender_party_id: string, receiving_member_id: string, classify_reasons: array}`.
- `schemas/output.schema.json` — required output keys must round-trip into `CommitmentProposedV1` without coercion drift: `{kind: string, owed_by_member_id: string, owed_to_party_id: string, text_summary: string, suggested_due: ["string", "null"], urgency: string, confidence: number}`. The `kind` enum matches `CommitmentProposedV1.kind`'s Literal exactly. `urgency` matches `CommitmentProposedV1.urgency`'s Literal exactly. (Schemas use string + enum constraint rather than Pydantic Literal here; the round-trip happens in the pipeline.)
- `handler.py` — same shape as `classify_commitment_candidate`'s handler.
- `tests/test_skill.py` — pack-loader canary + 3–4 handler-direct unit cases.

**Commit 1 verification:**

```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest tests/unit/test_event_registry.py -v
poetry run pytest packs/skills/classify_commitment_candidate/tests/ -v
poetry run pytest packs/skills/extract_commitment_fields/tests/ -v
poetry run pytest tests/integration/test_pipeline_runner_integration.py -v
git add adminme/pipelines/base.py adminme/pipelines/runner.py adminme/events/schemas/domain.py packs/skills/classify_commitment_candidate/ packs/skills/extract_commitment_fields/
git commit -m "phase 10b-ii-alpha commit 1: parties-DB seam + commitment.suppressed + 2 skill packs"
```

Expected: mypy clean, event-registry test confirms `commitment.suppressed` registers at v1, both new skill-pack test suites pass (4–5 each), all 5 existing 10a runner-integration construction sites stay green.

### Commit 2 — `commitment_extraction` pipeline pack

**File: `packs/pipelines/commitment_extraction/pipeline.yaml`** — pack manifest. Required keys per `adminme/pipelines/pack_loader.py`:

```
pack:
  id: pipeline:commitment_extraction
  name: Commitment extraction
  version: 4.2.0
  kind: pipeline
runtime:
  language: python
  python_version: ">=3.11"
  entrypoint: handler.py
  class: CommitmentExtractionPipeline
triggers:
  events:
    - messaging.received
    - messaging.sent
    # TODO(prompt-XX): subscribe to telephony.voicemail_transcribed,
    # calendar.event.concluded, capture.note_created once those event
    # types register. Same handler logic; just trigger-list extension.
depends_on:
  skills:
    - classify_commitment_candidate@^3.0.0
    - extract_commitment_fields@^2.1.0
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

**File: `packs/pipelines/commitment_extraction/config.example.yaml`** — per-member overrides shape per `REFERENCE_EXAMPLES.md` §2 lines 638–663, but with tenant-specific keys replaced by placeholders. Top-level keys: `min_confidence: 0.55`, `review_threshold: 0.75`, `dedupe_window_hours: 72`, `per_member_overrides:` (with two illustrative comment-only placeholders like `"member_id_example_1"` showing the override structure but no real ids), `skip_party_tags:` (array — `[privileged, opposing_counsel, "provider:attorney"]` — these are tags, not tenant identity, so `[§12.4]` doesn't bind).

**File: `packs/pipelines/commitment_extraction/config.schema.json`** — JSON Schema for the above.

**File: `packs/pipelines/commitment_extraction/handler.py`** — class `CommitmentExtractionPipeline` implementing `Pipeline`. Logic per `BUILD.md` §L4 + `REFERENCE_EXAMPLES.md` §2 architecture:

1. Class attributes: `pack_id = "pipeline:commitment_extraction"`, `version = "4.2.0"`, `triggers: Triggers = {"events": ["messaging.received", "messaging.sent"]}`.
2. `async def handle(event, ctx)`:
   a. Load config via `_load_config()` helper (reads `config.example.yaml` packaged with the pipeline pack; real values get overridden by `<instance_dir>/packs/pipelines/commitment_extraction/config.yaml` post-bootstrap — leave a comment noting this).
   b. Resolve sender via `ctx.parties_conn_factory`. If `ctx.parties_conn_factory is None`, emit `commitment.suppressed` with `reason="skill_failure_defensive_default"`, `confidence=0.0`, `threshold=config["min_confidence"]`, `source_event_id=event["event_id"]`. Return. (Defense-in-depth path; production wiring always provides the factory post-bootstrap.)
   c. Open the connection inside `with ctx.parties_conn_factory() as conn:`. Derive `kind` and `value_normalized` from the inbound's `from_identifier` (re-use the 10b-i `_classify_identifier` mapping logic — copy it inline; don't introduce a shared helper module yet, that's a future refactor). Call `find_party_by_identifier(conn, ctx.session, kind=kind, value_normalized=value_normalized)`. If hit, the resolved party's `party_id` is the `sender_party_id` for skill input. If miss, the auto-create path from 10b-i's `identity_resolution` will eventually catch up — for this event, emit `commitment.suppressed` with `reason="skill_failure_defensive_default"` (sender unresolvable means we can't attribute the commitment correctly) and return.
   d. Determine `receiving_member_id` from the event's `to_identifier` similarly. If unresolvable, suppress + return (same pattern).
   e. Skip rules: if `event["payload"].get("party_tags", [])` intersects `config["skip_party_tags"]`, return (silent skip — privileged content). Cite `[§13]` (privacy floors).
   f. Call `await ctx.run_skill_fn("classify_commitment_candidate@^3.0.0", inputs, SkillContext(session=ctx.session, correlation_id=ctx.correlation_id))`. Inputs: `{message_text, sender_party_id, receiving_member_id, thread_context: []}` (thread context is an empty array for now; threading lands in a future prompt).
   g. If `result.output["confidence"] < config["min_confidence"]`: emit `commitment.suppressed` with `reason="below_confidence_threshold"`, `confidence=result.output["confidence"]`, `threshold=config["min_confidence"]`, `source_event_id=event["event_id"]`. Return.
   h. If above min but below review_threshold: classify as "weak"; if at/above review_threshold: classify as "confident". This becomes `CommitmentProposedV1.strength`.
   i. Call `await ctx.run_skill_fn("extract_commitment_fields@^2.1.0", inputs2, SkillContext(...))`. Inputs2: `{message_text, sender_party_id, receiving_member_id, classify_reasons: result.output["reasons"]}`.
   j. Build `CommitmentProposedV1` from `extract_commitment_fields` output + classification metadata. `commitment_id = "cmt_" + ulid_new()`. `source_message_preview = event["payload"]["text"][:240]` (or however the messaging schema names the body field — verify in `ingest.py`'s `MessagingReceivedV1`). `classify_reasons = result.output["reasons"]`.
   k. Emit `commitment.proposed`. Use `causation_id = ctx.triggering_event_id` and `correlation_id = ctx.correlation_id` per the 10a carry-forward.
   l. **Dedupe is a TODO**: insert a `# TODO(prompt-XX): dedupe against open commitments in <commitments> projection within dedupe_window_hours; currently always emits` comment immediately before the emit on step k.
3. The handler's `except` block (around steps f and i — wrap each skill call independently) catches: `OpenClawTimeout`, `OpenClawUnreachable`, `OpenClawResponseMalformed`, `SkillInputInvalid`, `SkillOutputInvalid`, `SkillSensitivityRefused`, `SkillScopeInsufficient`. On any of those, emit `commitment.suppressed` with `reason="skill_failure_defensive_default"`, `confidence=0.0`, `threshold=config["min_confidence"]`. Return without raising. This is the F-2 widening per 10b-i build_log carry-forward.

**File: `packs/pipelines/commitment_extraction/tests/test_pack_load.py`** — pack-loader canary (1 test, mirrors `packs/pipelines/noise_filtering/tests/test_pack_load.py`). Asserts the pack loads, exposes the right class, registers correct triggers.

**File: `tests/unit/test_pipeline_commitment_extraction.py`** — handler-direct unit cases, mirroring `tests/unit/test_pipeline_noise_filtering.py` (10b-i's pattern). Each test constructs `PipelineContext` manually (with a stub `run_skill_fn` and a tmp parties DB seeded with one party). Cases:

- Below-threshold confidence emits `commitment.suppressed` with correct `reason`.
- At-threshold "confident" path emits `commitment.proposed` with `strength="confident"`.
- Between min and review_threshold "weak" path emits `commitment.proposed` with `strength="weak"`.
- Sender-resolution miss (parties DB empty) emits `commitment.suppressed` with `reason="skill_failure_defensive_default"`.
- `parties_conn_factory is None` defense-in-depth path emits `commitment.suppressed`.
- Skip-rule party_tag match returns silently (no emit).
- Skill timeout (`OpenClawTimeout`) emits `commitment.suppressed`.
- F-2 case: skill raises `SkillSensitivityRefused` → emits `commitment.suppressed`.

**Commit 2 verification:**

```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest packs/pipelines/commitment_extraction/tests/ -v
poetry run pytest tests/unit/test_pipeline_commitment_extraction.py -v
git add packs/pipelines/commitment_extraction/ tests/unit/test_pipeline_commitment_extraction.py
git commit -m "phase 10b-ii-alpha commit 2: commitment_extraction pipeline pack"
```

Expected: mypy clean, pack-load canary passes, 8 handler-direct unit tests pass.

### Commit 3 — Runner-test extension + per-member overrides coverage

**File: `tests/unit/test_pipeline_runner.py`** (or wherever the existing 10a runner unit tests live — locate via `grep -rn "PipelineRunner" tests/unit/`) — add 2–3 unit tests:

1. `test_runner_accepts_parties_conn_factory_none_default` — backward-compat: `PipelineRunner(bus, log, config)` without the new kwarg works exactly as before; confirmed `_make_callback` produces a `PipelineContext` with `parties_conn_factory is None`.
2. `test_runner_threads_parties_conn_factory_into_context` — pass a stub callable as the kwarg; register a fixture pipeline; dispatch one event; assert the pipeline received a `PipelineContext` whose `parties_conn_factory is the_same_callable`.
3. `test_runner_parties_conn_factory_per_dispatch_isolation` — confirm the factory is the same object across dispatches (not re-constructed per event); the pipeline-side per-call invocation pattern (`with ctx.parties_conn_factory() as conn:`) is what produces fresh connections.

**File: `tests/unit/test_pipeline_commitment_extraction.py`** (extend) — add 1–2 cases exercising `config.example.yaml` per-member-override loading:

1. `test_per_member_override_lowers_threshold` — config has a `per_member_overrides["member_alpha"] = {"min_confidence": 0.40}`; an event from `member_alpha` with confidence 0.45 should emit `commitment.proposed` (not suppressed), proving the override applied. Use placeholder member ids like `"member_alpha"` per `[§12.4]`.
2. `test_per_member_override_disables_via_high_threshold` — config has `per_member_overrides["member_omega"] = {"min_confidence": 1.1}`; any event from `member_omega` with confidence < 1.1 emits `commitment.suppressed` (impossibly-high disables). Cite `REFERENCE_EXAMPLES.md` §2 line 666 for the disable-via-1.1 pattern.

**Commit 3 verification:**

```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest tests/unit/test_pipeline_runner.py -v
poetry run pytest tests/unit/test_pipeline_commitment_extraction.py -v
git add tests/unit/test_pipeline_runner.py tests/unit/test_pipeline_commitment_extraction.py
git commit -m "phase 10b-ii-alpha commit 3: runner-test extension + per-member-override coverage"
```

Expected: mypy clean, runner unit tests + 2–3 new pass, commitment_extraction unit tests at 10 total.

### Commit 4 — Integration round-trip + verification + BUILD_LOG + push

**File: `tests/integration/test_pipeline_10b_ii_alpha_integration.py`** — round-trip integration tests against the live runner (mirrors `tests/integration/test_pipeline_10b_i_integration.py`). Cases:

1. `test_commitment_extraction_emits_proposed_for_above_threshold` — seed parties DB with one auto-created party (the sender). Register the `commitment_extraction` pipeline. Construct a `PipelineRunner` with a `parties_conn_factory` that opens the seeded DB. Monkeypatch `adminme.pipelines.runner.run_skill` to return deterministic skill outputs (classify: `confidence=0.85, is_candidate=true, reasons=["promise_keyword"]`; extract: full `CommitmentProposedV1`-ready output). Append a `messaging.received`. Notify + wait-for-checkpoint. Assert event log contains a `commitment.proposed` with `causation_id == messaging.received.event_id` and `strength=="confident"`.

2. `test_commitment_extraction_emits_suppressed_for_below_threshold` — same harness, classify returns `confidence=0.30`. Assert event log contains a `commitment.suppressed` with `reason=="below_confidence_threshold"` and `confidence==0.30`.

3. `test_commitment_extraction_emits_suppressed_on_skill_timeout` — same harness, monkeypatch first skill call to raise `OpenClawTimeout`. Assert `commitment.suppressed` with `reason=="skill_failure_defensive_default"` and `confidence==0.0`.

For monkeypatching: use `monkeypatch.setattr("packs.pipelines.commitment_extraction.handler.run_skill", stub)` if the handler imports the function directly, OR pass the stub via `ctx.run_skill_fn` if the test constructs `PipelineContext` manually. Pick one approach during depth-read of 10b-i's integration test and apply consistently. The test must NOT call OpenClaw — there's no live OpenClaw in Phase A.

**File: `docs/build_log.md`** — append entry under `## Build prompts`, AFTER the prompt 10b-i entry. Template (fill placeholders BEFORE pushing — Partner fills `<PR-N>` / `<sha1>...<sha4>` / `<merge-date>` post-merge):

```
### Prompt 10b-ii-α — reactive pipelines (parties-DB seam + commitment_extraction)
- **Refactored**: by Partner in Claude Chat, 2026-04-28. Prompt file: prompts/10b-ii-alpha-commitment-extraction.md (~<NNN> lines, quality bar = 10b-i + parties-DB seam). Secondary-split memo at docs/02-split-memo-10b-ii.md.
- **Session merged**: PR #<N>, commits <sha1> / <sha2> / <sha3> / <sha4>, merged <merge-date>.
- **Outcome**: IN FLIGHT (PR open).
- **Evidence**:
  - adminme/pipelines/base.py — PipelineContext extended with parties_conn_factory: Callable[[], sqlcipher3.Connection] | None = None per docs/02-split-memo-10b-ii.md §10b-ii-α. Closes UT-12 via option (a)+(c).
  - adminme/pipelines/runner.py — PipelineRunner.__init__ gains optional parties_conn_factory kwarg; _make_callback threads into PipelineContext. Backward compatible (default None) — all 5 existing 10a runner-integration construction sites stay green without modification.
  - adminme/events/schemas/domain.py — appended CommitmentSuppressedV1 registered at v1 per [D7]; reason is closed Literal["below_confidence_threshold", "dedupe_hit", "skill_failure_defensive_default"].
  - packs/skills/classify_commitment_candidate/ — full 09b-shape skill pack at v3.0.0 (BUILD.md §L4 names it @^3.0.0). 4–5 unit tests via pack-loader canary + handler-direct.
  - packs/skills/extract_commitment_fields/ — full 09b-shape skill pack at v2.1.0 (BUILD.md §L4 names it @^2.1.0). Output schema round-trips into CommitmentProposedV1 without coercion drift. 4–5 unit tests.
  - packs/pipelines/commitment_extraction/{pipeline.yaml,config.example.yaml,config.schema.json,handler.py,tests/test_pack_load.py} — CommitmentExtractionPipeline per BUILD.md §L4 + REFERENCE_EXAMPLES.md §2 architecture; resolves sender via find_party_by_identifier using ctx.parties_conn_factory; calls classify → extract; emits commitment.proposed (above review_threshold) or commitment.suppressed (below or on skill failure); per-member overrides config-driven; tenant-agnostic (placeholder member ids).
  - tests/unit/test_pipeline_commitment_extraction.py — 8–10 handler-direct unit tests covering threshold paths, suppression reasons, F-2 defensive widening (SkillSensitivityRefused, SkillScopeInsufficient), and per-member overrides.
  - tests/unit/test_pipeline_runner.py — 2–3 new tests covering parties_conn_factory default-None backward-compat + threading.
  - tests/integration/test_pipeline_10b_ii_alpha_integration.py — 3 round-trip tests against the live runner with seeded parties DB and monkeypatched skill runner.
  - Total new tests: <count by file>; suite tally <before> → <after> passed.
  - F-2 carry-forward CLOSED: both new skill packs declare sensitivity_required: normal and context_scopes_required: []; pipeline's except list nonetheless catches SkillSensitivityRefused and SkillScopeInsufficient defense-in-depth.
  - [§7.3] (no projection direct writes): pipeline emits only via ctx.event_log.append; pipeline→projection canary in verify_invariants.sh clean.
  - [§7.4] / [§8] / [D6]: zero new SDK imports; the two skill calls go through ctx.run_skill_fn per [ADR-0002].
  - [§7.7]: skill failure on either call does NOT raise — emits commitment.suppressed with reason="skill_failure_defensive_default". Bus checkpoint advances normally.
  - [D7]: new event type registers at v1.
  - [§12.4]: per-member-override config uses placeholder member ids; verify script clean.
  - [§15]/[D15]: parties-DB path resolved through InstanceConfig.projection_db_path("parties") in the test harness; no hardcoded literal in handler or runner.
  - Causation-id wiring: every emit uses causation_id=ctx.triggering_event_id per the 10a echo_emitter canary contract.
  - verify_invariants.sh exit 0.
  - UT-12 CLOSED by this merge per docs/02-split-memo-10b-ii.md §"Self-check" — option (c)+(a) shipped: split is option (c), parties-DB seam wired through PipelineContext is option (a).
- **Carry-forward for prompt 10b-ii-β** (thank_you_detection + extract_thank_you_fields):
  - Parties-DB seam already in PipelineContext from this prompt. thank_you_detection constructs its handler the same way: with ctx.parties_conn_factory() as conn: for sender resolution.
  - The defensive-default-on-skill-failure pattern (suppress, do not raise) is now established for two-skill-chain pipelines.
  - The per-member-overrides config shape is settled. thank_you_detection reuses the same min_confidence / review_threshold / skip_party_tags skeleton.
  - commitment.proposed with kind="other" is the default thank-you path. If BUILD.md §1150 implies thank_you should be its own kind in CommitmentProposedV1.kind's Literal, that's a Literal-extension migration (forward-only per [D7]); flag as 10b-ii-β's open question — do NOT silently extend the enum.
  - 09b's classify_thank_you_candidate@1.3.0 is already on main. 10b-ii-β only ships extract_thank_you_fields (smaller because the binary classify already happened upstream).
- **Carry-forward for prompt 10c** (proactive pipelines):
  - Per-member-overrides config shape shipped here is reusable for proactive pipelines that have member-specific cadence (e.g., morning_digest per-member quiet hours).
- **Carry-forward for prompt 14b** (inbox surface):
  - commitment.suppressed events with reason="below_confidence_threshold" and confidence between e.g. 0.40 and 0.55 may surface in the inbox as "near-miss" entries for principal calibration. Decide at 14b refactor.
- **Carry-forward for prompt 16** (bootstrap wizard):
  - Bootstrap §7 wires PipelineRunner lifecycle. The new parties_conn_factory is constructed by bootstrap from instance_config + the encryption key (derived from secrets) and passed at runner construction.
  - The pipeline's config.example.yaml is copied to <instance_dir>/packs/pipelines/commitment_extraction/config.yaml on first run; bootstrap §3 collects per-member overrides during the wizard.
- **Carry-forward for prompt 19** (Phase B smoke test):
  - Confirm commitment_extraction correctly resolves a real sender against the live parties projection and proposes a commitment from a seeded test message.
```

**Commit 4 verification:**

```
poetry run ruff check . 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -20
bash scripts/verify_invariants.sh
git add docs/build_log.md tests/integration/test_pipeline_10b_ii_alpha_integration.py
git commit -m "phase 10b-ii-alpha commit 4: integration tests + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean, mypy clean, full suite green with ≥25 new tests across 10b-ii-α, `verify_invariants.sh` exit 0.

PR title: `phase 10b-ii-α: parties-DB seam + commitment_extraction pipeline`

PR body: copy the BUILD_LOG `Evidence` section verbatim. Add a top line: "Closes the first half of the 10b-ii secondary split per `docs/02-split-memo-10b-ii.md`. 10b-ii-β (thank_you_detection + extract_thank_you_fields) ships separately and reuses this prompt's parties-DB seam. UT-12 closed by this merge."

After PR opens: one round of `mcp__github__pull_request_read` (status, reviews, comments), report, stop.

---

## Stop

Parties-DB seam wired through `PipelineContext` + `PipelineRunner`. `commitment_extraction` pipeline pack registered. Two new skill packs (`classify_commitment_candidate@3.0.0`, `extract_commitment_fields@2.1.0`) live. `commitment.suppressed` event type at v1. Round-trip integration verified. UT-12 closed. Ready for 10b-ii-β (thank_you_detection + extract_thank_you_fields).
