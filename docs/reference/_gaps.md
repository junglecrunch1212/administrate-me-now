# Reference documentation gaps

Content the build references that could NOT be mirrored from GitHub in prompt 00.5.
Claude Code CANNOT automatically fill these — operator action required.

**Last updated:** 2026-04-22

---

## Preferred resolution: widen the sandbox allowlist

These hosts are lightweight, well-behaved, serve static HTML, and are unlikely to
block a reasonable user-agent. If the operator can widen the Anthropic sandbox
allowlist (or the local `~/.claude/settings.json` allowlist), re-running 00.5 with
a `--retry-gaps` flag (or manually re-invoking the relevant fetch blocks) would
close most of these gaps without manual clipping:

| Host | What's there | Approximate size | Priority |
|------|--------------|------------------|----------|
| `rfc-editor.org` | RFC 4791 (CalDAV), RFC 5545 (iCalendar). Plain text. | ~200 KB total | LOW |
| `tailscale.com` | 6 KB pages (Serve, Funnel, identity headers, ACLs, exit nodes, webhooks). | ~60 KB total | LOW-MEDIUM |
| `plaid.com` | 3 narrative guides (Link update mode, errors taxonomy, institution status). | ~80 KB total | LOW |

If the allowlist is widened, Claude Code can re-fetch and update `_status.md`.

---

## Unavoidable manual clipping (Apple only)

Apple does not publish documentation source anywhere public. No combination of
allowlist changes will help — only a real browser session reaches these pages
reliably. The operator must clip manually.

### ~~apple-eventkit~~ — RESOLVED

**FILLED 2026-04-22** via Cowork Chrome clip — see `apple-eventkit/` section. All 7 pages present. Three Apple slug renames noted in file frontmatter.

~~Original gap:~~

Prompt 11 (Apple Reminders bidirectional adapter) depends on these. Clip before
running prompt 11, or the adapter implementation will be under-specified.

| # | URL | Target file | Priority |
|---|-----|-------------|----------|
| 1 | https://developer.apple.com/documentation/eventkit | `docs/reference/apple-eventkit/overview.md` | HIGH |
| 2 | https://developer.apple.com/documentation/eventkit/ekreminder | `docs/reference/apple-eventkit/ekreminder.md` | HIGH |
| 3 | https://developer.apple.com/documentation/eventkit/ekeventstore | `docs/reference/apple-eventkit/ekeventstore.md` | HIGH |
| 4 | https://developer.apple.com/documentation/eventkit/accessing-the-event-store | `docs/reference/apple-eventkit/access.md` | HIGH |
| 5 | https://developer.apple.com/documentation/eventkit/creating-reminders-and-alarms | `docs/reference/apple-eventkit/create.md` | MEDIUM |
| 6 | https://developer.apple.com/documentation/eventkit/fetching-events-and-reminders | `docs/reference/apple-eventkit/fetch.md` | MEDIUM |
| 7 | https://developer.apple.com/documentation/eventkit/responding-to-calendar-database-changes | `docs/reference/apple-eventkit/changes.md` | MEDIUM |

Estimated effort: **~15 minutes** for all 7 pages with a Markdown Web Clipper
browser extension.

### apple-shortcuts — LOW priority

Orientation-only. Prompt 13b documents the webhook shape; operator configures
Shortcuts at Phase B time. Optional.

| URL | Target file | Priority |
|-----|-------------|----------|
| https://support.apple.com/guide/shortcuts/welcome/ios | `docs/reference/apple-shortcuts/guide-overview.md` | LOW |
| https://support.apple.com/guide/shortcuts/intro-to-shortcut-actions-apd07c25bb38/ios | `docs/reference/apple-shortcuts/actions.md` | LOW |

### Manual clipping procedure

1. Open each URL in Chrome with your normal session.
2. Copy the main content (skip navigation, footer, "was this helpful?" widget).
3. Paste into the target file with this header:

   ```markdown
   ---
   **Source:** <URL>

   **Fetched:** <today's date>

   **License:** Apple developer documentation — reference only; not redistributed

   **Method:** Manual clip (Apple docs not published on GitHub)
   ---
   ```

4. Commit:
   ```bash
   git add docs/reference/apple-eventkit/
   git commit -m "docs: manual clip of Apple EventKit reference"
   ```

Optional: use a Chrome extension like "Markdown Web Clipper" for cleaner output.

---

## ~~Plaid narrative gaps~~ — RESOLVED

**FILLED 2026-04-22** via Cowork Chrome clip — see `plaid/link-update-mode.md`, `plaid/errors-taxonomy.md`, `plaid/institutions-api.md`. Plaid section now fully mirrored.

~~Original gap:~~

The Plaid OpenAPI spec (`docs/reference/plaid/openapi.yaml`) covers every
endpoint, request/response shape, product coverage, error code, and webhook
event — that's ~90% of what the build needs. The remaining ~10% is narrative
UX guidance that lives only at https://plaid.com/docs/:

| URL | Purpose | Priority |
|-----|---------|----------|
| https://plaid.com/docs/link/update-mode/ | Re-auth flow after `ITEM_LOGIN_REQUIRED` | LOW |
| https://plaid.com/docs/errors/ | Error taxonomy narrative | LOW |
| https://plaid.com/docs/api/institutions/#institutionsget_by_idstatus | How to interpret institution status JSON | LOW |

If `plaid.com` is allowlisted, Claude Code can fetch these. Otherwise manual
clipping is optional — the build can proceed; prompt 11 (adapter) can consult
the OpenAPI spec for every specific.

---

## ~~Tailscale~~ — RESOLVED

**FILLED 2026-04-22** via Cowork Chrome clip — see `tailscale/` section. All 6 pages present. Note: Tailscale migrated from `/kb/<id>/<slug>` URLs to `/docs/features/<slug>`; mirror reflects the new canonical paths.

~~Original gap:~~

See `tailscale/_index.md` for the six specific KB pages. The most relevant one
(identity headers) has its contract duplicated into `ADMINISTRATEME_CONSOLE_PATTERNS.md`
§2, which is authoritative for the build. Phase A is not blocked.

---

## RFCs (protocols) — LOW priority

| RFC | Subject | Host | Alternative |
|-----|---------|------|-------------|
| 4791 | CalDAV | rfc-editor.org | `docs/reference/caldav/` (python-caldav docs cover practical usage) |
| 5545 | iCalendar | rfc-editor.org | `icalendar` + `vobject` Python libraries handle parsing/emitting |

No Phase A code decision depends on reading the RFCs directly. Documented here
for completeness.

---

## How to update this file

When a gap is filled (operator clips docs or allowlist is widened and files are
re-fetched), strike through the row in the relevant section and add a line:

> ~~[original row]~~ **FILLED on <date>** — see `docs/reference/<section>/<file>`

Also update `_status.md` to move that section from ✗ to ✓ (or △).
