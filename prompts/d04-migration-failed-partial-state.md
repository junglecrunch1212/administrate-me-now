# Diagnostic d04: Migration failed mid-run, instance is in partial state

**Symptom.** `adminme migration apply` was running. It failed partway through. Now the instance is in an inconsistent state — some tables have new columns, others don't; some events have the new envelope field, others don't. Services may not start cleanly.

**When to use.** After any failed migration. Critical: if this happens on the family instance, the family is affected.

---

## Read first

1. The migration's forward SQL / Python in `adminme/migrations/<id>.py`.
2. The error output from the failed run. `adminme migration status` shows the last-attempted migration and where it was when it failed.
3. `ADMINISTRATEME_FIELD_MANUAL.md` chapter 8 emergency playbook.

## Is this urgent?

**If this is the family instance (Mac Mini):** rollback immediately per FIELD_MANUAL chapter 8. Get the family back online. Investigate the failed migration in the lab, not live.

**If this is the lab:** take your time. No one is affected.

## Likely causes (ranked)

1. **Non-atomic migration.** The migration wraps multiple DDL statements + data transforms, but SQLite DDL doesn't honor transactions uniformly. A midpoint failure leaves some statements applied.
2. **Projection rebuild failure after schema change.** The schema migrated, but the rebuild failed (probably one of d03's causes), leaving projections in the old shape with the new schema.
3. **Data transform failure.** The forward_code hit an unexpected payload it didn't handle (e.g., assumed every commitment had a `confidence` field, but some v1 events from 6 months ago don't).
4. **Services still running.** Migration tried to ALTER TABLE while another process was holding a read lock.

## Procedure

1. `adminme migration status` — confirms which migration, which step.
2. `adminme service stop` — ensures nothing is racing with the recovery.
3. Back up the current state before changing anything:
   ```bash
   cp -r ~/.adminme ~/.adminme.pre-recovery.<timestamp>
   ```
4. Choose a path:
   - **A: Complete the migration.** If you can identify the specific failure (e.g., a handler crashed on a payload shape), fix the handler and re-run the migration. Safe if you understand what failed.
   - **B: Reverse the migration.** If the migration has `reverse_sql` / `reverse_code`, run them manually or via `adminme migration rollback <id>`. This restores to pre-migration state. Safe.
   - **C: Restore from backup.** If the migration started writing but backup is recent enough that you won't lose important data. Use the `~/.adminme.pre-recovery.*` from step 3.

## Fix pattern

**A.** All migrations must be wrapped in a transaction where possible. For SQLite limitations, use `BEGIN IMMEDIATE` + explicit savepoints.

**B.** Every migration must have a forward AND reverse. If you can't write a clean reverse, the migration is too risky and should be split.

**C.** Migrations that touch data (not just schema) must be idempotent — re-running the forward after a partial failure should produce the same final state as a clean run.

**D.** Add pre-migration dry-run: `adminme migration apply --dry-run` must enumerate every statement that would execute, every row it would touch. Operator reviews before committing.

## Verify fix

```bash
# In lab:
adminme migration apply <id> --dry-run     # review
adminme migration apply <id>                 # run
adminme migration verify <id>                # assert final state
adminme projection rebuild parties           # ensure projections match new schema
adminme instance status                      # all green

# Property: run the full migration suite twice; should produce identical results
adminme migration apply-all
diff <(adminme instance state-hash) saved_hash
```

## Escalate if

The event log itself is corrupted. Symptoms: `adminme event query` returns SQL errors, events are missing (visible via `adminme event count-by-type` showing an impossible drop), the .db file size decreased. This is the one scenario where restore-from-backup is the only option. Do not proceed with further operations on a corrupted event log — stop services, restore from backup, and only resume once integrity is verified.

## Also: prevention

The migration framework (prompt 17) includes `--dry-run` for every migration. Future migrations should go through it in lab before ever being applied to the family instance. If it fails in lab, you fix the migration. Never apply to family an untried migration.
