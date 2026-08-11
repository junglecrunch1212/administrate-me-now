# CO-016 Triage queue-continuity evidence

Status: local candidate evidence complete; independent review and hosted gates
pending; not merged or deployed.

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
- a pending Add-for-review request resisting native Escape and retaining its
  exact input after a definite pre-write failure;
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
  browser reconciles the exact terminal receipt instead of claiming rejection
  or substituting another matter;
- explicit navigation to Recent;
- exact selection of an eligibility-retired historical candidate;
- provenance-bounded lifecycle-receipt language that cannot be mistaken for a
  proven migration or learned sender rule; and
- the previously proven exact destination receipt and resolved deep-link
  behavior.

## Local candidate results

```text
Focused Triage/projection/pipeline/production-composition tests
48 passed

Gate 7 rendered production composition
250 assertions; 39 screenshots; 0 console errors; 0 page errors;
0 request failures

Full release-candidate suite
49 strict acceptance gates; 1,792 root/code/pack tests; 60 bridge tests;
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

Hosted checks, an independent exact-head review, and release evidence will be
added only when obtained. G-001 continues to prohibit merge or deployment
without a fresh exact-head authorization.
