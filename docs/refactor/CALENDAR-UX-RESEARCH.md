# Multi-calendar UX research and AdministrateMe translation

Status: reviewed design evidence for UX-23 and AC-27

Date: 2026-08-06

Classification: **MODERNIZE** the member Calendar presentation; **PRESERVE**
provider authority and deterministic household scheduling policy.

## Comparative findings

Current first-party product guidance converges on a small set of durable
calendar-management patterns:

| Pattern | Product evidence | AdministrateMe decision |
|---|---|---|
| Spatial time plus multiple zoom levels | [Google Calendar](https://support.google.com/calendar/answer/6110849) offers Day, Week, Month, Year, Schedule, custom-day views, date arrows, and Today. [Outlook](https://support.microsoft.com/en-us/outlook/training/welcome-to-your-outlook-calendar) exposes Day, Week, Work Week, Month, date navigation, and Today. | Day and Week use a time grid; Month uses an anchored six-week grid; Agenda provides the low-spatial-cost list alternative; Today and previous/next are persistent. |
| Visible source identity and reversible filters | [Google](https://support.google.com/calendar/answer/37095) and [Apple Calendar](https://support.apple.com/en-au/guide/calendar/icl1006/mac) let people show or hide calendars. Google also supports per-calendar color. | Each provider route has a stable color and checkbox. Filtering is explicitly view-only and cannot alter capacity, conflicts, Today, or Triage routing. |
| Overlay and separation are different questions | [Outlook](https://support.microsoft.com/en-US/Outlook/calendar/view-multiple-calendars-at-the-same-time-in-outlook) supports both color-coded overlay and side-by-side/split views. | Capacity colors answer “what consumes or warns about my time”; calendar colors answer “where did this come from.” Both operate on the same effective events and retain text labels. A future people-lane view may add separation without changing truth. |
| Context-specific calendar sets reduce noise | [Fantastical calendar sets](https://flexibits.com/fantastical/help/calendar-sets) group calendars into quickly switchable contexts and may change with time or location. | The initial release provides Show all, Capacity only, and individual source controls. Persistent or context-triggered sets remain a typed preference feature, not an inferred model decision. |
| Availability is not identical to visibility | [Google appointment availability](https://support.google.com/calendar/answer/16287054) checks selected calendars for Busy/Maybe conflicts, while visibility remains separately controllable. | Provider availability, approved hard/soft/context rules, and display visibility remain three separate fields and code paths. A hidden calendar is never assumed free. |
| Buffers and focus protection need visible semantics | [Google appointment schedules](https://support.google.com/calendar/answer/11423292) support before/after buffers, and [Google focus time](https://support.google.com/calendar/answer/11190973) visually distinguishes protected work. | Household guardrails render behind events, typed buffers render as dashed halos, and event detail states exact before/after minutes. No automatic reschedule or decline is inferred. |
| Planning changes should be inspectable before writes | [Reclaim's Planner overview](https://help.reclaim.ai/en/articles/14846468-reclaim-ai-2-0-overview) highlights previewing schedule changes before applying them. | AdministrateMe keeps provider writes behind Triage plus independent governance. This release improves inspection only; it does not introduce auto-scheduling or a model decision loop. |

## ADHD-oriented interaction model

The redesign deliberately reduces working-memory and switching cost:

- The calendar, not explanatory prose, occupies the primary visual area.
- Stable spatial position answers “when”; source color answers “where from”;
  explicit effect text answers “does this consume me.” Color is never the sole
  carrier of meaning.
- The default Capacity lens keeps hard blocks, soft load, context, household
  guardrails, and buffers visually distinct without asking the member to
  remember provider semantics.
- All-day facts have their own lane. Event provenance, duplicate copies,
  buffers, location, and evidence stay in one detail pane instead of repeating
  on every block.
- Month supports orientation, Week supports capacity planning, Day supports
  execution, Agenda supports scanning, and Find time supports a bounded choice.
- Rules are summarized first and editable only after explicit disclosure. This
  separates frequent calendar reading from infrequent high-consequence policy
  editing.

## Deterministic boundary

The browser may choose layout, source visibility, zoom, and color lens. It may
not decide whether an event is busy, whether a duplicate is deleted, which
calendar receives a write, whether a soft hold should be displaced, or which
opening a member must choose. The Python composition root receives the scoped
provider facts, explicit member rule set, recurring schedule policy, timezone,
anchor, bounded view, and clock; it returns the effective events, guardrails,
conflicts, and open windows. An LLM is not called anywhere in this path.

The browser filter consumes `source_routes` retained on each duplicate group.
That field exists so hiding one provider copy cannot erase the provenance of
another visible copy. The filter is applied only after the full deterministic
cockpit is returned; counts and scheduling results therefore remain unchanged.
