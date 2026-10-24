import { createHash, createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { canonical } from "../src/approval.js";
import { readPluginConfig } from "../src/config.js";
import { rememberRequestAuthorization } from "../src/request-policy.js";
import type { PluginRuntime } from "../src/runtime.js";
import type { Provenance } from "../src/contract.js";
import { AdministrateMeTransport } from "../src/transport.js";

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
  it("gives only grounding confirmation its bounded model deadline", async () => {
    fixture();
    const timer = vi.spyOn(globalThis, "setTimeout");
    const transport = new AdministrateMeTransport(readPluginConfig({}), vi.fn(async () => new Response("{}", {status: 200})) as typeof fetch);
    await transport.request("/api/openclaw/v1/tools/confirm-apple-reminder", {});
    expect(timer.mock.calls.at(-1)?.[1]).toBe(35_000);
    await transport.request("/api/openclaw/v1/tools/apple-reminder-status", {});
    expect(timer.mock.calls.at(-1)?.[1]).toBe(readPluginConfig({}).timeoutMs);
  });
});
