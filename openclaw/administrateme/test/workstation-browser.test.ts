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

it("binds only a real unique native tab and substitutes only the reviewed read expression", async () => {
  const request = vi.fn(async () => ({ tabs: [{ targetId: "real-native-tab", url: policy.url }] }));
  const native = { currentConfig: config, request };
  const prepared = await prepareBrowserRead(native, policy, { action: "snapshot" });
  expect(request).toHaveBeenCalledWith("browser.request", expect.objectContaining({ path: "/tabs", query: { profile: "chrome" } }));
  expect(prepared.params).toMatchObject({ action: "act", target: "host", profile: "chrome", targetId: "real-native-tab",
    request: { kind: "evaluate", targetId: "real-native-tab", fn: identityBoundReadExpression(policy) } });
  for (const request of [{ action: "snapshot", profile: "user" }, { action: "snapshot", target: "node" }, { action: "act", request: { kind: "evaluate", fn: "arbitrary()" } }, { action: "snapshot", selector: "body" }]) {
    await expect(prepareBrowserRead(native, policy, request)).rejects.toThrow();
  }
  request.mockResolvedValueOnce({ tabs: [{ targetId: "one", url: policy.url }, { targetId: "two", url: policy.url }] });
  await expect(prepareBrowserRead(native, policy, { action: "snapshot" })).rejects.toThrow("operator-verified CoS tab");
  await expect(prepareBrowserRead(native, policy, { action: "snapshot", targetId: "fabricated" })).rejects.toThrow();
});

it("withholds content after routing, target, identity or source authority changes", async () => {
  let current = config();
  const native = { currentConfig: () => current, request: vi.fn(async () => ({ tabs: [{ targetId: "real-native-tab", url: policy.url }] })) };
  const prepared = await prepareBrowserRead(native, policy, { action: "snapshot" });
  const result = { details: { targetId: "real-native-tab", result: page("cos@example.invalid").result } };
  expect(verifiedBrowserRead(native, prepared, policy, result).text).toBe("Authorized household planning text");
  expect(() => verifiedBrowserRead(native, prepared, { ...policy, policy_revision: 2 }, result)).toThrow("authority changed");
  expect(() => verifiedBrowserRead(native, prepared, policy, { details: { ...result.details, targetId: "new-session-tab" } })).toThrow();
  expect(() => verifiedBrowserRead(native, prepared, policy, { details: { targetId: "real-native-tab", result: page("member@example.invalid").result } })).toThrow();
  current = { ...current, gateway: { nodes: { browser: { mode: "auto" } } } };
  expect(() => verifiedBrowserRead(native, prepared, policy, result)).toThrow("host-only");
});
