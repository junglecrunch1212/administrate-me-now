#!/usr/bin/env bash
#
# scripts/verify_invariants.sh — canonical invariant-grep script.
#
# Checks the single OpenClaw LLM seam, instance-path discipline, content-free
# tenant-neutrality scanning, projection emit allowlists, and the prohibition
# on direct pipeline writes into projections.

set -u

if [ ! -f "pyproject.toml" ] || [ ! -d "adminme" ]; then
    echo "verify_invariants.sh: must be run from repo root" >&2
    exit 2
fi

violations=0
report() {
    echo ""
    echo "VIOLATION: $1"
    violations=$((violations + 1))
}

# ─── [§8] / [D6]: no LLM / embedding SDK imports in adminme/ ─────────────

if grep -rnE "^\s*(import|from)\s+(anthropic|openai|sentence_transformers)(\.|,|\s+as\s+|\s+import\s+|\s*#|\s*$)" adminme/ 2>/dev/null; then
    report "[§8] / [D6] LLM/embedding SDK import in adminme/"
fi

if grep -iE "^(anthropic|openai|sentence-transformers)\s*=|^(anthropic|openai|sentence-transformers)\s*>=" pyproject.toml 2>/dev/null; then
    report "[§8] / [D6] LLM/embedding SDK declared as adminme dependency"
fi

# ─── [§15] / [D15]: no hardcoded instance paths ─────────────────────────

hardcoded=$(grep -rnE "(~/\.adminme|'/\.adminme|\"/\.adminme)" \
    adminme/ bootstrap/ packs/ \
    --include='*.py' --include='*.sh' 2>/dev/null \
    | grep -v "bootstrap/install\.sh" \
    | grep -v "bootstrap/wizard\.py" \
    || true)
if [ -n "$hardcoded" ]; then
    report "[§15] / [D15] hardcoded instance path:"
    printf '%s\n' "$hardcoded" | awk '{print "    " $0}'
fi

# ─── [§12.4]: no tenant identity in platform code, built-ins, or tests ───
#
# Household literals belong only in an owner-only file outside the repository.
# Hosted/static use stays credential-free and structural. The local release
# tier sets ADMINME_REQUIRE_PRIVATE_IDENTITY_PATTERNS=1 and fails closed unless
# the categorized owner profile is present. Results never echo matched content.

identity_command=(poetry run python -m scripts.identity_scan --root .)
if [ -n "${ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE:-}" ]; then
    identity_command+=(--patterns-file "$ADMINME_PRIVATE_IDENTITY_PATTERNS_FILE")
fi
if [ "${ADMINME_REQUIRE_PRIVATE_IDENTITY_PATTERNS:-0}" = "1" ]; then
    identity_command+=(--require-private-patterns)
fi
if ! "${identity_command[@]}"; then
    report "[§12.4] tenant identity coordinates detected"
fi

# ─── [§2.2]: projections emit only allowed system events ────────────────

ALLOWED_EMITS='xlsx\.regenerated|xlsx\.reverse_projected|xlsx\.reverse_skipped_during_forward'
ALLOWED_EMIT_FILES=(
    "adminme/projections/xlsx_workbooks/__init__.py"
)

emit_files=$(grep -rlE "log\.append\(|append\(\s*EventEnvelope" adminme/projections/ 2>/dev/null || true)

if [ -n "$emit_files" ]; then
    while IFS= read -r f; do
        allowed=false
        for af in "${ALLOWED_EMIT_FILES[@]}"; do
            if [ "$f" = "$af" ]; then
                allowed=true
                break
            fi
        done
        if [ "$allowed" = "false" ]; then
            report "[§2.2] projection emitting events from disallowed file: $f"
            continue
        fi
        bad_types=$(grep -nE 'type\s*=\s*"[^"]+"' "$f" 2>/dev/null \
            | grep -vE "type\s*=\s*\"($ALLOWED_EMITS)\"" \
            || true)
        if [ -n "$bad_types" ]; then
            report "[§2.2] projection emitting non-allowed event type in $f:"
            printf '%s\n' "$bad_types" | awk '{print "    " $0}'
        fi
    done <<< "$emit_files"
fi

# ─── Skill-runner single-seam emits (09a) ───────────────────────────────

SKILL_EMITS='skill\.call\.recorded|skill\.call\.failed|skill\.call\.suppressed'

skill_violations=$(grep -rnE "type\s*=\s*\"($SKILL_EMITS)\"" \
    adminme/ --include='*.py' 2>/dev/null \
    | grep -vE "^adminme/+lib/skill_runner/wrapper\.py:" \
    || true)
if [ -n "$skill_violations" ]; then
    report "[§7]/[ADR-0002] skill.call.* emitted outside skill_runner wrapper:"
    printf '%s\n' "$skill_violations" | awk '{print "    " $0}'
fi

# ─── Pipeline → projection direct writes ────────────────────────────────

if grep -rnE "INSERT INTO.*projection|projection_db.*write|from adminme\.projections\.[a-z_]+\.handlers" \
    adminme/pipelines/ --include='*.py' 2>/dev/null > /dev/null; then
    report "pipeline writing projection directly (use events, not direct writes)"
fi

if [ "$violations" -gt 0 ]; then
    echo ""
    echo "$violations invariant violation(s) found. Fix before commit."
    exit 1
fi

exit 0
