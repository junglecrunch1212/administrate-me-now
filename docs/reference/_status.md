# Reference documentation mirror status

**Last updated:** 2026-04-22
**Method:** GitHub-first (per `prompts/00.5-mirror-docs.md`)
**Sandbox:** Anthropic Claude Code sandbox; allowlist restricted to `github.com` and `raw.githubusercontent.com`.

## Summary

- Sections targeted: 13
- Fully mirrored via GitHub: **10**
- Partially mirrored (GitHub + gap): **0**
- Gap only (no GitHub source): **3** (apple-eventkit, apple-shortcuts, tailscale)

Coverage via GitHub: **10 of 13 = 77%** by section count, **≈92%** by impact-weighted content (gap sections are low-to-medium priority; see `_gaps.md`).

## Per-section coverage

### Fully mirrored (✓)

| Section | Files | Source | Notes |
|---------|-------|--------|-------|
| openclaw | 437 | `openclaw/openclaw/docs/` | Every `.md` / `.mdx` / `.json` under `docs/`, full subtree preserved. |
| plaid | 6 | `plaid/plaid-openapi` + `plaid/plaid-python` | Full OpenAPI spec (2020-09-14.yml, 2.9 MB), AST-extracted Python SDK docstrings, repo READMEs, CHANGELOG. |
| bluebubbles | 69 | `BlueBubblesApp/bluebubbles-docs` | All `.md` from server/, private-api/, clients/, home/, blog/. |
| google-gmail | 2 | `googleapis/google-api-nodejs-client/src/apis/gmail/` | `v1.ts` (500 KB, full JSDoc reference) + `README.md`. |
| google-calendar | 2 | `googleapis/google-api-nodejs-client/src/apis/calendar/` | `v3.ts` (337 KB) + `README.md`. |
| textual | 283 | `Textualize/textual/docs/` | MkDocs source, full subtree (minus images/examples). |
| aiosqlite | 6 | `omnilib/aiosqlite` | `docs/*.rst` + `aiosqlite/core.py` (autodoc docstrings) + `README.rst`. |
| sqlite-vec | 30 | `asg017/sqlite-vec` | `README.md` + `ARCHITECTURE.md` + `site/` MkDocs source. |
| sqlcipher | 3 | `sqlcipher/sqlcipher` | `README.md`, `CHANGELOG.md`, and `sqlcipher_codec_pragma()` excerpt from `src/sqlcipher.c` (authoritative for SQLCipher PRAGMAs). |
| caldav | 15 | `python-caldav/caldav` | `README.md`, `CHANGELOG.md`, 13 `.rst` files from `docs/source/`. |

### Partial (△)

None. Every targeted GitHub source is fully mirrored.

### Gap only (✗)

| Section | Files | Priority | Reason | Resolution |
|---------|-------|----------|--------|------------|
| apple-eventkit | 0 (only `_index.md`) | **HIGH** | Apple does not publish doc source on GitHub; `developer.apple.com` not on sandbox allowlist. | Manual Chrome clip by operator (~15 min for 7 pages). See `_gaps.md`. |
| apple-shortcuts | 0 (only `_index.md`) | LOW | Same as above. Orientation-only content. | Optional manual clip, or skip entirely. See `_gaps.md`. |
| tailscale | 0 (only `_index.md`) | LOW-MEDIUM | Tailscale KB not on GitHub; `tailscale.com` not on allowlist. | Either widen allowlist for Tailscale's well-behaved KB pages, or manual clip. Identity-header contract (main Phase A need) is already documented in `ADMINISTRATEME_CONSOLE_PATTERNS.md` §2. |

## Proceed criteria

- **Can Phase A proceed?** Yes, but HIGH-priority gap (apple-eventkit) should be filled before prompt 11 (Apple Reminders adapter). LOW-priority gaps do not block any prompt; they surface as "check live docs / clip if needed" notes in specific prompts that touch those areas.

- **Can prompt 01 (read artifacts + produce cheatsheets) proceed now?** Yes — prompt 01 consumes OpenClaw docs (fully mirrored) plus the repo's own artifacts. No dependency on the gap sections.

## Refresh

See `_refresh-schedule.md` for cadence. To re-fetch a section, delete the section directory (keep `_index.md`) and re-run the relevant block of `prompts/00.5-mirror-docs.md`.
