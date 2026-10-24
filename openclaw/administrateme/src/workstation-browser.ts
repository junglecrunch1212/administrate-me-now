/** Supported native-browser read preparation; no browser session import. */
export type BrowserIdentityProbe = {
  selector: string;
  attribute: "textContent" | "aria-label" | "title";
  extraction: "exact" | "email";
};
export type BrowserReadPolicy = {
  resource_id: string;
  inventory_binding_id: string;
  binding_revision: number;
  policy_revision: number;
  native_target_id: string;
  target_evidence_reference: string;
  profile: "chrome";
  url: string;
  expected_identity: Record<string, string>;
  identity_probes: Record<string, BrowserIdentityProbe>;
};
export type NativeBrowserAccess = {
  currentConfig: () => Record<string, unknown>;
};
export type PreparedBrowserRead = {
  targetId: string;
  policy: BrowserReadPolicy;
  params: Record<string, unknown>;
};
const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
const normalized = (value: string) => value.replace(/\s+/g, " ").trim();

export function assertNativeBrowserRoute(config: Record<string, unknown>): void {
  const browser = object(config.browser);
  const profiles = object(browser.profiles);
  const extensions = Object.entries(profiles).filter(([, row]) => object(row).driver === "extension").map(([name]) => name);
  const route = object(object(object(config.gateway).nodes).browser);
  if (browser.enabled !== true || browser.defaultProfile !== "chrome" ||
      browser.allowSystemProfileImport !== false || browser.evaluateEnabled !== true ||
      route.mode !== "off" || extensions.length !== 1 || extensions[0] !== "chrome" ||
      Object.keys(object(profiles.chrome)).some((key) => !["driver", "color", "cdpPort"].includes(key))) {
    throw new Error("One host-only CoS extension profile with session import disabled is required.");
  }
}

/**
 * Synchronous identity comparison precedes the private text read in the same
 * browser callback. The model cannot supply JavaScript, selectors or accounts.
 */
export function identityBoundReadExpression(policy: BrowserReadPolicy): string {
  const input = JSON.stringify({ url: policy.url, probes: policy.identity_probes, expected: policy.expected_identity });
  return `() => {
    const policy = ${input};
    const url = window.location.href;
    if (url !== policy.url) return {kind:"identity_unverified",reason:"destination_changed",url};
    const identity = {};
    for (const [field, probe] of Object.entries(policy.probes)) {
      const nodes = document.querySelectorAll(probe.selector);
      if (nodes.length !== 1) return {kind:"identity_unverified",reason:"identity_indicator_unavailable",url};
      const node = nodes[0];
      let value = probe.attribute === "textContent" ? node.textContent : node.getAttribute(probe.attribute);
      if (typeof value !== "string" || value.length > 1000) return {kind:"identity_unverified",reason:"identity_indicator_invalid",url};
      value = value.replace(/\\s+/g," ").trim();
      if (probe.extraction === "email") {
        const emails = value.match(/[A-Za-z0-9.!#$%&'*+/=?^_\x60{|}~-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}/g) || [];
        if (emails.length !== 1) return {kind:"identity_unverified",reason:"ambiguous_account",url};
        value = emails[0].toLowerCase();
      }
      identity[field] = value;
      const expected = probe.extraction === "email" ? policy.expected[field].toLowerCase() : policy.expected[field].replace(/\\s+/g," ").trim();
      if (value !== expected) return {kind:"identity_unverified",reason:"wrong_account_workspace_or_role",url};
    }
    if (Object.keys(identity).length !== Object.keys(policy.expected).length) return {kind:"identity_unverified",reason:"incomplete_identity",url};
    // No page body is touched before every required identity field matches.
    const body = document.body;
    const text = body ? body.innerText : "";
    if (typeof text !== "string" || !text.trim()) return {kind:"read_unavailable",reason:"no_visible_text",url};
    return {kind:"identity_bound_read",url,identity,text:text.slice(0,2000),truncated:text.length>2000};
  }`;
}

export function prepareBrowserRead(
  native: NativeBrowserAccess, policy: BrowserReadPolicy, requested: Record<string, unknown>,
): PreparedBrowserRead {
  assertNativeBrowserRoute(native.currentConfig());
  if (policy.profile !== "chrome" || requested.action !== "snapshot" ||
      Object.keys(requested).some((key) => !["action", "target", "profile", "targetId"].includes(key)) ||
      (requested.target !== undefined && requested.target !== "host") ||
      (requested.profile !== undefined && requested.profile !== policy.profile) ||
      (requested.targetId !== undefined && requested.targetId !== policy.native_target_id)) {
    throw new Error("This work permits only the exact CoS browser read; other actions and routes are unavailable.");
  }
  // The private policy records the actual target observed through the native
  // route after operator profile verification. The native browser invocation
  // verifies this target still exists; the in-page expression verifies its
  // current URL/account. Custom plugins cannot use runtime.gateway.request.
  const targetId = policy.native_target_id;
  if (!targetId || !policy.target_evidence_reference) throw new Error("A verified native tab binding is required.");
  return { targetId, policy, params: {
    action: "act", target: "host", profile: policy.profile, targetId,
    request: { kind: "evaluate", targetId, fn: identityBoundReadExpression(policy) },
  } };
}

/** Recheck current domain policy and the native result before source capture. */
export function verifiedBrowserRead(
  native: NativeBrowserAccess, prepared: PreparedBrowserRead, current: BrowserReadPolicy, toolResult: unknown,
): { text: string; truncated: boolean; url: string; targetId: string; identity: Record<string, string> } {
  assertNativeBrowserRoute(native.currentConfig());
  if (JSON.stringify(current) !== JSON.stringify(prepared.policy)) throw new Error("Browser authority changed during the read.");
  const details = object(object(toolResult).details);
  const result = object(details.result);
  if (details.targetId !== prepared.targetId || result.kind !== "identity_bound_read" ||
      result.url !== current.url || typeof result.text !== "string" || !result.text.trim() ||
      result.text.length > 2000 || typeof result.truncated !== "boolean") {
    throw new Error("The native browser did not verify the requested account and page read.");
  }
  const identity = object(result.identity);
  if (Object.keys(identity).length !== Object.keys(current.expected_identity).length) throw new Error("Incomplete destination identity.");
  for (const [field, expected] of Object.entries(current.expected_identity)) {
    const value = identity[field];
    const probe = current.identity_probes[field];
    if (!probe) throw new Error("Required identity probe is missing.");
    const wanted = probe.extraction === "email" ? expected.toLowerCase() : normalized(expected);
    if (value !== wanted) throw new Error("Destination identity mismatch.");
  }
  return { text: result.text, truncated: result.truncated, url: current.url,
    targetId: prepared.targetId, identity: identity as Record<string, string> };
}
