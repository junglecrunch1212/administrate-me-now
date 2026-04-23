# Console routes

One file per nav surface (per ADMINISTRATEME_CONSOLE_PATTERNS.md §7
`NAV_ITEMS`): `today.js`, `inbox.js`, `crm.js`, `capture.js`, `finance.js`,
`calendar.js`, `scoreboard.js`, `settings.js`, plus `chat.js` (SSE
pass-through per §5) and `reward_stream.js` (SSE reward channel per §8).

Each route file authenticates via the session middleware, authorizes via
guardedWrite, and proxies to the appropriate Python product API via the
HTTP bridge (CONSOLE_PATTERNS.md §10). Child sessions hit
`CHILD_BLOCKED_API_PREFIXES` server-side before reaching route handlers
(CONSOLE_PATTERNS.md §7).

Filled in by prompt 14.
