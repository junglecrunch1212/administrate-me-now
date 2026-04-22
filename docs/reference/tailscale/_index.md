# Tailscale documentation — PARTIAL GAP

**Status:** NOT MIRRORED. Tailscale does not publish KB docs source on GitHub, and `tailscale.com` is not on the sandbox egress allowlist.

**Purpose in this build:** Tailscale is the tailnet that connects the Mac Mini, the optional Vault VPS, and family devices. The Node console (L5) uses Tailscale identity headers (`Tailscale-User-Login`, etc.) for authentication — CONSOLE_PATTERNS.md §2 documents the identity-resolution contract in detail and is authoritative for the build. Tailscale Serve / Funnel / webhooks are referenced during Phase B bootstrap for exposing the console to family members' devices and the Mac Mini's LAN.

**Priority:** LOW to MEDIUM.
- Identity-header contract: already documented in-repo at `ADMINISTRATEME_CONSOLE_PATTERNS.md` §2. Build is not blocked.
- Serve / Funnel / TLS setup: referenced in Phase B bootstrap (prompt 16). Operator can cross-reference live docs at clip-time; Phase A prompts don't need the narrative.

## Resolution options (either, or skip)

### Option A — widen sandbox allowlist (preferred if available)

If the operator has control of the sandbox allowlist (local Claude Code with editable `~/.claude/settings.json`, or hosted with permissions negotiable), adding `tailscale.com` lets Claude Code re-fetch the six KB pages below in a future 00.5 refresh. The content is static and the KB serves well-behaved HTML.

### Option B — manual clip

| URL | Target file | Priority |
|-----|-------------|----------|
| https://tailscale.com/kb/1086/identity-headers | `identity-headers.md` | MEDIUM (confirms CONSOLE_PATTERNS.md §2) |
| https://tailscale.com/kb/1242/tailscale-serve | `serve.md` | MEDIUM (bootstrap wizard Section 7) |
| https://tailscale.com/kb/1223/funnel | `funnel.md` | LOW |
| https://tailscale.com/kb/1336/tailnet-policy-file | `acls.md` | LOW |
| https://tailscale.com/kb/1103/exit-nodes | `exit-nodes.md` | LOW |
| https://tailscale.com/kb/1185/webhooks | `webhooks.md` | LOW |

Use the same manual clipping procedure described in `../apple-eventkit/_index.md`.

## Option C — skip entirely

Phase A does not strictly require any Tailscale documentation beyond what's already in `ADMINISTRATEME_CONSOLE_PATTERNS.md`. Phase B bootstrap (prompt 16) can surface specific Tailscale CLI commands as needed; operator reads live docs at install time.

## Downstream impact

None for Phase A code. Prompt 16 (bootstrap wizard) mentions Tailscale commands the operator will run; those commands can be referenced from upstream docs at operator install time.
