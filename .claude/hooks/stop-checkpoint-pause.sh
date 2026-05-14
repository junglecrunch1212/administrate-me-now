#!/bin/bash
# stop-checkpoint-pause.sh — Stop hook
# Fires when Claude Code finishes a session (operator-initiated /exit or natural completion).
#
# Two responsibilities:
# 1. If we just finished work on a checkpoint prompt (10d / 14e / 15.5), and the work
#    has not yet been audited, write NEEDS_HUMAN.md to invoke the checkpoint Routine.
# 2. Otherwise, write a minimal session-end record to .claude/state/last-session.json
#    for diagnostic purposes.
#
# This hook is informational. It does not block (always exits 0 unless the script itself fails).

set -u
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_DIR" 2>/dev/null || { exit 1; }

INPUT=$(cat 2>/dev/null || true)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null || echo "false")

# If we're being re-invoked due to the hook firing again on our own action, exit early
if [ "$STOP_ACTIVE" = "true" ]; then
  exit 0
fi

mkdir -p .claude/state

# Detect checkpoint state from ORCHESTRATOR_STATE.md if present, or from the active branch name
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
CURRENT_PROMPT=""
if [ -f docs/ORCHESTRATOR_STATE.md ]; then
  CURRENT_PROMPT=$(jq -r '.current_prompt // empty' docs/ORCHESTRATOR_STATE.md 2>/dev/null || true)
fi
if [ -z "$CURRENT_PROMPT" ]; then
  # Fall back: parse from branch name (phase-10d-foo or phase-14e-bar)
  CURRENT_PROMPT=$(echo "$CURRENT_BRANCH" | grep -oE '(10d|14e|15\.5|07\.5)' | head -1 || true)
fi

# Are we at a checkpoint?
IS_CHECKPOINT=false
case "$CURRENT_PROMPT" in
  10d|14e|15.5|07.5)
    IS_CHECKPOINT=true
    ;;
esac

# Write the session-end record (always)
cat > .claude/state/last-session.json <<EOF
{
  "session_id": "$SESSION_ID",
  "ended_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "branch": "$CURRENT_BRANCH",
  "current_prompt": "$CURRENT_PROMPT",
  "is_checkpoint": $IS_CHECKPOINT
}
EOF

# If at a checkpoint AND NEEDS_HUMAN.md doesn't already exist, signal the checkpoint Routine.
# Note: the Routine is also triggered by the GitHub event (PR-merged with checkpoint prompt id),
# so this is a belt-and-suspenders approach. The Routine writes the full audit; this hook
# just creates a placeholder.

if [ "$IS_CHECKPOINT" = "true" ] && [ ! -f NEEDS_HUMAN.md ]; then
  cat > NEEDS_HUMAN.md <<EOF
# NEEDS_HUMAN.md — Checkpoint pause

**Halt class:** CHECKPOINT_PAUSE
**Triggered at:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Checkpoint:** $CURRENT_PROMPT
**Branch at trigger:** $CURRENT_BRANCH

A checkpoint prompt just finished work. The checkpoint Routine (named \`checkpoint-${CURRENT_PROMPT}\`) should fire next and run the CSO and Codex (and for 14e, QA) audits, then update this file with the audit results.

If the Routine has run and you're seeing structured audit JSON below this section: open \`04-HALTS.md\` § CHECKPOINT_PAUSE and use the \`04-checkpoint-review.md\` Cowork session prompt.

If you're seeing only this placeholder (no audit results yet), the checkpoint Routine hasn't fired. Wait 1-2 minutes; the Routine triggers on PR-merged. If it doesn't fire after 5 minutes, check Routines at claude.ai/code/routines for errors.

After the audit results land below this section, you can resolve the halt per \`04-HALTS.md\`.

---

(Audit results will be appended here by the checkpoint Routine.)
EOF

  echo "stop-checkpoint-pause: wrote NEEDS_HUMAN.md placeholder for checkpoint $CURRENT_PROMPT" >&2
fi

exit 0
