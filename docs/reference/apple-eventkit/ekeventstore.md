---
source_url: https://developer.apple.com/documentation/eventkit/ekeventstore
fetched: 2026-04-22
page_title: EKEventStore
---

# EKEventStore

Class

An object that accesses a person's calendar events and reminders and supports the scheduling of new events.

**Availability:** iOS 4.0+ · iPadOS 4.0+ · Mac Catalyst 13.1+ · macOS 10.8+ · visionOS 1.0+ · watchOS 2.0+

## Declaration

```swift
class EKEventStore
```

Mentioned in [Retrieving events and reminders](https://developer.apple.com/documentation/eventkit/fetching-events-and-reminders), [Accessing the event store](https://developer.apple.com/documentation/eventkit/accessing-the-event-store), [Creating events and reminders](https://developer.apple.com/documentation/eventkit/creating-reminders-and-alarms).

## Overview

The `EKEventStore` class is an app's point of contact for accessing calendar and reminder data.

After initializing the event store, you must request access to events or reminders before attempting to fetch or create data. To request access to reminders, call `requestFullAccessToReminders(completion:)`. To request access to events, call `requestWriteOnlyAccessToEvents(completion:)` or `requestFullAccessToEvents(completion:)`.

> **Important:** To request access to events and reminders, your app needs to include permission strings in its `Info.plist` file that explain to someone why the app needs access. For more information, see [Accessing the event store](https://developer.apple.com/documentation/eventkit/accessing-the-event-store).

A typical workflow for using an event store is:

1. Create a predicate, or a search query for events, with `predicateForEvents(withStart:end:calendars:)`.
2. Fetch and process events that match the predicate with the `events(matching:)` and `enumerateEvents(matching:using:)` methods.
3. Save and delete events from the event store with the `save(_:span:commit:)` and `remove(_:span:commit:)` methods.

Use similar methods to access and manipulate reminders.

After receiving an object from an event store, don't use that object with a different event store. This restriction applies to `EKObject` subclasses such as `EKEvent`, `EKReminder`, `EKCalendar`, and `EKSource`, as well as predicates that the event store creates. For example, don't fetch an event from one event store, modify the event, and then pass it to `save(_:span:)` in a different store.

## Topics

### Creating event stores

- [`init()`](https://developer.apple.com/documentation/eventkit/ekeventstore/init()) — Creates a new event store.
- [`init(sources: [EKSource])`](https://developer.apple.com/documentation/eventkit/ekeventstore/init(sources:)) — Creates an event store that contains data for the specified sources.
- [`var eventStoreIdentifier: String`](https://developer.apple.com/documentation/eventkit/ekeventstore/eventstoreidentifier) — The unique identifier for the event store.

### Requesting access to events and reminders

- [`func requestWriteOnlyAccessToEvents(completion: (Bool, (any Error)?) -> Void)`](https://developer.apple.com/documentation/eventkit/ekeventstore/requestwriteonlyaccesstoevents(completion:)) — Prompts the person using your app to grant or deny write access to event data.
- [`func requestFullAccessToEvents(completion: (Bool, (any Error)?) -> Void)`](https://developer.apple.com/documentation/eventkit/ekeventstore/requestfullaccesstoevents(completion:)) — Prompts people to grant or deny read and write access to event data.
- [`func requestFullAccessToReminders(completion: (Bool, (any Error)?) -> Void)`](https://developer.apple.com/documentation/eventkit/ekeventstore/requestfullaccesstoreminders(completion:)) — Prompts people to grant or deny read and write access to reminders.
- [`class func authorizationStatus(for: EKEntityType) -> EKAuthorizationStatus`](https://developer.apple.com/documentation/eventkit/ekeventstore/authorizationstatus(for:)) — Determines the authorization status for the given entity type.
- [`enum EKAuthorizationStatus`](https://developer.apple.com/documentation/eventkit/ekauthorizationstatus) — The current authorization status for a specific entity type.
- [`typealias EKEventStoreRequestAccessCompletionHandler`](https://developer.apple.com/documentation/eventkit/ekeventstorerequestaccesscompletionhandler) — The signature for a closure that EventKit calls when requesting access to event and reminder data.
- [`NSCalendarsFullAccessUsageDescription`](https://developer.apple.com/documentation/bundleresources/information_property_list/nscalendarsfullaccessusagedescription) — A message that tells people why the app is requesting access to read and write their calendar data.
- [`NSCalendarsWriteOnlyAccessUsageDescription`](https://developer.apple.com/documentation/bundleresources/information_property_list/nscalendarswriteonlyaccessusagedescription) — A message that tells people why the app is requesting access to create calendar events.
- [`NSRemindersFullAccessUsageDescription`](https://developer.apple.com/documentation/bundleresources/information_property_list/nsremindersfullaccessusagedescription) — A message that tells people why the app is requesting access to read and write their reminders data.

### Accessing account sources

- [`var sources: [EKSource]`](https://developer.apple.com/documentation/eventkit/ekeventstore/sources) — An unordered array of objects that represent accounts that contain calendars.
- [`var delegateSources: [EKSource]`](https://developer.apple.com/documentation/eventkit/ekeventstore/delegatesources) — The event sources delegated to the person using your app.
- [`func source(withIdentifier: String) -> EKSource?`](https://developer.apple.com/documentation/eventkit/ekeventstore/source(withidentifier:)) — Locates an event source with the specified identifier.

### Saving and restoring state

- [`func commit() throws`](https://developer.apple.com/documentation/eventkit/ekeventstore/commit()) — Commits all unsaved changes to the event store.
- [`func reset()`](https://developer.apple.com/documentation/eventkit/ekeventstore/reset()) — Reverts the event store to its saved state.
- [`func refreshSourcesIfNecessary()`](https://developer.apple.com/documentation/eventkit/ekeventstore/refreshsourcesifnecessary()) — Pulls new data from remote sources, if necessary.

### Accessing calendars

- [`var defaultCalendarForNewEvents: EKCalendar?`](https://developer.apple.com/documentation/eventkit/ekeventstore/defaultcalendarfornewevents) — The calendar that events are added to by default, as specified by user settings.
- [`func defaultCalendarForNewReminders() -> EKCalendar?`](https://developer.apple.com/documentation/eventkit/ekeventstore/defaultcalendarfornewreminders()) — Identifies the default calendar for adding reminders to, as specified by user settings.
- [`func calendars(for: EKEntityType) -> [EKCalendar]`](https://developer.apple.com/documentation/eventkit/ekeventstore/calendars(for:)) — Identifies the calendars that support a given entity type, such as reminders or events.
- [`func calendar(withIdentifier: String) -> EKCalendar?`](https://developer.apple.com/documentation/eventkit/ekeventstore/calendar(withidentifier:)) — Locates a calendar with the specified identifier.
- [`func saveCalendar(EKCalendar, commit: Bool) throws`](https://developer.apple.com/documentation/eventkit/ekeventstore/savecalendar(_:commit:)) — Saves a calendar to the event store by either committing or batching the changes.
- [`func removeCalendar(EKCalendar, commit: Bool) throws`](https://developer.apple.com/documentation/eventkit/ekeventstore/removecalendar(_:commit:)) — Removes a calendar from the event store by either committing or batching the changes.
- [`var calendars: [EKCalendar]`](https://developer.apple.com/documentation/eventkit/ekeventstore/calendars) — The calendars associated with the event store. (Deprecated)

### Accessing calendar events

- [`func event(withIdentifier: String) -> EKEvent?`](https://developer.apple.com/documentation/eventkit/ekeventstore/event(withidentifier:)) — Locates the first occurrence of an event with a given identifier.
- [`func calendarItem(withIdentifier: String) -> EKCalendarItem?`](https://developer.apple.com/documentation/eventkit/ekeventstore/calendaritem(withidentifier:)) — Locates a reminder or the first occurrence of an event with the specified identifier.
- [`func calendarItems(withExternalIdentifier: String) -> [EKCalendarItem]`](https://developer.apple.com/documentation/eventkit/ekeventstore/calendaritems(withexternalidentifier:)) — Locates all reminders or the first occurrences of all events with the specified external identifier.
- [`func remove(EKEvent, span: EKSpan) throws`](https://developer.apple.com/documentation/eventkit/ekeventstore/remove(_:span:)) — Removes an event from the event store.
- [`func remove(EKEvent, span: EKSpan, commit: Bool) throws`](https://developer.apple.com/documentation/eventkit/ekeventstore/remove(_:span:commit:)) — Removes an event or recurring events from the event store by either committing or batching the changes.
- [`func remove(EKReminder, commit: Bool) throws`](https://developer.apple.com/documentation/eventkit/ekeventstore/remove(_:commit:)) — Removes a reminder from the event store by either committing or batching the changes.
- [`func save(EKEvent, span: EKSpan) throws`](https://developer.apple.com/documentation/eventkit/ekeventstore/save(_:span:)) — Saves changes to an event permanently.
- [`func save(EKEvent, span: EKSpan, commit: Bool) throws`](https://developer.apple.com/documentation/eventkit/ekeventstore/save(_:span:commit:)) — Saves an event or recurring events to the event store by either committing or batching the changes.
- [`func save(EKReminder, commit: Bool) throws`](https://developer.apple.com/documentation/eventkit/ekeventstore/save(_:commit:)) — Saves changes to a reminder by either committing or batching the changes.

### Searching calendars

- [`func enumerateEvents(matching: NSPredicate, using: EKEventSearchCallback)`](https://developer.apple.com/documentation/eventkit/ekeventstore/enumerateevents(matching:using:)) — Finds all events that match a given predicate and calls a given callback for each event found.
- [`func events(matching: NSPredicate) -> [EKEvent]`](https://developer.apple.com/documentation/eventkit/ekeventstore/events(matching:)) — Finds all events that match a given predicate.
- [`func fetchReminders(matching: NSPredicate, completion: ([EKReminder]?) -> Void) -> Any`](https://developer.apple.com/documentation/eventkit/ekeventstore/fetchreminders(matching:completion:)) — Fetches reminders that match a given predicate.
- [`func cancelFetchRequest(Any)`](https://developer.apple.com/documentation/eventkit/ekeventstore/cancelfetchrequest(_:)) — Cancels the request to fetch reminders.
- [`func predicateForEvents(withStart: Date, end: Date, calendars: [EKCalendar]?) -> NSPredicate`](https://developer.apple.com/documentation/eventkit/ekeventstore/predicateforevents(withstart:end:calendars:)) — Creates a predicate to identify events that occur within a given date range.
- [`func predicateForReminders(in: [EKCalendar]?) -> NSPredicate`](https://developer.apple.com/documentation/eventkit/ekeventstore/predicateforreminders(in:)) — Creates a predicate to identify all reminders in a collection of calendars.
- [`func predicateForCompletedReminders(withCompletionDateStarting: Date?, ending: Date?, calendars: [EKCalendar]?) -> NSPredicate`](https://developer.apple.com/documentation/eventkit/ekeventstore/predicateforcompletedreminders(withcompletiondatestarting:ending:calendars:)) — Creates a predicate to identify all completed reminders that occur within a given date range.
- [`func predicateForIncompleteReminders(withDueDateStarting: Date?, ending: Date?, calendars: [EKCalendar]?) -> NSPredicate`](https://developer.apple.com/documentation/eventkit/ekeventstore/predicateforincompletereminders(withduedatestarting:ending:calendars:)) — Creates a predicate to identify all incomplete reminders that occur within a given date range.
- [`typealias EKEventSearchCallback`](https://developer.apple.com/documentation/eventkit/ekeventsearchcallback) — The signature for a closure that operates on events when enumerating them.

### Deprecated methods

- [`func requestAccess(to: EKEntityType, completion: (Bool, (any Error)?) -> Void)`](https://developer.apple.com/documentation/eventkit/ekeventstore/requestaccess(to:completion:)) — Prompts the person using your app to grant or deny access to event or reminder data. (Deprecated)

### Structures

- [`struct EventStoreChanged`](https://developer.apple.com/documentation/eventkit/ekeventstore/eventstorechanged) — A notification posted when changes are made to the Calendar or Reminders database.

## Relationships

### Inherits From

- `NSObject`

### Conforms To

- `CVarArg`
- `CustomDebugStringConvertible`
- `CustomStringConvertible`
- `Equatable`
- `Hashable`
- `NSObjectProtocol`

## See Also

### Essentials

- [Accessing the event store](https://developer.apple.com/documentation/eventkit/accessing-the-event-store) — Request access to a person's calendar data through the event store.
- [Accessing Calendar using EventKit and EventKitUI](https://developer.apple.com/documentation/eventkit/accessing-calendar-using-eventkit-and-eventkitui) — Choose and implement the appropriate Calendar access level in your app.
