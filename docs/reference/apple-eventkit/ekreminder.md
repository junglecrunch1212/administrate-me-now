---
source_url: https://developer.apple.com/documentation/eventkit/ekreminder
fetched: 2026-04-22
page_title: EKReminder
---

# EKReminder

Class

A class that represents a reminder in a calendar.

**Availability:** iOS 6.0+ · iPadOS 6.0+ · Mac Catalyst 13.1+ · macOS 10.8+ · visionOS 1.0+ · watchOS 2.0+

## Declaration

```swift
class EKReminder
```

Mentioned in [Creating events and reminders](https://developer.apple.com/documentation/eventkit/creating-reminders-and-alarms).

## Overview

Use the `init(eventStore:)` method to create a new reminder. Use the properties in the class to get and modify certain information about a reminder.

## Topics

### Creating a Reminder

- [`init(eventStore: EKEventStore)`](https://developer.apple.com/documentation/eventkit/ekreminder/init(eventstore:)) — Creates and returns a new reminder in the given event store.

### Accessing Reminder Properties

- [`enum EKReminderPriority`](https://developer.apple.com/documentation/eventkit/ekreminderpriority) — The priority of the reminder.
- [`var priority: Int`](https://developer.apple.com/documentation/eventkit/ekreminder/priority) — The reminder's priority.
- [`var startDateComponents: DateComponents?`](https://developer.apple.com/documentation/eventkit/ekreminder/startdatecomponents) — The start date of the task.
- [`var dueDateComponents: DateComponents?`](https://developer.apple.com/documentation/eventkit/ekreminder/duedatecomponents) — The date by which the reminder should be completed.
- [`var isCompleted: Bool`](https://developer.apple.com/documentation/eventkit/ekreminder/iscompleted) — A Boolean value determining whether or not the reminder is marked completed.
- [`var completionDate: Date?`](https://developer.apple.com/documentation/eventkit/ekreminder/completiondate) — The date on which the reminder was completed.

## Relationships

### Inherits From

- [`EKCalendarItem`](https://developer.apple.com/documentation/eventkit/ekcalendaritem)

### Conforms To

- `CVarArg`
- `CustomDebugStringConvertible`
- `CustomStringConvertible`
- `Equatable`
- `Hashable`
- `NSObjectProtocol`

## See Also

### Events and reminders

- [Creating events and reminders](https://developer.apple.com/documentation/eventkit/creating-reminders-and-alarms) — Create and modify events and reminders in a person's database.
- [Retrieving events and reminders](https://developer.apple.com/documentation/eventkit/fetching-events-and-reminders) — Fetch events and reminders from the Calendar database.
- [Updating with notifications](https://developer.apple.com/documentation/eventkit/responding-to-calendar-database-changes) — Register for notifications about changes and keep your app up to date.
- [Managing location-based reminders](https://developer.apple.com/documentation/eventkit/managing-location-based-reminders) — Access reminders set up with geofence-enabled alarms on a person's calendars.
- [`class EKEvent`](https://developer.apple.com/documentation/eventkit/ekevent) — A class that represents an event in a calendar.
