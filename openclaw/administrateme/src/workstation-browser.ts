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
  identity_verifier?: "dom_fields_v1" | "gmail_self_mailbox_v1";
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

// Fixed provider interpretation, not caller-supplied selectors or JavaScript.
// Google documents that delegated mailboxes cannot use Chat in Gmail:
// https://support.google.com/mail/answer/138350
// Only provider app navigation supplies the positive self-role indicator.
const gmailIdentityChecks = String.raw`
    if (!/^https:\/\/mail\.google\.com\/mail\/u\/(?:0|[1-9][0-9]*)\/#inbox$/.test(url) ||
        Object.keys(policy.expected).sort().join(",") !== "account,role,workspace" ||
        policy.expected.role !== "account user" || Object.keys(policy.probes).length !== 0) {
      return {kind:"identity_unverified",reason:"unsupported_gmail_identity",url};
    }
    const normalize = value => typeof value === "string" ? value.replace(/\s+/g," ").trim() : "";
    const oneEmail = value => {
      const matches = value.match(/[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) || [];
      return matches.length === 1 ? matches[0].toLowerCase() : null;
    };
    const visible = node => typeof node.checkVisibility === "function" &&
      node.checkVisibility({opacityProperty:true,visibilityProperty:true}) &&
      Array.from(node.getClientRects()).some(rect => rect.width > 0 && rect.height > 0) &&
      !node.closest('[hidden],[aria-hidden="true"]');
    const enabled = node => !node.matches(":disabled") &&
      !node.closest(':disabled,[inert],[aria-disabled="true"]');
    const inContent = node => Boolean(node.closest('[role="main"],[role="article"],article,[contenteditable="true"],[data-message-id],.a3s'));
    const label = node => normalize(node.getAttribute("aria-label") || node.textContent);
    // A nested accessible representation may describe one control twice. Only
    // one containing control with identical labels is accepted; siblings reject.
    const singleControl = nodes => {
      if (!nodes.length) return null;
      const roots = nodes.filter(node => !nodes.some(parent => parent !== node && parent.contains(node)));
      if (roots.length !== 1) return null;
      const root = roots[0];
      return nodes.every(node => label(node) === label(root)) ? root : null;
    };
    const chrome = document.querySelectorAll("#gb");
    if (chrome.length !== 1 || !visible(chrome[0]) || inContent(chrome[0])) {
      return {kind:"identity_unverified",reason:"google_account_chrome_unavailable",url};
    }
    const accountControl = singleControl(Array.from(chrome[0].querySelectorAll('[aria-label^="Google Account:"]')).filter(visible));
    if (!accountControl || !enabled(accountControl) ||
        !accountControl.matches('a[role="button"],button,[role="button"]')) {
      return {kind:"identity_unverified",reason:"google_account_control_unavailable",url};
    }
    const accountLabel = label(accountControl);
    const accountShape = /^Google Account: .+ \(([^\s()]+)\)$/.exec(accountLabel);
    const account = oneEmail(accountLabel);
    const title = normalize(document.title);
    const mailbox = /^Inbox(?: \([1-9][0-9]*\))? - ([^\s]+) - (.+) Mail$/.exec(title);
    if (!accountShape || !account || accountShape[1].toLowerCase() !== account ||
        !mailbox || oneEmail(title) !== mailbox[1].toLowerCase() ||
        account !== mailbox[1].toLowerCase() || account !== policy.expected.account.toLowerCase() ||
        mailbox[2] !== normalize(policy.expected.workspace) || /\bDelegated\b/i.test(accountLabel)) {
      return {kind:"identity_unverified",reason:"wrong_account_workspace_or_role",url};
    }
    const chatLinks = Array.from(document.querySelectorAll('div[role="navigation"][jscontroller="s6IIOd"] > div[jscontroller="m50PKc"] > div[jsname="hSRGPd"][role="link"]'))
      .filter(node => /^Chat(?:, [1-9][0-9]* unread messages?)?$/.test(label(node)));
    if (chatLinks.length !== 1 || !visible(chatLinks[0]) || !enabled(chatLinks[0]) || inContent(chatLinks[0])) {
      return {kind:"identity_unverified",reason:"gmail_self_chat_navigation_unavailable",url};
    }
    // The roster may be dormant (zero height) while Inbox is selected. Its
    // provider-owned embedding corroborates routing, not feature availability.
    const frames = document.querySelectorAll('iframe#gtn-roster-iframe-id');
    if (frames.length !== 1 ||
        !frames[0].matches('div[gh="chat"] > div#talk_roster > iframe#gtn-roster-iframe-id') ||
        frames[0].getAttribute("name") !== "gtn-roster-iframe-id" ||
        frames[0].getAttribute("title") !== "Chat" || frames[0].getAttribute("aria-label") !== "Chat" ||
        frames[0].hasAttribute("srcdoc") || inContent(frames[0])) {
      return {kind:"identity_unverified",reason:"gmail_chat_frame_mismatch",url};
    }
    let chatUrl;
    try { chatUrl = new URL(frames[0].getAttribute("src")); }
    catch { return {kind:"identity_unverified",reason:"gmail_chat_frame_mismatch",url}; }
    const mailboxIndex = new URL(url).pathname.split("/")[3];
    const origins = chatUrl.searchParams.getAll("origin");
    if (chatUrl.origin !== "https://chat.google.com" || chatUrl.pathname !== "/u/" + mailboxIndex + "/frame" ||
        chatUrl.username || chatUrl.password || origins.length !== 1 || origins[0] !== "https://mail.google.com") {
      return {kind:"identity_unverified",reason:"gmail_chat_frame_mismatch",url};
    }
    // Role comes from the available self-only Chat feature, not mailbox labels,
    // iframe presence, a URL index or absence of the word 'Delegated'.
    identity.account = account;
    identity.workspace = mailbox[2];
    identity.role = "account user";
    identityEvidence = {method:"gmail_self_mailbox_v1",account,mailbox_account:mailbox[1].toLowerCase(),
      workspace:mailbox[2],role_basis:"self_chat_navigation",chat_frame_origin:chatUrl.origin};
`;

export function assertNativeBrowserRoute(config: Record<string, unknown>): void {
  const browser = object(config.browser);
  const profiles = object(browser.profiles);
  const extensions = Object.entries(profiles).filter(([, row]) => object(row).driver === "extension").map(([name]) => name);
  if (browser.enabled !== true || browser.defaultProfile !== "chrome" ||
      browser.allowSystemProfileImport !== false || browser.evaluateEnabled !== true ||
      extensions.length !== 1 || extensions[0] !== "chrome" ||
      Object.keys(object(profiles.chrome)).some((key) => !["driver", "cdpPort"].includes(key))) {
    throw new Error("One explicit CoS extension profile with session import disabled is required.");
  }
}

/**
 * Synchronous identity comparison precedes the private text read in the same
 * browser callback. The model cannot supply JavaScript, selectors or accounts.
 */
export function identityBoundReadExpression(policy: BrowserReadPolicy): string {
  const verifier = policy.identity_verifier ?? "dom_fields_v1";
  if (!["dom_fields_v1", "gmail_self_mailbox_v1"].includes(verifier)) throw new Error("Unsupported browser identity verifier.");
  const input = JSON.stringify({ url: policy.url, probes: policy.identity_probes, expected: policy.expected_identity });
  return `() => {
    const policy = ${input};
    const url = window.location.href;
    if (url !== policy.url) return {kind:"identity_unverified",reason:"destination_changed",url};
    const identity = {};
    let identityEvidence;
    ${verifier === "gmail_self_mailbox_v1" ? gmailIdentityChecks : `
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
    }`}
    if (Object.keys(identity).length !== Object.keys(policy.expected).length) return {kind:"identity_unverified",reason:"incomplete_identity",url};
    // No page body is touched before every required identity field matches.
    const body = document.body;
    const text = body ? body.innerText : "";
    if (typeof text !== "string" || !text.trim()) return {kind:"read_unavailable",reason:"no_visible_text",url};
    let excerpt = "", bytes = 0;
    for (const character of text) {
      const point = character.codePointAt(0);
      const width = point <= 0x7f ? 1 : point <= 0x7ff ? 2 : point <= 0xffff ? 3 : 4;
      if (bytes + width > 2000) break;
      excerpt += character; bytes += width;
    }
    return {kind:"identity_bound_read",url,identity,text:excerpt,truncated:excerpt.length<text.length,
      ...(identityEvidence ? {identity_evidence:identityEvidence} : {})};
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
      Buffer.byteLength(result.text, "utf8") > 2000 || typeof result.truncated !== "boolean") {
    throw new Error("The native browser did not verify the requested account and page read.");
  }
  const identity = object(result.identity);
  if (Object.keys(identity).length !== Object.keys(current.expected_identity).length) throw new Error("Incomplete destination identity.");
  const gmail = current.identity_verifier === "gmail_self_mailbox_v1";
  if (gmail) {
    const evidence = object(result.identity_evidence);
    if (evidence.method !== "gmail_self_mailbox_v1" || evidence.role_basis !== "self_chat_navigation" ||
        evidence.account !== identity.account || evidence.mailbox_account !== identity.account ||
        evidence.chat_frame_origin !== "https://chat.google.com" || evidence.workspace !== identity.workspace || identity.role !== "account user") {
      throw new Error("Gmail self-mailbox identity evidence is incomplete.");
    }
  }
  for (const [field, expected] of Object.entries(current.expected_identity)) {
    const value = identity[field];
    const probe = current.identity_probes[field];
    if (!gmail && !probe) throw new Error("Required identity probe is missing.");
    const email = gmail ? field === "account" : probe?.extraction === "email";
    const wanted = email ? expected.toLowerCase() : normalized(expected);
    if (value !== wanted) throw new Error("Destination identity mismatch.");
  }
  return { text: result.text, truncated: result.truncated, url: current.url,
    targetId: prepared.targetId, identity: identity as Record<string, string> };
}
