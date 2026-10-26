import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";
import { identityBoundReadExpression, prepareBrowserRead, verifiedBrowserRead,
  type BrowserReadPolicy } from "../src/workstation-browser.js";

const policy: BrowserReadPolicy = {
  resource_id: "own-page", inventory_binding_id: "cos-browser", binding_revision: 1,
  policy_revision: 1, native_target_id: "real-native-tab", target_evidence_reference: "operator-observation:verified-profile-tab", profile: "chrome", url: "https://work.example.invalid/planning",
  expected_identity: { account: "cos@example.invalid", role: "staff" },
  identity_probes: {
    account: { selector: "#account", attribute: "aria-label", extraction: "email" },
    role: { selector: "#role", attribute: "textContent", extraction: "exact" },
  },
};
function config() {
  return { browser: { enabled: true, defaultProfile: "chrome", allowSystemProfileImport: false,
    evaluateEnabled: true, profiles: { chrome: { driver: "extension" } } },
    gateway: { nodes: { browser: { mode: "off" } } } };
}
function page(account: string, role = "staff", url = policy.url) {
  const readBody = vi.fn(() => ({ innerText: "Authorized household planning text" }));
  const document = { querySelectorAll: (selector: string) => selector === "#account"
    ? [{ getAttribute: () => `Current account (${account})` }]
    : [{ textContent: role }], get body() { return readBody(); } };
  const result = runInNewContext(`(${identityBoundReadExpression(policy)})()`, { document, window: { location: { href: url } } });
  return { result, readBody };
}

it("checks exact current site identity synchronously before reading any private body", () => {
  for (const attempted of [page("member@example.invalid"), page("cos@example.invalid", "external"), page("cos@example.invalid", "staff", "https://other.example.invalid")]) {
    expect(attempted.result.kind).toBe("identity_unverified");
    expect(attempted.readBody).not.toHaveBeenCalled();
    expect(attempted.result.text).toBeUndefined();
  }
  const correct = page("cos@example.invalid");
  expect(correct.result).toMatchObject({ kind: "identity_bound_read", url: policy.url,
    identity: policy.expected_identity, text: "Authorized household planning text", truncated: false });
  expect(correct.readBody).toHaveBeenCalledTimes(1);
});

it("binds only the operator-observed native tab and substitutes only the reviewed read expression", () => {
  const native = { currentConfig: config };
  const prepared = prepareBrowserRead(native, policy, { action: "snapshot" });
  expect(prepared.params).toMatchObject({ action: "act", target: "host", profile: "chrome", targetId: "real-native-tab",
    request: { kind: "evaluate", targetId: "real-native-tab", fn: identityBoundReadExpression(policy) } });
  for (const request of [{ action: "snapshot", profile: "user" }, { action: "snapshot", target: "node" }, { action: "act", request: { kind: "evaluate", fn: "arbitrary()" } }, { action: "snapshot", selector: "body" }]) {
    expect(() => prepareBrowserRead(native, policy, request)).toThrow();
  }
  expect(() => prepareBrowserRead(native, { ...policy, native_target_id: "" }, { action: "snapshot" })).toThrow("verified native tab");
  expect(() => prepareBrowserRead(native, policy, { action: "snapshot", targetId: "fabricated" })).toThrow();
});

it("withholds content after routing, target, identity or source authority changes", () => {
  let current = config();
  const native = { currentConfig: () => current };
  const prepared = prepareBrowserRead(native, policy, { action: "snapshot" });
  const result = { details: { targetId: "real-native-tab", result: page("cos@example.invalid").result } };
  expect(verifiedBrowserRead(native, prepared, policy, result).text).toBe("Authorized household planning text");
  expect(() => verifiedBrowserRead(native, prepared, { ...policy, policy_revision: 2 }, result)).toThrow("authority changed");
  expect(() => verifiedBrowserRead(native, prepared, policy, { details: { ...result.details, targetId: "new-session-tab" } })).toThrow();
  expect(() => verifiedBrowserRead(native, prepared, policy, { details: { targetId: "real-native-tab", result: page("member@example.invalid").result } })).toThrow();
  current = { ...current, browser: { ...current.browser, allowSystemProfileImport: true } };
  expect(() => verifiedBrowserRead(native, prepared, policy, result)).toThrow("session import disabled");
});

const gmailPolicy: BrowserReadPolicy = {
  ...policy, url: "https://mail.google.com/mail/u/0/#inbox",
  identity_verifier: "gmail_self_mailbox_v1", identity_probes: {},
  expected_identity: { account: "cos@example.invalid", workspace: "Synthetic household", role: "account user" },
};

type GmailOptions = {
  accountLabel?: string; title?: string; url?: string; chromeCount?: number;
  accountDuplicate?: "nested" | "siblings" | "nested-different";
  chatLabel?: string; chatCount?: number; chatVisible?: boolean;
  chatAncestor?: "hidden" | "disabled" | "native-disabled" | "inert" | "content";
  chatDisabled?: boolean; chatAncestry?: boolean;
  frameSrc?: string; frameCount?: number; frameSrcdoc?: boolean;
  frameAncestry?: boolean; frameContent?: boolean; frameHeight?: number;
};
function gmailPage(options: GmailOptions = {}, current = gmailPolicy) {
  function node(label: string, attributes: Record<string, string> = {}, visible = true,
    ancestor?: GmailOptions["chatAncestor"], nativeDisabled = false, height = 20) {
    const children = new Set<unknown>();
    return { textContent: label, children,
      getAttribute: (name: string) => name === "aria-label" ? label : attributes[name] ?? null,
      hasAttribute: (name: string) => name in attributes,
      checkVisibility: () => visible, getClientRects: () => visible ? [{ width: 256, height }] : [],
      matches: (selector: string) => selector === ":disabled" ? nativeDisabled : true,
      closest: (selector: string) => ancestor && (
        (ancestor === "hidden" && selector.includes("[hidden]")) ||
        (ancestor === "disabled" && selector.includes("[aria-disabled")) ||
        (ancestor === "native-disabled" && selector.includes(":disabled")) ||
        (ancestor === "inert" && selector.includes("[inert]")) ||
        (ancestor === "content" && selector.includes('[role="main"]'))
      ) ? {} : null,
      contains: (other: unknown) => children.has(other) };
  }
  const account = node(options.accountLabel ?? "Google Account: Synthetic Staff (cos@example.invalid)");
  const accounts = [account];
  if (options.accountDuplicate) {
    const child = node(options.accountDuplicate === "nested-different"
      ? "Google Account: Other Staff (other@example.invalid)" : account.textContent);
    if (options.accountDuplicate !== "siblings") account.children.add(child);
    accounts.push(child);
  }
  const chat = node(options.chatLabel ?? "Chat, 1 unread message", {},
    options.chatVisible ?? true, options.chatAncestor, options.chatDisabled);
  const frame = { ...node("Chat", {
    name: "gtn-roster-iframe-id", title: "Chat",
    src: options.frameSrc ?? "https://chat.google.com/u/0/frame?shell=6&pt=6&origin=https%3A%2F%2Fmail.google.com#cb=gtn-brain-iframe-id",
    ...(options.frameSrcdoc ? { srcdoc: "" } : {}),
  }, true, options.frameContent ? "content" : undefined, false, options.frameHeight ?? 0),
    matches: () => options.frameAncestry !== false };
  const chrome = { ...node("Google account chrome"), querySelectorAll: (selector: string) => {
    if (selector === '[aria-label^="Google Account:"]') return accounts;
    throw new Error(`Unexpected identity selector: ${selector}`);
  } };
  const readBody = vi.fn(() => ({ innerText: "Synthetic mailbox content" }));
  const document = { title: options.title ?? "Inbox (22) - cos@example.invalid - Synthetic household Mail",
    querySelectorAll: (selector: string) => {
      if (selector === "#gb") return Array.from({ length: options.chromeCount ?? 1 }, () => chrome);
      if (selector === 'div[role="navigation"][jscontroller="s6IIOd"] > div[jscontroller="m50PKc"] > div[jsname="hSRGPd"][role="link"]') {
        return Array.from({ length: options.chatAncestry === false ? 0 : options.chatCount ?? 1 }, () => chat);
      }
      if (selector === 'iframe#gtn-roster-iframe-id') return Array.from({ length: options.frameCount ?? 1 }, () => frame);
      throw new Error(`Unexpected identity selector: ${selector}`);
    }, get body() { return readBody(); } };
  const result = runInNewContext(`(${identityBoundReadExpression(current)})()`, {
    document, URL, window: { location: { href: options.url ?? current.url } },
  });
  return { result, readBody };
}

it("derives Gmail's self-mailbox role from visible Chat navigation even when the roster frame is dormant", () => {
  for (const options of [{}, { accountDuplicate: "nested" as const },
    { title: "Inbox - cos@example.invalid - Synthetic household Mail", chatLabel: "Chat", frameHeight: 0 }]) {
    const actual = gmailPage(options);
    expect(actual.result).toMatchObject({ kind: "identity_bound_read", identity: gmailPolicy.expected_identity,
      identity_evidence: { method: "gmail_self_mailbox_v1", account: "cos@example.invalid",
        mailbox_account: "cos@example.invalid", chat_frame_origin: "https://chat.google.com",
        workspace: "Synthetic household", role_basis: "self_chat_navigation" } });
    expect(actual.readBody).toHaveBeenCalledTimes(1);
    const native = { currentConfig: config };
    const prepared = prepareBrowserRead(native, gmailPolicy, { action: "snapshot" });
    const wrapped = { details: { targetId: prepared.targetId, result: actual.result } };
    expect(verifiedBrowserRead(native, prepared, gmailPolicy, wrapped).identity).toEqual(gmailPolicy.expected_identity);
  }
});

it.each<GmailOptions>([
  { accountLabel: "Google Account: Other Actor (other@example.invalid)" },
  // A delegated view may name the owner in chrome/title; that alone never proves self.
  { chatCount: 0, accountLabel: "Google Account: Synthetic Staff (cos@example.invalid)" },
  { accountLabel: "Google Account: Delegated Staff (cos@example.invalid)" },
  { accountLabel: "Google Account: second@example.invalid (cos@example.invalid)" },
  { accountLabel: "Synthetic Staff cos@example.invalid" },
  { title: "Inbox (22) - other@example.invalid - Synthetic household Mail" },
  { title: "Inbox (22) - cos@example.invalid - Different tenant Mail" },
  { title: "Inbox (22) - cos@example.invalid - Gmail" },
  { title: "Sent Mail - cos@example.invalid - Synthetic household Mail" },
  { title: "Inbox - cos@example.invalid - other@example.invalid Mail" },
  { title: "Unknown localized mailbox title" },
  { chatVisible: false }, { chatDisabled: true }, { chatCount: 2 },
  { chatAncestor: "hidden" }, { chatAncestor: "disabled" }, { chatAncestor: "native-disabled" }, { chatAncestor: "inert" },
  { chatAncestor: "content" }, { chatAncestry: false }, { chatLabel: "Not Chat" },
  { chromeCount: 0 }, { chromeCount: 2 },
  { accountDuplicate: "siblings" }, { accountDuplicate: "nested-different" },
  { frameCount: 0 }, { frameCount: 2 }, { frameAncestry: false }, { frameContent: true }, { frameSrcdoc: true },
  { frameSrc: "https://chat.google.com.evil.invalid/u/0/frame?origin=https%3A%2F%2Fmail.google.com" },
  { frameSrc: "https://chat.google.com/u/1/frame?origin=https%3A%2F%2Fmail.google.com" },
  { frameSrc: "https://chat.google.com/u/0/frame?origin=https%3A%2F%2Fother.example.invalid" },
  { frameSrc: "https://chat.google.com/u/0/frame" },
  { frameSrc: "https://chat.google.com/u/0/frame?origin=https%3A%2F%2Fmail.google.com&origin=https%3A%2F%2Fother.example.invalid" },
  { url: "https://mail.google.com/mail/u/1/#inbox" },
  { url: "https://other.example.invalid/mail/u/0/#inbox" },
])("withholds Gmail content when identity proof is unavailable or inconsistent: %j", (options) => {
  const actual = gmailPage(options);
  expect(actual.result.kind).toBe("identity_unverified");
  expect(actual.readBody).not.toHaveBeenCalled();
  expect(actual.result.text).toBeUndefined();
});

it("does not accept message-content indicators, alternate roles, custom probes or changed provenance", () => {
  // The expression searches provider chrome; matching message-body indicators cannot prove identity.
  expect(gmailPage({ chromeCount: 0 }).readBody).not.toHaveBeenCalled();
  expect(gmailPage({ chatVisible: false }).result.reason).toBe("gmail_self_chat_navigation_unavailable");
  expect(gmailPage({ frameSrcdoc: true }).result.reason).toBe("gmail_chat_frame_mismatch");
  for (const changed of [
    { ...gmailPolicy, expected_identity: { ...gmailPolicy.expected_identity, role: "administrator" } },
    { ...gmailPolicy, expected_identity: { account: "cos@example.invalid", role: "account user" } },
    { ...gmailPolicy, identity_probes: policy.identity_probes },
    { ...gmailPolicy, url: "https://mail.google.com/mail/u/0/#sent" },
  ]) {
    const actual = gmailPage({}, changed);
    expect(actual.result.kind).toBe("identity_unverified");
    expect(actual.readBody).not.toHaveBeenCalled();
  }
  const native = { currentConfig: config };
  const prepared = prepareBrowserRead(native, gmailPolicy, { action: "snapshot" });
  const actual = gmailPage().result;
  const wrap = (result: unknown, targetId = prepared.targetId) => ({ details: { targetId, result } });
  expect(() => verifiedBrowserRead(native, prepared, { ...gmailPolicy, binding_revision: 2 }, wrap(actual))).toThrow("authority changed");
  expect(() => verifiedBrowserRead(native, prepared, { ...gmailPolicy, identity_verifier: "dom_fields_v1" }, wrap(actual))).toThrow("authority changed");
  expect(() => verifiedBrowserRead(native, prepared, gmailPolicy, wrap(actual, "other-tab"))).toThrow();
  expect(() => verifiedBrowserRead(native, prepared, gmailPolicy, wrap({ ...actual, identity_evidence: undefined }))).toThrow("evidence");
  expect(() => verifiedBrowserRead(native, prepared, gmailPolicy, wrap({ ...actual,
    identity_evidence: { ...actual.identity_evidence, role_basis: "matching_mailbox_labels" } }))).toThrow("evidence");
});
