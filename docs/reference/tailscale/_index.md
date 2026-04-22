# Tailscale documentation

**Purpose in this build:** Tailscale is the tailnet connecting the Mac Mini, optional Vault VPS, and family devices. The Node console (L5) uses Tailscale identity headers for authentication (CONSOLE_PATTERNS.md §2). Tailscale Serve exposes the console on the tailnet; Funnel exposes Plaid webhook endpoints publicly.

**Source:** https://tailscale.com/docs/features/ (the `/kb/` URLs referenced in the original gap doc have all redirected into `/docs/features/`).

**Fetched:** 2026-04-22

**License:** Tailscale documentation — reference only; not for redistribution.

**Method:** Manual Chrome clip via Claude Cowork (tailscale.com is not on the sandbox egress allowlist).

## Files mirrored

- `serve.md` — Tailscale Serve: proxy local traffic to tailnet devices with HTTPS certs and identity headers.
- `funnel.md` — Tailscale Funnel: expose a local service publicly via Funnel relay servers (TCP + TLS).
- `acls.md` — Tailnet policy file: HuJSON sections (`acls`, `grants`, `groups`, `hosts`, `ipsets`, `tagOwners`, `autoApprovers`, `nodeAttr`, `postures`, `ssh`, `sshTests`, `tests`) plus network options.
- `exit-nodes.md` — Exit nodes: route all internet traffic through a device, advertisement/approval, destination logging, expired-key fail-close behavior.
- `webhooks.md` — Webhooks: setup, events table, payload schema, retry policy, HMAC-SHA256 `Tailscale-Webhook-Signature` verification.
- `identity-headers.md` — Identity headers: `Tailscale-User-Login`, `Tailscale-User-Name`, `Tailscale-User-Profile-Pic`, `Tailscale-App-Capabilities`. (The standalone KB article has been retired; content now lives as a section inside the Serve page. Section clip with redirect note in frontmatter.)

## How to use for build questions

- "What headers does Tailscale Serve inject?" → `identity-headers.md` (CONSOLE_PATTERNS.md §2 contract derives from this).
- "How do I expose the Plaid webhook endpoint publicly?" → `funnel.md`.
- "How do I restrict tailnet access to specific family devices?" → `acls.md`.
- "How do webhook signatures work?" → `webhooks.md`.

## Downstream impact (cleared gap)

Resolves the LOW-MEDIUM-priority gap previously documented in `../_gaps.md`. CONSOLE_PATTERNS.md §2 remains authoritative for the Phase A identity-resolution contract; this mirror is the source that CONSOLE_PATTERNS.md §2 was written against.

## Refresh

Semi-annual cadence via manual clip. Tailscale migrated from `/kb/<id>/` URLs to `/docs/features/` between 2024–2026; if URLs change again, update the canonical paths.
