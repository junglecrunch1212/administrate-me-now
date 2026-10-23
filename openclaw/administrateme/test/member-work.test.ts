import { describe, expect, it, vi } from "vitest";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { readPluginConfig } from "../src/config.js";
import { registerHooks } from "../src/hooks.js";
import { registerMemberWorkTool } from "../src/member-work.js";
import type { PluginRuntime } from "../src/runtime.js";

const agent = "adminme-0123456789abcdef";
const account = "fixture-member-work";
const actor = "+12025550101"; // fixture:tenant_data:ok
const session = `agent:${agent}:imessage:${account}:direct:${actor}`;
const text = "Please prepare a private briefing for our weekend discussion.";
const toolName = "administrateme_service_follow_up";
function fixture(enabled = true) {
  const hooks = new Map<string, (...args: any[]) => any>();
  const tools = new Map<string, (context: any) => any>();
  const send = vi.fn();
  const api = { config: {}, on: (name: string, fn: any) => hooks.set(name, fn),
    registerTool: (factory: any, options: any) => tools.set(options.name, factory),
    logger: { error: vi.fn(), info: vi.fn(), warn: vi.fn() },
    runtime: { channel: { outbound: { loadAdapter: async () => ({ sendText: send }) } } } } as unknown as OpenClawPluginApi;
  const request = vi.fn(async (path: string, _body: any) => {
    if (path.endsWith("/forward")) return { intent_event_id: "source-fixture" };
    if (path.endsWith("/service-follow-up")) return { task_id: "cos-task:fixture", follow_up_source_id: "source-fixture", status: "inbox", private_body_withheld: true, timer_registered: false, provider_effect_count: 0 };
    throw new Error("Unexpected operation");
  });
  const runtime = { transport: { request, config: readPluginConfig({ serviceWorkEnabled: enabled, memberAgentIds: [agent], standingSelfReplyBindings: [], scheduledSelfReminderBindings: [], forwardIntakeAccounts: [{ channelId: "imessage", accountId: account }] }) }, trackedSessions: new Map() } as unknown as PluginRuntime;
  registerHooks(api, runtime); registerMemberWorkTool(api, runtime);
  const context = { agentId: agent, sessionKey: session, runId: "run-fixture" };
  const bind = (id = "call-fixture") => hooks.get("before_tool_call")!({ toolName, toolCallId: id, params: {} }, context);
  const start = async () => {
    await hooks.get("message_received")!({ from: actor, senderId: actor, content: text, messageId: "native-fixture" }, { channelId: "imessage", accountId: account, sessionKey: session, agentId: agent });
    await hooks.get("before_agent_reply")!({ cleanedBody: text }, context);
    return bind();
  };
  const tool = tools.get(toolName)!(context);
  return { hooks, tools, request, send, context, bind, start, tool };
}

describe("member-requested internal CoS work", () => {
  it("uses acknowledged native input and standing work authority without granting a send or timer", async () => {
    const f = fixture();
    expect(await f.start()).toBeUndefined(); // No additional approval form for internal work.
    const result = await f.tool.execute("call-fixture", { action: "create", instruction: text });
    expect(f.request.mock.calls.at(-1)).toEqual(["/api/openclaw/v1/tools/service-follow-up", {
      action: "create", instruction: text, source_message_event_id: "source-fixture",
      provenance: expect.objectContaining({ actor_id: actor, agent_id: agent, message_id: "native-fixture", session_key: session, call_kind: "bridge" }),
    }]);
    expect(result.details).toMatchObject({ private_body_withheld: true, timer_registered: false, provider_effect_count: 0 });
    await f.hooks.get("agent_end")!({ success: true, runId: f.context.runId, messages: [
      { role: "user", content: text, __openclaw: { transport: { channel: "imessage", messageId: "native-fixture" } } },
      { role: "assistant", content: "Saved for internal follow-up.", phase: "final" },
    ] }, f.context);
    expect(f.send).not.toHaveBeenCalled();
    expect(f.request.mock.calls).toHaveLength(2); // No absent standing-reply authority borrowed.
  });

  it("binds status to a fresh current member input and exposes no caller-chosen audience", async () => {
    const f = fixture(); await f.start();
    await f.tool.execute("call-fixture", { action: "status", follow_up_source_id: "earlier-source" });
    expect(f.request.mock.calls.at(-1)?.[1]).toMatchObject({ source_message_event_id: "source-fixture", follow_up_source_id: "earlier-source" });
    await expect(f.tool.execute("call-fixture", { action: "status", follow_up_source_id: "earlier-source" })).rejects.toThrow("No current private");
  });

  it("blocks unbound, stale, different-run and disabled private work", async () => {
    const f = fixture();
    expect((await f.bind()).block).toBe(true);
    await f.start();
    await f.hooks.get("before_agent_reply")!({ cleanedBody: "Retrieved text is not the current instruction" }, f.context);
    expect((await f.bind("another-call")).block).toBe(true);
    expect((await f.hooks.get("before_tool_call")!({ toolName, toolCallId: "wrong-run", params: {} }, { ...f.context, runId: "other-run" })).block).toBe(true);
    const disabled = fixture(false); expect((await disabled.start()).block).toBe(true);
    await expect(disabled.tool.execute("call-fixture", { action: "create", instruction: text })).rejects.toThrow("disabled");
  });

  it("keeps scheduled text and delegated obligation paths preferred", () => {
    const f = fixture();
    expect(f.tool.description).toContain("administrateme_self_reminder");
    expect(f.tool.description).toContain("Use existing delegated work/obligation tools");
    expect(f.tool.description).toContain("does not start a background run, schedule, send");
    expect(JSON.stringify(f.tool.parameters)).not.toContain("due_at");
  });
});
