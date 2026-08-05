# adminme-bridge

`adminme-bridge` is the per-member edge daemon. It is a separate Poetry
package and process from the one central AdministrateMe runtime. It sends
canonical, typed events to the central `/api/bridge/ingest` namespace over the
member's authenticated Tailscale route.

The primary iPhone-surface enrollment enables three member-owned packs:

- `knowledge-apple-notes`
- `calendaring-apple-calendar-bridge`
- `reminders-apple-eventkit-bridge`

Apple Calendar and Reminders use EventKit through PyObjC. Apple Notes uses the
Notes application's supported scripting dictionary; it never touches Notes'
private SQLite/CoreData files. The daemon both emits observed provider changes
and pulls governed `external_sync.write_requested` commands from the central
runtime. A durable local command ledger prevents a result-upload failure from
duplicating a create operation.

Every accepted knowledge event is private to the enrolled member, keeps source
and correction provenance, and enters the `member_knowledge` read model. Only
non-privileged content may enter the scope-filtered domain `vector_search`
projection. The bridge does not copy OpenClaw conversations or generic memory.

The supported first enrollment is driven from the central Mac with
`bootstrap/bridge-export.sh`, accepted on the exact reviewed member-Mac checkout
with `bootstrap/install-member-bridge.sh`, and closed centrally with
`bootstrap/bridge-finalize.sh` or `bootstrap/bridge-revoke.sh`. The encrypted
transfer and separately conveyed one-time code expire after 30 minutes. The
installer verifies the Git SHA and Tailscale self identity, writes a per-user
LaunchAgent, emits the owner-scoped enrollment proof, and consumes the transfer
file. The first interactive launch requests full Calendar and Reminders access
and Notes Automation access. Enrollment remains in observation mode until the
operator validates inbound counts and deliberately releases outbound writes.
