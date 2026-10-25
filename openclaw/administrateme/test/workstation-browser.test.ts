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
