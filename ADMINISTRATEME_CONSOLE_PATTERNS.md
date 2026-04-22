# CONSOLE_PATTERNS.md

**Node/Express reference patterns for the AdministrateMe console.**
Tenant-agnostic. Port the patterns, not the code verbatim.

---

## How to read this document

Each section is a self-contained pattern. Format:

1. **What it is** — one sentence.
2. **Why it matters** — the failure mode if you skip or mis-implement it.
3. **Where it lives** — the file/folder in the console repo that owns it.
4. **The pattern** — working Node/Express code you can copy and adapt.
5. **Subtleties** — things that look like accidents but are load-bearing.
6. **Testing notes** — what your tests must cover.

All code uses:
- Node.js 22 LTS
- Express 4.x
- Native `fetch` (Node 20+)
- No TypeScript (the console is plain JS; TS lives in the `adminme/` Python side, where type discipline matters more)

Identifier conventions used below:
- `PRINCIPAL_ID` — a member of the household (e.g. a parent)
- `MEMBER_ID` — any resident: principal, child, ambient entity
- `TENANT_ID` — the installation (one household = one tenant = one SQLite database)
- `SESSION_ID` — one browser session

**None of these IDs are Stice-specific.** Claude Code's console should use these abstract names. Don't hardcode names, phone numbers, emails, or any other instance-specific data.

---

## Pattern index

1. [Tailscale identity resolution](#1-tailscale-identity-resolution)
2. [Session model and the authMember/viewMember split](#2-session-model-and-the-authmemberviewmember-split)
3. [guardedWrite: the three-layer write check](#3-guardedwrite-the-three-layer-write-check)
4. [RateLimiter: sliding window](#4-ratelimiter-sliding-window)
5. [SSE chat handler](#5-sse-chat-handler)
6. [Calendar privacy filtering](#6-calendar-privacy-filtering)
7. [HIDDEN_FOR_CHILD navigation filter](#7-hidden_for_child-navigation-filter)
8. [Reward toast emission](#8-reward-toast-emission)
9. [Degraded-mode fallback](#9-degraded-mode-fallback)
10. [HTTP bridge to Python APIs](#10-http-bridge-to-python-apis)
11. [Observation mode enforcement](#11-observation-mode-enforcement)
12. [Error handling and correlation IDs](#12-error-handling-and-correlation-ids)

---

## 1. Tailscale identity resolution

**What it is.** Every incoming request carries a Tailscale-injected header identifying the authenticated device user. The console trusts this header absolutely — Tailscale terminates at the edge — and converts it into a canonical household member ID.

**Why it matters.** The console has *no password-based auth*. There is no `/login` endpoint, no JWT, no session cookie containing principal identity. If you add one, you've created a second attack surface that has to be kept in sync with Tailscale's, and Tailscale's is already the source of truth. The household's security model is: if you're on the tailnet, you're authenticated. Period.

**Where it lives.** `console/middleware/identity.js`, called on *every* request as the first middleware after body parsing.

**The pattern.**

```js
// console/middleware/identity.js
//
// Resolves the Tailscale device identity into a household member.
// This is the ONLY identity source. No fallback auth.
//
// Header contract (set by Tailscale serve config, not forgeable from outside tailnet):
//   x-tailscale-user-login: alice@example.ts.net
//   x-tailscale-user-display-name: Alice Example
//   x-tailscale-user-profile-pic: https://...
//
// In dev we also honor x-fake-tailscale-login for tests; this is gated
// behind NODE_ENV === 'development' and a DEV_ALLOW_FAKE_TAILSCALE=1 flag.

const { getDb } = require('../db');

async function resolveIdentity(req, res, next) {
  const tsLogin =
    req.header('x-tailscale-user-login') ||
    (process.env.NODE_ENV === 'development' && process.env.DEV_ALLOW_FAKE_TAILSCALE === '1'
      ? req.header('x-fake-tailscale-login')
      : null);

  if (!tsLogin) {
    // No tailnet identity — refuse.
    // Do NOT redirect to a login page; there is no login page.
    // The user has routed outside the tailnet somehow (or Tailscale is down).
    return res.status(401).json({
      error: 'no_tailnet_identity',
      message: 'This console is accessible only from the household tailnet.',
    });
  }

  const db = getDb();

  // The identity projection maps tailscale_login → member_id.
  // Populated at bootstrap and when members are added/removed.
  // One login maps to exactly one member; reuse forbidden.
  const row = db
    .prepare(
      `SELECT member_id, role, profile_id, display_name
         FROM member_identities
        WHERE tailscale_login = ?
          AND revoked_at IS NULL`,
    )
    .get(tsLogin);

  if (!row) {
    // Valid tailnet, but unknown to this household. Guest device or
    // ex-member whose access was revoked. Emit an event so the
    // principal can see unauthorized access attempts.
    await emitSecurityEvent('unknown_tailscale_identity', { tsLogin, ip: req.ip });
    return res.status(403).json({
      error: 'member_not_found',
      message:
        'Your device is on the tailnet but not registered as a household member.',
    });
  }

  req.identity = {
    memberId: row.member_id,
    role: row.role,          // 'principal' | 'child' | 'ambient' | 'guest'
    profileId: row.profile_id,
    displayName: row.display_name,
    tailscaleLogin: tsLogin,
  };
  next();
}

module.exports = { resolveIdentity };
```

**Subtleties.**

- **The header is trusted absolutely.** Do not try to "verify" it with a second check — there's nothing to verify against. The threat model is: compromised tailnet = compromised everything. If you add a second factor (e.g. a session token), you've just created a way for the app to be authenticated while the tailnet is not, which is worse.
- **Dev override is gated by two conditions** (NODE_ENV + explicit flag), not one. This is deliberate. It's easy to `NODE_ENV=development` by accident in a prod-adjacent context; the second flag prevents that becoming an auth bypass.
- **`revoked_at IS NULL`** matters. When a coparent's ex-spouse's device leaves the tailnet, their row in `member_identities` gets a `revoked_at` timestamp. The row is not deleted — that would lose audit history — but access stops immediately.
- **Unknown identity ≠ 401.** An unknown-to-us-but-on-tailnet device is a 403, not a 401. The distinction matters for logging and for the error the user sees: 401 means "prove you're someone," 403 means "we know who you are and you can't be here."

**Testing notes.** Middleware unit tests should cover: missing header (401), unknown login (403 + event emitted), known active member (attaches identity), known revoked member (403), dev override with flag (allowed), dev override without flag (denied), production with dev header (denied regardless).

---

## 2. Session model and the authMember/viewMember split

**What it is.** Every request has two member contexts: the member who *authenticated* (from Tailscale) and the member they are *acting as* (from the view-as selector). These are almost always the same, but the split matters when a principal views another member's Today.

**Why it matters.** Without the split, you have one of two bad outcomes. Either (a) everything uses the authed member, and the "viewing as other-principal" dropdown is just cosmetic — but then the other principal's Today looks exactly like the viewer's because the backend reads data for the authed user. Or (b) everything uses the viewed member, and now the viewer can read the other principal's privileged work calendar by selecting them in the dropdown. Neither works.

The correct model: **authMember** governs *what you're allowed to do*; **viewMember** governs *whose surface data you're reading*; and the combination is checked against per-view ACLs.

**Where it lives.** `console/lib/session.js` (builds the session object); every route handler that reads member-scoped data (uses the session).

**The pattern.**

```js
// console/lib/session.js

/**
 * Session: the per-request context that every handler consumes.
 *
 * Fields:
 *   authMemberId  — from Tailscale. The principal making the request.
 *   authRole      — 'principal' | 'child' | 'ambient' | 'guest'
 *   viewMemberId  — the member whose Today/surface is being rendered.
 *                   Defaults to authMemberId. Set via ?view_as= query or
 *                   x-view-as header. Only principals can change it.
 *   viewRole      — profile role of viewMember (may differ from authRole)
 *   profileId     — profileId of viewMember (drives view rendering)
 *   tenantId      — household installation identifier
 *
 * Methods:
 *   isSelf()           — viewMemberId === authMemberId
 *   canAct()           — authRole === 'principal' (only principals write)
 *   canViewOther(id)   — whether authMember is allowed to view member `id`
 */

function buildSession(req) {
  const identity = req.identity; // set by resolveIdentity middleware
  const requestedView = req.query.view_as || req.header('x-view-as') || null;
  const db = require('../db').getDb();

  let viewMemberId = identity.memberId;
  let viewRole = identity.role;
  let profileId = identity.profileId;

  if (requestedView && requestedView !== identity.memberId) {
    // Principal requesting to view another member's surface.
    // Rule: only principals can view-as. Children and ambient entities
    // cannot (they don't see the view-as control anyway, but enforce
    // server-side regardless).
    if (identity.role !== 'principal') {
      throw new ForbiddenError('only_principals_can_view_as');
    }

    // Rule: view-as target must be a member of the same household.
    const target = db
      .prepare(
        `SELECT member_id, role, profile_id
           FROM members
          WHERE member_id = ?
            AND tenant_id = ?
            AND active = 1`,
      )
      .get(requestedView, identity.tenantId);

    if (!target) {
      throw new ForbiddenError('view_target_not_in_household');
    }

    // Rule: principal may view any non-ambient member's surface.
    // Ambient entities (e.g. a newborn's profile) have no surface to view.
    if (target.role === 'ambient') {
      throw new ForbiddenError('view_target_has_no_surface');
    }

    viewMemberId = target.member_id;
    viewRole = target.role;
    profileId = target.profile_id;
  }

  return {
    authMemberId: identity.memberId,
    authRole: identity.role,
    viewMemberId,
    viewRole,
    profileId,
    tenantId: identity.tenantId,
    isSelf: () => viewMemberId === identity.memberId,
    canAct: () => identity.role === 'principal',
    canViewOther: (otherId) => {
      if (identity.role !== 'principal') return false;
      return db
        .prepare(
          'SELECT 1 FROM members WHERE member_id = ? AND tenant_id = ? AND role != ?',
        )
        .get(otherId, identity.tenantId, 'ambient') != null;
    },
  };
}

class ForbiddenError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
    this.status = 403;
  }
}

module.exports = { buildSession, ForbiddenError };
```

Usage in a route handler:

```js
// console/routes/today.js
const { buildSession } = require('../lib/session');

router.get('/api/today', async (req, res, next) => {
  try {
    const session = buildSession(req);
    // Read data for viewMember (whose surface we're rendering)
    const tasks = await fetchTodayTasks(session.tenantId, session.viewMemberId);
    // ...but enforce privacy filtering based on authMember (the one
    // actually looking at the screen). This is how privileged events
    // show as "[busy]" when principalA views-as principalB: data is B's,
    // privacy filter is "what can authMember=A see of B's data?"
    const filtered = applyPrivacyFilter(tasks, {
      authMember: session.authMemberId,
      viewMember: session.viewMemberId,
    });
    res.json({ tasks: filtered, session_meta: redactSession(session) });
  } catch (err) {
    next(err);
  }
});
```

**Subtleties.**

- **Write endpoints ignore viewMember.** A write is always "performed by authMember." There is no valid "write as another member" operation. If principal A has principal B's view open and clicks a task complete, the `task.completed` event is emitted with `actor_member_id = A`, not B. This is surprising at first — "but the task is B's!" — and correct: A did the action.
- **Two-member commitments need both IDs.** If principal A is viewing principal B's inbox and approving a commitment that B owes to an outside party, the resulting `commitment.confirmed` event captures `approved_by = A` *and* `owner = B`. Separate fields. Do not collapse.
- **`isSelf()` is the fast path.** Most views don't need the view-as logic — the user is looking at their own data. Handlers should branch `if (session.isSelf()) { ...fast path... }` to avoid loading a second profile and running cross-member ACL checks every time.
- **Children don't have view-as.** Enforce server-side regardless of what the UI does. The console UI hides the dropdown for children, but if a child sends `?view_as=<parent>` the server rejects 403.
- **Coparent non-user members.** A coparent like Mike appears as a Party but has no login, no profile, no agent binding. `view-as=mike` fails at the "active = 1" check (he has no member row) or at the role check (if he's a Party, not a Member). The session model only deals with Members.

**Testing notes.** Cover: self-view (happy path), principal viewing another principal, principal viewing child (allowed), principal viewing ambient (denied), child attempting view-as (denied regardless of target), non-existent target, target in a different household (denied — the `tenant_id = ?` filter). Also test that write endpoints correctly use authMember, not viewMember.

---

## 3. guardedWrite: the three-layer write check

**What it is.** Every write performed by the console (or by a pipeline via the console) passes through one function that runs three checks in order: agent allowlist (is this agent permitted to do this action?), governance action_gate (is the action globally gated by household rules?), and rate limit (have we exceeded the sliding window?). If any check fails, the write is rejected and an appropriate event is recorded.

**Why it matters.** Writes are where harm happens: sending the wrong message to the wrong party, DMing opposing counsel, auto-replying during a privileged thread, spamming the principal with rewards, running a skill loop that writes 10,000 events in 10 minutes. Each of the three layers catches a different failure mode, and they must be checked in order: allowlist first (is the agent even allowed to *think* about this?), gate second (is the *action* permitted given governance?), rate third (we'd permit this but we've had too many recently).

Getting the order wrong means either (a) you rate-limit requests that shouldn't have been allowed (wastes rate budget) or (b) you log allowed-then-gated actions as if they were allowed (audit noise).

**Where it lives.** `console/lib/guarded_write.js`. Called by every write endpoint and by pipelines via the HTTP bridge before the bridge forwards to Python.

**The pattern.**

```js
// console/lib/guarded_write.js
//
// Three-layer write check. All writes pass through this.
//
// Layer 1: agent allowlist
//   Each agent (assistant persona, skill, pipeline, user) has an allowlist
//   of actions it may even attempt. This is the coarsest filter.
//   Example: the 'reward_dispatch' pipeline is allowed to perform
//   'reward.send' and 'reward.suppressed' but NOT 'email.send'.
//
// Layer 2: governance action_gate
//   Per-action policy from authority.yaml. Can be 'allow', 'review',
//   'deny', 'hard_refuse'. Certain actions (e.g. 'send_as_principal')
//   are hard_refuse and cannot be overridden even by principals.
//
// Layer 3: rate limit
//   Sliding window per (scope, action). Protects against loops,
//   accidental spam, runaway pipelines.

const { getDb } = require('../db');
const { getRateLimiter } = require('./rate_limiter');
const { loadGovernance } = require('./governance');

/**
 * @param {object} ctx
 * @param {string} ctx.agentId       — who is trying to write
 *                                     ('user:member_abc', 'skill:foo@v2',
 *                                      'pipeline:commitment_extraction', ...)
 * @param {string} ctx.action         — qualified action name
 *                                     ('message.send', 'task.create',
 *                                      'commitment.confirm', ...)
 * @param {string} ctx.tenantId
 * @param {string} ctx.scope          — rate-limit key scope
 *                                     (typically 'global' or a principal id)
 * @param {object} ctx.payload        — the write's payload
 * @param {function} writeFn          — the function that actually performs
 *                                     the write if all layers pass
 *
 * @returns {Promise<{ok: boolean, result?: any, denied_at?: string, reason?: string}>}
 */
async function guardedWrite(ctx, writeFn) {
  const correlationId = makeCorrelationId();

  // ---- Layer 1: agent allowlist ----
  const allowed = await agentAllowsAction(ctx.agentId, ctx.action, ctx.tenantId);
  if (!allowed) {
    await recordDenial({
      correlationId,
      agentId: ctx.agentId,
      action: ctx.action,
      layer: 'allowlist',
      reason: 'agent_not_permitted_for_action',
      tenantId: ctx.tenantId,
    });
    return {
      ok: false,
      denied_at: 'allowlist',
      reason: 'agent_not_permitted_for_action',
    };
  }

  // ---- Layer 2: governance action_gate ----
  const gov = loadGovernance(ctx.tenantId);
  const gate = gov.action_gates[ctx.action] || 'allow';

  if (gate === 'hard_refuse') {
    await recordDenial({
      correlationId,
      agentId: ctx.agentId,
      action: ctx.action,
      layer: 'governance',
      reason: 'hard_refuse_by_governance',
      tenantId: ctx.tenantId,
    });
    return {
      ok: false,
      denied_at: 'governance',
      reason: 'hard_refuse',
    };
  }

  if (gate === 'deny') {
    await recordDenial({
      correlationId,
      agentId: ctx.agentId,
      action: ctx.action,
      layer: 'governance',
      reason: 'denied_by_governance',
      tenantId: ctx.tenantId,
    });
    return {
      ok: false,
      denied_at: 'governance',
      reason: 'deny',
    };
  }

  // 'review' means: action is held in a review queue, not fired.
  // The write function is NOT called; a review_request event is emitted.
  if (gate === 'review') {
    await emitReviewRequest({
      correlationId,
      agentId: ctx.agentId,
      action: ctx.action,
      payload: ctx.payload,
      tenantId: ctx.tenantId,
    });
    return {
      ok: false,
      denied_at: 'governance',
      reason: 'held_for_review',
      review_id: correlationId,
    };
  }

  // ---- Layer 3: rate limit ----
  const limiter = getRateLimiter();
  const limit = limiter.check({
    key: `${ctx.tenantId}:${ctx.scope}:${ctx.action}`,
    window_s: gov.rate_limits[ctx.action]?.window_s || 60,
    max: gov.rate_limits[ctx.action]?.max || 60,
  });

  if (!limit.allowed) {
    await recordDenial({
      correlationId,
      agentId: ctx.agentId,
      action: ctx.action,
      layer: 'rate_limit',
      reason: 'rate_limit_exceeded',
      retry_after_s: limit.retry_after_s,
      tenantId: ctx.tenantId,
    });
    return {
      ok: false,
      denied_at: 'rate_limit',
      reason: 'rate_limit_exceeded',
      retry_after_s: limit.retry_after_s,
    };
  }

  // ---- All three passed. Perform the write. ----
  try {
    const result = await writeFn({ correlationId });
    await recordSuccess({
      correlationId,
      agentId: ctx.agentId,
      action: ctx.action,
      tenantId: ctx.tenantId,
    });
    return { ok: true, result, correlation_id: correlationId };
  } catch (err) {
    await recordWriteFailure({
      correlationId,
      agentId: ctx.agentId,
      action: ctx.action,
      error: err.message,
      tenantId: ctx.tenantId,
    });
    throw err; // bubble up to Express error handler
  }
}

async function agentAllowsAction(agentId, action, tenantId) {
  const db = getDb();
  // agent_allowlist is populated from pack manifests at install time.
  // Rows: (agent_id, action_pattern). Patterns support glob: 'message.*'.
  const rows = db
    .prepare(
      `SELECT action_pattern
         FROM agent_allowlist
        WHERE agent_id = ?
          AND tenant_id = ?`,
    )
    .all(agentId, tenantId);
  return rows.some((r) => matchPattern(r.action_pattern, action));
}

function matchPattern(pattern, action) {
  if (pattern === action) return true;
  if (pattern.endsWith('.*')) {
    const prefix = pattern.slice(0, -2);
    return action.startsWith(prefix + '.');
  }
  return false;
}

function makeCorrelationId() {
  return `w_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

// recordDenial / recordSuccess / recordWriteFailure / emitReviewRequest
// all emit events to the event log via the HTTP bridge.

module.exports = { guardedWrite };
```

Usage:

```js
router.post('/api/tasks/complete', async (req, res, next) => {
  try {
    const session = buildSession(req);
    if (!session.canAct()) throw new ForbiddenError('write_requires_principal');

    const result = await guardedWrite(
      {
        agentId: `user:${session.authMemberId}`,
        action: 'task.complete',
        tenantId: session.tenantId,
        scope: session.authMemberId,
        payload: { task_id: req.body.task_id },
      },
      async ({ correlationId }) => {
        return await bridge.post('/core/tasks/complete', {
          task_id: req.body.task_id,
          actor_member_id: session.authMemberId,
          correlation_id: correlationId,
        });
      },
    );

    if (!result.ok) {
      const status =
        result.denied_at === 'rate_limit'
          ? 429
          : result.denied_at === 'governance'
          ? 403
          : 403;
      return res.status(status).json({
        error: result.reason,
        denied_at: result.denied_at,
        ...(result.retry_after_s && { retry_after_s: result.retry_after_s }),
      });
    }

    res.json(result.result);
  } catch (err) {
    next(err);
  }
});
```

**Subtleties.**

- **Layers are ordered; denials short-circuit.** A write denied at layer 1 does not touch layers 2 or 3. This keeps rate budget clean (you don't burn a token on a request that wasn't allowed to happen) and makes audit logs readable (each denial has exactly one layer recorded).
- **`review` is not a denial in the ethical sense.** It's "held for a principal to decide." The UI surfaces it in an approval inbox. The correlation ID returned becomes the review request ID.
- **Allowlist uses glob patterns.** `message.*` matches `message.send`, `message.draft`, etc. Do not use full regex — the surface area is too wide and pack authors can accidentally over-match.
- **Rate limits are per-action, not per-agent.** `email.send` has a limit; that limit is shared across all agents that might call it. This is deliberate: the household's budget for "emails sent today" shouldn't be gameable by spinning up a new skill.
- **`writeFn` is only called if all three layers pass.** Inside `writeFn`, the correlation ID is available and should be stamped on every event emitted by the write. This gives you post-hoc trace: one correlation_id → one check result → one write → one set of downstream events.
- **Never return the raw governance reason to the user.** `hard_refuse` and `deny` both look the same to the caller: 403 with `error: 'hard_refuse'` or `error: 'deny'`. The deeper reason lives in the audit event, not the API response.

**Testing notes.** Cover each layer's denial path with its own test. Cover `review` → review_request emitted, writeFn NOT called. Cover the happy path where all three pass. Cover the writeFn throwing; verify `recordWriteFailure` was called and the error propagated. Cover rate-limit bucket isolation (two principals' limits should not interfere).

---

## 4. RateLimiter: sliding window

**What it is.** A single-process in-memory rate limiter using the sliding log approach. Keys are strings; each key has a max count and a window in seconds. Returns `{allowed, retry_after_s}`.

**Why it matters.** Fixed-window limiters have a burstiness bug: at the window boundary, a caller can send `2 * max` requests in a short span (max at end of window N, max at start of window N+1). Sliding window fixes this but is more expensive; for household-scale traffic (a few hundred writes per minute max), the cost is irrelevant.

The console is single-process (one Node.js instance per household), so in-memory is fine. If you ever shard the console, move this to Redis or a similar — but you almost certainly won't shard the console, because a household's traffic fits in one process.

**Where it lives.** `console/lib/rate_limiter.js`. Instantiated once at boot; reused across all guardedWrite calls.

**The pattern.**

```js
// console/lib/rate_limiter.js
//
// Sliding window rate limiter, per-key, in-memory.
//
// Semantics:
//   - Each key has its own bucket (array of timestamps).
//   - On check, prune timestamps older than `window_s` seconds.
//   - If count < max, append now and allow.
//   - If count >= max, deny with retry_after = (oldest + window) - now.
//
// Memory management:
//   - Buckets empty naturally as timestamps age out, but an unused
//     key never gets revisited and will grow to max and stay there.
//   - Every 5 minutes, sweep: remove any bucket where the newest
//     timestamp is > 2 * max_known_window old.

class RateLimiter {
  constructor() {
    this.buckets = new Map(); // key -> array of timestamps (ms)
    this.maxKnownWindow = 60;
    setInterval(() => this.sweep(), 5 * 60 * 1000).unref();
  }

  /**
   * @param {object} params
   * @param {string} params.key
   * @param {number} params.window_s  — window size in seconds
   * @param {number} params.max       — max calls in that window
   * @returns {{allowed: boolean, retry_after_s?: number}}
   */
  check({ key, window_s, max }) {
    this.maxKnownWindow = Math.max(this.maxKnownWindow, window_s);
    const now = Date.now();
    const cutoff = now - window_s * 1000;

    let bucket = this.buckets.get(key);
    if (!bucket) {
      bucket = [];
      this.buckets.set(key, bucket);
    }

    // In-place prune (cheaper than filter when bucket stays small)
    while (bucket.length > 0 && bucket[0] < cutoff) {
      bucket.shift();
    }

    if (bucket.length < max) {
      bucket.push(now);
      return { allowed: true };
    }

    const retry_ms = bucket[0] + window_s * 1000 - now;
    return {
      allowed: false,
      retry_after_s: Math.ceil(retry_ms / 1000),
    };
  }

  sweep() {
    const staleCutoff = Date.now() - this.maxKnownWindow * 2 * 1000;
    for (const [key, bucket] of this.buckets) {
      if (bucket.length === 0 || bucket[bucket.length - 1] < staleCutoff) {
        this.buckets.delete(key);
      }
    }
  }
}

let instance = null;
function getRateLimiter() {
  if (!instance) instance = new RateLimiter();
  return instance;
}

module.exports = { RateLimiter, getRateLimiter };
```

**Subtleties.**

- **Shift vs. filter.** `bucket.shift()` is O(n) but buckets are small (≤ max, typically ≤ 100). `bucket.filter(t => t >= cutoff)` allocates a new array every call, which is worse at household scale. Keep shift.
- **`setInterval().unref()` is mandatory.** Without `.unref()`, the sweep timer keeps the Node process alive indefinitely, preventing clean shutdown on SIGTERM.
- **retry_after_s is rounded up.** If 1.2 seconds remain, return 2, not 1. If you round down, clients retry too early and hit the limit again.
- **Boot-time reset is intentional.** When the console restarts, all rate limits reset. This is acceptable at household scale because: (a) restarts are rare, (b) if a runaway loop was the cause of a rate-limit hit, the restart likely killed the loop too. If you cared about surviving restarts, you'd persist — but you don't need to here.
- **The key is constructed by the caller, not the limiter.** `{tenantId}:{scope}:{action}` is the convention guardedWrite uses. The limiter itself is action-agnostic; it's a pure sliding-window primitive.

**Testing notes.** Cover: first call allowed, max-th call allowed, (max+1)th denied with correct retry_after, after window passes denial becomes allow, concurrent calls to different keys are independent, sweep removes stale buckets but keeps active ones. Use fake timers (Sinon or Node's `--experimental-test-module-mocks`) to simulate time passing.

---

## 5. SSE chat handler

**What it is.** The chat FAB on the console streams assistant responses token-by-token over Server-Sent Events. The handler must stream from a Python endpoint (which in turn streams from Anthropic or another LLM provider), manage backpressure, handle client disconnects, and emit structured events (text chunks, tool-use starts, tool-use results, turn completion).

**Why it matters.** If you buffer the entire response before sending, the chat feels broken (latency dominates UX for any reply longer than a sentence). If you don't handle disconnect properly, you leak Python HTTP connections — and after enough leaks, the Python side stops accepting requests and the console goes dark. If you don't frame the events, the client can't distinguish text from tool-use from errors.

**Where it lives.** `console/routes/chat.js`, backed by `console/lib/bridge.js` (for the upstream connection to **OpenClaw's gateway at :18789**, not the Python automation API). The chat pane in the console proxies to OpenClaw so that chat turns inside the console have the same session/memory/skills/standing-orders context as iMessage/Telegram/Discord chat turns. AdministrateMe's Python APIs (:3333-:3336) do not host chat — they host tasks, comms, capture, automation as domain resources.

**The pattern.**

```js
// console/routes/chat.js

const router = require('express').Router();
const { buildSession } = require('../lib/session');
const { bridge } = require('../lib/bridge');
const { guardedWrite } = require('../lib/guarded_write');

// SSE requires specific response headers. No body parser on this route.
router.post('/api/chat/stream', express.json({ limit: '64kb' }), async (req, res) => {
  let session;
  try {
    session = buildSession(req);
  } catch (err) {
    return res.status(err.status || 500).json({ error: err.code || 'session_error' });
  }

  const { text, session_id } = req.body;
  if (!text || typeof text !== 'string' || text.length > 8000) {
    return res.status(400).json({ error: 'invalid_text' });
  }

  // Guarded write: is this user/agent allowed to send a chat message?
  const writeCheck = await guardedWrite(
    {
      agentId: `user:${session.authMemberId}`,
      action: 'chat.message',
      tenantId: session.tenantId,
      scope: session.authMemberId,
      payload: { text_len: text.length, session_id },
    },
    async () => ({ ok: true }),
  );
  if (!writeCheck.ok) {
    const status = writeCheck.denied_at === 'rate_limit' ? 429 : 403;
    return res.status(status).json({
      error: writeCheck.reason,
      ...(writeCheck.retry_after_s && { retry_after_s: writeCheck.retry_after_s }),
    });
  }

  // SSE headers
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no', // disables proxy buffering (nginx etc.)
  });

  // Helper: send a framed SSE event
  const sendEvent = (type, data) => {
    if (res.writableEnded) return;
    res.write(`event: ${type}\n`);
    res.write(`data: ${JSON.stringify(data)}\n\n`);
  };

  // Heartbeat: keep the connection warm (some proxies close idle SSE).
  const heartbeat = setInterval(() => {
    if (res.writableEnded) return;
    res.write(': heartbeat\n\n');
  }, 15000);
  heartbeat.unref();

  // Connect to the Python automation SSE endpoint.
  // Use AbortController so a client disconnect cancels the upstream.
  const abortCtrl = new AbortController();
  let upstream;

  req.on('close', () => {
    clearInterval(heartbeat);
    abortCtrl.abort();
    if (upstream && !upstream.bodyUsed) {
      // body may already be consumed; silent-fail here is fine.
      try { upstream.body.cancel(); } catch {}
    }
  });

  try {
    upstream = await fetch('http://127.0.0.1:18789/agent/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Tenant-Id': session.tenantId,
        'X-Auth-Member-Id': session.authMemberId,
        'X-View-Member-Id': session.viewMemberId,
      },
      body: JSON.stringify({ text, session_id, auth_role: session.authRole }),
      signal: abortCtrl.signal,
    });
  } catch (err) {
    sendEvent('error', { code: 'upstream_connect_failed', message: err.message });
    clearInterval(heartbeat);
    return res.end();
  }

  if (!upstream.ok) {
    sendEvent('error', {
      code: 'upstream_http_error',
      status: upstream.status,
    });
    clearInterval(heartbeat);
    return res.end();
  }

  // Relay upstream SSE to client. Upstream frames events identically;
  // we forward each chunk as-is.
  const reader = upstream.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      buffer += chunk;

      // Forward complete SSE frames (double-newline terminated).
      // Partial frame stays in buffer.
      let frameEnd;
      while ((frameEnd = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, frameEnd + 2);
        buffer = buffer.slice(frameEnd + 2);
        if (!res.writableEnded) res.write(frame);
      }
    }
    if (buffer && !res.writableEnded) res.write(buffer);
    if (!res.writableEnded) sendEvent('done', { ok: true });
  } catch (err) {
    if (err.name !== 'AbortError') {
      sendEvent('error', { code: 'stream_error', message: err.message });
    }
  } finally {
    clearInterval(heartbeat);
    if (!res.writableEnded) res.end();
  }
});

module.exports = router;
```

Client-side consumption:

```js
// client: opens an EventSource-like POST stream via fetch + ReadableStream
async function streamChat(text, sessionId, onEvent) {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, session_id: sessionId }),
  });
  if (!resp.ok) {
    onEvent({ type: 'error', status: resp.status });
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let frameEnd;
    while ((frameEnd = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, frameEnd);
      buffer = buffer.slice(frameEnd + 2);
      const parsed = parseSseFrame(frame);
      if (parsed) onEvent(parsed);
    }
  }
}
```

**Subtleties.**

- **`X-Accel-Buffering: no` matters even without nginx.** Tailscale's reverse proxy and some iOS middleware buffer responses by default; the header tells them not to.
- **Heartbeat interval of 15s, not 30s.** Some proxies close after 30s idle. 15s is conservative and costs nothing (a comment-only SSE frame is ~12 bytes).
- **AbortController on the upstream is the critical piece.** Without it, a client disconnect leaks the upstream HTTP connection for the full LLM response duration (can be 60+ seconds). Do 10 of those and the Python process runs out of connection slots.
- **`res.writableEnded` check before every write.** Between the `done` check and the actual `res.write`, the client can disconnect; Node will throw EPIPE. The check is not optional.
- **Frame the events, don't just dump text.** The upstream sends `event: text`, `event: tool_use`, `event: tool_result`, `event: done`, `event: error`. Forward them verbatim. The client needs to distinguish "add these tokens to the bubble" from "the assistant is now searching Gmail" — you cannot reconstruct the distinction from a raw text stream.
- **Guarded write happens before the stream opens.** If the user is rate-limited, they get a 429 with JSON, not an SSE error event. Do not start streaming and then send an error mid-stream; the client's buffer-and-display logic will display the partial response, which may be empty or garbled.

**Testing notes.** Unit test is hard; this is mostly integration. Cover: happy path (chunks relayed in order, `done` at end), client disconnect (abort propagates upstream, no leaked connections), upstream 500 (error event, stream closes cleanly), upstream timeout (error event), message too long (400 before stream opens), rate-limited (429 before stream opens), invalid session (403 before stream opens).

---

## 6. Calendar privacy filtering

**What it is.** The household calendar aggregates events from multiple sources (Google Calendar, iCloud, coparent-shared calendars). Each event carries a `sensitivity` field (`normal` | `sensitive` | `privileged`). When a member views the calendar, events marked above their access level are transformed into opaque "busy" blocks — same time, same duration, no title, no participants, no location.

**Why it matters.** A household principal's work events may be attorney-client privileged, medically sensitive, or contractually confidential; the other principal cannot see their content, even in summary, even in passing. If the calendar view leaks a case name or client identity or patient detail to the non-owner's surface, the household has a real legal or ethical problem. Conversely, the owner needs to see their own events with full fidelity. The filter is applied at read time, not at ingest time — so the events themselves remain intact in the projection; only the view is censored.

**Where it lives.** `console/lib/privacy_filter.js`. Called by any route that reads calendar events or schedule data.

**The pattern.**

```js
// console/lib/privacy_filter.js

const BUSY_TITLE = '[busy]';

/**
 * Filter calendar events for display to authMember.
 *
 * Sensitivity levels (from lowest to highest):
 *   'normal'     — visible to all household members
 *   'sensitive'  — visible to principals of the household, not children
 *   'privileged' — visible only to the event's owner; others see busy block
 *
 * @param {Array<Event>} events
 * @param {object} ctx
 * @param {string} ctx.authMemberId — who is looking
 * @param {string} ctx.authRole     — 'principal' | 'child' | 'ambient'
 * @returns {Array<Event>} — events with sensitive/privileged ones redacted
 */
function filterCalendarEvents(events, ctx) {
  return events.map((ev) => applyEventPolicy(ev, ctx)).filter(Boolean);
}

function applyEventPolicy(ev, ctx) {
  const sensitivity = ev.sensitivity || 'normal';
  const isOwner = ev.owner_member_id === ctx.authMemberId;
  const isPrincipal = ctx.authRole === 'principal';
  const isChild = ctx.authRole === 'child';

  // Privileged: owner only. Everyone else sees a busy block.
  if (sensitivity === 'privileged' && !isOwner) {
    return redactToBusy(ev);
  }

  // Sensitive: principals and owner. Children see busy block.
  if (sensitivity === 'sensitive' && !isPrincipal && !isOwner) {
    return redactToBusy(ev);
  }

  // Ambient: no calendar surface at all. Filtered upstream, but
  // defense-in-depth: if somehow in this function, drop the event.
  if (ctx.authRole === 'ambient') {
    return null;
  }

  // Child-specific: always drop events tagged finance, health, or legal
  // regardless of sensitivity level.
  if (isChild && ev.tags) {
    if (ev.tags.some((t) => CHILD_FORBIDDEN_TAGS.includes(t))) {
      return null;
    }
  }

  return ev;
}

function redactToBusy(ev) {
  // Preserve ONLY the time/duration and a coarse owner hint.
  // No title, no description, no participants, no location, no attachments.
  return {
    id: ev.id,
    start: ev.start,
    end: ev.end,
    all_day: ev.all_day,
    title: BUSY_TITLE,
    kind: 'busy_block',
    source_calendar: ev.source_calendar_visible_name || 'private',
    // For the UI: tells it to render with the "privileged" grey style.
    display_hint: 'privileged',
    // Owner hint: optional, instance-configurable. Default: first name only.
    owner_hint: formatOwnerHint(ev.owner_member_id),
    // Do NOT include: description, location, attendees, recurrence,
    // attachments, organizer, custom fields, raw_calendar_event.
  };
}

const CHILD_FORBIDDEN_TAGS = ['finance', 'health', 'legal', 'adult_only'];

function formatOwnerHint(ownerMemberId) {
  // Look up the member's first name for the owner_hint. A child viewer
  // sees "Mom is busy" rather than nothing at all — useful context
  // for a 7-year-old reading the scoreboard.
  // If the instance has disabled owner hints, return null.
  // ...
}

module.exports = { filterCalendarEvents, redactToBusy, BUSY_TITLE };
```

Usage in the calendar route:

```js
// console/routes/calendar.js
router.get('/api/calendar/week', async (req, res, next) => {
  try {
    const session = buildSession(req);
    const { week_start } = req.query;
    const events = await bridge.get(
      `/core/calendar/events?tenant_id=${session.tenantId}&start=${week_start}&days=7`,
    );
    const filtered = filterCalendarEvents(events, {
      authMemberId: session.authMemberId,
      authRole: session.authRole,
    });
    res.json({ events: filtered });
  } catch (err) {
    next(err);
  }
});
```

**Subtleties.**

- **Filter at read, store at full fidelity.** The projection holds the real event. Redaction is applied on the way out. This means audit queries, pipelines, and the owner themselves see the real data; only non-owner views are redacted.
- **`redactToBusy` is allowlist, not blocklist.** Start from nothing; add back only the fields that are safe. If you start from the full event and `delete ev.title`, you will eventually add a new field and forget to delete it. Allowlist prevents that failure mode.
- **`owner_hint` is configurable and can be disabled.** Some households want "[busy]" with zero owner info. Others want "Owner is busy" so the kid understands the constraint. The platform default is first-name-only. This setting lives in `~/.adminme/config/governance.yaml`.
- **Children get two layers of filtering.** Normal sensitivity rules plus the tag-based forbidden list. A "normal"-sensitivity event tagged `finance` is still hidden from children. This catches the case where a parent creates a calendar event for "pay credit card" at `normal` sensitivity — it's not privileged, but it's not for kids either.
- **The `kind: 'busy_block'` flag lets the UI render differently.** The console reference HTML uses italic grey text for `display_hint: 'privileged'`. Without the flag, the UI would render it as a normal event titled "[busy]", which looks like a bug ("why does this say busy in title case?").
- **This function is synchronous and pure.** Given the same inputs, always the same outputs. No DB lookups. No side effects. If you need more context (e.g. the owner's display name), the caller should enrich the events first and pass them in, or the filter should take a lookup function — but the current implementation uses `formatOwnerHint` which is a pure function of the member ID and a static name table.

**Testing notes.** Cover all four role/sensitivity combinations. Cover the child-tag-filter. Cover the ambient case (returns null). Cover edge cases: missing sensitivity (defaults to normal), missing owner_member_id (fail-closed? depends on your threat model — I'd fail-closed to busy), all-day events. Property-test that `redactToBusy` never returns an object with a `description`, `location`, `attendees`, or `notes` field.

---

## 7. HIDDEN_FOR_CHILD navigation filter

**What it is.** The list of console routes and nav entries that are not just denied for child-role sessions but entirely hidden from the UI. For a child, Inbox, CRM, Capture, Finance, Calendar, and Settings don't appear in the nav at all; the Scoreboard is essentially the whole app.

**Why it matters.** "Hidden" vs "denied" is a UX distinction, not a security one. The security comes from server-side role checks on every endpoint. But the UX matters a lot: a child seeing greyed-out tabs labeled "Finance" and "Settings" is an invitation to poke at them, and a confused error message when they do. Hiding them entirely means the kid interface feels like a kid app, not a crippled admin tool.

**Where it lives.** `console/lib/nav.js` (the canonical list) and `console/client/nav.js` (the render logic). The same list is also enforced server-side by a middleware.

**The pattern.**

```js
// console/lib/nav.js

// Canonical nav structure. Single source of truth.
const NAV_ITEMS = [
  { id: 'today',      label: 'Today',      path: '/',                              visible_to: ['principal', 'child'] },
  { id: 'inbox',      label: 'Inbox',      path: '/inbox',     hidden_for_child: true, visible_to: ['principal'] },
  { id: 'crm',        label: 'CRM',        path: '/crm',       hidden_for_child: true, visible_to: ['principal'] },
  { id: 'capture',    label: 'Capture',    path: '/capture',   hidden_for_child: true, visible_to: ['principal'] },
  { id: 'finance',    label: 'Finance',    path: '/finance',   hidden_for_child: true, visible_to: ['principal'] },
  { id: 'calendar',   label: 'Calendar',   path: '/calendar',  hidden_for_child: true, visible_to: ['principal'] },
  { id: 'scoreboard', label: 'Scoreboard', path: '/scoreboard',                     visible_to: ['principal', 'child'] },
  { id: 'settings',   label: 'Settings',   path: '/settings',  hidden_for_child: true, visible_to: ['principal'] },
];

// Routes that are always blocked for children, even if reached directly by URL.
const CHILD_BLOCKED_API_PREFIXES = [
  '/api/inbox',
  '/api/crm',
  '/api/capture',
  '/api/finance',
  '/api/calendar',     // child sees schedule via /api/scoreboard/schedule only
  '/api/settings',
  '/api/tasks',        // child sees chores via /api/scoreboard/chores only
  '/api/chat',
  '/api/tools',
];

function navForRole(role) {
  return NAV_ITEMS.filter((item) => item.visible_to.includes(role));
}

// Middleware: block child-role sessions from hitting protected APIs.
function blockChildOnAdminRoutes(req, res, next) {
  const session = req.session; // attached by session middleware
  if (!session) return next(); // earlier middleware will have refused

  if (session.authRole !== 'child') return next();

  if (CHILD_BLOCKED_API_PREFIXES.some((p) => req.path.startsWith(p))) {
    return res.status(403).json({
      error: 'not_accessible_from_child_role',
      message:
        'This section is not part of the child view. Ask a parent to help.',
    });
  }
  next();
}

module.exports = { NAV_ITEMS, navForRole, blockChildOnAdminRoutes };
```

Server route that provides the nav to the client:

```js
// console/routes/nav.js
router.get('/api/nav', (req, res) => {
  const session = buildSession(req);
  res.json({
    items: navForRole(session.authRole),
    current_role: session.authRole,
  });
});
```

Client render:

```js
// console/client/nav.js
async function renderNav() {
  const { items, current_role } = await fetch('/api/nav').then((r) => r.json());
  const el = document.getElementById('nav-tabs');
  el.innerHTML = items
    .map(
      (item) =>
        `<button class="nav-tab" data-page="${item.id}">${item.label}</button>`,
    )
    .join('');
  // ...
}
```

**Subtleties.**

- **Client-side hiding is cosmetic; server-side blocking is the real security.** Do not rely on the nav list for access control. The middleware exists because a child could type `/finance` into the URL bar, and the server must refuse regardless of what the nav said.
- **The nav list and the blocklist are separate arrays.** They correspond (every hidden-for-child nav item has a corresponding blocked prefix) but they're not derived from each other. This is deliberate — they answer different questions (what tabs to show vs. what URLs to block) and can diverge (e.g. `/api/chat` is blocked for children but has no nav entry because chat is a FAB).
- **Same-origin URL guessing doesn't help the child.** All admin API prefixes are blocked; the nav-less routes also return 403. The child sees a consistent "not part of your view" response instead of a variety of errors that might hint at what's there.
- **The error message is kid-friendly.** "Not accessible from child role" would be fine for a developer but reads like an error message. "Ask a parent to help" is better — the child isn't being blamed for clicking something; they're being redirected.
- **Ambient entities don't have a nav at all.** The session middleware refuses to render the console for ambient-role members; they have no surface. This is the baby's case: they're a member for data-modeling purposes, but they don't get a login.

**Testing notes.** Cover: principal gets full nav, child gets Today + Scoreboard, ambient gets 403 at session level. Middleware blocks child on each listed prefix, allows on `/api/today` and `/api/scoreboard`. URL direct entry (e.g. GET /api/finance by child) returns 403 with the expected message.

---

## 8. Reward toast emission

**What it is.** When a task, commitment, chore, or capture is completed, the reward pipeline decides a tier (`done | warm | delight | jackpot`) and a message, then emits a `reward.ready` event. The console listens for this event on an SSE channel and displays the toast on the relevant member's screen.

**Why it matters.** Two subtle properties of the reward system determine whether it feels like magic or like a slot-machine tic: (a) the tier draw must be non-gameable — the user cannot predict or influence it, which is what keeps variable-ratio reinforcement working; (b) the toast must display *near-synchronously* with the action (< 400ms), otherwise the reward feels disconnected from the behavior that earned it.

The pattern splits the work: the completion write fires synchronously and the toast fires locally from the response; asynchronously, the backend processes the reward pipeline (which may run sampling, template selection, personalization) and emits the canonical reward event to the member's SSE channel for other open tabs and for history.

**Where it lives.** `console/routes/reward_stream.js` (the SSE channel), `console/client/rewards.js` (the toast renderer), and the Python side's `reward_dispatch` pipeline.

**The pattern.**

**Server side — SSE channel:**

```js
// console/routes/reward_stream.js

const reward_subscribers = new Map(); // memberId -> Set<res>

router.get('/api/reward/stream', (req, res) => {
  const session = buildSession(req);
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });

  // Subscribe this connection.
  if (!reward_subscribers.has(session.authMemberId)) {
    reward_subscribers.set(session.authMemberId, new Set());
  }
  const subs = reward_subscribers.get(session.authMemberId);
  subs.add(res);

  // Heartbeat.
  const hb = setInterval(() => {
    if (!res.writableEnded) res.write(': hb\n\n');
  }, 15000);
  hb.unref();

  req.on('close', () => {
    clearInterval(hb);
    subs.delete(res);
    if (subs.size === 0) reward_subscribers.delete(session.authMemberId);
  });
});

// Called by the bridge when a reward.ready event arrives from Python.
// Fan out to all open tabs for the targeted member.
function dispatchReward(memberId, reward) {
  const subs = reward_subscribers.get(memberId);
  if (!subs) return;
  const frame = `event: reward\ndata: ${JSON.stringify(reward)}\n\n`;
  for (const res of subs) {
    if (!res.writableEnded) res.write(frame);
  }
}

module.exports = { dispatchReward };
```

**The completion endpoint — synchronous local toast, async canonical reward:**

```js
// console/routes/tasks.js
router.post('/api/tasks/complete', async (req, res, next) => {
  try {
    const session = buildSession(req);
    const { task_id } = req.body;

    const result = await guardedWrite(
      { /* ...as before... */ },
      async ({ correlationId }) => {
        // This bridge call performs the task completion AND returns a
        // *synchronous preview* of the reward tier/message. The full
        // reward pipeline runs in the background and emits the canonical
        // event separately; this preview is what the clicking tab shows
        // immediately.
        return await bridge.post('/core/tasks/complete', {
          task_id,
          actor_member_id: session.authMemberId,
          correlation_id: correlationId,
          want_reward_preview: true,
        });
      },
    );

    if (!result.ok) {
      return res
        .status(result.denied_at === 'rate_limit' ? 429 : 403)
        .json({ error: result.reason });
    }

    // result.result.reward_preview = { tier, message, sub }
    res.json({
      ok: true,
      reward_preview: result.result.reward_preview,
    });
  } catch (err) {
    next(err);
  }
});
```

**Client side — fire local toast immediately, subscribe to SSE for cross-tab:**

```js
// console/client/rewards.js
async function completeTask(taskId) {
  const resp = await fetch('/api/tasks/complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_id: taskId }),
  });
  if (resp.status === 429) {
    fireRateLimitToast();
    return;
  }
  if (!resp.ok) {
    return; // error handling elsewhere
  }
  const { reward_preview } = await resp.json();
  if (reward_preview) {
    fireRewardToast(reward_preview.tier, reward_preview.message, reward_preview.sub);
  }
}

// SSE subscription for canonical reward events (cross-tab + background)
const rewardStream = new EventSource('/api/reward/stream');
rewardStream.addEventListener('reward', (e) => {
  const reward = JSON.parse(e.data);
  // Dedupe: if this correlation_id already showed a toast, skip.
  if (shownRewards.has(reward.correlation_id)) return;
  shownRewards.add(reward.correlation_id);
  fireRewardToast(reward.tier, reward.message, reward.sub);
});

const shownRewards = new Set(); // correlation_id -> shown
function fireRewardToast(tier, message, sub) {
  // ...DOM manipulation identical to the console reference mockup...
}
function fireRateLimitToast() { /* ... */ }
```

**Subtleties.**

- **Two reward paths, one deduplication key.** The synchronous preview fires on the clicking tab; the SSE canonical event fires on all tabs (including the clicking one). Dedup by `correlation_id`. Without dedup, the clicking tab shows two toasts for the same completion.
- **The tier draw happens server-side.** Never in the client. If the tier is calculated in JavaScript, the user can inspect the code and game the reward — and worse, future "fairness" claims are unaudittable. The server samples, signs the result (conceptually — the event log is append-only), and returns the tier as the output.
- **Cross-tab matters.** A principal with the console open on both a phone and a laptop should see the reward on both. The SSE channel + set-based fan-out handles this cleanly.
- **Observation mode suppresses the external dispatch, not the local toast.** When observation is on, the reward pipeline still runs (so you can see what *would* have happened), the local toast still shows (so completion feedback isn't broken during onboarding), but the side effects — sending a push notification to the member's phone, for instance — are suppressed. This means the "reward preview" is always returned synchronously; the SSE canonical event is also sent; what's suppressed is channels outside the console.
- **`reward.ready` is distinct from `task.completed`.** The task event is the source of truth; the reward event is derived. Downstream consumers (the scoreboard projection, the velocity calculator) should listen on `task.completed`, not `reward.ready`. Reward events are specifically for UI feedback.

**Testing notes.** Cover: completion returns preview, SSE fans out to multiple subs, dedup prevents double-toast, completion during observation still returns preview (but suppression event is emitted), SSE heartbeat keeps connection alive through 30+ seconds idle, tab close cleans up subscriber entry.

---

## 9. Degraded-mode fallback

**What it is.** When a Python API is unreachable or slow, the console falls back to a last-known-good cached response, displays a degraded banner, and queues writes for later. The experience doesn't break — it just gets narrower.

**Why it matters.** Household software runs on a Mac Mini in a closet. Services restart, updates roll out, network blips happen. If the console goes to an error page every time `adminme-core` restarts (which takes 3-6 seconds), it feels broken. The user loses trust. If instead it shows yesterday's Today stream with a small "last synced 30s ago" note, the software feels sturdy.

The tradeoff: showing stale data can be confusing. Mitigate by making "degraded" obvious in the UI (the banner), by keeping the window short (cache for 60s), and by never letting writes succeed in degraded mode — they queue.

**Where it lives.** `console/lib/bridge.js` (the fetch wrapper with cache and fallback) and `console/lib/degraded_banner.js` (the UI state).

**The pattern.**

```js
// console/lib/bridge.js

const CACHE_TTL_MS = 60 * 1000;       // 60s fresh
const DEGRADED_TTL_MS = 5 * 60 * 1000; // 5min usable while degraded
const FETCH_TIMEOUT_MS = 3000;

const cache = new Map(); // key -> { data, fetched_at }

async function getCached(path, opts = {}) {
  const key = path + (opts.query ? '?' + new URLSearchParams(opts.query) : '');
  const cached = cache.get(key);
  const now = Date.now();

  // Fresh cache → use it, kick off background refresh.
  if (cached && now - cached.fetched_at < CACHE_TTL_MS) {
    return { data: cached.data, from_cache: false };
  }

  try {
    const data = await fetchWithTimeout(path, opts);
    cache.set(key, { data, fetched_at: now });
    clearDegradedFlag(); // success → exit degraded mode
    return { data, from_cache: false };
  } catch (err) {
    // Upstream failed. Fall back to cache if within degraded TTL.
    if (cached && now - cached.fetched_at < DEGRADED_TTL_MS) {
      setDegradedFlag(err.message);
      return {
        data: cached.data,
        from_cache: true,
        cache_age_ms: now - cached.fetched_at,
      };
    }
    // No usable cache. Re-throw so the route returns an error.
    setDegradedFlag(err.message);
    throw err;
  }
}

async function fetchWithTimeout(path, opts) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const url = resolveUpstream(path) + (opts.query ? '?' + new URLSearchParams(opts.query) : '');
    const resp = await fetch(url, { signal: ctrl.signal, headers: opts.headers });
    if (!resp.ok) throw new Error(`upstream_${resp.status}`);
    return await resp.json();
  } finally {
    clearTimeout(timer);
  }
}

function resolveUpstream(path) {
  // path: '/core/today' → 'http://127.0.0.1:3333/today'
  // path: '/comms/inbox' → 'http://127.0.0.1:3334/inbox'
  if (path.startsWith('/core')) return 'http://127.0.0.1:3333' + path.slice(5);
  if (path.startsWith('/comms')) return 'http://127.0.0.1:3334' + path.slice(6);
  if (path.startsWith('/capture')) return 'http://127.0.0.1:3335' + path.slice(8);
  if (path.startsWith('/automation')) return 'http://127.0.0.1:3336' + path.slice(11);
  throw new Error(`unknown_upstream_prefix:${path}`);
}

// ---- Degraded flag management ----

let degradedSince = null;
let degradedReason = null;
const degradedSubscribers = new Set();

function setDegradedFlag(reason) {
  if (!degradedSince) degradedSince = Date.now();
  degradedReason = reason;
  broadcastDegraded();
}

function clearDegradedFlag() {
  if (degradedSince) {
    degradedSince = null;
    degradedReason = null;
    broadcastDegraded();
  }
}

function getDegradedState() {
  return degradedSince
    ? { degraded: true, since: degradedSince, reason: degradedReason }
    : { degraded: false };
}

function subscribeDegraded(res) {
  degradedSubscribers.add(res);
  return () => degradedSubscribers.delete(res);
}

function broadcastDegraded() {
  const state = getDegradedState();
  const frame = `event: degraded\ndata: ${JSON.stringify(state)}\n\n`;
  for (const res of degradedSubscribers) {
    if (!res.writableEnded) res.write(frame);
  }
}

module.exports = {
  bridge: { get: getCached },
  getDegradedState,
  subscribeDegraded,
};
```

**Write queueing under degraded mode:**

```js
// console/lib/write_queue.js

// When a write fails because upstream is down, queue it to disk.
// On recovery (first successful read), drain the queue.

const { appendQueue, drainQueue } = require('./queue_store');

async function postWithQueue(path, body, opts = {}) {
  try {
    return await fetchWithTimeout(path, { method: 'POST', body: JSON.stringify(body), ...opts });
  } catch (err) {
    // Queue the write for retry
    await appendQueue({
      path,
      body,
      opts,
      queued_at: Date.now(),
      source: opts.source || 'unknown',
    });
    throw new QueuedWriteError('write_queued', { queued: true });
  }
}

// On boot and after any successful read, try draining.
async function tryDrain() {
  const items = await drainQueue();
  for (const item of items) {
    try {
      await fetchWithTimeout(item.path, { method: 'POST', body: JSON.stringify(item.body), ...item.opts });
    } catch (err) {
      // Failed again; requeue and stop draining.
      await appendQueue(item);
      return;
    }
  }
}
```

**Subtleties.**

- **Two TTLs: fresh and degraded.** Within 60s, serve from cache without fanfare (no banner, `from_cache: false`). Between 60s and 5min without a successful upstream call, still serve but set the degraded flag. Past 5min, treat as down — the data is too stale to be useful.
- **Write queueing is optional per route.** Some writes are safe to queue (a task completion, a capture). Some are not (a chat message — if the assistant is offline, the user should know). Per-route, decide: queue-and-return-200, or fail-and-return-503. The default should be fail, not queue; queue is the exception.
- **Clearing the degraded flag requires a successful *read*.** A successful queue drain shouldn't clear the flag, because the writes may still fail silently. Only an end-to-end GET with a 200 response proves the path is healthy.
- **Per-upstream flags.** If core is down but comms is up, the Calendar tab works but Today is degraded. The implementation above uses a single global flag, which is simple but coarse. The more correct version keeps per-prefix state (`core: degraded, comms: healthy`). Start with the coarse version; upgrade if it gets complained about.
- **Cache is in-memory.** This means a console restart loses the cache. That's fine — the first request after restart will either succeed (cache repopulates) or hit the degraded banner immediately, which is correct behavior. If you persist cache to disk, you now have cache-consistency bugs when projections update.

**Testing notes.** Cover: fresh cache served directly, stale cache triggers upstream fetch, upstream success repopulates cache, upstream failure with fresh cache serves cache and sets degraded flag, upstream failure with stale cache past 5min throws, queue-and-retry for writes, drain on recovery, degraded state broadcast to subscribers.

---

## 10. HTTP bridge to Python APIs

**What it is.** The console is a Node process; the platform (event log, projections, pipelines, skills, adapters) is Python. They talk over HTTP on localhost. The bridge is a thin wrapper that adds: tenant header injection, correlation ID propagation, structured retries, timeouts, and JSON error unwrapping.

**Why it matters.** Without a common wrapper, every route ends up writing its own `fetch` with subtly different retry/timeout behavior. Errors propagate inconsistently. Correlation IDs get dropped. Tenant headers get forgotten (critical — if you forget the header on a read, the Python side doesn't know which household's data to return, and either errors or — worse — returns empty).

**Where it lives.** `console/lib/bridge.js` (the full version, extending the cache layer from section 9).

**The pattern.**

```js
// console/lib/bridge.js (extended)

const UPSTREAM_MAP = {
  '/core': 'http://127.0.0.1:3333',
  '/comms': 'http://127.0.0.1:3334',
  '/capture': 'http://127.0.0.1:3335',
  '/automation': 'http://127.0.0.1:3336',
};

const DEFAULT_TIMEOUT_MS = 3000;
const LONG_TIMEOUT_MS = 30000; // for LLM calls, SSE openings

class BridgeError extends Error {
  constructor(code, { status, upstream_body, correlation_id } = {}) {
    super(code);
    this.code = code;
    this.status = status;
    this.upstream_body = upstream_body;
    this.correlation_id = correlation_id;
  }
}

async function bridgeFetch(method, path, { body, query, timeout_ms, headers = {}, session, correlation_id } = {}) {
  const upstream = resolveUpstream(path);
  const url =
    upstream + (query ? '?' + new URLSearchParams(query) : '');

  const finalHeaders = {
    'Content-Type': 'application/json',
    ...(correlation_id && { 'X-Correlation-Id': correlation_id }),
    ...(session && {
      'X-Tenant-Id': session.tenantId,
      'X-Auth-Member-Id': session.authMemberId,
      'X-View-Member-Id': session.viewMemberId,
      'X-Auth-Role': session.authRole,
    }),
    ...headers,
  };

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout_ms || DEFAULT_TIMEOUT_MS);

  try {
    const resp = await fetch(url, {
      method,
      headers: finalHeaders,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });

    const text = await resp.text();
    let parsed;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = { raw: text };
    }

    if (!resp.ok) {
      throw new BridgeError(`upstream_${resp.status}`, {
        status: resp.status,
        upstream_body: parsed,
        correlation_id,
      });
    }

    return parsed;
  } catch (err) {
    if (err instanceof BridgeError) throw err;
    if (err.name === 'AbortError') {
      throw new BridgeError('upstream_timeout', { correlation_id });
    }
    throw new BridgeError('upstream_unreachable', {
      correlation_id,
      upstream_body: { message: err.message },
    });
  } finally {
    clearTimeout(timer);
  }
}

function resolveUpstream(path) {
  for (const [prefix, host] of Object.entries(UPSTREAM_MAP)) {
    if (path.startsWith(prefix + '/')) {
      return host + path.slice(prefix.length);
    }
  }
  throw new BridgeError('unknown_upstream_prefix', {
    upstream_body: { path },
  });
}

// Public API
const bridge = {
  get: (path, opts = {}) => bridgeFetch('GET', path, opts),
  post: (path, body, opts = {}) => bridgeFetch('POST', path, { ...opts, body }),
  put: (path, body, opts = {}) => bridgeFetch('PUT', path, { ...opts, body }),
  del: (path, opts = {}) => bridgeFetch('DELETE', path, opts),
  // Long-running (SSE, LLM calls): caller passes stream handling.
  openStream: async (path, opts = {}) =>
    bridgeFetch('POST', path, { ...opts, timeout_ms: LONG_TIMEOUT_MS }),
};

module.exports = { bridge, BridgeError };
```

Usage:

```js
// In a route handler
try {
  const result = await bridge.post('/core/tasks/complete',
    { task_id, actor_member_id: session.authMemberId },
    { session, correlation_id: corrId },
  );
  res.json(result);
} catch (err) {
  if (err instanceof BridgeError && err.code === 'upstream_timeout') {
    return res.status(504).json({ error: 'upstream_timeout' });
  }
  next(err);
}
```

**Subtleties.**

- **Tenant header is auto-injected when session is passed.** If a handler forgets to pass session, the bridge call will succeed but without tenant context, and the Python side will (correctly) reject it. This fails closed — the failure is visible, not silent.
- **Correlation ID is propagated to Python.** When a write fails and shows up in audit, it carries the same `correlation_id` that the guardedWrite layer stamped. Trace one user action across all four backend processes by grepping for one ID.
- **`bridgeFetch` does not retry.** Retry policy depends on the operation. A GET that failed with `upstream_timeout` might be safely retried with backoff; a POST that timed out might have already succeeded on the server and retry would duplicate. Add retry in specific route handlers where semantics are clear, not globally.
- **Error shape is canonical.** `BridgeError` always has `code`, optional `status`, optional `upstream_body`, optional `correlation_id`. Downstream handlers pattern-match on `err.code`. No ad-hoc string parsing.
- **JSON parse fallback.** If the upstream returns HTML (e.g. Python crashed and an HTTP server is serving a default 500 page), don't crash on the parse; wrap it in `{ raw: text }` so the caller still gets something to log.

**Testing notes.** Cover: happy path, upstream 404, upstream 500 with JSON body, upstream 500 with HTML body, timeout, upstream unreachable, correlation ID propagation, tenant header injection, unknown prefix.

---

## 11. Observation mode enforcement

**What it is.** When observation mode is on, outbound-capable actions (sending messages, pushing notifications, making API calls to external services) compose and log their payloads but don't actually fire. Read-only actions (receiving messages, ingesting events, running pipelines) continue unchanged.

**Why it matters.** The first few days of a new AdministrateMe instance are the highest-stakes period. The identity resolution might be wrong. A skill might be miscalibrated. Rate limits might be set too permissive. Observation mode says: *show me everything you would do, but don't do it*. Then after the principal reviews a day or two of suppressed payloads and sees they're reasonable, they flip it off and the system becomes live.

The enforcement point is critical: observation mode is checked at the **final outbound filter**, not at the policy layer or the action-decision layer. If a pipeline decides to send, a skill drafts the message, governance approves, rate limit passes — all of that happens normally. Only at the very last step, before the network call to the external service, observation mode intervenes.

**Where it lives.** Mostly Python side (in adapters), but the console surfaces the suppressed payloads and provides the toggle. The Node bridge also enforces at its boundary for any console-originated outbound (like the push notification fan-out in rewards).

**The pattern.**

```js
// console/lib/observation.js

const { getDb } = require('../db');

/**
 * Wrap an outbound action. If observation mode is on, record a
 * `observation.suppressed` event instead of calling the action fn.
 *
 * @param {object} ctx
 * @param {string} ctx.tenantId
 * @param {string} ctx.action         — 'message.send', 'push.send', ...
 * @param {object} ctx.payload        — what would have been sent
 * @param {string} ctx.correlation_id
 * @param {function} actionFn         — the actual outbound call
 */
async function outbound(ctx, actionFn) {
  const isObserving = isObservationModeOn(ctx.tenantId);

  if (isObserving) {
    await emitEvent({
      type: 'observation.suppressed',
      tenant_id: ctx.tenantId,
      correlation_id: ctx.correlation_id,
      payload: {
        action: ctx.action,
        would_have_sent: ctx.payload,
        suppressed_at: new Date().toISOString(),
      },
    });
    return { suppressed: true, reason: 'observation_mode' };
  }

  return await actionFn();
}

function isObservationModeOn(tenantId) {
  const db = getDb();
  const row = db
    .prepare('SELECT value FROM tenant_config WHERE tenant_id = ? AND key = ?')
    .get(tenantId, 'observation_mode_active');
  return row && row.value === 'true';
}

async function setObservationMode(tenantId, active, actorMemberId) {
  const db = getDb();
  const prev = isObservationModeOn(tenantId);
  db.prepare(
    `INSERT INTO tenant_config (tenant_id, key, value)
       VALUES (?, 'observation_mode_active', ?)
       ON CONFLICT(tenant_id, key) DO UPDATE SET value = excluded.value`,
  ).run(tenantId, active ? 'true' : 'false');

  await emitEvent({
    type: active ? 'observation.enabled' : 'observation.disabled',
    tenant_id: tenantId,
    actor_member_id: actorMemberId,
    payload: { previous_state: prev },
  });
}

module.exports = { outbound, isObservationModeOn, setObservationMode };
```

Usage in a reward push handler:

```js
async function sendRewardPush(session, reward) {
  return await outbound(
    {
      tenantId: session.tenantId,
      action: 'push.send',
      payload: {
        member_id: session.authMemberId,
        tier: reward.tier,
        message: reward.message,
      },
      correlation_id: reward.correlation_id,
    },
    async () => {
      // The real push call — only reached if observation is off.
      return await apnsClient.push({
        token: pushTokenFor(session.authMemberId),
        payload: { title: personaName, body: reward.message },
      });
    },
  );
}
```

**Subtleties.**

- **Observation mode is per-tenant, not per-agent.** An instance is either observing or it isn't. Don't try to let some skills be live while others observe — the complexity compounds and the audit story gets muddy.
- **The event schema for `observation.suppressed` is stable.** It must include the full would-have-sent payload, so the principal can see exactly what would have gone out. Keep the schema backward-compatible; downstream tools (the Settings > Observation pane, the Bootstrap wizard's "review suppressions" step) depend on it.
- **Emitting the suppressed event is itself subject to the tenant's event store, not the outbound filter.** Events are writes to the local database. Never route them through the outbound filter — you'd get infinite recursion.
- **The local toast still fires.** As noted in section 8, observation suppresses the *external* side effect; the console's own reward preview still shows. This is deliberate. The principal is using the console while in observation mode; breaking the console experience would defeat the point.
- **Default is ON for new instances.** The bootstrap wizard ends with observation mode set to true. The principal opts out explicitly after review. Never ship with observation off as the default — the failure mode (surprise bulk email to every contact during onboarding) is too severe.

**Testing notes.** Cover: observation on → suppressed event emitted, actionFn NOT called. Observation off → actionFn called, no suppressed event. Toggle persists across console restart. Setter records the enabled/disabled event. Observation of emit-event itself doesn't recurse.

---

## 12. Error handling and correlation IDs

**What it is.** Every request gets a correlation ID at the first middleware. Every downstream call, event, and log entry includes it. The Express error handler catches all thrown errors, logs them with the correlation ID and stack, and returns a sanitized response to the client.

**Why it matters.** Household-scale debugging usually has one symptom ("it said there was a problem") and no reproduction steps. The only thread back is the correlation ID. Without it, you grep audit logs by time window and member ID and hope; with it, you run `grep w_abc123 logs/*.jsonl` and get the full trace of one action across four processes in milliseconds.

Sanitized response matters because: (a) stack traces leak implementation details, (b) raw upstream errors sometimes contain tenant data that shouldn't appear in a different tenant's error, (c) a consistent `{ error, correlation_id }` shape lets the client surface the ID to the user, who can then tell the principal "it said `w_abc123`" and the principal can look it up.

**Where it lives.** `console/middleware/correlation.js`, `console/middleware/error.js`.

**The pattern.**

```js
// console/middleware/correlation.js

function attachCorrelationId(req, res, next) {
  // Honor an incoming ID if the client passed one; otherwise mint.
  const incoming = req.header('x-correlation-id');
  const cid = incoming && /^[\w-]{6,64}$/.test(incoming)
    ? incoming
    : mint();
  req.correlationId = cid;
  res.setHeader('X-Correlation-Id', cid);
  next();
}

function mint() {
  return `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

module.exports = { attachCorrelationId };
```

```js
// console/middleware/error.js

const { log } = require('../lib/log');

function errorHandler(err, req, res, next) {
  const cid = req.correlationId || 'none';
  const identity = req.identity || {};

  // Log the full error with trace context.
  log.error('request_error', {
    correlation_id: cid,
    path: req.path,
    method: req.method,
    auth_member_id: identity.memberId,
    tenant_id: identity.tenantId,
    error_code: err.code || 'unknown',
    error_message: err.message,
    error_stack: err.stack,
    status: err.status || 500,
  });

  // Sanitized response. Only fields safe for the client.
  const status = err.status || 500;
  const payload = {
    error: clientSafeCode(err),
    correlation_id: cid,
  };
  // For 4xx errors, include a human-readable message if one exists on the error.
  if (status >= 400 && status < 500 && err.userMessage) {
    payload.message = err.userMessage;
  }
  // For rate limit errors, surface retry_after.
  if (err.retry_after_s) payload.retry_after_s = err.retry_after_s;

  res.status(status).json(payload);
}

function clientSafeCode(err) {
  // Known error codes are safe. Unknown errors become 'internal_error'.
  const allowed = new Set([
    'no_tailnet_identity',
    'member_not_found',
    'only_principals_can_view_as',
    'view_target_not_in_household',
    'view_target_has_no_surface',
    'write_requires_principal',
    'rate_limit_exceeded',
    'hard_refuse',
    'deny',
    'held_for_review',
    'upstream_timeout',
    'upstream_unreachable',
    'not_accessible_from_child_role',
    'invalid_text',
    'invalid_payload',
    'unknown_upstream_prefix',
  ]);
  if (err.code && allowed.has(err.code)) return err.code;
  return 'internal_error';
}

module.exports = { errorHandler };
```

Boot order in `console/server.js`:

```js
// console/server.js
const app = express();

app.use(require('./middleware/correlation').attachCorrelationId);
app.use(express.json({ limit: '256kb' }));
app.use(require('./middleware/identity').resolveIdentity);
app.use(require('./middleware/session').attachSession);
app.use(require('./lib/nav').blockChildOnAdminRoutes);

app.use('/api/today', require('./routes/today'));
app.use('/api/inbox', require('./routes/inbox'));
// ...other routes...

app.use(require('./middleware/error').errorHandler);

app.listen(3000, '127.0.0.1', () => {
  console.log('[console] listening on 127.0.0.1:3000');
});
```

**Subtleties.**

- **Correlation ID middleware is first.** Before identity, before body parsing. Even a 400 for malformed JSON should carry a correlation ID — that's often exactly the case you want to trace ("my request was rejected, here's the ID").
- **Accepting an incoming correlation ID is bounded.** Clients can propagate a correlation ID across a multi-step flow, but the regex `^[\w-]{6,64}$` prevents injection of arbitrary strings (which might end up in log indices or DB fields).
- **The allowed-code set is explicit.** Adding new error codes to the client response requires adding them to the set. This makes it impossible to accidentally leak an internal error code to the client — the mechanism fails closed.
- **Log the full error; return the sanitized one.** The log has stack traces, full error messages, request metadata. The response has a code, a correlation ID, and a user-safe message at most. Never return `err.message` directly; it might say something like `"could not connect to postgres at 10.0.0.3"` which is both a security leak and useless to the user.
- **`userMessage` is an explicit opt-in.** Throw errors with a `userMessage` field when the message is safe to show. The error handler only surfaces it if the status is 4xx. For 5xx errors, the user gets "internal_error" and the correlation ID; they can tell the principal the ID and the principal can look up the real error.

**Testing notes.** Cover: each middleware runs in order, correlation ID set on request and response, error thrown in a route is caught by handler, 4xx error with userMessage surfaces it, 5xx error does not surface err.message, unknown error code maps to 'internal_error', rate-limit error includes retry_after_s in response, logged entry has full context.

---

## Appendix: what this document does NOT cover

These patterns are intentionally out of scope for CONSOLE_PATTERNS.md — they're either in BUILD.md, in a skill pack, or in the Python product APIs:

- **Event schema and the event log.** BUILD.md's "Event taxonomy" section is canonical. The console emits events via the bridge; it doesn't own the schema.
- **Projection queries (SQL).** Projections live in Python. The console calls the product APIs and consumes JSON.
- **Pipeline orchestration.** Pipelines run on the Python side via the automation API at :3336. The console triggers them but doesn't implement them.
- **Adapter implementations.** Same — adapters are Python, talking to external services. The console's bridge talks to the comms API, which itself talks to adapters.
- **xlsx projection (forward and reverse).** See BUILD.md's xlsx projector section.
- **Profile pack JSX compilation.** See REFERENCE_EXAMPLES.md for a worked example.
- **Skill runner internals.** See REFERENCE_EXAMPLES.md.
- **The specific content of `authority.yaml` and `governance.yaml`.** Covered in BUILD.md and bootstrapped per-instance.

When in doubt about whether a concern belongs in the console or the platform: if it's about *how* data is read, rendered, gated, streamed, or returned to a browser, it's the console. If it's about *what* the data means, how events compose, or how external services are reached, it's the platform. The bridge is the seam.
