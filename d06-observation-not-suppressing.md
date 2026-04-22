# Diagnostic d06: Observation mode is on but an outbound happened anyway

**Symptom.** `adminme observation status` returns `ACTIVE`. But an `external.sent` event appeared in the log (or an actual iMessage/email went out). This should be impossible.

**When to use.** After prompt 08 (observation implemented) and before you trust observation mode for production. This is a **high-severity bug** if it happens — the whole observation model depends on suppression being airtight.

---

## Read first

1. `ADMINISTRATEME_DIAGRAMS.md` §9 (observation fire/suppress).
2. `platform/lib/observation.py::outbound`.
3. The `external.sent` event's `source` field — what pipeline or surface emitted it?

## Likely causes (ranked)

1. **Code path bypasses `outbound()`.** Some pipeline or L5 surface writes the event directly rather than going through the wrapper. This is the #1 cause.
2. **Observation check returns stale value.** `ObservationManager.is_active()` caches the answer; cache wasn't invalidated after an `adminme observation on` command from another process.
3. **Adapter auto-reply.** The adapter (e.g., Gmail auto-forward, Apple Reminders sync) acts on its own when it receives an inbound. Observation only gates deliberate outbound from pipelines; adapter behaviors are a separate category that needs its own suppression.
4. **OpenClaw fires independently.** A standing order fired in OpenClaw that AdministrateMe didn't register. OpenClaw's own skills may send messages outside the `outbound()` wrapper.

## Procedure

1. **Find the culprit.** Query the `external.sent` event:
   ```bash
   adminme event query --type external.sent --since "1 hour ago" --json | jq .source
   ```
   The source field tells you what emitted it.
2. **Check for `outbound()` usage.** For every module that should be calling `outbound()`:
   ```bash
   grep -rn "outbound(" platform/
   ```
   Compare against every module that emits `external.*`:
   ```bash
   grep -rn 'emit.*external\.\|emit.*messaging\.sent\|emit.*email\.sent' platform/
   ```
   Every emission should be INSIDE an `outbound()` call. Any direct emission is a bug.
3. **Check observation state propagation.** `ObservationManager.is_active()` should query the config file (or an always-fresh event log query) — NOT a process-local cache with a stale TTL.
4. **Check adapter autonomy.** Any adapter that sends in response to received events is a problem. Grep for `send\|write\|post` in adapter code.
5. **Check OpenClaw.** `openclaw standing-orders list` — any that AdministrateMe didn't register? If so, disable them.

## Fix pattern

**A.** **Audit all emission sites.** The `external.sent` event type should be private to `platform/lib/observation.py::outbound`. Nowhere else should emit it. Use the Pydantic model's `model_config = {"extra": "forbid"}` plus a module-level check that rejects emission from anywhere else.

**B.** **No caching in ObservationManager.** `is_active()` hits disk or event log every call. Cost is negligible; safety is critical.

**C.** **Adapters go through outbound too.** Any adapter that can send (Reminders writing to iCloud, Gmail sending replies) must call `outbound()` with appropriate action key.

**D.** **OpenClaw alignment.** On platform boot, `openclaw_audit` (from prompt 15) checks every OpenClaw standing order is one AdministrateMe registered. Any foreign ones are disabled by default (operator can opt-in).

## Verify fix

Turn observation on, then attempt to send by every mechanism:

```bash
adminme observation on --reason "verifying fix"

# Attempt outbound via each path:
adminme pipeline trigger morning_digest --member stice-james   # proactive
# (trigger task.completed to exercise reward_dispatch)
# (send a test email that would normally produce a reminder via reminder_dispatch)

# Now query:
adminme event query --type external.sent --since "10 min ago"
# Should be ZERO.
adminme event query --type observation.suppressed --since "10 min ago"
# Should show N suppressions corresponding to the N outbound attempts.

adminme observation off --reason "resume after verification"
```

## Escalate if

You find an `external.sent` event you can't attribute to any code path. This suggests OpenClaw itself is sending outside our gating (unlikely but possible), or the event log has been written to by something outside the platform. Investigate OpenClaw logs and check whether the event has a non-AdministrateMe source field.

Absolutely do not re-enable normal operation until you understand the source of every unexpected `external.sent`.
