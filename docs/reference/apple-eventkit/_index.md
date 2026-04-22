# Apple EventKit documentation — GAP

**Status:** NOT MIRRORED. Apple does not publish documentation source publicly, and `developer.apple.com` is not on the sandbox egress allowlist.

**Purpose in this build:** AdministrateMe's Apple Reminders bidirectional adapter (BUILD.md "APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC", prompt 11) needs EventKit concepts to interact with the Reminders database on the Mac Mini: `EKEventStore`, `EKReminder`, access-permission prompts, list enumeration, and create/update flows. The underlying Phase B implementation uses a Swift or JXA helper; the Python layer calls it via subprocess.

**Priority:** HIGH (prompt 11 depends on knowing the exact EventKit entry points).

## Resolution: manual Chrome clip by the operator

Apple's EventKit docs are only available at `developer.apple.com` and require a browser with a real UA. The operator clips these pages manually into this directory:

| # | URL | Target file | Priority |
|---|-----|-------------|----------|
| 1 | https://developer.apple.com/documentation/eventkit | `overview.md` | HIGH |
| 2 | https://developer.apple.com/documentation/eventkit/ekreminder | `ekreminder.md` | HIGH |
| 3 | https://developer.apple.com/documentation/eventkit/ekeventstore | `ekeventstore.md` | HIGH |
| 4 | https://developer.apple.com/documentation/eventkit/accessing-the-event-store | `access.md` | HIGH |
| 5 | https://developer.apple.com/documentation/eventkit/creating-reminders-and-alarms | `create.md` | MEDIUM |
| 6 | https://developer.apple.com/documentation/eventkit/fetching-events-and-reminders | `fetch.md` | MEDIUM |
| 7 | https://developer.apple.com/documentation/eventkit/responding-to-calendar-database-changes | `changes.md` | MEDIUM |

### Manual clipping procedure

1. Open each URL in Chrome with your normal session.
2. Copy the main article content (skip navigation, footer, "was this helpful?" widget).
3. Save as `<target-file>.md` in this directory with this header:

   ```
   ---
   **Source:** <URL>

   **Fetched:** <today's date>

   **License:** Apple developer documentation (permitted for reference; not redistributed here verbatim for external audiences)

   **Method:** Manual clip in Chrome (Apple docs not published on GitHub)
   ---
   ```

4. Commit the clipped files:
   ```bash
   git add docs/reference/apple-eventkit/
   git commit -m "docs: manual clip of Apple EventKit reference"
   ```

Estimated effort: ~15 minutes for all 7 pages.

## Alternative (informational only)

The open-source Python package `pyobjc-framework-EventKit` has docstrings that mirror the Apple headers, but those docstrings are machine-translated from the Swift headers and lose context. They can be consulted as a fallback but are not a substitute for the official pages.

## Downstream impact

Until this is resolved, prompt 11's Apple Reminders adapter skeleton can be written against the contract described in `ADMINISTRATEME_BUILD.md` "APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC", but the exact Swift/JXA helper implementation waits for this gap to be filled.
