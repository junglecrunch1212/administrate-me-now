# Standing order: reminder_dispatch

## Scope

Every 15 minutes, ask AdministrateMe for commitments due inside the configured
lookahead window (60 minutes by default). For each eligible commitment, invoke
`compose_reminder@v1`. AdministrateMe records the compatibility
`reminder.dispatched` fact with `delivered=false` and the canonical typed chain:

1. `notification.signal.raised`
2. `notification.decision.made`
3. `notification.delivery.result`

The domain policy may route, suppress, hold, or retry. Only a route decision
delegates a delivery attempt to OpenClaw. Only OpenClaw may emit the delivery
result; AdministrateMe never claims delivery from a no-op callback.

## Triggers

OpenClaw owns the schedule declared in `bootstrap/openclaw/cron.yaml`. The
proactive pack has no event trigger and is never scheduled by the in-process
`PipelineRunner`.

## Approval gate

Routine reminders require no per-message approval. Policy still enforces
observation mode, profile quiet hours, scope, retry, and circuit state before
returning a route decision.

## Escalation

Composition or validation failures emit the non-delivered compatibility fact
and continue the cohort. A missing approval, active observation window, quiet
hours, expiry, or open delivery circuit produces a typed non-route decision;
none is converted into an external send.

## Policy and deduplication

- Deduplicate a commitment inside `dedupe_window_minutes` (90 by default).
- Honor per-member enablement, lookahead, dedupe, profile, quiet-hour, and
  observation settings.
- Missing approval produces `hold`; observation mode produces `suppress`;
  quiet hours or `not_before` produce `retry`; three failed attempts open the
  delivery circuit.
- The signal and decision retain member scope, correlation, causation, and
  commitment provenance.

## Execution steps

1. Load due commitments from the scoped commitment read model.
2. Remove recent duplicates and opted-out members.
3. Compose through the pinned OpenClaw `llm-task` skill seam.
4. On validation failure, emit only the non-delivered compatibility sentinel
   and continue with the next commitment.
5. On success, persist signal and policy decision through the real EventSink.
6. If the decision is `route`, OpenClaw performs the channel action and posts a
   typed `notification.delivery.result` back through its authenticated plugin
   seam. Suppressed, held, and retry decisions perform no channel action.

## What NOT to do

- Do not import provider SDKs or create an AdministrateMe scheduler.
- Do not mutate projections directly.
- Do not use `external.sent` as a speculative delivery receipt.
- Do not expose reminder content across owner or visibility scope.
- A per-row skill or delivery failure must not abort the remaining cohort.
