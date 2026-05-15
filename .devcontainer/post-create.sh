#!/bin/bash
# post-create.sh — Runs once after the Codespace container is created.
# v4 changes per POSTMORTEM-v3-to-v4.md:
#   - chown ~/.claude to current user (fixes silent claude CLI hang)
#   - remove broken LFS hooks if git-lfs missing
#   - disable VS Code credential.helper (Executor pushes via explicit URL)
#   - GREEN/YELLOW/RED verdict at end
#
# 2026-05-15 sidecar fix: install claude CLI via npm directly here, replacing
# the broken ghcr.io/anthropics/devcontainer-features/claude-code feature that
# was causing container builds to fail with error 1302. The node:1 feature is
# already in devcontainer.json so npm is available in the build path.

set -e
echo "=== AdministrateMe Codespace post-create (v4) ==="

REPO_ROOT="${PWD}"
PREFLIGHT_VERDICT="GREEN"
PREFLIGHT_NOTES=()

note() { PREFLIGHT_NOTES+=("$1"); }
yellow() { PREFLIGHT_VERDICT="YELLOW"; note "$1"; }
red() { PREFLIGHT_VERDICT="RED"; note "$1"; }

# 1. Verify essential tooling
echo "--- Verifying tooling ---"
for tool in git gh python3 node npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: required tool '$tool' not found"
    red "Required tool '$tool' missing"
    exit 1
  fi
  echo "  $tool: $(command -v $tool) — $($tool --version 2>&1 | head -1)"
done

# 2. Install claude CLI via npm if not present (devcontainer feature replacement)
if ! command -v claude >/dev/null 2>&1; then
  echo "--- Installing claude CLI via npm (sidecar fallback for broken ghcr.io/anthropics/devcontainer-features/claude-code) ---"
  npm install -g @anthropic-ai/claude-code 2>&1 | tail -5 || \
    yellow "npm install of @anthropic-ai/claude-code returned non-zero; verifying anyway"
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: 'claude' not found after npm install attempt."
  red "claude CLI install failed"
  exit 1
fi
echo "  claude: $(command -v claude) — $(claude --version 2>&1)"

# 2.5. Fix ~/.claude perms (v4 — fixes the silent-hang bug)
if [ -d "$HOME/.claude" ]; then
  OWNER=$(stat -c '%U' "$HOME/.claude" 2>/dev/null || echo "unknown")
  if [ "$OWNER" != "$USER" ] && [ "$OWNER" != "unknown" ]; then
    echo "--- Fixing ~/.claude perms (was $OWNER, now $USER) ---"
    sudo chown -R "$USER:$USER" "$HOME/.claude" 2>/dev/null || \
      yellow "Could not chown ~/.claude (sudo failed); claude CLI may hang"
  else
    echo "  ~/.claude: owned by $USER OK"
  fi
fi

# 3. Verify OAuth token secret
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "WARNING: CLAUDE_CODE_OAUTH_TOKEN not set."
  yellow "CLAUDE_CODE_OAUTH_TOKEN missing; falls back to API billing"
else
  echo "  CLAUDE_CODE_OAUTH_TOKEN: $(echo "$CLAUDE_CODE_OAUTH_TOKEN" | sed 's/.\{8,\}/<REDACTED>/')"
fi

# 4. Verify GitHub auth
if ! gh auth status >/dev/null 2>&1; then
  echo "WARNING: 'gh' is not authenticated."
  yellow "gh CLI not authenticated"
else
  echo "  gh auth: $(gh auth status 2>&1 | head -1)"
fi
if [ -z "${GITHUB_TOKEN:-}" ]; then
  yellow "GITHUB_TOKEN env var not set"
else
  echo "  GITHUB_TOKEN: set"
fi

# 4.5. Remove broken Git LFS hooks if git-lfs not installed
if [ -d "${REPO_ROOT}/.git/hooks" ] && ! command -v git-lfs >/dev/null 2>&1; then
  for hook in post-checkout post-merge pre-push post-commit; do
    if [ -f "${REPO_ROOT}/.git/hooks/$hook" ]; then
      if grep -q 'git-lfs\|git lfs' "${REPO_ROOT}/.git/hooks/$hook" 2>/dev/null; then
        echo "--- Removing broken LFS hook: $hook ---"
        rm -f "${REPO_ROOT}/.git/hooks/$hook"
      fi
    fi
  done
fi

# 4.6. Disable VS Code credential helper for this repo
if [ -d "${REPO_ROOT}/.git" ]; then
  git -C "${REPO_ROOT}" config --local credential.helper "" 2>/dev/null || true
  echo "  git credential.helper: disabled for this repo"
fi

# 5. Install Poetry
if ! command -v poetry >/dev/null 2>&1; then
  echo "--- Installing Poetry ---"
  pip install poetry --quiet
fi
echo "  poetry: $(command -v poetry) — $(poetry --version)"

# 6. Install Python deps
if [ -f "${REPO_ROOT}/pyproject.toml" ]; then
  echo "--- Installing Python deps ---"
  cd "$REPO_ROOT"
  poetry install --no-interaction --no-ansi 2>&1 | tail -5 || \
    yellow "poetry install completed with warnings"
fi

# 7. Install Node deps
if [ -f "${REPO_ROOT}/console/package.json" ]; then
  echo "--- Installing Node deps (console/) ---"
  cd "${REPO_ROOT}/console"
  npm install --silent 2>&1 | tail -3 || \
    yellow "npm install completed with warnings"
  cd "$REPO_ROOT"
fi

# 8. Make hook scripts executable
if [ -d "${REPO_ROOT}/.claude/hooks" ]; then
  chmod +x "${REPO_ROOT}/.claude/hooks/"*.sh 2>/dev/null || true
  echo "  Hooks made executable."
fi

# 9. Initialize .claude/state/
mkdir -p "${REPO_ROOT}/.claude/state"
[ -f "${REPO_ROOT}/.claude/state/spend.json" ] || \
  echo '{"total_usd": 0, "by_day": {}}' > "${REPO_ROOT}/.claude/state/spend.json"

# 10. claude CLI smoke test
echo "--- Smoke-testing claude CLI ---"
if timeout 10 claude --help >/dev/null 2>&1; then
  echo "  claude --help: OK"
else
  yellow "claude --help did not return; check ~/.claude perms"
fi

# 11. Final verdict
echo ""
echo "=== Codespace pre-flight verdict: $PREFLIGHT_VERDICT ==="
if [ "${#PREFLIGHT_NOTES[@]}" -gt 0 ]; then
  echo "Notes:"
  for n in "${PREFLIGHT_NOTES[@]}"; do
    echo "  - $n"
  done
fi
echo ""
case "$PREFLIGHT_VERDICT" in
  RED)
    echo "RED - fix the issues above before any Executor session."
    ;;
  YELLOW)
    echo "YELLOW - review notes; some features may degrade gracefully."
    ;;
  *)
    echo "GREEN - ready for Executor invocations per RUNBOOK-EXECUTOR.md."
    ;;
esac
echo ""
echo "Next: see RUNBOOK-EXECUTOR.md for the c1-c4 cycle, or POSTMORTEM-v3-to-v4.md if anything fails."
