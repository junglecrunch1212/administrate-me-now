# Apple EventKit documentation

**Purpose in this build:** AdministrateMe's Apple Reminders bidirectional adapter (BUILD.md "APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC", prompt 11) uses EventKit to interact with the Reminders database on the Mac Mini. This mirror covers `EKEventStore`, `EKReminder`, authorization flow, create/update, fetch-by-predicate, and change notifications.

**Source:** https://developer.apple.com/documentation/eventkit/ (clipped manually via Claude-in-Chrome; see `docs/reference/_manifests/2026-04-22-cowork-clips.md`).

**Fetched:** 2026-04-22

**License:** Apple developer documentation — reference only; not for redistribution.

**Method:** Manual Chrome clip via Claude Cowork (Apple does not publish doc source publicly; developer.apple.com is not on the sandbox egress allowlist).

## Files mirrored

- `overview.md` — EventKit framework overview with full Topics list (Essentials, Events and reminders, Calendars, Recurrence, Alarms, Common objects, Virtual conferences, Errors).
- `ekeventstore.md` — `EKEventStore` class reference: declaration, overview, topics (authorization, events, reminders, calendars, sources, notifications, predicates), and relationships.
- `ekreminder.md` — `EKReminder` class reference: declaration, overview, topics (priority, completion, due date, start date, alarms), and relationships (inherits from `EKCalendarItem`).
- `access.md` — Accessing the event store: permission flow for full vs. write-only access, required `Info.plist` usage-description keys, transition from iOS 16 legacy behavior.
- `create.md` — Creating events and reminders. (Apple renamed this article's slug from `creating-reminders-and-alarms` to `creating-events-and-reminders`; redirect noted in file frontmatter.)
- `fetch.md` — Retrieving events and reminders. (Slug renamed from `fetching-events-and-reminders` to `retrieving-events-and-reminders`.)
- `changes.md` — Updating with notifications. (Slug renamed from `responding-to-calendar-database-changes` to `updating-with-notifications`.)

## How to use for build questions

- "How do I request reminders access?" → `access.md` + `ekeventstore.md` authorization section.
- "What fields does `EKReminder` expose?" → `ekreminder.md` Topics section.
- "How do I observe changes made outside my app?" → `changes.md`.
- "What's the difference between write-only and full access?" → `access.md`.

## Downstream impact (cleared gap)

Resolves the HIGH-priority gap previously documented in `../_gaps.md`. Prompt 11 (Apple Reminders bidirectional adapter) can now proceed with complete EventKit context.

## Refresh

Yearly cadence via manual clip. Apple renames pages occasionally; if a URL returns 404 during refresh, check the canonical location via search at developer.apple.com.
