#!/bin/bash
# post-edit-verify.sh — PostToolUse hook
# Fires after every Write / Edit / MultiEdit tool use.
# Runs scripts/verify_invariants.sh against current state.
# If verify_invariants exits non-zero, this hook exits 2 (HARD BLOCK).
#
# Exit codes:
#   0 — verify_invariants passed, no block
#   2 — verify_invariants failed, BLOCK the tool action
#   1 — hook itself failed (treated as soft warn, not block)

set -u
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_DIR" 2>/dev/null || { echo "post-edit-verify: cannot cd to project"; exit 1; }

# Read the stdin payload (Claude Code passes JSON about the tool use)
INPUT=$(cat 2>/dev/null || true)

# Extract file_path if available — we only run invariant check if a relevant file was touched
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || true)

# Skip invariant check if the touched file is purely meta (docs/, .github/, etc.)
# Invariant check is for adminme/, packs/, bootstrap/, integrations/ — actual code paths
if [ -n "$FILE_PATH" ]; then
  case "$FILE_PATH" in
    *.md|docs/*|.github/*|.devcontainer/*|.claude/*|README*)
      # Meta change — skip
      exit 0
      ;;
  esac
fi

# Run verify_invariants.sh if it exists. If it doesn't exist (early in the build before
# its prompt has merged), do nothing — there's nothing yet to verify.
if [ ! -x scripts/verify_invariants.sh ]; then
  exit 0
fi

# Run verify_invariants. Capture stdout and stderr.
OUTPUT=$(bash scripts/verify_invariants.sh 2>&1)
EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ]; then
  # HARD BLOCK
  # Print a message Claude Code will surface in the session transcript
  echo "===================================================================" >&2
  echo "POST-EDIT VERIFY FAILED: scripts/verify_invariants.sh exited $EXIT_CODE" >&2
  echo "===================================================================" >&2
  echo "$OUTPUT" >&2
  echo "" >&2
  echo "This commit cannot proceed. Fix the invariant violation above before retrying." >&2
  echo "Common causes:" >&2
  echo "  * Importing 'anthropic' or 'openai' in adminme/ (violates [§8.2] / [D6])" >&2
  echo "  * Hardcoded tenant identity in non-fixture files (violates [§12.4-12.5])" >&2
  echo "  * Direct '~/.adminme/' string literal outside InstanceConfig (violates [§15] / [D15])" >&2
  echo "  * Projection emitting an event not in ALLOWED_EMITS (violates [§2.2])" >&2
  echo "  * outbound() bypass — direct external write (violates [§6.14])" >&2
  exit 2
fi

exit 0
