# Reference doc refresh schedule

External APIs and frameworks evolve; this mirror ages. Cadence per section:

| Section | Cadence | Source | Last refreshed |
|---------|---------|--------|----------------|
| openclaw | Quarterly | `openclaw/openclaw` | 2026-04-22 |
| plaid | Quarterly | `plaid/plaid-openapi` + `plaid/plaid-python` + plaid.com/docs (manual clip) | 2026-04-22 |
| bluebubbles | Semi-annually | `BlueBubblesApp/bluebubbles-docs` | 2026-04-22 |
| google-gmail | Semi-annually | `googleapis/google-api-nodejs-client` (`src/apis/gmail/v1.ts`) | 2026-04-22 |
| google-calendar | Semi-annually | `googleapis/google-api-nodejs-client` (`src/apis/calendar/v3.ts`) | 2026-04-22 |
| textual | Quarterly | `Textualize/textual` (`docs/`) | 2026-04-22 |
| aiosqlite | Yearly | `omnilib/aiosqlite` | 2026-04-22 |
| sqlite-vec | Quarterly | `asg017/sqlite-vec` | 2026-04-22 |
| sqlcipher | Yearly | `sqlcipher/sqlcipher` | 2026-04-22 |
| caldav | Every 3 years (stable protocol) | `python-caldav/caldav` | 2026-04-22 |
| apple-eventkit | Yearly (manual clip) | developer.apple.com | 2026-04-22 |
| apple-shortcuts | Yearly (manual clip) | support.apple.com | never — gap |
| tailscale | Semi-annually (allowlist or manual) | tailscale.com | 2026-04-22 |

## Rationale for cadence choices

- **Quarterly** for the most active sources: OpenClaw (pre-1.0 platform), Plaid (frequent API additions), Textual (active development), sqlite-vec (pre-1.0).
- **Semi-annually** for stable but maintained sources: BlueBubbles, Google APIs (Gmail/Calendar v1/v3 are stable but get new fields).
- **Yearly** for mature, slow-moving sources: aiosqlite, SQLCipher.
- **Every 3 years** for protocol-level mirrors: CalDAV (RFC 4791 ratified 2007, barely changes).
- **Yearly or on-demand** for gap sections: EventKit / Shortcuts / Tailscale. Refresh whenever the operator clips updated pages.

## Refresh procedure (for GitHub-sourced sections)

1. Note the current last-refreshed date in this table.
2. Delete the section directory except for `_index.md`:
   ```bash
   find docs/reference/<section> -mindepth 1 ! -name '_index.md' -delete
   ```
3. Re-run the relevant block of `prompts/00.5-mirror-docs.md` (or a future `--section=<name>` flag).
4. Review the diff for material changes:
   ```bash
   git diff HEAD -- docs/reference/<section>/
   ```
   If upstream added, renamed, or removed files, decide whether the build
   needs changes to match.
5. Update this file's last-refreshed date.
6. Commit:
   ```bash
   git commit -m "docs: refresh <section> mirror ($(date -I))"
   ```

## Refresh procedure (manual-clip sections)

1. The operator opens each URL in the `_gaps.md` table for that section.
2. Clips the page content into the target file with the standard header.
3. Commits and updates the last-refreshed date here.

## Automated reminder

A monthly cron on the Mac Mini (added by Phase B bootstrap) runs:
```bash
adminme docs check-staleness
```
which prints any section whose last-refreshed date is beyond its cadence, so
the operator knows when to schedule a refresh.
