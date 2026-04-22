# Apple Shortcuts documentation — GAP

**Status:** NOT MIRRORED. Apple does not publish documentation source publicly, and `support.apple.com` / `developer.apple.com` are not on the sandbox egress allowlist.

**Purpose in this build:** Mobile Shortcuts webhooks (L5 "Capture" product) let household members trigger AdministrateMe flows from iOS/iPadOS via the Shortcuts app — for quick capture, reminder dispatch confirmation, and the reward toast. Orientation-level only; the HTTP shape of what Shortcuts posts is straightforward (POST JSON to a Tailscale-protected endpoint) and documented in `ADMINISTRATEME_BUILD.md` and `ADMINISTRATEME_REFERENCE_EXAMPLES.md` independently.

**Priority:** LOW. Build can proceed without this. The Shortcuts guide is user-orientation; no Phase A code decision depends on it.

## Optional resolution

If the operator wants this mirrored for reference:

| URL | Target file |
|-----|-------------|
| https://support.apple.com/guide/shortcuts/welcome/ios | `guide-overview.md` |
| https://support.apple.com/guide/shortcuts/intro-to-shortcut-actions-apd07c25bb38/ios | `actions.md` |
| https://support.apple.com/guide/shortcuts/get-contents-of-url-action-apd58d46713f/ios | `get-contents-of-url.md` |

Use the same manual clipping procedure described in `../apple-eventkit/_index.md`. Estimated effort: ~5 minutes.

## Downstream impact

None for Phase A. Prompt 13b (Capture + Automation APIs) documents the webhook shape that Shortcuts will POST; the operator configures Shortcuts to match during Phase B.
