"""
xlsx_sync — xlsx round-trip reverse daemon and supporting modules.

Per [§2.2] the reverse xlsx daemon is L1-adjacent, NOT a projection. It does
not register with ``ProjectionRunner`` and does not derive read state from
the event log; instead it watches the workbook on disk, diffs the live
contents against the per-sheet sidecar baseline written by the forward
daemon, and emits domain events on principal authority. PM-14 binds the
placement: projections live in ``adminme/projections/``, daemons live in
``adminme/daemons/``.

Modules in this package (07c-α lands the foundations; 07c-β lands the
daemon class itself + watchdog + lock contention + integration tests):
- ``sheet_schemas`` — per-sheet diff descriptors (event mappings, editable
  columns, derived columns, drop-vs-emit dispositions).
- ``diff`` — pure-functional diff core comparing live workbook rows to
  sidecar rows; returns added / updated / deleted / dropped_edits sets.
- ``reverse`` (07c-β) — the daemon class consuming the descriptors and
  diff core inside the workbook lock.

The forward daemon stays at ``adminme/projections/xlsx_workbooks/`` because
its derived state IS a projection per the [§2.2] resolution; the asymmetry
is intentional and documented in BUILD.md §3.11.
"""
