# Change Order 015 — Decision comprehension and surface integrity

Status: deployed and authenticated-live-verified in observation mode.

Governing sources: product thesis; ADR-0000, ADR-0001, ADR-0008; CO-001,
CO-007, CO-009, CO-011, CO-012, and CO-014; UX-03, UX-05, UX-10, UX-24,
UX-46, UX-48, and UX-50–UX-54.

## 1. Incident and outcome

Today rendered a candidate title, target kind, generic micro-step, and a
`Review` control under **Needs a decision**. That was not enough to answer what
happened, what the member was deciding, why it mattered now, which source
created the question, what remained uncertain, or what confirmation and
dismissal would do. The control carried a candidate id, but Triage loaded only
its first cursor page; a candidate beyond that page silently fell back to the
first visible matter. The same generic-Triage defect remained in Comms, and
Notes did not carry the matter identity. Scoreboard next steps had no exact
return path.

CO-015 establishes one deterministic **decision comprehension contract** and
one **surface-link integrity contract**. A member can understand a decision on
Today without opening its raw source, then open the exact candidate and matter
in Triage. Triage resolves the requested id independently of cursor position,
distinguishes pending, resolved, and unavailable state, preserves the request
through reload/history, and never substitutes another member's decision.

## 2. Constitutional classification

| Change | Classification | Boundary preserved |
|---|---|---|
| Publish `member-decision-context-v1` on Today | MODERNIZE | Read-time presentation over authorized Triage/source rows; it is not truth, evidence, ranking, or authority. |
| Use a bounded interpretation for case/owner/next-step wording when present | PRESERVE/HARDEN | The pinned OpenClaw seam may supply schema-bound language fields; code owns the contract, fallback, choices, route, and disclosure. |
| Add candidate-and-matter exact links plus server-side focus lookup | MODERNIZE | Link state changes presentation only; tenant/member/scope checks still fail closed. |
| Keep resolved exact links inspectable and unavailable links explicit | HARDEN | Immutable decisions and evidence remain; no stale link can reopen, confirm, or rewrite a decision. |
| Replace generic Comms/Notes Triage links and add Scoreboard return links | SUPERSEDE generic navigation | Domain identity and source-of-truth boundaries remain unchanged. |
| Extend `surface-link-v1` to all ten stable adult routes | HARDEN | The contract remains non-authorizing and contains no household data at rest. |

This change adds no projection, graph, source store, model router, provider SDK,
write path, or external side effect. The exact projection count remains
fifteen. No provider body is copied into Context Fabric or a second summary
store.

## 3. `member-decision-context-v1`

Each Today clarifying decision publishes:

- stable `candidate_id` and `matter_id`;
- one direct `decision_prompt`;
- `what_happened` and deterministic `why_now`;
- authorized source state, channel, sender, subject, and observation time;
- unresolved questions without invented answers;
- fixed choices — Confirm, Correct, Dismiss — with code-owned consequences;
- the smallest next step and an evidence-availability flag;
- `decision_maker: member`; and
- an explicit `bounded_language_review_aid` or
  `deterministic_source_facts` disclosure.

The compact card shows the decision, what happened, why it is present, and
source label. Uncertainty, consequences, and method disclosure are
progressively disclosed. Exact source wording remains behind evidence controls
and is never relabelled as generated explanation.

The deterministic fallback question is “Should AdministrateMe add this
`target_kind`: `candidate_title`?” A source-grounded unresolved question may
replace that wording, but it cannot select the target, route, priority,
authority, member, write, or outcome.

## 4. Exact navigation contract

An unresolved decision link has the canonical shape:

```text
/member/triage?item=<candidate-id>&matter=<matter-id>&view=all_candidates
```

A resolved decision uses `view=recent`. The API receives the candidate id as
`triage_item`, checks the tenant, viewed member, private owner/visibility scope,
and row authorization, and returns one of:

- `pending` — the complete matter is prepended if it is outside the cursor;
- `resolved` — the immutable decision is made reachable even outside the
  ordinary recent limit;
- `not_found` — no other item is substituted and no cross-scope fact leaks; or
- `not_requested` — ordinary lens behavior applies.

The browser selects and focuses the exact candidate, keeps its matter and view
in the URL, and clears stale focus state when the member deliberately changes
lenses. Confirmation may return the now-resolved exact item; choosing another
lens must not snap back to it.

## 5. Ten-surface audit

| Surface | Audit result and CO-015 action |
|---|---|
| Today | MODERNIZE decision cards and exact Review navigation in Day Brief and Carousel. |
| Triage | MODERNIZE focus lookup, pending/resolved/unavailable state, exact DOM focus, lens escape, paging, and reload behavior. |
| Comms | MODERNIZE candidate count from source-count to actual candidate inventory; show individually linked candidate/matter rows instead of generic Triage. |
| Reminders | PRESERVE typed item deep links and destination receipts; browser proof remains green. |
| Calendar | PRESERVE URL-backed view/date/event selection and typed event identity; browser proof remains green. |
| People | PRESERVE typed Party links, URL selection, deterministic cues, and scoped detail; browser proof remains green. |
| Notes | MODERNIZE candidate links to carry candidate, matter, view, and evidence identity; retain no inline approval. |
| Finance | PRESERVE URL-backed deterministic views and workbook/provider authority; browser proof remains green. |
| Scoreboard | MODERNIZE open canonical Tasks with an exact Reminders return link; retain evidence for terminal outcomes. |
| Settings | PRESERVE nine URL-backed control areas and truthful identity/capability states; correct stale “seven-section” constitutional copy. |

## 6. Failure handling

- Missing or withheld source context is named and never filled by inference.
- A focus id outside the member's private scope is indistinguishable from a
  nonexistent id.
- A focus id outside the loaded page does not alter cursor totals or ordering.
- A resolved id cannot be confirmed again and remains linked to its receipt.
- A deliberate lens change clears focus so a stale server response cannot
  force the member back to Recent.
- Back, reload, phone reflow, reduced motion, and keyboard focus preserve the
  same authorized truth and exact identity.
- No link or card authorizes a domain mutation or provider action.

## 7. Acceptance evidence

Required release evidence is:

1. unit proof of repeatable decision wording, source disclosure, choices,
   exact link encoding, all-ten-surface route coverage, cursor-independent
   matter lookup, and cross-member denial;
2. production-composition proof that Today publishes the typed contract, an
   off-page pending candidate resolves exactly, another member receives
   `not_found`, and the same link reaches a later immutable dismissal;
3. production Comms proof that a linked candidate retains its exact matter;
4. Gate 7 browser proof for decision comprehension, Today-to-Triage focus,
   Notes-to-Triage identity, Scoreboard-to-Reminders identity, every adult tab,
   desktop/tablet/phone, reload, accessibility, and degraded domain behavior;
5. zero browser console, page, and request failures; and
6. final release checks, reviewed commit, deployment, authenticated live
   browser observation, Doctor, backup, and zero unintended provider effects.

The final exact-head candidate run passes 229 Gate 7 assertions with 36
screenshots and zero console/page/request failures.

## 8. Phase B activation evidence

PR #331 head `ca1a29033e48d5d6d32a0cd77de3fc0c1266778f` received a
content-free independent review receipt after complete exact-head release and
browser gates, two green hosted checks, clean mergeability, and a thread-aware
zero-review-thread check. Its merge commit
`2fede3f6a72f3ee6df99c0cefb7fca71471ff7fe` became `origin/main` before
Phase B began.

The attested same-runtime updater created encrypted backup
`administrateme-20260811T102316Z.ambackup`, reported no runtime or OpenClaw
migration, retained predecessor `0e0f13e`, and left rollback available.
Independent final status and full Doctor are healthy in observation mode with
15/15 projections, 8/8 required pipelines, 62,580/62,580 settled source
obligations, zero pending/missing/failed, the exact OpenClaw/`llm-task` seam,
and zero synthetic external side effects.

Authenticated read-only live acceptance found decision-grade context in both
Day Brief and Carousel; followed a candidate + matter + `all_candidates` link
to the exact rendered and focused Triage item; confirmed a nonexistent id
produces an explicit no-substitution state; and measured a 375 px document in
a 390 px phone viewport. Observation remained visible and active, the browser
recorded no warning or error, and the provider-called terminal count remained
zero before and after activation. No member decision or provider action was
performed.
