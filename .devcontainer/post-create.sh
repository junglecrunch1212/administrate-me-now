#!/bin/bash
# post-create.sh — Runs once after the Codespace container is created.
# Sets up the AdministrateMe Executor environment: tooling, Poetry deps, Node deps, auth verification.

set -e

echo "=== AdministrateMe Codespace post-create ==="

REPO_ROOT="${PWD}"

# 1. Verify essential tooling installed by features
echo "--- Verifying tooling ---"
for tool in git gh python3 node npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: required tool '$tool' not found"
    exit 1
  fi
  echo "  $tool: $(command -v $tool) — $($tool --version 2>&1 | head -1)"
done

# 2. Verify claude CLI from the Anthropic devcontainer feature
if ! command -v claude >/dev/null 2>&1; then
  echo "ERROR: 'claude' not found. Anthropic devcontainer feature failed to install."
  echo "       Check .devcontainer/devcontainer.json — features block must include"
  echo "       ghcr.io/anthropics/devcontainer-features/claude-code:latest"
  exit 1
fi
echo "  claude: $(command -v claude) — $(claude --version 2>&1)"

# 3. Verify the OAuth token secret is injected
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "WARNING: CLAUDE_CODE_OAUTH_TOKEN is not set."
  echo "         Claude Code will fall back to API key auth if ANTHROPIC_API_KEY is present,"
  echo "         which bills per call. Set the OAuth token as a Codespaces user secret"
  echo "         at github.com/settings/codespaces to bill against Max subscription."
else
  echo "  CLAUDE_CODE_OAUTH_TOKEN: set (first 30 chars: $(echo ${CLAUDE_CODE_OAUTH_TOKEN} | head -c 30)...)"
fi

# 4. Verify GitHub auth (Codespaces injects GITHUB_TOKEN by default)
if ! gh auth status >/dev/null 2>&1; then
  echo "WARNING: 'gh' is not authenticated. Run 'gh auth login' to set it up."
else
  echo "  gh auth: $(gh auth status 2>&1 | head -1)"
fi

# 5. Install Poetry if not present (AdministrateMe uses Poetry for Python deps)
if ! command -v poetry >/dev/null 2>&1; then
  echo "--- Installing Poetry ---"
  pip install poetry --quiet
fi
echo "  poetry: $(command -v poetry) — $(poetry --version)"

# 6. Install Python deps if pyproject.toml exists
if [ -f "${REPO_ROOT}/pyproject.toml" ]; then
  echo "--- Installing Python dependencies (poetry install) ---"
  cd "$REPO_ROOT"
  poetry install --no-interaction --no-ansi 2>&1 | tail -5 || true
fi

# 7. Install Node deps if console/package.json exists
if [ -f "${REPO_ROOT}/console/package.json" ]; then
  echo "--- Installing Node dependencies (console/) ---"
  cd "${REPO_ROOT}/console"
  npm install --silent 2>&1 | tail -3 || true
  cd "$REPO_ROOT"
fi

# 8. Make hook scripts executable
if [ -d "${REPO_ROOT}/.claude/hooks" ]; then
  chmod +x "${REPO_ROOT}/.claude/hooks/"*.sh 2>/dev/null || true
  echo "  Hooks made executable."
fi

# 9. Initialize .claude/state/ for hook bookkeeping
mkdir -p "${REPO_ROOT}/.claude/state"
[ -f "${REPO_ROOT}/.claude/state/spend.json" ] || echo '{"total_usd": 0, "by_day": {}}' > "${REPO_ROOT}/.claude/state/spend.json"

# 10. Final report
echo ""
echo "=== Codespace ready ==="
echo "NEXT STEPS for the operator:"
echo "  1. Verify auth: claude --version (should print version)"
echo "  2. Check repo state: git status && git log -1 --oneline"
echo "  3. Read docs/partner_handoff.md 'Current build state' section"
echo "  4. When Partner has refactored the next prompt to prompts/_active/, start:"
echo "       claude   (interactive)"
echo "     and paste the executor invocation per 02-OPERATE.md"
echo ""
echo "If anything is missing or fails, see 06-TROUBLESHOOTING.md."
