# Prompt 14d: Reward system + observation banner + degraded mode

**Phase:** BUILD.md L5 — UX polish. Implements CONSOLE_PATTERNS.md §8 (reward emission), §9 (degraded mode), §11 (observation enforcement UX).
**Depends on:** Prompt 14c.
**Estimated duration:** 2-3 hours.
**Stop condition:** Completing a task fires a tier-appropriate reward toast via SSE; observation banner visible + interactive; degraded mode UI activates when Python APIs are unreachable.

## Read first

1. `ADMINISTRATEME_CONSOLE_PATTERNS.md` §8, §9, §11.
2. `ADMINISTRATEME_CONSOLE_REFERENCE.html` — observe the reward toast animation; the observation banner's placement; what degraded mode looks like.

## Objective

Three small but consequential UX pieces.

## Deliverables

- **Reward emission** (`console/lib/rewardEmitter.js`): dual-path (sync preview inline + SSE canonical after pipeline runs). Correlation-ID deduplication so the same reward doesn't show twice.
- **Observation banner**: always-mounted component (injected into every authenticated view's header). Shows active state. Click to toggle (requires principal role; triggers guardedWrite on the toggle action).
- **Degraded mode** (`console/lib/degradedMode.js`): detects Python API unreachability (rolling failure count + TTL). When in degraded mode, views show cached data with "last updated X minutes ago" hint, writes queue to `~/.adminme/queued/writes/` and emit a local toast.

## Tests

- Reward fires with correct tier for each profile's distribution.
- Dedup: same correlation_id doesn't double-fire.
- Observation toggle: child can't, principal can, state updates everywhere.
- Degraded mode: force Python API down, verify degraded banner appears, verify queued writes replay when service returns.

## Verification

```bash
cd console && npm test
# Manual
# 1. Complete a task in console; observe reward toast
# 2. Toggle observation banner as principal; verify state
# 3. Kill a Python API; observe degraded mode banner
git commit -m "phase 14d: reward + observation banner + degraded mode"
```

## Stop

> Console is polished. All surfaces render, reward works, observation is visible, degraded mode handles Python-API failures gracefully. Ready for prompt 15 (OpenClaw integration finalization).
