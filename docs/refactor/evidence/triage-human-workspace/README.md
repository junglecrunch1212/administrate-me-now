# Human-centered Triage evidence

Status: **PASS — PHASE A CREDENTIAL-FREE EVIDENCE; NO DEPLOYMENT AUTHORIZED**

This evidence closes CO-020 against the production composition root with the
fixed fixture clock `2026-08-30T14:00:00Z`, Python 3.14.6, Node 24.19.0, Google
Chrome 151, and Playwright Core 1.62.0. The disposable instance contained no
family data, provider credentials, live provider calls, or deployment action.

## Human audit comparison

The retained pre-change Gate 7 captures are:

- `../gate-7/visual/surface-triage-task-desktop.png`
- `../gate-7/visual/surface-triage-narrow.png`

The CO-020 captures are:

- `after-decision-desktop.png` — decision-first hierarchy with optional edit
  details;
- `after-narrow.png` — non-sticky narrow navigation, horizontally scrollable
  lenses, and no page overflow;
- `after-gmail-category.png` — reviewed Gmail Promotions context labelled as
  non-authoritative, with arbitrary labels absent; and
- `after-caught-up-tablet.png` — truthful empty state for a filtered complete
  inventory after an exact decision.

## Automated evidence

`gate7-results.json` records 696/696 passing assertions. It includes:

- real cursor traversal of 520 synthetic low-signal candidates exactly once;
- complete inventory/lens/facet counts without loaded-window bookkeeping;
- source/type filters, exact off-page links, stale-cursor recovery, and
  post-decision continuity;
- desktop, 390 px, 820 px, 1024 px, 1280 px, and 200% reflow checks;
- names/roles/values, tab order, focus visibility, contrast, target size, live
  regions, reduced motion, and overflow checks;
- reviewed Gmail category presentation with no arbitrary provider-label leak;
- date-only display without a timezone-day shift; and
- zero unexpected console errors, warnings, page errors, request failures, or
  HTTP error responses.

The result file is source-bound to member asset version `ba59985062d3` and the
following retained SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| `after-decision-desktop.png` | `85328bea54d4408742d940f32de7af5b7e10e63158989f5df7a963efc0ba44f8` |
| `after-narrow.png` | `2cdc5c6c2e37420e5bef52241d43876da185757add8294b33de069abbd09db79` |
| `after-gmail-category.png` | `738f9e36ed56b66299a85c75a8cd2e4f8cacfed6a304051ef2ddbb9ed374a25b` |
| `after-caught-up-tablet.png` | `c290d17b1029fed9cf0044b6b99c1ec219c661744f1af89fc08e538bb31405bd` |
| `gate7-results.json` | `da1f2e4fb4c2b0b9832df089a2d5a476fd455880fa99e2de86fb18e747df6178` |

This is product and release-candidate evidence only. It grants no authority to
merge, deploy, migrate a runtime, enroll a provider, enable writes, or alter a
live family instance.
