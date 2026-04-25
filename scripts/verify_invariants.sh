#!/usr/bin/env bash
#
# scripts/verify_invariants.sh — canonical invariant-grep script.
#
# Run by every build prompt's Commit 4 verification block. Replaces the
# ~8–12 lines of inline invariant greps each prompt used to duplicate.
#
# Checks:
#   [§8] / [D6]    — no LLM/embedding SDK imports in adminme/
#                    (anthropic, openai, sentence_transformers)
#   [§15] / [D15]  — no hardcoded instance paths (~/.adminme, /.adminme)
#                    in adminme/, bootstrap/, packs/
#   [§12.4]        — no tenant identity (james/laura/charlie/stice/morningside)
#                    in adminme/ outside tests/ or fixture-marked lines
#   [§2.2]         — projections emit only allowed SYSTEM events, and only
#                    from allowed files
#   pipeline→projection — no direct projection writes from adminme/pipelines/
#
# Exit 0 if clean; exit 1 on any violation. Prints the offending lines so
# the operator can see what failed.
#
# Maintenance:
#   - When a new system event type is registered (e.g. xlsx.reverse_skipped
#     in 07c), append to ALLOWED_EMITS below.
#   - When a new projection earns the right to emit, append its file path
#     to ALLOWED_EMIT_FILES.
#   - When a new inviolable invariant is introduced, add a section here.
#     This script is the canonical place; do not duplicate checks in a
#     prompt's Verification block.

set -u  # undefined-variable = error. No `set -e`: we want to run every
        # check and aggregate, not fail-fast on the first violation.

# Run from repo root. If invoked elsewhere, make it explicit.
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
#
# Pattern anchors on legal Python import continuations (`.`, `,`, ` as `,
# ` import `, trailing ` #`, or end-of-line). This prevents docstring prose
# like "pipelines NEVER import anthropic / openai / any provider SDK" from
# tripping the canary, while still catching every syntactically valid
# import of a banned SDK.

if grep -rnE "^\s*(import|from)\s+(anthropic|openai|sentence_transformers)(\.|,|\s+as\s+|\s+import\s+|\s*#|\s*$)" adminme/ 2>/dev/null; then
    report "[§8] / [D6] LLM/embedding SDK import in adminme/"
fi

# Also fail if the banned SDKs appear as declared dependencies. OpenClaw is
# the only LLM client on the host — adminme/ takes no direct SDK dep.
if grep -iE "^(anthropic|openai|sentence-transformers)\s*=|^(anthropic|openai|sentence-transformers)\s*>=" pyproject.toml 2>/dev/null; then
    report "[§8] / [D6] LLM/embedding SDK declared as adminme dependency"
fi

# ─── [§15] / [D15]: no hardcoded instance paths ─────────────────────────
#
# Every instance-directory path must resolve through InstanceConfig.
# Bootstrap scripts and docs are allowed to mention ~/.adminme (bootstrap
# is the thing that *creates* the instance dir; docs describe it). Code
# under adminme/, bootstrap/*.py (non-install scripts), and packs/ is not.

hardcoded=$(grep -rnE "(~/\.adminme|'/\.adminme|\"/\.adminme)" \
    adminme/ bootstrap/ packs/ \
    --include='*.py' --include='*.sh' 2>/dev/null \
    | grep -v "bootstrap/install\.sh" \
    | grep -v "bootstrap/wizard\.py" \
    || true)
if [ -n "$hardcoded" ]; then
    report "[§15] / [D15] hardcoded instance path:"
    echo "$hardcoded" | sed 's/^/    /'
fi

# ─── [§12.4]: no tenant identity in platform code ────────────────────────
#
# These names are fixtures. Illustrative uses (docstrings that say "e.g.
# James the principal") are excluded by the fixture-marker carveout below.
# Platform code under adminme/ must not reference specific tenants; tests
# may, with the `# fixture:tenant_data:ok` marker on the relevant line
# where prudence warrants.

tenant_hits=$(grep -rniE "\b(james|laura|charlie|stice|morningside)\b" adminme/ --include='*.py' 2>/dev/null \
    | grep -v "tests/" \
    | grep -v "# fixture:tenant_data:ok" \
    | grep -v "# example" \
    | grep -v "# illustration" \
    || true)
if [ -n "$tenant_hits" ]; then
    report "[§12.4] tenant identity in platform code:"
    echo "$tenant_hits" | sed 's/^/    /'
fi

# ─── [§2.2]: projections emit only allowed system events ────────────────
#
# Maintenance-driven allowlists. When a projection earns the right to emit,
# add its file to ALLOWED_EMIT_FILES and its type string to ALLOWED_EMITS.

# When the reverse xlsx daemon (07c-β) lands, it lives at
# adminme/daemons/xlsx_sync/reverse.py — that path is OUTSIDE
# adminme/projections/, so ALLOWED_EMIT_FILES does NOT extend for it.
# This script only audits projection emits; the daemon's emits are
# governed by the schemas it appends, not by its file location. The
# allowlist below names the system event types a projection MAY emit.
ALLOWED_EMITS='xlsx\.regenerated|xlsx\.reverse_projected|xlsx\.reverse_skipped_during_forward'
ALLOWED_EMIT_FILES=(
    "adminme/projections/xlsx_workbooks/__init__.py"
)

# Find every projection file that contains an event-log append.
emit_files=$(grep -rlE "log\.append\(|append\(\s*EventEnvelope" adminme/projections/ 2>/dev/null || true)

if [ -n "$emit_files" ]; then
    while IFS= read -r f; do
        # Is this file in the allowed set?
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
        # File is allowed to emit. Confirm every type="..." in the file
        # matches ALLOWED_EMITS. Any other type= literal is a violation.
        bad_types=$(grep -nE 'type\s*=\s*"[^"]+"' "$f" 2>/dev/null \
            | grep -vE "type\s*=\s*\"($ALLOWED_EMITS)\"" \
            || true)
        if [ -n "$bad_types" ]; then
            report "[§2.2] projection emitting non-allowed event type in $f:"
            echo "$bad_types" | sed 's/^/    /'
        fi
    done <<< "$emit_files"
fi

# ─── Pipeline → projection direct writes ────────────────────────────────
#
# Lights up at prompt 10a. Vacuous until then (adminme/pipelines/ has no
# real code). Kept now so the check is in place when pipelines arrive.

if grep -rnE "INSERT INTO.*projection|projection_db.*write|from adminme\.projections\.[a-z_]+\.handlers" \
    adminme/pipelines/ --include='*.py' 2>/dev/null > /dev/null; then
    report "pipeline writing projection directly (use events, not direct writes)"
fi

# ─── Summary ────────────────────────────────────────────────────────────

if [ "$violations" -gt 0 ]; then
    echo ""
    echo "$violations invariant violation(s) found. Fix before commit."
    exit 1
fi

exit 0
