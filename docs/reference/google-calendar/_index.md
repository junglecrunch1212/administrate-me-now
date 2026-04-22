# Google Calendar API documentation

**Purpose in this build:** The Google Calendar adapter (standalone Python) uses the Calendar v3 REST API for the calendar projection and for outbound (reminder → calendar event) paths.

**Source:** https://github.com/googleapis/google-api-nodejs-client/blob/main/src/apis/calendar/v3.ts
**Fetched:** 2026-04-22
**License:** Apache-2.0 (googleapis/google-api-nodejs-client/LICENSE)

## Files mirrored

- `calendar-v3.ts` — Full TypeScript definition of every Calendar v3 endpoint (~8.5 K lines) with JSDoc on every method.
- `calendar-README.md` — Top-level per-API README.

## How to use for build questions

- Event shape: search for `interface Schema$Event` in `calendar-v3.ts`.
- "How do I list changes since a sync token?" → search for `syncToken` and read `events.list`.
- "What does recurrence look like?" → search for `recurrence` / `RRULE`.

## Python mapping

TS `calendar.events.list` ↔ Python `service.events().list(...)`. Same pattern as Gmail.

## Known gaps

None for API-truth. Narrative recurrence (RFC 5545) handling is documented via RFC text, not in this mirror; see the caldav section's reference to iCalendar handling.
