import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { callGatewayTool } from "openclaw/plugin-sdk/agent-harness-runtime";
import { readPluginConfig } from "../src/config.js";
import { registerHooks } from "../src/hooks.js";
import { registerSelfReminderTools, reminderJob, verifyReminderJob } from "../src/self-reminders.js";
import type { PluginRuntime } from "../src/runtime.js";

vi.mock("openclaw/plugin-sdk/agent-harness-runtime", () => ({ callGatewayTool: vi.fn() }));
const agent = "adminme-0123456789abcdef";
const account = "fixture-reminder";
const actor = "+12025550101"; // fixture:tenant_data:ok
const session = `agent:${agent}:imessage:${account}:direct:${actor}`;
const binding = createHash("sha256").update(JSON.stringify(["cos-self-reply-v1", agent, "imessage", account, actor])).digest("hex");
const due = "2030-01-01T14:45:00.000Z";
const reminder = "01JREMINDERSYNTHETIC";
const text = "Text me tomorrow morning about the weekend plan.";

function fixture() {
  const hooks = new Map<string, (...args: any[]) => any>();
  const tools = new Map<string, (context: any) => any>();
  const send = vi.fn(async () => ({}));
  const api = { config: {}, on: (name: string, fn: any) => hooks.set(name, fn),
    registerTool: (factory: any, options: any) => tools.set(options.name, factory),
    logger: { error: vi.fn(), info: vi.fn() },
    runtime: { channel: { outbound: { loadAdapter: async () => ({ sendText: send }) } } } } as unknown as OpenClawPluginApi;
  const request = vi.fn(async (path: string, body: Record<string, any>): Promise<Record<string, any>> => {
    if (path.endsWith("/forward")) return { intent_event_id: "source-fixture" };
    if (path.endsWith("/prepare")) return { reminder_event_id: reminder, due_at: due, job_id: null };
    if (path.endsWith("/bind")) return { status: "registered" };
    if (path.endsWith("/cancel")) return { status: "cancelled", job_id: "job-fixture" };
    if (path.endsWith("/status")) return { status: "waiting", reminder_event_id: reminder, task_id: "work-fixture", due_at: due, job_id: "job-fixture", automatic_retry_allowed: false };
    if (path.endsWith("/fire")) return { transport_dispatch: { channel: "imessage", account_id: account, recipient: actor, body: "Weekend reminder.", approval_event_id: "approval-fixture" }, result_provenance: { account_id: account, actor_id: actor } };
    if (path.endsWith("/result")) return { status: "sent" };
    throw new Error(`Unexpected fixture request: ${path}`);
  });
  const runtime = { transport: { request, config: readPluginConfig({ memberAgentIds: [agent], standingSelfReplyBindings: [binding], scheduledSelfReminderBindings: [binding], forwardIntakeAccounts: [{ channelId: "imessage", accountId: account }] }) }, trackedSessions: new Map() } as unknown as PluginRuntime;
  registerHooks(api, runtime);
  registerSelfReminderTools(api, runtime);
  let saved: any;
  vi.mocked(callGatewayTool).mockReset().mockImplementation(async (method, _options, raw) => {
    const params = raw as any;
    if (method === "cron.add") { saved = { ...params, id: "job-fixture" }; return { job: saved }; }
    if (method === "cron.get") return saved;
    if (method === "cron.update") { saved = { ...saved, ...params.patch }; return saved; }
    if (method === "cron.remove") return { removed: true };
    throw new Error("Unexpected RPC");
  });
  const context = { agentId: agent, sessionKey: session, runId: "run-fixture" };
  const start = async () => {
    await hooks.get("message_received")!({ from: actor, senderId: actor, content: text, messageId: "native-fixture" }, { channelId: "imessage", accountId: account, sessionKey: session, agentId: agent });
    await hooks.get("before_agent_reply")!({ cleanedBody: text }, context);
    return hooks.get("before_tool_call")!({ toolName: "administrateme_self_reminder", toolCallId: "call-fixture", params: {} }, context);
  };
  return { tools, hooks, request, send, start, context, schedule: () => tools.get("administrateme_self_reminder")!(context).execute("call-fixture", { action: "schedule", due_at: due, body: "Weekend reminder." }) };
}

describe("requested self reminders", () => {
  it("binds the exact inbound run, persists a fixed timer, and confirms only after readback", async () => {
    const f = fixture();
    expect(await f.start()).toBeUndefined();
    const result = await f.schedule();
    expect(result.details).toMatchObject({ status: "scheduled", reminder_event_id: reminder });
    expect(vi.mocked(callGatewayTool).mock.calls.map(([method]) => method)).toEqual(["cron.add", "cron.update", "cron.get"]);
    const job = vi.mocked(callGatewayTool).mock.calls[0]![2];
    expect(JSON.stringify(job)).not.toContain(actor);
    expect(JSON.stringify(job)).not.toContain("Weekend reminder.");
    expect(job).toMatchObject({ delivery: { mode: "none" }, payload: { kind: "script", toolsAllow: ["administrateme_self_reminder_fire"], toolBudget: 1 } });
    expect(f.send).not.toHaveBeenCalled();
  });

  it("does not claim scheduled after an unacknowledged timer enable", async () => {
    const f = fixture(); await f.start();
    const impl = vi.mocked(callGatewayTool).getMockImplementation()!;
    vi.mocked(callGatewayTool).mockImplementation(async (...args) => {
      if (args[0] === "cron.update") throw new Error("uncertain timeout");
      return impl(...args);
    });
    await expect(f.schedule()).rejects.toThrow("uncertain timeout");
    expect(f.send).not.toHaveBeenCalled();
  });

  it("reads the exact native timer for status without scheduling or resending", async () => {
    const f = fixture(); await f.start(); await f.schedule();
    await f.hooks.get("before_tool_call")!({ toolName: "administrateme_self_reminder", toolCallId: "status-fixture", params: {} }, f.context);
    vi.mocked(callGatewayTool).mockClear();
    const result = await f.tools.get("administrateme_self_reminder")!(f.context).execute("status-fixture", { action: "status", reminder_event_id: reminder });
    expect(result.details).toMatchObject({ status: "scheduled", task_id: "work-fixture", automatic_retry_allowed: false });
    expect(result.details).not.toHaveProperty("job_id");
    expect(vi.mocked(callGatewayTool).mock.calls.map(([method]) => method)).toEqual(["cron.get"]);
    expect(f.send).not.toHaveBeenCalled();
  });

  it("reports unavailable timers and terminal uncertainty without a retry", async () => {
    const f = fixture(); await f.start();
    vi.mocked(callGatewayTool).mockRejectedValue(new Error("timer unavailable"));
    const tool = f.tools.get("administrateme_self_reminder")!(f.context);
    expect((await tool.execute("call-fixture", { action: "status", reminder_event_id: reminder })).details).toMatchObject({ status: "timer_unconfirmed" });
    await f.hooks.get("before_tool_call")!({ toolName: "administrateme_self_reminder", toolCallId: "status-terminal", params: {} }, f.context);
    f.request.mockResolvedValueOnce({ status: "unknown", reminder_event_id: reminder, automatic_retry_allowed: false });
    vi.mocked(callGatewayTool).mockClear();
    expect((await tool.execute("status-terminal", { action: "status", reminder_event_id: reminder })).details).toMatchObject({ status: "unknown", automatic_retry_allowed: false });
    expect(vi.mocked(callGatewayTool)).not.toHaveBeenCalled();
    expect(f.send).not.toHaveBeenCalled();
  });

  it("rejects ordinary tool calls and wrong current input instead of borrowing stale receipt authority", async () => {
    const f = fixture();
    await expect(f.schedule()).rejects.toThrow("No current private");
    await f.start();
    await f.hooks.get("before_agent_reply")!({ cleanedBody: "Different current request" }, f.context);
    const result = await f.hooks.get("before_tool_call")!({ toolName: "administrateme_self_reminder", toolCallId: "another", params: {} }, f.context);
    expect(result.block).toBe(true);
    const fire = f.tools.get("administrateme_self_reminder_fire")!(f.context);
    await expect(fire.execute("fire", { reminder_event_id: reminder })).rejects.toThrow("Only the bound native timer");
  });

  it("uses guarded delivery from the native timer and exposes only the terminal status", async () => {
    const f = fixture();
    const fire = f.tools.get("administrateme_self_reminder_fire")!({ agentId: agent, sessionKey: `agent:${agent}:cron:job-fixture:trigger` });
    const result = await fire.execute("fire", { reminder_event_id: reminder });
    expect(result.details).toEqual({ status: "sent" });
    expect(f.send).toHaveBeenCalledOnce();
    expect(JSON.stringify(result)).not.toContain(actor);
  });

  it("cancels domain authority before attempting timer removal", async () => {
    const f = fixture(); await f.start();
    vi.mocked(callGatewayTool).mockRejectedValue(new Error("gateway unavailable"));
    const result = await f.tools.get("administrateme_self_reminder")!(f.context).execute("call-fixture", { action: "cancel", reminder_event_id: reminder });
    expect(result.details).toEqual({ status: "cancelled", timer_cleanup_pending: true });
    expect(f.request.mock.calls.at(-1)?.[0]).toMatch(/\/cancel$/);
  });

  it("rejects timer drift and propagates callback failures from the fixed script", async () => {
    const expected = reminderJob(reminder, due, agent);
    expect(() => verifyReminderJob({ ...expected, id: "job-fixture", delivery: { mode: "announce" } }, expected)).toThrow("does not match");
    const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
    const run = new AsyncFunction("tools", "json", expected.payload.script);
    const json = vi.fn();
    await expect(run({ call: async () => ({ result: { details: { status: "unknown" } } }) }, json)).rejects.toThrow("unconfirmed");
    expect(json).not.toHaveBeenCalled();
    await run({ call: async () => ({ result: { details: { status: "sent" } } }) }, json);
    expect(json).toHaveBeenCalledWith({ status: "sent" });
  });
});
