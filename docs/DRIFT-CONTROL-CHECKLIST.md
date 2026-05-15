# DRIFT-CONTROL-CHECKLIST.md

**v4 — six questions, applied by Partner before submitting a refactor PR and by Reviewer's Job 4 after every merge.**

This is the operationalized form of the strict drift policy chosen by the operator on 2026-05-14 (per POSTMORTEM-v3-to-v4.md §1.10). Vision drift compounds across 33 prompts; every individual merge looks fine but the cumulative effect can erode what makes AdministrateMe useful for a family.

The checklist anchors to `WHAT-IS-ADMINISTRATEME.md` (pinned in the Cowork Partner Project as doc 14). Every question references a specific section.

---

## How to use this checklist

**Partner (pre-refactor):** Walk the six questions before opening a `partner/refactor-<PROMPT_ID>` PR. If any answer is "no" or "unsure", pause and surface to operator via a `<NEEDS_OPERATOR_DECISION/>` note in the PR description, OR refine the prompt until all six are "yes".

**Reviewer (Job 4, post-merge):** Walk the same six questions against the merged PR's diff. Compute a `vision_drift_score`:

- All six = aligned → **score 0** (aligned)
- 4-5 = aligned, 1-2 unsure/neutral → **score 1** (neutral)
- 2-3 = aligned, 3-4 sideways/drift → **score 2** (drift_sideways; operator-eyes-on)
- 0-1 = aligned, 5-6 sideways or any "anti-vision" → **score 3** (drift_backward; STRICT-FAIL block)

A `vision_drift_score` of 3 triggers a STRICT-FAIL PR comment from Reviewer, `overall_verdict=FAIL_CRITICAL`, and the operator MUST revert or sidecar-fix before further merges in the stack.

---

## The six questions

### Q1 — Family utility: does this advance daily usefulness for a real family member?

**Anchored to:** `[VISION §"What AdministrateMe is for"]`, `[VISION §"household chief-of-staff"]`.

Does the shipped code make AdministrateMe more useful in a specific, named way for a specific kind of household event? Examples that would be "yes":
- A reminder pipeline that fires for a real overdue commitment → yes
- A custody brief that surfaces tomorrow's pickup time the parent would otherwise miss → yes
- A morning digest that summarizes overnight messages the member needs to act on → yes

Examples that would be "no" / "unsure":
- A pipeline that technically works but has no consumer of its output → unsure
- A skill pack that produces text but the consumer pipeline doesn't deliver it → unsure
- Test scaffolding only — no behavior change → neutral (not a "no")

**Reviewer evidence:** point to the user-facing surface (`channel`, `member_id`, `recipient`) the merged code touches.

### Q2 — Privacy-first: does the code respect the privacy boundary, especially around member data?

**Anchored to:** `[VISION §"privacy-first"]`, `[§12 tenant isolation]`, `[§15 instance paths]`.

Does the merged code:
- Keep tenant identity out of platform paths (`adminme/`, `bootstrap/`, `profiles/`, `personas/`)? — `verify_invariants.sh` catches the obvious cases; Job 4 confirms the diff-level intent.
- Avoid leaking member data into logs, env vars, or commit messages?
- Respect the `~/.adminme/` discipline — no hardcoded paths?
- If outbound delivery: route through `outbound()`?

A "no" on any of these is a Job 2 invariants violation (HARD). Q2 isn't usually the issue — Job 2 already catches it. But Q2 also catches subtler drift like: code that doesn't violate an invariant but normalizes a pattern that would in a future change (e.g., introducing a `member_id` into a logger call that the developer might extend later to log `member_name`).

### Q3 — ADHD-aware: does the code preserve neuroprosthetic patterns for ADHD members?

**Anchored to:** `[VISION §"speaks ADHD"]`, `[VISION §"reduces cognitive load"]`, `[BUILD.md §"member_profile.adhd_aware"]`.

The product is designed for ADHD-aware households. ADHD-relevant patterns include:
- Externalizing working memory (reminder pipelines, custody briefs, morning digests)
- Reducing context-switch cost (channel-native delivery in the member's preferred channel)
- Avoiding executive-function tax (one decision per surface, not five)
- Gentle nudges, never strict commands

Does the merged code preserve these? Cases where Q3 fails:
- A new pipeline that pushes a long list of decisions the member must triage
- A skill prompt that uses imperative voice ("complete this task") instead of suggestive voice ("here's what's coming up")
- A console surface that piles options into a single screen

**Reviewer evidence:** quote a representative line from the skill prompt or pipeline handler that demonstrates the ADHD-aware intent (or the absence of it).

### Q4 — Observation mode: does this code preserve the observation-mode-default boundary?

**Anchored to:** `[VISION §"observation mode is default"]`, `[§6.13 observation mode]`, `[DIAGRAMS §9]`, `[D22 observation default-on]`.

Every outbound delivery MUST gate on `InstanceConfig.observation_mode`. Specifically:
- All `outbound.deliver()` calls check observation mode and emit `observation.suppressed` when on
- Composed events (`*.composed`, `*.dispatched`) are appended regardless — only the send is suppressed
- Tests verify the observation-on path AND the observation-off path
- Phase A default is `observation_mode=on`; Phase B can flip it

**This is a hard tenet, not soft.** If the merged code adds an outbound path that bypasses observation mode (direct channel API call, missing dispatcher.deliver, missing observation_mode check), Q4 = **anti-vision** = **score 3** alone, regardless of the other five questions.

### Q5 — Channel-native: does new functionality live in channels (OpenClaw side) when it should?

**Anchored to:** `[VISION §"channel-native"]`, `[§8 OpenClaw boundaries]`, `[§8.9 skill runner only outbound]`.

The console is for surfaces specific to the household (an inbox, a calendar, an admin view). Channel-native behavior (sending iMessages, voicemail transcription, calendar invites) lives in OpenClaw plugins. The boundary is:

- **In AdministrateMe:** the projection that decides "this member needs a reminder" + the pipeline that composes it
- **In OpenClaw:** the channel adapter that actually sends the iMessage / SMS / push

Q5 catches when a console-side feature should have been a channel-side feature, OR when channel-specific code leaked into AdministrateMe's `adminme/` tree.

Cases where Q5 fails:
- A `bluebubbles_client.py` inside `adminme/` — that belongs to OpenClaw
- A console view that schedules outbound iMessage sends directly — should go through `outbound.deliver()` → OpenClaw
- A pipeline handler that contains BlueBubbles-specific logic — should call into `outbound()` only

### Q6 — Multi-member: does the code respect that this is for households, not single users?

**Anchored to:** `[VISION §"multi-member"]`, `[VISION §"every member has their own profile"]`, `[BUILD.md §MEMBER BRIDGES]`.

AdministrateMe is for households of 2-10 members. Every relevant data structure carries a `member_id`. Every relevant pipeline iterates per-member. Every relevant surface is filtered by `owner_member_id`.

Cases where Q6 fails:
- A pipeline that assumes a single user (no per-member loop)
- A projection table without `(tenant_id, member_id)` composite key where member-scope matters
- A skill prompt that uses singular pronouns assuming one user
- A console view that doesn't accept a `member_id` filter

**v3 test-drive note:** 10c-iii's `custody_brief` pipeline explicitly per-coparent. The refactored prompt got Q6 right. Future Partners should preserve this pattern.

---

## Reviewer's Job 4 output

After walking the six questions, Reviewer emits this JSON block as part of `reviewer_verdict.json`:

```json
"job_4_vision_check": {
  "verdict": "aligned" | "neutral" | "drift_sideways" | "drift_backward",
  "vision_drift_score": 0 | 1 | 2 | 3,
  "strict_block": true | false,
  "questions": {
    "q1_family_utility": {"answer": "yes" | "no" | "unsure", "evidence": "<file:line or quote>"},
    "q2_privacy": {"answer": ...},
    "q3_adhd_aware": {"answer": ...},
    "q4_observation_mode": {"answer": ...},
    "q5_channel_native": {"answer": ...},
    "q6_multi_member": {"answer": ...}
  },
  "summary": "<2-3 sentences explaining the score>",
  "recommended_action": null | "operator_review" | "sidecar_fix" | "revert"
}
```

`strict_block=true` iff `vision_drift_score=3`. In that case, Reviewer ALSO posts a PR comment titled "STRICT VISION-DRIFT BLOCK" with the summary, and `overall_verdict` is forced to `FAIL_CRITICAL` regardless of Jobs 1-3.

---

## Calibrating the score over time

The score boundaries (what's a 1 vs a 2 vs a 3) are author's-best-guess in v4. Real calibration happens over the first ~5 merged PRs:

- If operator sees a vision_drift_score of 2 and disagrees ("this is fine, score should be 1"), that's a calibration data point.
- After 5 PRs of calibration, Reviewer's Job 4 is more reliable.
- The rubric here is the v4 starting point; v5 may tighten the boundaries based on data.

The operator can ALWAYS override Reviewer's verdict via a `<JOB_4_OVERRIDE score=N reason="..."/>` note in the PR comment thread. The override is logged in `docs/build_log.md`.

---

## When Partner pre-refactor Q4 fires

Partner runs through the six questions before opening a refactor PR. If any answer would be "no" or "unsure" for the prompt as-drafted:

1. **Refine the prompt.** Add a §6.X note that says "this prompt MUST respect observation mode per Q4" with a citation. Make the constraint explicit in the prompt so the Executor sees it.
2. **OR** add an explicit `## Vision-drift acknowledgment` section to the refactored prompt that says: "This prompt scores a likely [1 | 2] on Job 4 because [reason]. Operator pre-approved the drift because [reason]."
3. **OR** open a `<NEEDS_OPERATOR_DECISION/>` in the PR description and stop.

Don't refactor a prompt that scores a likely 3. That's not a refactor — that's an architecture amendment.

---

## What Q4 catches that v3 didn't

v3's Job 4 was advisory: "vision findings don't fail a merge." The pattern over 33 prompts would have been: each PR's Job 4 says "unsure on Q1, neutral on Q5", the operator skims, the operator merges, the cumulative drift never surfaces because no single PR triggers a hard signal.

v4's strict mode forces a binary at the worst level (score 3 = STRICT-FAIL block). The frequency of score-3 hits should be very low (the v3 Job 4 found one in 22 PRs over the supervised build). When it does fire, the operator's attention is summoned to a specific, citable concern. The bundle's cumulative drift over 33 prompts goes from "subtle, hard to detect" to "any time it would be catastrophic, the merge gate catches it."

The remaining drift surface (score 0-2) is still under operator discretion and still depends on Partner's pre-refactor Q1-Q6 walk. Strict mode raises the floor, doesn't replace the ceiling.
