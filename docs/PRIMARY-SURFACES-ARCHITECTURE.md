# AdministrateMe and its primary personal surfaces

## Corrected product definition

AdministrateMe is a local-first, event-sourced personal operations system. Its
append-only event log is the system of record for what the platform observed,
inferred, proposed, approved, attempted, and completed. It is not merely an
event-log viewer: typed adapters, deterministic projections, reactive
pipelines, governance, member-specific context, and action surfaces turn that
history into a continuously usable answer to “what matters now?”

The platform is designed as executive-function infrastructure. It externalizes
working memory, keeps commitments and tasks from disappearing between tools,
reduces repeated prioritization decisions, and records completion evidence.
It does not attempt to automate a member out of the loop. Uncertain extraction,
identity resolution, enrichment, and consequential actions are staged for
review; external side effects pass through governance and observation mode.

The core flow is:

```text
provider state / messages / files / human input
                 |
          source adapters
                 |
       typed immutable events
                 |
        append-only event log
          /              \
 deterministic       reactive pipelines
 projections       (derive more typed events)
          \              /
        member context + governance
                 |
       portal and native surfaces
                 |
     governed provider write commands
                 |
     result / conflict / completion events
```

## What the platform knows

The event taxonomy preserves both generic operational concepts and provider
specific fidelity. Important families include communications, parties and
identities, commitments, tasks, lists and list items, calendar events and
outcomes, artifacts, member-owned knowledge, governance decisions, observation
suppression, adapter health, and external-sync commands/results.

The projections are disposable read models. They can be rebuilt from the log
and may never be written by adapters. The key operational projections include
tasks, commitments, lists, calendars, parties, interactions, member knowledge,
and activity-oriented surfaces. A provider edit is therefore evidence in the
same history as a portal action, not an untraceable overwrite.

## Primary-surface topology

Each Google account is a central adapter instance. Google Calendar is written
through the Google Calendar API even though the same calendar appears in the
iPhone Calendar app. Writing a second EventKit copy would create duplicate
events and split provider identity.

Each Apple identity is a member-bound bridge instance running in the macOS user
session signed into that member's iCloud account. The bridge owns Apple
Calendar, Apple Reminders, and Apple Notes access for exactly that member. Two
members therefore need two enrolled bridge identities. They may be two Macs or
isolated macOS user sessions, but they must not share one daemon process or one
Tailscale identity.

The bridge-to-central path is identity bound. The central server derives the
member from the authenticated Tailscale identity and rejects a conflicting
owner-scope claim. All Apple data is private to its member unless a later,
explicit sharing policy projects it into a household surface.

## Calendar contract

`calendar.event_added@v2` and `calendar.event_updated@v2` are full snapshots,
not sparse guesses. They preserve:

- member, provider account, calendar, native event ID, iCalendar UID, provider
  revision, status, event type, sequence, creation/update timestamps;
- summary, description, location, structured provider data, timed/all-day
  start and end values, time zones, availability, and visibility;
- organizer and attendee identity, role, response, optional/resource state,
  comments, and additional guests;
- alarms, RFC 5545 recurrence lines and recurring-instance identity,
  conferencing, attachments, color, provider links, and source URL;
- an explicit AdministrateMe item link and completion policy.

Google Calendar uses OAuth refresh credentials stored behind an `op://`
reference. It supports initial bounded history, incremental sync tokens,
cancelled tombstones, etags, create/update/delete/move/respond operations,
conference/attachment flags, attendee notifications, and optimistic `If-Match`
writes. Advanced event types are preserved as provider data.

Apple Calendar uses EventKit with a full-events access grant. It supports
event create/update/delete/move, timed and all-day values, time zones, URL,
time alarms, and RFC recurrence conversion. EventKit does not expose attendee
mutation or Google conferencing metadata. Google-backed calendars seen inside
Calendar.app must therefore remain owned by the Google adapter. The Apple
adapter is for iCloud/local calendars.

An elapsed event becomes `calendar.event_outcome_recorded`. Elapsed time is
completion evidence with deliberately moderate confidence; it is not proof of
attendance. A linked task or commitment closes automatically only when its
explicit policy is `on_elapsed` or `on_attended`. Manual is the default.

## Apple Reminders contract

The bridge discovers all reminder lists, including empty lists, and mirrors
their native identifiers, names, colors, and default status. Supported list
writes are create, rename/recolor, and delete.

Each `list_item.*` rich snapshot preserves native account/list/item identity,
title, notes, URL, EventKit priority, completion and completion time, floating
or zoned start/due date components, absolute/relative/location alarms,
enter/leave proximity and radius, RFC recurrence rules, and provider revision.
It supports create, update, delete, complete, uncomplete, and move.

AdministrateMe-created reminders linked to internal work receive an
`adminme://task/<id>` or `adminme://commitment/<id>` URL. Completing or
uncompleting such a reminder on an iPhone updates the linked projection only
when `completion_policy=mirror`; unrelated personal reminders remain list
items and are never silently converted into tasks.

EventKit does not expose every Reminders.app user-interface feature. Tags,
subtasks, sections, templates, assignment, and some smart-list semantics are
nullable/unsupported instead of being falsely synthesized. Priority is the
public EventKit flag/priority representation. These are platform API limits,
not hidden database fields AdministrateMe should bypass.

## Google Tasks contract

The central Google Tasks adapter discovers authorized task lists and mirrors
task/list identity, etag, title, notes, date-only due value, completion and
completion time, nesting, opaque position, source links, hidden/deleted state,
web link, and assignment provenance. It supports task-list CRUD plus task
create/update/delete/complete/uncomplete/move and clear-completed where the
exact resource permits the operation. Polling includes completed, deleted,
hidden, and assigned records so replay can derive transitions and tombstones
instead of presenting an open-only partial truth.

Google explicitly discards the time portion of `due`; AdministrateMe preserves
that as a floating all-day scheduled date. Google Tasks exposes no native
priority, alarm, recurrence, or geofence fields, so those controls are neither
sent nor synthesized. Assigned and recurrent tasks have provider-enforced
write restrictions; assignment metadata is read-only provenance. Google
Tasks list items remain list items and never silently become AdministrateMe
Tasks, Commitments, calendar events, or scheduler jobs.

## Apple Notes contract and hard boundary

Apple publishes no Notes data framework comparable to EventKit. Supported
“full Notes” integration therefore means the maximum supported automation
surface, not private-database feature parity.

The member bridge uses the Notes application's scripting dictionary through
JXA. It observes accounts, folders (including empty folders), note IDs, name,
HTML body, creation/modification dates, locked/shared indicators, and provider
revision. It supports create/update/append/move/delete note and
create/rename/delete folder commands. Empty and locked notes remain visible;
locked content is represented as unavailable rather than discarded.

Attachments, tags, pinning, collaboration membership/permissions, and locked
note contents are not reliably scriptable. The canonical event records these
as capability warnings. AdministrateMe never reads or mutates Notes' private
SQLite/CoreData store because doing so is unsupported, brittle, and unsafe.

## Bidirectional command and conflict model

Portal or pipeline intent becomes `external_sync.write_requested@v1`. The
event contains a stable command ID, member, provider account, operation,
target native ID, expected provider revision, and provider-shaped payload.

Central Google adapters consume Google commands. Member bridges pull only
Apple commands for the member established by the Tailscale connection. Every
attempt ends as `external_sync.write_completed` or
`external_sync.write_failed`. Google etags and Apple last-modified revisions
prevent stale edits. The bridge stores the exact result envelope before upload,
so an interrupted response cannot repeat a create operation.

Observation mode remains the final outbound filter. Google adapters use the
shared outbound guard directly. The bridge command endpoint converts Apple
commands into terminal suppressed results while observation is active; it does
not defer them into a surprise queue.

## Bootstrap behavior

Bridge enrollment packages now enable the three primary Apple packs with rich
schema v2. The initial selectors are the member's default EventKit calendar and
default Reminders list. The first interactive run must grant:

1. full Calendar access;
2. full Reminders access;
3. Automation permission to control Notes.

Bootstrap stays in observation mode while initial scans populate projections.
Before disabling observation, verify provider account/member identity, event
and reminder counts, list/folder discovery, recurrence and geofence payloads,
Google OAuth refresh, bridge command polling, and zero cross-member scope
violations.

## Supported-feature matrix

| Surface | Inbound | Outbound | Native completion evidence | Important limit |
|---|---|---|---|---|
| Google Calendar | Rich events, incremental changes, cancellations | Create/update/delete/move/respond with etag | Cancelled and elapsed outcomes | Use Google API, not duplicate EventKit writes |
| iCloud Apple Calendar | Rich EventKit events | Create/update/delete/move, alarms, recurrence | Elapsed outcomes | No attendee mutation/conference parity |
| Apple Reminders | Lists and full public EKReminder state | List and reminder CRUD, completion, dates, recurrence, geofences | Complete/uncomplete linked work | UI-only tags/subtasks/sections are not public EventKit data |
| Google Tasks | Task lists; tasks including date, completion, nesting, position, links, assignment provenance, tombstones | List CRUD; task CRUD, completion, move, clear completed with etag | Provider completion/uncompletion transitions | Due is date-only; no priority, alarm, recurrence, or geofence API |
| Apple Notes | Accounts, folders, HTML notes, dates, lock/share observations | Supported note/folder scripting CRUD | Note edits are activity evidence, not task completion | No public Notes framework; several features are read/write inaccessible |

## Official platform references

- [Apple EventKit](https://developer.apple.com/documentation/eventkit)
- [Apple: creating events and reminders](https://developer.apple.com/documentation/eventkit/creating-events-and-reminders)
- [Apple: creating a recurring event](https://developer.apple.com/documentation/eventkit/creating-a-recurring-event)
- [Apple EKReminder](https://developer.apple.com/documentation/eventkit/ekreminder)
- [Apple EKStructuredLocation](https://developer.apple.com/documentation/eventkit/ekstructuredlocation)
- [Google Tasks Task resource](https://developers.google.com/workspace/tasks/reference/rest/v1/tasks)
- [Google Tasks Tasklists resource](https://developers.google.com/workspace/tasks/reference/rest/v1/tasklists)
- [Google Calendar Events resource](https://developers.google.com/workspace/calendar/api/v3/reference/events)
- [Google Calendar incremental synchronization](https://developers.google.com/workspace/calendar/api/guides/sync)
- [Google Calendar resource versions and etags](https://developers.google.com/calendar/api/guides/version-resources)
- [Apple Notes user guide](https://support.apple.com/guide/notes/welcome/mac)
- [Apple automation scripting guide](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/)
