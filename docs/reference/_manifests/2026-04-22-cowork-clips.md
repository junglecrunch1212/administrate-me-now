<!--
Record of an operator-run documentation-clip pass using Claude Cowork +
Claude-in-Chrome on 2026-04-22, to close gaps in docs/reference/apple-eventkit,
tailscale, and plaid that were documented by prompt 00.5. Clipped content
lives under the respective section directories.
-->

---
generated: 2026-04-22
total_files: 16
---

# Documentation clip manifest

A local markdown mirror of 16 source documentation pages across three vendors (Apple EventKit, Tailscale, Plaid). Each file has a YAML frontmatter block recording the canonical `source_url`, the `fetched` date, and the `page_title`. Where a documentation page has been moved, renamed, or retired since the URL was first requested, the file opens with a `> **Note:** ...` block describing the redirect so downstream readers can trace the provenance.

## Files

### apple-eventkit/

| File | Description |
| --- | --- |
| `apple-eventkit/overview.md` | EventKit framework overview page — the root of Apple's EventKit reference, including the full Topics list (Essentials, Events and reminders, Calendars, Recurrence, Alarms, Common objects, Virtual conferences, Errors). |
| `apple-eventkit/ekeventstore.md` | `EKEventStore` class reference — the central entry point for EventKit access. Declaration, Overview, Topics (authorization, events, reminders, calendars, sources, notifications, predicates), and Relationships. |
| `apple-eventkit/ekreminder.md` | `EKReminder` class reference — Declaration, Overview (describes reminders as calendar items that capture a to-do), Topics (priority, completion, due date, start date, alarms), and Relationships (inherits from `EKCalendarItem`). |
| `apple-eventkit/access.md` | "Accessing the event store" article — describes the permission flow for full vs. write-only access, required `Info.plist` usage-description keys, and the transition away from iOS 16 "legacy" permission behavior. |
| `apple-eventkit/create.md` | "Creating events and reminders" article — patterns for constructing `EKEvent`/`EKReminder` instances, setting calendar/title/dates, and committing with `save(_:span:)`. See Note in frontmatter: Apple redirected the originally-requested slug `creating-reminders-and-alarms` to `creating-events-and-reminders`. |
| `apple-eventkit/fetch.md` | "Retrieving events and reminders" article — predicate construction via `predicateForEvents(withStart:end:calendars:)`, async reminders fetch, and pagination considerations. See Note in frontmatter: Apple redirected the originally-requested slug `fetching-events-and-reminders` to `retrieving-events-and-reminders`. |
| `apple-eventkit/changes.md` | "Updating with notifications" article — how to observe `EKEventStoreChanged` notifications, reset caches, and re-fetch. See Note in frontmatter: Apple redirected the originally-requested slug `responding-to-calendar-database-changes` to `updating-with-notifications`. |

### tailscale/

| File | Description |
| --- | --- |
| `tailscale/serve.md` | Tailscale Serve — proxy traffic to a local service for other devices inside the tailnet, with HTTPS certificates, identity headers, and app-capabilities header. |
| `tailscale/funnel.md` | Tailscale Funnel — expose a local service publicly over the internet via Funnel relay servers, TCP proxy, and TLS. Requirements, limits, and troubleshooting. |
| `tailscale/acls.md` | Tailnet policy file — top-level HuJSON sections (`acls`, `grants`, `groups`, `hosts`, `ipsets`, `tagOwners`, `autoApprovers`, `nodeAttr`, `postures`, `ssh`, `sshTests`, `tests`) plus network-policy options (`derpMap`, `disableIPv4`, `OneCGNATRoute`, `randomizeClientPort`). |
| `tailscale/exit-nodes.md` | Exit nodes — route all internet traffic through a device, configure advertisement and approval, `--exit-node-allow-lan-access`, destination logging, and expired-key fail-close behavior. |
| `tailscale/webhooks.md` | Webhooks — setting up a webhook endpoint, full events table (device misconfiguration, tailnet management, webhook management), events payload schema, retry policy, and HMAC-SHA256 `Tailscale-Webhook-Signature` verification. |
| `tailscale/identity-headers.md` | Identity headers section extracted from the Tailscale Serve page (`Tailscale-User-Login`, `Tailscale-User-Name`, `Tailscale-User-Profile-Pic`, plus `Tailscale-App-Capabilities`). See notes in frontmatter: the standalone KB article has been retired; identity-headers content now lives only as a section inside the Serve page. |

### plaid/

| File | Description |
| --- | --- |
| `plaid/link-update-mode.md` | Link update mode — re-authenticating an Item after `ITEM_LOGIN_REQUIRED`, `PENDING_EXPIRATION`, or consent expiry; creating a `link_token` with `access_token` in update mode and handling partner (OAuth) flows. |
| `plaid/errors-taxonomy.md` | Errors reference — most-common-errors table, full category breakdown (Item, Institution, API, Assets, Payment, Virtual Account, Transactions, Transfer, Signal, Income, Sandbox, Invalid Request, Invalid Input, Invalid Result, Rate Limit, ReCAPTCHA, OAuth, Micro-deposits, Partner, Check Report, User) with every `error_code` listed, plus the full error-object schema (`error_type`, `error_code`, `error_code_reason`, `error_message`, `display_message`, `request_id`, `causes`, `status`, `documentation_url`, `suggested_action`, `required_account_subtypes`, `provided_account_subtypes`). |
| `plaid/institutions-api.md` | Institutions API — `/institutions/get`, `/institutions/get_by_id`, `/institutions/search`. Request/response examples, options-object parameters, and the full `status` object structure (`item_logins`, `transactions_updates`, `auth`, `identity`, `investments_updates`, `liabilities_updates`, `liabilities`, `investments`) with the `breakdown.success/error_plaid/error_institution/refresh_interval` sub-fields. |

## Notes on redirects, renames, and retirements

Several source URLs have moved since the list was first compiled. Every renamed/retired page carries an in-file `> **Note:** ...` block that records both the original URL and the canonical URL.

- **Apple — three slug renames.** Apple's developer docs renamed three EventKit article slugs; the old URLs now 404. The renames are: `creating-reminders-and-alarms` → `creating-events-and-reminders` (saved as `apple-eventkit/create.md`); `fetching-events-and-reminders` → `retrieving-events-and-reminders` (saved as `apple-eventkit/fetch.md`); and `responding-to-calendar-database-changes` → `updating-with-notifications` (saved as `apple-eventkit/changes.md`). The filenames match the requested list; the frontmatter of each file records the canonical URL.
- **Tailscale — KB-to-Docs migration.** All six originally-requested `/kb/<id>/<slug>` URLs now redirect into the `/docs/features/*` tree. The canonical URLs used are: `/docs/features/tailscale-serve`, `/docs/features/tailscale-funnel`, `/docs/features/tailnet-policy-file`, `/docs/features/exit-nodes`, `/docs/features/webhooks`, and `/docs/features/tailscale-serve#identity-headers`.
- **Tailscale — one page retired.** The standalone identity-headers KB article has been retired. Its content now lives only as the `#identity-headers` section inside the Serve page. `tailscale/identity-headers.md` was clipped from that section; a redirect note in frontmatter makes the provenance explicit. Note that the ACLs KB slug (`/kb/1336/tailnet-policy-file`) currently redirects to an unrelated QR-code page; I routed around that via the canonical `/docs/features/tailnet-policy-file` URL.
- **Plaid — no redirects.** All three Plaid pages resolved at their originally-requested URLs.

## Pages skipped

None. All 16 originally-requested pages have a corresponding clip in this folder, either at the originally-requested URL or — where Apple or Tailscale has moved the canonical location — at the current canonical URL with a redirect note in the file's frontmatter.

## Conventions

- **Frontmatter.** Every file begins with a YAML block containing `source_url` (canonical URL actually fetched), `fetched` (ISO date), and `page_title` (H1).
- **Prose and structure.** H1 is the page title; H2/H3 preserve the source's heading hierarchy. Notes and warnings render as Markdown blockquotes (`> **Note:** …`).
- **Code.** Code blocks use language hints (`swift`, `bash`, `json`, `objectivec`). For Apple symbol pages only the Swift variant is retained; Objective-C tabs are omitted per the original brief.
- **Tables.** Rendered as pipe tables.
- **Images.** Any inline images are linked by URL only — no image files were downloaded.
- **Links.** Internal links are preserved with their canonical URLs; tracking parameters were stripped.
- **Omitted chrome.** Navigation, breadcrumbs, footers, sidebar TOCs, "was this helpful" widgets, cookie banners, newsletter signups, and marketing CTAs are all stripped.
