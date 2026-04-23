# Calendaring adapters (L1)

External calendar translators: Google Calendar, Microsoft Graph Calendar,
iCloud CalDAV. Each emits `calendar.*` events on ingest.

Per SYSTEM_INVARIANTS.md §5: calendar flow is external → internal; adapters
do NOT write back to external calendars unless configured for bidirectional
sync.

Filled in by prompt 11 and provider-specific prompts.
