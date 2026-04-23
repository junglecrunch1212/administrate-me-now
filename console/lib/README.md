# Console libraries

Shared modules for the Express server per ADMINISTRATEME_CONSOLE_PATTERNS.md:

- `session.js` — Tailscale identity resolution + authMember/viewMember split
  (§1, §2). Only principals may set view-as; children cannot; ambient
  entities have no surface.
- `bridge.js` — HTTP bridge to Python APIs (§10). Canonical `BridgeError`
  shape, automatic tenant-header injection, correlation-ID propagation on
  every hop.
- `guardedWrite.js` — three-layer write gate (§3): agent allowlist →
  governance action_gate → sliding-window rate limit. Short-circuits on
  first denial; records which layer refused. `hard_refuse` is never
  overridable; `review` returns 202 `held_for_review`.
- `rate_limiter.js` — sliding-window limiter (§4). `web_chat` at 20/60s,
  `writes_per_minute` at 60/60s, plus per-endpoint windows.
- `privacy_filter.js` — calendar read-time redaction (§6). Allowlist-shaped
  `redactToBusy` so new `Event` fields do not accidentally leak.
- `nav.js` — HIDDEN_FOR_CHILD client-side nav list + server-side
  `CHILD_BLOCKED_API_PREFIXES` (§7). Client-side is UX; server-side is
  security.
- `observation.js` — final-outbound-filter wrapper (§11). Per-tenant;
  default-on for new instances.
- `client_fanout.js` — `UpstreamBusSubscriber` (one) + `ClientFanOut` (many)
  per DECISIONS.md §D2. One upstream subscription to Core's SSE endpoint;
  in-console fan-out to many browser tabs.

Filled in by prompt 14a.
