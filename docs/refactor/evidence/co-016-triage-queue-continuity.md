# CO-016 Triage queue-continuity evidence

Status: **RELEASED IN OBSERVATION.** Independent exact-head review passed on PR
#334; candidate `40f1c27a1259b35c263cf980fbbb9cf3e4f5d239` merged as
`2d739ad2e89a49bf330a854317326482907b956d` and is contained by active
observation release `d181fad24ef488a71ce5568cc40fb81309cb3817`.

## Reproduction

Authenticated live read-only inspection showed a settled source inventory and
hundreds of unresolved candidates. The Acorns dismissal existed as one
reversible feedback receipt and no active learned rule existed. Browser state
still named the resolved candidate; the resolved focus response switched the
whole surface to Recent, where historical `eligibility_rule_replaced` items
were displayed with raw reason labels. No household data is copied here.

## Candidate proof

Gate 7 contains generated tenant-neutral fixtures and assertions for:

- confirmation staying in Ready with candidate/matter URL state cleared;
- typed Spam dismissal staying in Ready and selecting a remaining matter;
- dismissal of an isolated member's only Ready candidate reporting the empty
  refreshed Ready queue as caught up;
- explicit navigation to another lens clearing the prior lens-relative
  continuation message;
- reselecting the active lens preserving its continuation message;
- a non-continuation enrichment failure replacing prior continuation feedback
  and remaining visible through a subsequent lens change;
- a forced post-write refresh failure preserving its warning and locking stale
  surface controls, the shared Add-for-review button, and a retained
  Add-for-review form through active/different-lens clicks and programmatic
  submission until successful reload;
- a pending Add-for-review request resisting native Escape, plus a definite
  pre-capture 409 that is not retried and retains its editable exact input;
- a load-more request begun and held before confirmation, then released only
  after the authoritative refresh, with browser-side response consumption
  observed before asserting that its stale result cannot overwrite the queue;
- a dirty Save & confirm held before enrichment and then through decision, with
  every surface and shared-shell Triage control disabled and programmatic lens,
  intent, enrichment-form, and pagination attempts rejected without new
  transport;
- invalidated load-more state normalized before a forced no-render enrichment
  failure, with the late response unable to alter the later view;
- an enrichment response changing review state to Needs details and preventing
  any automatic decision request while exact selection is preserved outside
  the ordinary first-page response;
- successful enrichment followed by a forced decision conflict, proving the
  exact saved input and revision are authoritatively refreshed independently
  of the ordinary cursor and an immediate retry succeeds;
- a committed confirmation whose response is replaced by a 503, proving the
  browser reconciles the exact event/request-specific terminal receipt instead
  of claiming rejection or substituting another matter, and a separate case
  proving another request's terminal receipt cannot settle the browser action;
- an actual guard refusal inside the local materialization handler after domain
  confirmation, followed by one continuation with the identical request ID and
  a proven local materialization receipt;
- an exact pending candidate changing to evidence-only during reconciliation,
  remaining visible in All candidates without an unrelated matter replacing it
  and without a confirm capability in the browser, Triage API, or underlying
  commitment approval route;
- ambiguous confirmation/dismissal responses settling only from the exact
  member-authored event and request receipt, never another request or a system
  supersession;
- Add for review from Today held through native Escape, failed inside the
  handler after exact intent capture, and completed on one retry with the same
  client request ID and the one recorded feature interpretation;
- a structured post-capture 409 receiving one same-ID retry, then retaining the
  exact dialog and an enabled, focused action that opens the exact durable
  Needs interpretation item from both Today and Triage;
- an unknown 503 outcome from Today retaining the one request identity and
  opening All candidates for inspection before any resubmission;
- two recorded candidates governance-preflighted before atomic append, proving
  a denial on the second child leaves no partial proposal family and recovery
  reuses the one recorded interpretation;
- matching canonical truth injected between API read and confirmation
  projection, proving confirmation is not accepted and no duplicate Task is
  materialized;
- a v7 Triage read model explicitly opted into v8 replay from retained event
  zero so an existing pending provider-action receipt becomes visible without
  deleting legacy rows or missing exact confirmation receipts after a later
  local outcome, plus an interrupted replay that leaves v7 stamped and retries
  successfully on the next start; no generic projection replay is enabled;
- explicit navigation to Recent;
- exact selection of an eligibility-retired historical candidate;
- provenance-bounded lifecycle-receipt language that cannot be mistaken for a
  proven migration or learned sender rule; and
- the previously proven exact destination receipt and resolved deep-link
  behavior.

## Reviewed candidate results

```text
Focused Triage/projection/pipeline/production-composition tests
27 passed

Gate 7 rendered production composition
259 assertions; 39 screenshots; 0 console errors; 0 page errors;
0 request failures

Full release-candidate suite
53 strict acceptance gates; 1,799 root/code/pack tests; 60 bridge tests;
Ruff/format/mypy/Pyright; dependency audits; performance budgets;
architecture checks; clean install; tenant isolation; backup/restore;
pinned OpenClaw contract, 17 plugin tests, build, package, and audit

Exact OpenClaw source-install rehearsal
PASS: 2026.7.2 at ce67ffb70e83e5ebb56ea02e814e577153f84f61

Apple-silicon macOS bootstrap rehearsal
69 passed; runtime and OpenClaw launchd plists valid; PASS (arm64)
```

The first direct macOS rehearsal invocation stopped before candidate execution
with `ModuleNotFoundError: No module named 'yaml'` because its bare `python3`
resolved to macOS system Python. Binding `python3` to the clean Poetry Python
3.14 environment produced the passing result above. No test was waived and no
installed runtime was touched.

Hosted checks and the independent exact-head human review were subsequently
obtained on PR #334 for head
`40f1c27a1259b35c263cf980fbbb9cf3e4f5d239`. G-001 authorized its merge as
`2d739ad2e89a49bf330a854317326482907b956d`; active observation release
`d181fad24ef488a71ce5568cc40fb81309cb3817` contains that merge. This retained
evidence records the completed historical transaction and grants no authority
for a new merge, deployment, provider action, or live change.
