# Requested reminders as durable CoS work

An authorized private member request for a timed text now creates a CoS-owned
work record linked to the existing encrypted request, native timer binding and
terminal provider receipt. The requesting member can ask whether it is still
scheduled or was sent. The status tool reads the exact native timer and never
retries an uncertain send.

This is a **MODERNIZE** of the existing reminder workflow, preserving its
independent host/domain grants, recipient binding, observation controls and
native send guard. `adminme/cos_reminder_work.py` is an idempotent durable-bus
consumer, not a scheduler or provider client. It uses existing `task.created@4`
and `cos.work.state_changed@2` schemas; no storage format, projection schema or
provider enrollment changes. Work belongs to the CoS Party and service-private
scope, with the original member's current source grant and confidentiality
coordinates retained. The body remains in the original member-private intent;
the private task contains a generic title, due time and source reference.

The native `administrateme_self_reminder` tool retains its schedule and cancel
actions and adds `status`. Its backend receipt route verifies the exact member
scope before returning only reminder IDs, due time, timer binding and receipt
state. It never returns general CoS-private work or other members' records.
The tool reports `scheduled` only after exact timer read-back, and `sent` only
from a terminal result. Missing, inactive or overdue timers and unknown delivery
remain explicit. A saved binding alone is reported as waiting; it does not
prove that the timer was enabled.

The work consumer advances a bound reminder to waiting, a confirmed send to
completed, cancellation to cancelled, and other terminal results to a
reconciliation wait. It emits no provider effects. Current source permission
still governs private work reads; revocation does not erase historical provider
results. The append-only request and result remain authoritative if an internal
work item is subsequently edited. Bus checkpoints and deterministic keys recover
interrupted recording without registering another timer or sending again.

Focused evidence is in `tests/acceptance/test_cos_self_reminders.py` and
`openclaw/administrateme/test/self-reminders.test.ts`: real application composition,
restart and duplicate delivery suppression, service ownership, exact member
receipt isolation, cancellation, revoked grants, native timer read-back and
unknown outcomes. Existing service-private history and scope checks accompany
the `cos-reminders` release scope. Candidate behavior runs in hosted CI.

After protected deployment, inspect existing permitted reminder receipts and
the loaded status tool. A new real timed text needs an actual member request;
do not send an unsolicited deployment test. If the workflow regresses, disable
the affected `scheduledSelfReminderBindings` through supported activation while
retaining the schema-aware runtime, current requests, provider receipts and
ordinary reply grants. Never blindly resend an unknown outcome.

This completes one requested reminder workflow. General member-task delegation,
arbitrary provider actions, reply-based waits, Google/Apple service workspace
setup and other workflows remain separate deliverables.
