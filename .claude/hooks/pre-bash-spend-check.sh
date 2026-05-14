#!/bin/bash
# pre-bash-spend-check.sh — PreToolUse hook
# Fires before every Bash tool use.
# Reads .claude/state/spend.json. If today's spend exceeds the cap, exits 2 (HARD BLOCK).
#
# This is a circuit-breaker. Most of the build is subscription-billed, so spend.json
# tracks rough estimates (e.g., $0.50 per claude -p invocation, $0 for git/gh). The cap
# defaults to $50/day from ADMINME_DAILY_SPEND_CAP_USD_DEFAULT in containerEnv.
#
# Exit codes:
#   0 — under cap, allow the bash command
#   2 — over cap, BLOCK
#   1 — hook itself failed (soft warn)

set -u
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_DIR" 2>/dev/null || { exit 1; }

SPEND_FILE=".claude/state/spend.json"
CAP="${ADMINME_DAILY_SPEND_CAP_USD:-${ADMINME_DAILY_SPEND_CAP_USD_DEFAULT:-50}}"

# If spend.json doesn't exist, initialize it. No block.
if [ ! -f "$SPEND_FILE" ]; then
  mkdir -p .claude/state
  echo '{"total_usd": 0, "by_day": {}}' > "$SPEND_FILE"
  exit 0
fi

# Read today's spend
TODAY=$(date -u +%Y-%m-%d)
TODAY_SPEND=$(jq -r --arg day "$TODAY" '.by_day[$day] // 0' "$SPEND_FILE" 2>/dev/null || echo "0")

# Compare against cap
if (( $(echo "$TODAY_SPEND >= $CAP" | bc -l 2>/dev/null || echo "0") )); then
  echo "===================================================================" >&2
  echo "PRE-BASH SPEND CAP BLOCK: today's spend is \$${TODAY_SPEND}" >&2
  echo "Configured cap: \$${CAP}" >&2
  echo "===================================================================" >&2
  echo "" >&2
  echo "To proceed, either:" >&2
  echo "  1. Wait until tomorrow (cap resets at UTC midnight)" >&2
  echo "  2. Raise the cap: export ADMINME_DAILY_SPEND_CAP_USD=<higher value>" >&2
  echo "  3. Reset today's spend: jq '.by_day[\"$TODAY\"] = 0' $SPEND_FILE > /tmp/s && mv /tmp/s $SPEND_FILE" >&2
  echo "" >&2
  echo "Note: subscription-routed work (Max OAuth token in Codespace) doesn't increment" >&2
  echo "this counter the same way API calls do. If you're confident the work is" >&2
  echo "subscription-billed, raising the cap is safe." >&2
  exit 2
fi

exit 0
