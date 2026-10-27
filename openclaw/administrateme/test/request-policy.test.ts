import { createHash, createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { canonical } from "../src/approval.js";
import { readPluginConfig } from "../src/config.js";
import { COS_REMINDER_RULE, rememberRequestAuthorization } from "../src/request-policy.js";
import type { PluginRuntime } from "../src/runtime.js";
import type { Provenance } from "../src/contract.js";
import { AdministrateMeTransport, TransportError } from "../src/transport.js";

const secret = "request-policy-fixture-credential-free-secret";
const member = "member-request-policy-fixture";
const agent = `adminme-${createHash("sha256").update(member).digest("hex").slice(0, 16)}`;
function fixture() {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const runtime = { transport: { config: readPluginConfig({memberAgentIds: [agent]}) } } as PluginRuntime;
  const text = "Add a reminder to call my parent at 9:30 AM today 🌞";
  const input = { instruction_text: text, source_message_event_id: "source-current", provenance: { agent_id: agent } as Provenance };
  const requested = {title: "Call my parent", due_at: "2026-10-24T09:30:00-04:00", request_id: "request-policy-fixture"};
  const action = {provider: "apple_reminders", operation: "create_reminder", resource_owner: "requester", title: requested.title,
    due_at: "2026-10-24T13:30:00+00:00", notes: null, list_id: "only-list", selected_by_policy: true};
  const now = Date.now();
  const ticket = {contract: "authenticated-request-v1", member_id: member, domain_approval_event_id: "domain-exact", grounding_event_id: "grounding-exact",
    source_event_id: input.source_message_event_id, action_fingerprint: createHash("sha256").update(canonical(action)).digest("hex"),
    action, rule_id: "current-human-request.apple-reminder-create.v1", issued_at: new Date(now - 100).toISOString(), expires_at: new Date(now + 60_000).toISOString(),
    evidence: [{ start: 0, end: Array.from(text).length, text }]};
  const sign = (value: unknown) => ({ request_authorization: {...value as object, signature: createHmac("sha256", secret).update("authenticated-request-v1|" + canonical(value)).digest("hex")}});
  return {runtime, input, requested, ticket, sign};
}
afterEach(() => {vi.unstubAllEnvs(); vi.restoreAllMocks();});

describe("host request policy independently binds precise evidence", () => {
  it("accepts exact signed Unicode spans and equal due instants", () => {
    const f = fixture();
    expect(() => rememberRequestAuthorization(f.runtime, f.sign(f.ticket), f.input, f.requested)).not.toThrow();
  });
  it.each(["source", "member", "expiry", "evidence", "action", "signature"])("rejects %s mismatch", (mode) => {
    const f = fixture();
    if (mode === "source") f.ticket.source_event_id = "source-other";
    if (mode === "member") f.ticket.member_id = "member-other";
    if (mode === "expiry") f.ticket.expires_at = new Date(Date.now() - 1).toISOString();
    if (mode === "evidence") f.ticket.evidence[0]!.text = "Invented permission";
    if (mode === "action") f.ticket.action.title = "Unrequested action";
    const result = f.sign(f.ticket);
    if (mode === "signature") result.request_authorization.signature = "0".repeat(64);
    expect(() => rememberRequestAuthorization(f.runtime, result, f.input, f.requested)).toThrow();
  });
  it("does not accept model-selected notes or a different exact due time", () => {
    const f = fixture();
    for (const requested of [{...f.requested, notes: "Not requested"}, {...f.requested, due_at: "2026-10-24T10:30:00-04:00"}]) {
      expect(() => rememberRequestAuthorization(f.runtime, f.sign(f.ticket), f.input, requested)).toThrow("differs");
    }
  });
  it("binds a CoS-owned request to the dedicated confirmation rule and exact native coordinates", () => {
    const f = fixture();
    const action = {...f.ticket.action, resource_owner: "service-principal:chief-of-staff",
      operating_principal: "service-principal:chief-of-staff", provider_account_id: "cos-native-fixture",
      source_id: "native-source", device_identity_sha256: "a".repeat(64), os_uid: 501,
      selection_sha256: "b".repeat(64), write_policy_sha256: "c".repeat(64)};
    const ticket = {...f.ticket, rule_id: COS_REMINDER_RULE, action,
      action_fingerprint: createHash("sha256").update(canonical(action)).digest("hex")};
    expect(() => rememberRequestAuthorization(f.runtime, f.sign(ticket), f.input, f.requested, COS_REMINDER_RULE)).not.toThrow();
    expect(() => rememberRequestAuthorization(f.runtime, f.sign(ticket), f.input, f.requested)).toThrow();
    expect(() => rememberRequestAuthorization(f.runtime, f.sign(f.ticket), f.input, f.requested, COS_REMINDER_RULE)).toThrow();
    for (const field of ["operating_principal", "provider_account_id", "device_identity_sha256", "write_policy_sha256"]) {
      const altered = {...action, [field]: ""};
      const wrong = {...ticket, action: altered, action_fingerprint: createHash("sha256").update(canonical(altered)).digest("hex")};
      expect(() => rememberRequestAuthorization(f.runtime, f.sign(wrong), f.input, f.requested, COS_REMINDER_RULE)).toThrow();
    }
  });
  it("gives only grounding confirmation its bounded model deadline", async () => {
    fixture();
    const timer = vi.spyOn(globalThis, "setTimeout");
    const transport = new AdministrateMeTransport(readPluginConfig({}), vi.fn(async () => new Response("{}", {status: 200})) as typeof fetch);
    await transport.request("/api/openclaw/v1/tools/confirm-apple-reminder", {});
    expect(timer.mock.calls.at(-1)?.[1]).toBe(35_000);
    await transport.request("/api/openclaw/v1/tools/cos-apple-reminders/confirm", {});
    expect(timer.mock.calls.at(-1)?.[1]).toBe(35_000);
    await transport.request("/api/openclaw/v1/tools/apple-reminder-status", {});
    expect(timer.mock.calls.at(-1)?.[1]).toBe(readPluginConfig({}).timeoutMs);
  });
});

it.each(["request_authorization_unavailable", "request_clarification_required"].flatMap((code) => [
  { code, path: "/api/openclaw/v1/tools/confirm-apple-reminder" },
  { code, path: "/api/openclaw/v1/tools/cos-apple-reminders/confirm" },
  { code, path: "/api/openclaw/v1/tools/confirm-requested-email" },
]))("exposes only the fixed safe $code diagnostic for $path", async ({ code, path }) => {
  fixture();
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: { code, reason: "PRIVATE provider token and source text", debug: "PRIVATE" } }), { status: 409 }));
  const transport = new AdministrateMeTransport(readPluginConfig({}), fetcher as typeof fetch);
  const failure = await transport.request(path, {}).catch((error) => error);
  if (!(failure instanceof TransportError)) throw new Error("Expected transport rejection");
  expect(failure.message).toContain(code);
  if (code === "request_authorization_unavailable") {
    expect(failure.message).toContain("not a transport timeout");
    expect(failure.message).toContain("does not establish an account choice");
    expect(failure.message).toContain("do not blindly repeat");
  }
  expect(failure.message).not.toContain("PRIVATE");
  expect(failure.message.toLowerCase()).toContain("no action was confirmed or executed");
  expect(failure.state).toBe("degraded");
  expect(failure.status).toBe(409);
  expect(fetcher).toHaveBeenCalledOnce();
});

it("exposes only the fixed source provenance limit on the exact private-work endpoint", async () => {
  fixture();
  const fetcher = vi.fn(async () => new Response(JSON.stringify({
    detail: { code: "source_provenance_limit_exceeded", reason: "PRIVATE source ids and token" },
  }), { status: 409 }));
  const transport = new AdministrateMeTransport(readPluginConfig({}), fetcher as typeof fetch);
  const failure = await transport.request("/api/openclaw/v1/tools/service-private-work", {}).catch((error) => error);
  if (!(failure instanceof TransportError)) throw new Error("Expected transport rejection");
  expect(failure.message).toContain("source_provenance_limit_exceeded");
  expect(failure.message).toContain("fresh, narrowly scoped run");
  expect(failure.message).toContain("No work was created");
  expect(failure.message).toContain("do not retry");
  expect(failure.message).not.toContain("PRIVATE");
  expect(failure.state).toBe("degraded");
  expect(failure.status).toBe(409);
});

it.each([
  { path: "/api/openclaw/v1/tools/confirm-apple-reminder", value: { detail: { code: "unknown_private_code", reason: "PRIVATE" } } },
  { path: "/api/openclaw/v1/tools/apple-reminder-status", value: { detail: { code: "request_authorization_unavailable", reason: "PRIVATE" } } },
  { path: "/api/openclaw/v1/tools/confirm-apple-reminder", value: { detail: "PRIVATE" } },
  { path: "/api/openclaw/v1/tools/service-private-work", value: { detail: { code: "unknown_private_code", reason: "PRIVATE" } } },
  { path: "/api/openclaw/v1/tools/service-private-state", value: { detail: { code: "source_provenance_limit_exceeded", reason: "PRIVATE" } } },
  { path: "/api/openclaw/v1/tools/service-private-work", value: { detail: "PRIVATE" } },
])("keeps unrelated error bodies opaque ($path)", async ({ path, value }) => {
  fixture();
  const transport = new AdministrateMeTransport(readPluginConfig({}), vi.fn(async () => new Response(JSON.stringify(value), { status: 409 })) as typeof fetch);
  const failure = await transport.request(path, {}).catch((error) => error);
  if (!(failure instanceof TransportError)) throw new Error("Expected transport rejection");
  expect(failure.message).toBe("AdministrateMe transport rejected request (409)");
  expect(failure.message).not.toContain("PRIVATE");
  expect(failure.state).toBe("incompatible");
});


it.each([
  { status: 409, body: "PRIVATE malformed JSON" },
  { status: 403, body: JSON.stringify({ detail: { code: "source_provenance_limit_exceeded", reason: "PRIVATE" } }) },
  { status: 500, body: JSON.stringify({ detail: { code: "source_provenance_limit_exceeded", reason: "PRIVATE" } }) },
])("keeps malformed and non-conflict capacity responses opaque ($status)", async ({ status, body }) => {
  fixture();
  const transport = new AdministrateMeTransport(readPluginConfig({}), vi.fn(async () => new Response(body, { status })) as typeof fetch);
  const failure = await transport.request("/api/openclaw/v1/tools/service-private-work", {}).catch((error) => error);
  if (!(failure instanceof TransportError)) throw new Error("Expected transport rejection");
  expect(failure.message).toBe(`AdministrateMe transport rejected request (${status})`);
  expect(failure.message).not.toContain("PRIVATE");
  expect(failure.state).toBe(status === 409 ? "incompatible" : "degraded");
});
