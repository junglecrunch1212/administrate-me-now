"""
adminme/daemons/ — L1-adjacent adapters that emit domain events.

Per [§2.2] daemons under this tree are NOT projections: they do not subscribe
to the event bus to derive read state, they emit events on principal authority
into the log. PM-14 makes the placement binding — projections live under
``adminme/projections/`` and emit at most SYSTEM events; daemons live here.
The forward xlsx workbook daemon is the documented exception (it IS a
projection per [§2.2] resolution, since its derived state is two files on
disk).

Currently expected occupants:
- xlsx_sync/ — reverse xlsx daemon + descriptors + diff core (07c).
"""
