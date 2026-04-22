# Diagnostic d08: XLSX round-trip is emitting spurious events

**Symptom.** The xlsx forward daemon regenerates a workbook. The reverse daemon then observes the file change (rightly — the forward daemon just wrote it) and emits events re-announcing the already-known state. You see duplicate `task.created` events, or `task.updated` events with no semantic change. Or worse: events flap back and forth, creating a feedback loop.

**When to use.** After prompt 07 (xlsx implementation). This is a classic bidirectional-sync pitfall.

---

## Read first

1. `ADMINISTRATEME_BUILD.md` §3.11 (xlsx bidirectional) — the sidecar state JSON mechanism.
2. `platform/projections/xlsx_workbooks/forward.py` and `reverse.py`.
3. The sidecar JSON files: `~/.adminme/xlsx_state/<workbook>.sidecar.json`. These record "what the workbook currently represents from forward's perspective."

## Likely causes (ranked)

1. **Sidecar not updated after forward write.** Forward daemon writes the xlsx file but forgets to write the sidecar. Reverse daemon then sees the file differ from the (stale) sidecar and attributes the diff to human edit.
2. **Reverse daemon debounce too short.** Watchdog fires on forward's write before forward's lock releases. Reverse reads the xlsx and the sidecar at different times; they're momentarily inconsistent.
3. **Sidecar JSON non-deterministic.** Sidecar contains a timestamp, so each forward-regen produces a different sidecar hash — reverse diff always thinks "something changed."
4. **Cell-protection not enforced.** A protected cell is being modified on forward (shouldn't happen per spec) or read differently by reverse. Always triggers a diff.
5. **Event replay after restart.** Forward daemon regenerates the xlsx on startup from projection state. Reverse sees this as a user edit.

## Procedure

1. **Reproduce.** Seed some events, let forward regen, don't touch the file, watch reverse's log:
   ```bash
   adminme xlsx forward-sync --verbose
   tail -f ~/.adminme/logs/xlsx_reverse.jsonl
   ```
   If events are emitted without any user action, the bug is real.
2. **Check the sidecar.** After forward runs:
   ```bash
   cat ~/.adminme/xlsx_state/adminme-ops.sidecar.json | jq .
   ```
   Is it fresh? Does it match the workbook's state?
3. **Check what reverse is diffing.** Reverse compares live xlsx vs sidecar:
   ```bash
   adminme xlsx reverse-sync --dry-run --verbose
   ```
   Should report "0 changes." If it reports N changes matching the prior forward-sync's content, sidecar is stale.
4. **Check the lock.** Forward writes the xlsx under a lock; reverse takes the same lock before reading. If reverse reads mid-write, diffs are garbage.

## Fix pattern

**A.** Sidecar write is atomic with xlsx write. Both happen inside the same lock, in order: write-xlsx → write-sidecar → release-lock. If sidecar write fails, xlsx is reverted (via the backup that forward made at start).

**B.** Sidecar must be deterministic: no timestamps, no random, no UUIDs. It's a content-addressable snapshot of what forward just wrote. Two forward runs on the same projection state produce byte-identical sidecars.

**C.** Reverse daemon's debounce >= forward daemon's write-plus-fsync time + a safety margin. Default: 2 seconds. Also: reverse must check the lock before reading — if forward is holding it, wait.

**D.** On service startup, forward runs ONCE before reverse starts observing. The sidecar is written before the watchdog is armed. This way, reverse's first read matches the sidecar and produces no events.

**E.** Cell-protection is enforced on both sides. Forward never writes to a protected cell (if a projection field tries to, it's an error). Reverse explicitly excludes protected cells from its diff.

## Verify fix

```bash
# Setup
adminme xlsx forward-sync          # idle state
sha256sum ~/.adminme/xlsx_state/adminme-ops.sidecar.json > /tmp/sidecar-before.hash
sha256sum ~/.adminme/adminme-ops.xlsx > /tmp/xlsx-before.hash

# Run forward again — should be a no-op
adminme xlsx forward-sync
# Sidecar unchanged
diff /tmp/sidecar-before.hash <(sha256sum ~/.adminme/xlsx_state/adminme-ops.sidecar.json)
# XLSX unchanged
diff /tmp/xlsx-before.hash <(sha256sum ~/.adminme/adminme-ops.xlsx)
# No events emitted from reverse
adminme event query --source '{"daemon":"xlsx_reverse"}' --since "1 min ago"
# Should be zero.

# Run pytest
poetry run pytest platform/projections/xlsx_workbooks/tests/ -v

# Specifically:
poetry run pytest platform/projections/xlsx_workbooks/tests/test_no_spurious_reverse_events.py -v
```

## Escalate if

After A-E, spurious events still emerge. Suspect that openpyxl is non-deterministic — some xlsx metadata (sharedStrings order, zip timestamps) differs across runs. Normalize via:
- `openpyxl.utils.ZIP_DEFLATED` consistently.
- Clear `workbook.properties.modified` and `created` to a fixed value.
- Sort shared strings pool explicitly.

This is a known openpyxl gotcha. If normalization doesn't fix it, replace openpyxl in the affected paths with a lower-level zip+XML generator.
