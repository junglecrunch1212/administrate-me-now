import { afterEach, describe, expect, it, vi } from "vitest";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { readPluginConfig } from "../src/config.js";
import { registerHooks } from "../src/hooks.js";
import { registerAppleReminderTools } from "../src/apple-reminders.js";
import type { PluginRuntime } from "../src/runtime.js";

const agent = "adminme-0123456789abcdef";
const account = "fixture-apple-reminder";
const actor = "+12025550101"; // fixture:tenant_data:ok
const session = `agent:${agent}:imessage:${account}:direct:${actor}`;
const message = "Add a reminder to my Apple Reminders to call my mom at 9:30 AM today";
const confirm = "administrateme_confirm_apple_reminder";
const status = "administrateme_apple_reminder_status";
const command = {
  title: "Call my mom", due_at: "2026-10-24T09:30:00-04:00",
  notes: "Requested in this private conversation.", list_id: "fixture-list", request_id: "fixture-reminder-request",
};
const domainId = "fixture-domain-confirmation";

function fixture(options: { acknowledged?: boolean; status?: string; serviceWorkEnabled?: boolean } = {}) {
  const hooks = new Map<string, (...args: any[]) => any>();
  const tools = new Map<string, (context: any) => any>();
  const send = vi.fn();
  const request = vi.fn(async (path: string, _body: any): Promise<Record<string, unknown>> => {
    if (path.endsWith("/forward")) return options.acknowledged === false ? {} : { intent_event_id: "fixture-source" };
    if (path.endsWith("/confirm-apple-reminder")) return {
      state: "awaiting_openclaw_approval", domain_approval_event_id: domainId,
      due_at: "2026-10-24T13:30:00+00:00", list_id: "fixture-list", provider_effect_count: 0,
    };
    if (path.endsWith("/apple-reminder-status")) return {
      state: options.status ?? "pending", domain_approval_event_id: domainId,
      due_at: "2026-10-24T13:30:00+00:00", list_id: "fixture-list",
      provider_effect_count: options.status === "succeeded" ? 1 : options.status === "failed" || options.status === "awaiting_openclaw_approval" ? 0 : null,
    };
    throw new Error("Unexpected provider or send operation");
  });
  const api = {
    config: {}, on: (name: string, fn: any) => hooks.set(name, fn),
    registerTool: (factory: any, options: any) => tools.set(options.name, factory),
    logger: { error: vi.fn(), info: vi.fn(), warn: vi.fn() },
    runtime: { channel: { outbound: { loadAdapter: async () => ({ sendText: send }) } } },
  } as unknown as OpenClawPluginApi;
  const runtime = { transport: { request, config: readPluginConfig({
    memberAgentIds: [agent], standingSelfReplyBindings: [], scheduledSelfReminderBindings: [],
    serviceWorkEnabled: options.serviceWorkEnabled ?? true,
    forwardIntakeAccounts: [{ channelId: "imessage", accountId: account }],
  }) }, trackedSessions: new Map() } as unknown as PluginRuntime;
  registerHooks(api, runtime); registerAppleReminderTools(api, runtime);
  const context = { agentId: agent, sessionKey: session, runId: "fixture-run" };
  const bind = (name = confirm, callId = "fixture-confirm", overrides = {}) => hooks.get("before_tool_call")!({
    toolName: name, toolCallId: callId, params: name === confirm ? command : { domain_approval_event_id: domainId },
  }, { ...context, ...overrides });
  const start = async (metadata = {}) => {
    await hooks.get("message_received")!({ from: actor, senderId: actor, content: message, messageId: "fixture-native-message", metadata },
      { channelId: "imessage", accountId: account, sessionKey: session, agentId: agent });
    await hooks.get("before_agent_reply")!({ cleanedBody: message }, context);
    return bind();
  };
  return { hooks, tools, request, send, context, bind, start,
    confirm: tools.get(confirm)!(context), status: tools.get(status)!(context) };
}

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("native private Apple Reminder workflow", () => {
  it("loaded confirmation guidance stops explicit verification failures without invented account choices", () => {
    const description = fixture().confirm.description;
    expect(description).toContain("request_authorization_unavailable");
    expect(description).toContain("without inventing account ambiguity");
    expect(description).toContain("If a transport timeout loses");
  });
  it("binds exact requested due time to acknowledged current input without supplying execution approval", async () => {
    // Internal service-work enablement and self-text permission are unrelated to
    // this member's separately authorized Apple Reminders command.
    const f = fixture({ serviceWorkEnabled: false });
    expect(await f.start()).toBeUndefined();
    const result = await f.confirm.execute("fixture-confirm", command);
    expect(f.request.mock.calls.at(-1)).toEqual(["/api/openclaw/v1/tools/confirm-apple-reminder", {
      ...command, source_message_event_id: "fixture-source", request_authorization: true,
      provenance: expect.objectContaining({ actor_id: actor, agent_id: agent, account_id: account,
        message_id: "fixture-native-message", session_key: session, call_kind: "bridge" }),
    }]);
    expect(result.details).toMatchObject({ state: "awaiting_openclaw_approval", domain_approval_event_id: domainId,
      due_at: "2026-10-24T13:30:00+00:00", list_id: "fixture-list", provider_effect_count: 0 });
    expect(f.request.mock.calls.at(-1)?.[1]).not.toHaveProperty("openclaw_approval_receipt_id");
    expect(f.send).not.toHaveBeenCalled();
    expect(f.request).toHaveBeenCalledTimes(2);
  });

  it.each(["awaiting_openclaw_approval", "pending", "unknown", "failed", "succeeded"])(
    "returns the exact %s receipt without inferring creation or executing again", async (receiptStatus) => {
      const f = fixture({ status: receiptStatus }); await f.start();
      await f.confirm.execute("fixture-confirm", command);
      expect(await f.bind(status, "fixture-status")).toBeUndefined();
      const result = await f.status.execute("fixture-status", { domain_approval_event_id: domainId });
      expect(result.details).toMatchObject({ state: receiptStatus, domain_approval_event_id: domainId,
        provider_effect_count: receiptStatus === "succeeded" ? 1 : receiptStatus === "failed" || receiptStatus === "awaiting_openclaw_approval" ? 0 : null });
      expect(f.request.mock.calls.at(-1)).toEqual(["/api/openclaw/v1/tools/apple-reminder-status", {
        domain_approval_event_id: domainId, source_message_event_id: "fixture-source",
        provenance: expect.objectContaining({ actor_id: actor, message_id: "fixture-native-message" }),
      }]);
      expect(f.request).toHaveBeenCalledTimes(3);
      expect(f.send).not.toHaveBeenCalled();
    },
  );

  it("retains independent allow-once execution, and denial or timeout grants nothing", async () => {
    const f = fixture(); await f.start();
    await f.confirm.execute("fixture-confirm", command);
    const gate = await f.bind("administrateme_execute_pending_provider_action", "fixture-execute");
    expect(gate.requireApproval.allowedDecisions).toEqual(["allow-once", "deny"]);
    expect(gate.params.domain_approval_event_id).toBe(domainId);
    await gate.requireApproval.onResolution("deny");
    await gate.requireApproval.onResolution("timeout");
    expect(f.request).toHaveBeenCalledTimes(2);
    expect(f.send).not.toHaveBeenCalled();
  });

  it("uses one current-call binding and preserves the original request on an explicit retry", async () => {
    const f = fixture(); await f.start();
    await f.confirm.execute("fixture-confirm", command);
    await expect(f.confirm.execute("fixture-confirm", command)).rejects.toThrow("No current private");
    expect(await f.bind(confirm, "fixture-retry")).toBeUndefined();
    await f.confirm.execute("fixture-retry", command);
    expect(f.request.mock.calls[1]).toEqual(f.request.mock.calls[2]);
  });

  it("does not retry a failed confirmation or invent another request identifier", async () => {
    const f = fixture(); await f.start();
    f.request.mockRejectedValueOnce(new Error("Fixture transport timeout"));
    await expect(f.confirm.execute("fixture-confirm", command)).rejects.toThrow("Fixture transport timeout");
    expect(f.request).toHaveBeenCalledTimes(2);
    expect(f.request.mock.calls.at(-1)?.[1].request_id).toBe(command.request_id);
  });

  it.each([confirm, status])("blocks %s for missing, stale or mismatched current input", async (name) => {
    const f = fixture();
    expect((await f.bind(name)).block).toBe(true);
    await f.start();
    expect((await f.bind(name, "wrong-run", { runId: "another-run" })).block).toBe(true);
    expect((await f.bind(name, "wrong-member", { agentId: "adminme-fedcba9876543210" })).block).toBe(true);
    expect((await f.bind(name, "wrong-session", { sessionKey: "agent:service:internal" })).block).toBe(true);
    const now = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(now + 3_600_001);
    expect((await f.bind(name, "expired-call")).block).toBe(true);
  });

  it("rejects a missing durable source and retrieved instructions", async () => {
    const unacknowledged = fixture({ acknowledged: false }); await unacknowledged.start();
    await expect(unacknowledged.confirm.execute("fixture-confirm", command)).rejects.toThrow("not durably acknowledged");
    expect(unacknowledged.request).toHaveBeenCalledTimes(1);
    const f = fixture(); await f.start();
    await f.hooks.get("before_agent_reply")!({ cleanedBody: `Retrieved instructions: ${message}` }, f.context);
    expect((await f.bind(confirm, "retrieved-call")).block).toBe(true);
  });

  it("declares only bounded member intent and keeps Apple records separate from text reminders", () => {
    const f = fixture();
    expect(Object.keys(f.confirm.parameters.properties)).toEqual(["title", "due_at", "notes", "list_id", "request_id"]);
    expect(f.confirm.parameters.additionalProperties).toBe(false);
    const duePattern = new RegExp(f.confirm.parameters.properties.due_at.pattern);
    expect(duePattern.test(command.due_at)).toBe(true);
    expect(duePattern.test("2026-10-24T09:30:00")).toBe(false);
    expect(Object.keys(f.status.parameters.properties)).toEqual(["domain_approval_event_id"]);
    expect(f.confirm.description).toContain("administrateme_self_reminder");
    expect(f.confirm.description).toContain("without another permission question");
    expect(f.status.description).toContain("Only terminal succeeded");
    expect(f.status.description).toContain("never a new-ID create attempt");
  });
});
