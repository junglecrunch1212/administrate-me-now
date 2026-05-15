#!/bin/bash
# setup-complete.sh — v4 pre-flight verification script.
# Run once after every Codespaces: Rebuild Container.
# Output is a one-line GREEN/YELLOW/RED verdict.
#
# Usage:  bash validation/setup-complete.sh
# Exit codes: 0 (GREEN), 1 (YELLOW), 2 (RED)

set +e  # don't exit on first failure; collect all findings

VERDICT="GREEN"
NOTES=()

note_yellow() { VERDICT="YELLOW"; NOTES+=("YELLOW: $1"); }
note_red()    { VERDICT="RED";    NOTES+=("RED: $1"); }

# 1. Tooling
for tool in git gh python3 node npm poetry claude; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    note_red "Required tool '$tool' missing"
  fi
done

# 2. claude smoke test (catches ~/.claude perms bug)
if command -v claude >/dev/null 2>&1; then
  if ! timeout 10 claude --help >/dev/null 2>&1; then
    note_red "claude --help hangs (likely ~/.claude perms; run: sudo chown -R \$USER:\$USER \$HOME/.claude)"
  fi
fi

# 3. ~/.claude perms
if [ -d "$HOME/.claude" ]; then
  OWNER=$(stat -c '%U' "$HOME/.claude" 2>/dev/null || echo unknown)
  if [ "$OWNER" != "$USER" ]; then
    note_red "~/.claude owned by $OWNER (need $USER); claude CLI will hang silently"
  fi
else
  note_yellow "~/.claude does not exist (will be created on first claude invocation)"
fi

# 4. Env vars
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  note_yellow "CLAUDE_CODE_OAUTH_TOKEN not set; Executor billing will fall through to API"
fi
if [ -z "${GITHUB_TOKEN:-}" ]; then
  note_yellow "GITHUB_TOKEN not set; Executor pushes will need manual auth"
fi
if [ -z "${ADMINME_MODEL_EXECUTOR:-}" ]; then
  note_yellow "ADMINME_MODEL_EXECUTOR not set (devcontainer should set this to 'opus')"
fi

# 5. Repo state
if [ -d ".git" ]; then
  # Hook check — broken LFS hooks should be removed
  for hook in post-checkout pre-push; do
    if [ -f ".git/hooks/$hook" ]; then
      if grep -q 'git-lfs\|git lfs' ".git/hooks/$hook" 2>/dev/null; then
        if ! command -v git-lfs >/dev/null 2>&1; then
          note_red ".git/hooks/$hook references git-lfs but git-lfs not installed; will break && chains"
        fi
      fi
    fi
  done

  # Credential helper check
  HELPER=$(git config --local --get credential.helper 2>/dev/null || echo "")
  if [ -n "$HELPER" ]; then
    note_yellow "git credential.helper is set to '$HELPER'; may interfere with Executor pushes"
  fi
else
  note_red "Not inside a git repo (cd to /workspaces/administrate-me-now first)"
fi

# 6. .claude config
if [ ! -d ".claude/hooks" ]; then
  note_yellow ".claude/hooks/ missing (Phase 3 of setup not complete)"
fi
if [ ! -d ".claude/agents" ]; then
  note_yellow ".claude/agents/ missing (Phase 4 of setup not complete)"
fi

# 7. Required schemas
for s in preflight_verdict splitter_proposal reviewer_verdict cso_audit codex_review qa_report; do
  if [ ! -f "schemas/$s.json" ]; then
    note_yellow "schemas/$s.json missing (Phase 4 of setup not complete)"
  fi
done

# 8. Vision doc
if [ ! -f "WHAT-IS-ADMINISTRATEME.md" ]; then
  note_red "WHAT-IS-ADMINISTRATEME.md missing (required for Reviewer Job 4 and Partner drift-control)"
fi

# Final report
echo "=== v4 pre-flight verdict: $VERDICT ==="
if [ "${#NOTES[@]}" -gt 0 ]; then
  echo "Findings:"
  for n in "${NOTES[@]}"; do
    echo "  - $n"
  done
  echo ""
  echo "Fix per 06-TROUBLESHOOTING.md § v4 troubleshooting additions."
else
  echo "All v4 pre-flight checks pass. Ready for Executor invocations per RUNBOOK-EXECUTOR.md."
fi

case "$VERDICT" in
  GREEN)  exit 0 ;;
  YELLOW) exit 1 ;;
  RED)    exit 2 ;;
esac
