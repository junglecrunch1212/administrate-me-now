# CalDAV (python-caldav) documentation

**Purpose in this build:** The CalDAV adapter ingests calendar events from any RFC 4791 server (Apple iCloud Calendar, Nextcloud, Fastmail, Radicale). Used for non-Google calendars; Google Calendar uses the native Google API adapter.

**Source:** https://github.com/python-caldav/caldav
**Fetched:** 2026-04-22
**License:** Apache-2.0 OR GPL-3.0 (dual-licensed)

## Files mirrored

- `python-caldav-README.md` — Library overview, install, quick examples.
- `CHANGELOG.md` — Version history.
- `docs/index.rst` — Documentation landing.
- `docs/tutorial.rst`, `docs/async_tutorial.rst`, `docs/async.rst` — Practical usage (this is what the adapter calls).
- `docs/howtos.rst`, `docs/examples.rst` — Cookbook.
- `docs/reference.rst` — API reference for `DAVClient`, `Principal`, `Calendar`, `Event`, `Todo`.
- `docs/performance.rst`, `docs/http-libraries.rst` — Performance tuning and HTTP-client configuration.
- `docs/configfile.rst` — Config file format (the adapter uses similar patterns).
- `docs/jmap.rst`, `docs/v3-migration.rst` — Advanced topics.
- `docs/about.rst`, `docs/contact.rst` — Meta.

## Gap: RFC 4791 itself

The CalDAV protocol RFC lives at https://www.rfc-editor.org/rfc/rfc4791 — `rfc-editor.org` is not on the sandbox allowlist. Most practical protocol questions are answered by python-caldav docs and iCalendar handling in the Google Calendar section. If lower-level protocol details are needed during the build, see `../_gaps.md` for the remediation (allowlist widening is trivial; rfc-editor serves plain text).

## Known gaps

- RFC 4791 (documented above).
- RFC 5545 (iCalendar format): same situation; widely referenced. Python libraries (`icalendar`, `vobject`) handle parsing/emitting, so build rarely needs the RFC directly.
