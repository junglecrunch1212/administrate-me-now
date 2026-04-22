# Prompt 10c: Proactive pipelines (OpenClaw standing orders)

**Phase:** BUILD.md L4 continued — proactive / standing-order path.
**Depends on:** Prompt 10b.
**Estimated duration:** 4-5 hours.
**Stop condition:** Six proactive pipelines registered as OpenClaw standing orders; each fires on its schedule; observation-mode suppression works; reward dispatch UX works in console SSE.

## Read first

1. `ADMINISTRATEME_BUILD.md`:
   - Pipeline subsections: `morning_digest`, `paralysis_detection`, `reminder_dispatch`, `reward_dispatch`, `crm_surface`, `custody_brief`.
   - The section near L4 that distinguishes reactive from proactive pipelines (prompt 10a read first).
2. `docs/openclaw-cheatsheet.md` question 3 (standing order registration).
3. `docs/reference/openclaw/` — specifically any files covering standing orders, scheduler semantics, and channel send APIs. Mirror only; no WebFetch.
4. `ADMINISTRATEME_DIAGRAMS.md` §9 (observation mode fire/suppress — applies to all proactive outbound).

## Objective

Implement six proactive pipelines. Register each with OpenClaw as a standing order. Each one's handler composes output (digest text, reward toast, reminder, etc.), invokes `outbound()` to deliver, which respects observation mode.

## Deliverables

### Pipelines

- **`morning_digest`** — daily per member at member's configured time. Composes digest via `compose_morning_digest` skill (new skill this prompt). Delivers via preferred channel (iMessage typically, via OpenClaw send).
- **`paralysis_detection`** — per ADHD-profile member, at 15:00 and 17:00 local (configurable). Detects low-completion pattern; composes gentle nudge via `compose_paralysis_nudge` skill (new). Delivers.
- **`reminder_dispatch`** — every 15 min. Walks open commitments with due times approaching; composes reminder; delivers.
- **`reward_dispatch`** — triggered by `task.completed` (event-driven, but still classified as proactive because it emits to the user). Rolls tier (done/warm/delight/jackpot) per profile's reward_distribution. Picks template from persona pack's reward_templates. Emits `reward.ready` event which the console's SSE layer fans out.
- **`crm_surface`** — weekly per member (Sunday evening). Scans parties with gap > desired_frequency; composes surface list; delivers.
- **`custody_brief`** — nightly 20:00 if household has coparent relationship. Composes next-day brief per child.

### New skills (standard §3 shape)

- `compose_morning_digest`
- `compose_paralysis_nudge`
- `compose_reminder`
- `compose_zeigarnik_teaser` (optional; used by morning_digest)

### Registration with OpenClaw

Each pipeline's `pack.yaml` declares `triggers: {schedule: "...", proactive: true}`. The pipeline runner, on start, calls OpenClaw's standing-order registration API with the pipeline's handler HTTP endpoint. OpenClaw fires the standing order on the schedule; the handler runs; delivery goes through `outbound()`.

Add `platform/lib/skill_runner/openclaw_client.py` if not already present, with `register_standing_order(pipeline_id, schedule, handler_url)` method.

### Tests

- Per pipeline: fixture input → expected composed output.
- Observation mode: assert delivery is suppressed, `observation.suppressed` emitted.
- Integration: mock OpenClaw scheduler fires the standing order; handler runs; event log shows the expected sequence.

## Verification

```bash
poetry run pytest tests/unit/test_pipeline_* packs/pipelines/*/tests/ -v

# Manual verification of OpenClaw registration (with live OpenClaw)
openclaw standing-orders list | grep -E "morning_digest|paralysis|reminder|reward|crm_surface|custody_brief"

git commit -m "phase 10c: proactive pipelines as standing orders"
```

## Stop

> Six proactive pipelines registered. Observation mode suppresses outbound correctly. Ready for prompt 11 (standalone adapters).
