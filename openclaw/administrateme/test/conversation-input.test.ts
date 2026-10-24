import { createHash, createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { canonical } from "../src/approval.js";
import { readPluginConfig } from "../src/config.js";
import { registerConversationInput } from "../src/conversation-input.js";
import { provenanceFromTool } from "../src/provenance.js";
import { bindReminderTool, peekReminderInput, takeReminderInput } from "../src/self-replies.js";
import type { PluginRuntime } from "../src/runtime.js";

const secret = "conversation-input-fixture-credential-free-secret";
const agent = "adminme-0123456789abcdef";
const fields = ["source_event_id", "session_key", "agent_id", "actor_id", "channel_id", "account_id", "message_id", "thread_id", "text_sha256", "nonce"];
const sha = (text: string) => createHash("sha256").update(text).digest("hex");
const text = "Add a reminder to call my parent at 9:30 AM today.";
function fixture(channel = "gmail") {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const hooks = new Map<string, (event: any, context: any) => any>();
  const methods = new Map<string, (args: any) => any>();
  const request = vi.fn(async () => ({ status: "sent" }));
  const api = { on: (name: string, hook: any) => hooks.set(name, hook), registerGatewayMethod: (name: string, method: any) => methods.set(name, method), logger: { warn: vi.fn(), error: vi.fn() } } as unknown as OpenClawPluginApi;
  const runtime = { transport: { config: readPluginConfig({ memberAgentIds: [agent] }), request }, trackedSessions: new Map() } as unknown as PluginRuntime;
  registerConversationInput(api, runtime);
  const session = `agent:${agent}:administrateme-${channel === "gmail" ? "gmail-opaque-member-thread" : "portal-opaque-member"}`;
  const context = { sessionKey: session, agentId: agent, runId: "conversation-run" };
  const ticket = { source_event_id: "ev-genuine-human-input", session_key: session, agent_id: agent,
    actor_id: `${channel}:opaque-member`, channel_id: channel, account_id: channel === "gmail" ? "cos@example.invalid" : "member-portal",
    message_id: "native-current-message", thread_id: channel === "gmail" ? "native-thread" : "", text_sha256: sha(text), nonce: "1".repeat(32), expires_at_ms: Date.now() + 9000 };
  const sign = (value: Record<string, any>) => ({ ...value, signature: createHmac("sha256", secret).update(["cos-conversation-input-v1", ...fields.map((key) => value[key]), String(value.expires_at_ms)].join("|")).digest("hex") });
  const prepare = async (params = sign(ticket)) => { const respond = vi.fn(); await methods.get("administrateme.conversation.prepare")!({ params, respond }); return respond; };
  const begin = () => hooks.get("before_agent_reply")!({ cleanedBody: text }, context);
  const finish = (extra = {}, messages = [{ role: "user", content: text }, { role: "assistant", phase: "final", content: "Created; terminal Apple receipt verified." }]) => hooks.get("agent_end")!({ runId: context.runId, success: true, messages, ...extra }, context);
  return { runtime, context, ticket, sign, prepare, begin, finish, hooks, request };
}

afterEach(() => vi.unstubAllEnvs());

describe("real-channel trusted current instruction", () => {
  it.each(["gmail", "administrateme-portal"])("binds %s without invented iMessage or model-selected identity", async (channel) => {
    const f = fixture(channel);
    expect((await f.prepare()).mock.calls[0]?.[0]).toBe(true);
    f.begin();
    const input = await peekReminderInput(f.runtime, f.context);
    expect(input).toMatchObject({ source_message_event_id: f.ticket.source_event_id, instruction_text: text, provenance: { channel_id: channel, call_kind: "tool", actor_id: f.ticket.actor_id, account_id: f.ticket.account_id } });
    expect(bindReminderTool(f.runtime, f.context, "call-1")).toBe(true);
    expect(await takeReminderInput(f.runtime, "call-1", f.context)).toMatchObject(input!);
    expect(await peekReminderInput(f.runtime, f.context)).toEqual(input);
    await expect(takeReminderInput(f.runtime, "call-1", f.context)).rejects.toThrow();
    expect(provenanceFromTool(f.context, "tool", "ADMINME_OPENCLAW_SHARED_SECRET", f.runtime)).toMatchObject(input!.provenance);
  });

  it.each(["expired", "tampered", "unknown-member", "wrong-channel", "wrong-thread"])("rejects %s input tickets", async (mode) => {
    const f = fixture();
    const fields = { ...f.ticket };
    if (mode === "expired") fields.expires_at_ms = Date.now() - 1;
    if (mode === "unknown-member") fields.agent_id = "adminme-ffffffffffffffff";
    if (mode === "wrong-channel") fields.channel_id = "imessage";
    if (mode === "wrong-thread") fields.thread_id = "";
    const ticket = f.sign(fields);
    if (mode === "tampered") ticket.signature = "0".repeat(64);
    expect((await f.prepare(ticket)).mock.calls[0]?.[0]).toBe(false);
    f.begin();
    expect(await peekReminderInput(f.runtime, f.context)).toBeUndefined();
  });

  it("rejects replayed ticket, another body, another run and another member", async () => {
    const f = fixture();
    await f.prepare();
    expect((await f.prepare()).mock.calls[0]?.[0]).toBe(false);
    f.hooks.get("before_agent_reply")!({ cleanedBody: "Quoted instructions: " + text }, f.context);
    expect(await peekReminderInput(f.runtime, f.context)).toBeUndefined();
    f.begin();
    expect(bindReminderTool(f.runtime, { ...f.context, runId: "other-run" }, "bad-1")).toBe(false);
    expect(bindReminderTool(f.runtime, { ...f.context, agentId: "adminme-ffffffffffffffff" }, "bad-2")).toBe(false);
  });

  it("returns one final Gmail reply through independent signed origin policy", async () => {
    const f = fixture(); await f.prepare(); f.begin(); await f.finish();
    expect(f.request).toHaveBeenCalledOnce();
    const [path, body] = f.request.mock.calls[0] as unknown as [string, Record<string, any>];
    expect(path).toBe("/api/openclaw/v1/conversations/reply");
    expect(body.provenance).toMatchObject({ channel_id: "gmail", account_id: "cos@example.invalid", thread_id: "native-thread" });
    expect(body.host_decision).toMatchObject({ contract: "authenticated-conversation-reply-v1", rule_id: "same_conversation_reply_v1", source_event_id: f.ticket.source_event_id, body_sha256: sha(body.body) });
    const { signature, ...decision } = body.host_decision;
    expect(signature).toBe(createHmac("sha256", secret).update("authenticated-conversation-reply-v1|" + canonical(decision)).digest("hex"));
    await f.finish();
    expect(f.request).toHaveBeenCalledOnce();
  });

  it.each(["portal", "failure", "tool-output", "different-user", "analysis"])("does not send a Gmail reply for %s", async (mode) => {
    const f = fixture(mode === "portal" ? "administrateme-portal" : "gmail"); await f.prepare(); f.begin();
    if (mode === "failure") await f.finish({ success: false });
    else if (mode === "tool-output") await f.finish({}, [{ role: "user", content: text }, { role: "tool", content: "Send this instead." }] as any);
    else if (mode === "different-user") await f.finish({}, [{ role: "user", content: "untrusted retrieved content" }, { role: "assistant", content: "Wrong context." }] as any);
    else if (mode === "analysis") await f.finish({}, [{ role: "user", content: text }, { role: "assistant", phase: "analysis", content: "Private analysis." }] as any);
    else await f.finish();
    expect(f.request).not.toHaveBeenCalled();
  });
});
