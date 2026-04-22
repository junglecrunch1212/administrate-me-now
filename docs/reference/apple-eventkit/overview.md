---
source_url: https://developer.apple.com/documentation/eventkit
fetched: 2026-04-22
page_title: EventKit
---

# EventKit

Framework

Create, view, and edit calendar and reminder events.

**Availability:** iOS 4.0+ · iPadOS 4.0+ · Mac Catalyst 13.1+ · macOS 10.8+ · visionOS 1.0+ · watchOS 2.0+

## Overview

The EventKit framework provides access to calendar and reminders data so people can create, retrieve, and edit calendar items in your app. In iOS, EventKit UI provides user interfaces you can implement in your app so people can create and edit calendar items.

You can use EventKit to set up alarms and create recurring events. And if a change to the Calendar database occurs from outside your app, EventKit detects the change and sends a notification, allowing your app to stay up to date.

## Topics

### Essentials

- [Accessing the event store](https://developer.apple.com/documentation/eventkit/accessing-the-event-store) — Request access to a person's calendar data through the event store.
- [`class EKEventStore`](https://developer.apple.com/documentation/eventkit/ekeventstore) — An object that accesses a person's calendar events and reminders and supports the scheduling of new events.
- [Accessing Calendar using EventKit and EventKitUI](https://developer.apple.com/documentation/eventkit/accessing-calendar-using-eventkit-and-eventkitui) — Choose and implement the appropriate Calendar access level in your app.

### Events and reminders

- [Creating events and reminders](https://developer.apple.com/documentation/eventkit/creating-reminders-and-alarms) — Create and modify events and reminders in a person's database.
- [Retrieving events and reminders](https://developer.apple.com/documentation/eventkit/fetching-events-and-reminders) — Fetch events and reminders from the Calendar database.
- [Updating with notifications](https://developer.apple.com/documentation/eventkit/responding-to-calendar-database-changes) — Register for notifications about changes and keep your app up to date.
- [Managing location-based reminders](https://developer.apple.com/documentation/eventkit/managing-location-based-reminders) — Access reminders set up with geofence-enabled alarms on a person's calendars.
- [`class EKEvent`](https://developer.apple.com/documentation/eventkit/ekevent) — A class that represents an event in a calendar.
- [`class EKReminder`](https://developer.apple.com/documentation/eventkit/ekreminder) — A class that represents a reminder in a calendar.

### Calendars

- [`class EKCalendar`](https://developer.apple.com/documentation/eventkit/ekcalendar) — A class that represents a calendar in EventKit.
- [`class EKParticipant`](https://developer.apple.com/documentation/eventkit/ekparticipant) — A class that represents person, group, or room invited to a calendar event.

### Recurrence

- [Creating a recurring event](https://developer.apple.com/documentation/eventkit/creating-a-recurring-event) — Set up an event or reminder that repeats.
- [`class EKRecurrenceDayOfWeek`](https://developer.apple.com/documentation/eventkit/ekrecurrencedayofweek) — A class that represents the day of the week.
- [`class EKRecurrenceEnd`](https://developer.apple.com/documentation/eventkit/ekrecurrenceend) — A class that defines the end of a recurrence rule.
- [`class EKRecurrenceRule`](https://developer.apple.com/documentation/eventkit/ekrecurrencerule) — A class that describes the pattern for a recurring event.

### Alarms

- [Setting an alarm](https://developer.apple.com/documentation/eventkit/setting-an-alarm) — Alert users of events and reminders with an alarm.
- [`class EKAlarm`](https://developer.apple.com/documentation/eventkit/ekalarm) — A class that represents an alarm.
- [`class EKStructuredLocation`](https://developer.apple.com/documentation/eventkit/ekstructuredlocation) — A class that specifies a geofence to activate the alarm of a calendar item.

### Common objects

- [`class EKCalendarItem`](https://developer.apple.com/documentation/eventkit/ekcalendaritem) — An abstract superclass for calendar events and reminders.
- [`class EKObject`](https://developer.apple.com/documentation/eventkit/ekobject) — An abstract superclass for all EventKit classes that have persistent instances.
- [`class EKSource`](https://developer.apple.com/documentation/eventkit/eksource) — An abstract superclass that represents the account a calendar belongs to.

### Virtual conferences

- [Implementing a virtual conference extension](https://developer.apple.com/documentation/eventkit/implementing-a-virtual-conference-extension) — Support adding a virtual conference room to an event in Calendar.
- [`class EKVirtualConferenceProvider`](https://developer.apple.com/documentation/eventkit/ekvirtualconferenceprovider) — An object that associates virtual conferencing details with an event object in a user's calendar.
- [`class EKVirtualConferenceDescriptor`](https://developer.apple.com/documentation/eventkit/ekvirtualconferencedescriptor) — Details about a virtual conference that uses a custom room type.
- [`class EKVirtualConferenceRoomTypeDescriptor`](https://developer.apple.com/documentation/eventkit/ekvirtualconferenceroomtypedescriptor) — Details about a room where virtual conferences take place.

### Errors

- [`struct EKError`](https://developer.apple.com/documentation/eventkit/ekerror) — An EventKit error.
- [`enum Code`](https://developer.apple.com/documentation/eventkit/ekerror/code) — Error codes for EventKit errors.
- [`let EKErrorDomain: String`](https://developer.apple.com/documentation/eventkit/ekerrordomain) — A string that identifies the EventKit error domain.
