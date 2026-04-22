# Reference documentation mirror status

**Last updated:** 2026-04-22

**Method:** GitHub-first (prompt 00.5) + manual Chrome clip via Claude Cowork (prompt 00.5b, this update)

**Sandbox:** Anthropic Claude Code sandbox; allowlist restricted to `github.com` and `raw.githubusercontent.com`. Hosts outside the allowlist (Apple, Tailscale, Plaid narrative) are filled by operator-supervised Cowork clips.

## Summary

- Sections targeted: 13
- Fully mirrored: **12**
- Partially mirrored: **0**
- Gap only: **1** (apple-shortcuts — LOW priority, orientation-only, intentionally deferred)

## Per-section coverage

### Fully mirrored (✓)

| Section | Files | Source | Method |
|---------|-------|--------|--------|
| openclaw | 437 | `openclaw/openclaw/docs/` | GitHub clone |
| plaid | 10 | `plaid/plaid-openapi` + `plaid/plaid-python` + plaid.com/docs | GitHub + Cowork clips (3 narrative pages) |
| bluebubbles | 69 | `BlueBubblesApp/bluebubbles-docs` | GitHub clone |
| google-gmail | 2 | `googleapis/google-api-nodejs-client/src/apis/gmail/` | raw.githubusercontent.com |
| google-calendar | 2 | `googleapis/google-api-nodejs-client/src/apis/calendar/` | raw.githubusercontent.com |
| textual | 283 | `Textualize/textual/docs/` | GitHub clone |
| aiosqlite | 6 | `omnilib/aiosqlite` | GitHub clone |
| sqlite-vec | 30 | `asg017/sqlite-vec` | GitHub clone |
| sqlcipher | 3 | `sqlcipher/sqlcipher` | GitHub clone + source excerpt |
| caldav | 15 | `python-caldav/caldav` | GitHub clone |
| apple-eventkit | 7 | developer.apple.com | Cowork Chrome clip |
| tailscale | 6 | tailscale.com/docs/features/ | Cowork Chrome clip |

### Gap only (✗)

| Section | Files | Priority | Reason |
|---------|-------|----------|--------|
| apple-shortcuts | 0 (only `_index.md`) | LOW | Apple does not publish doc source; orientation-only content; no Phase A code decision depends on it. |

## Proceed criteria

Phase A proceeds. The HIGH-priority gap (Apple EventKit) is now filled. All sections referenced by the prompt sequence are mirrored.

## Refresh

See `_refresh-schedule.md` for cadence. Cowork-clipped sections (apple-eventkit, tailscale, plaid narrative) refresh via the same Cowork-in-Chrome flow used to produce this update.
