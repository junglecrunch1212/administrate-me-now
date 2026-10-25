import { createHash, createHmac } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { readPluginConfig } from "../src/config.js";
import { registerConversationInput } from "../src/conversation-input.js";
import type { PluginRuntime } from "../src/runtime.js";
import { serviceAgentId, serviceSessionKey } from "../src/service-private.js";
import { registerWorkstationInventory, WORKSTATION_INVENTORY_TOOL } from "../src/workstation.js";

const secret = "fixture-workstation-secret-at-least-24-characters";
function fixture(memberAgentIds: string[] = []) {
  const hooks = new Map<string, (event: any, context: any) => any>();
  const methods = new Map<string, (args: any) => any>();
  const tools = new Map<string, (context: any) => any>();
  const request = vi.fn(async () => ({ contract: "administrateme-workstation-inventory-v2", provider_effect_count: 0 }));
  const api = {
    registerTool: (factory: any, options: any) => tools.set(options.name, factory),
    on: (name: string, hook: any) => hooks.set(name, hook),
    registerGatewayMethod: (name: string, method: any) => methods.set(name, method),
    logger: { warn: vi.fn(), error: vi.fn() },
  } as unknown as OpenClawPluginApi;
  const runtime = { transport: { request, config: readPluginConfig({ memberAgentIds }) }, trackedSessions: new Map() } as unknown as PluginRuntime;
  registerConversationInput(api, runtime);
  registerWorkstationInventory(api, runtime);
  return { tool: tools.get(WORKSTATION_INVENTORY_TOOL)!, request, hooks, methods };
}

afterEach(() => { vi.unstubAllEnvs(); });

it("uses the actual native member or service context with no identity parameters", async () => {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const f = fixture();
  const member = f.tool({ requesterSenderId: "actor-a", messageChannel: "imessage", agentAccountId: "cos-account", agentId: "member-agent" });
  await member.execute("read", {});
  expect(f.request.mock.calls[0]).toEqual(["/api/openclaw/v1/tools/workstation-inventory", {
    provenance: expect.objectContaining({ actor_id: "actor-a", channel_id: "imessage", account_id: "cos-account" }),
  }]);
  const service = f.tool({ agentId: serviceAgentId(), sessionKey: serviceSessionKey(secret) });
  await service.execute("read", {});
  expect(f.request.mock.calls[1]).toEqual(["/api/openclaw/v1/tools/workstation-inventory", {
    provenance: expect.objectContaining({ actor_id: "service-principal:chief-of-staff", channel_id: "administrateme-service" }),
  }]);
  expect(member.parameters.additionalProperties).toBe(false);
  expect(Object.keys(member.parameters.properties).sort()).toEqual(["binding_id", "limit", "offset", "section", "snapshot", "view"]);
});

it("rejects wrong service sessions and ambient tool contexts before transport", async () => {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const f = fixture();
  await expect(f.tool({ agentId: serviceAgentId(), sessionKey: "untrusted" }).execute("read", {})).rejects.toThrow();
  await expect(f.tool({}).execute("read", {})).rejects.toThrow();
  expect(f.request).not.toHaveBeenCalled();
});


it("passes bounded page selection but never lets arguments replace native provenance", async () => {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const f = fixture();
  const service = f.tool({ agentId: serviceAgentId(), sessionKey: serviceSessionKey(secret) });
  const query = { view: "details", section: "identities", binding_id: "fixture-route-17", offset: 40, snapshot: "a".repeat(64) };
  await service.execute("details", query);
  expect(f.request.mock.calls[0]).toEqual(["/api/openclaw/v1/tools/workstation-inventory", {
    ...query, provenance: expect.objectContaining({ actor_id: "service-principal:chief-of-staff" }),
  }]);
});

it("fails closed before returning an oversized complete native result", async () => {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const f = fixture();
  f.request.mockResolvedValueOnce({ contract: "x".repeat(5000), provider_effect_count: 0 });
  const service = f.tool({ agentId: serviceAgentId(), sessionKey: serviceSessionKey(secret) });
  await expect(service.execute("large", {})).rejects.toThrow("native output budget");
});


function conversation(channel: "gmail" | "administrateme-portal") {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const agent = "adminme-0123456789abcdef";
  const f = fixture([agent]);
  const text = "Which account and tool routes are available?";
  const context = { agentId: agent, sessionKey: `agent:${agent}:administrateme-${channel === "gmail" ? "gmail" : "portal"}-inventory-fixture`, runId: "inventory-human-turn" };
  const ticket = {
    source_event_id: "fixture-current-inventory-input", session_key: context.sessionKey,
    agent_id: agent, actor_id: `${channel}:fixture-member`, channel_id: channel,
    account_id: channel === "gmail" ? "service@example.invalid" : "member-portal",
    message_id: "fixture-native-message", thread_id: channel === "gmail" ? "fixture-native-thread" : "",
    text_sha256: createHash("sha256").update(text).digest("hex"), nonce: "2".repeat(32), expires_at_ms: Date.now() + 9000,
  };
  const fields = ["source_event_id", "session_key", "agent_id", "actor_id", "channel_id", "account_id", "message_id", "thread_id", "text_sha256", "nonce"] as const;
  const params = { ...ticket, signature: createHmac("sha256", secret).update(["cos-conversation-input-v1", ...fields.map((name) => ticket[name]), String(ticket.expires_at_ms)].join("|")).digest("hex") };
  const prepare = async (signature = params.signature) => {
    const respond = vi.fn();
    await f.methods.get("administrateme.conversation.prepare")!({ params: { ...params, signature }, respond });
    f.hooks.get("before_agent_reply")!({ cleanedBody: text }, context);
    return respond;
  };
  return { ...f, context, ticket, prepare };
}

it.each(["gmail", "administrateme-portal"] as const)("uses retained signed %s provenance through the registered inventory tool", async (channel) => {
  const f = conversation(channel);
  expect((await f.prepare()).mock.calls[0]?.[0]).toBe(true);
  await f.tool({ ...f.context, requesterSenderId: "ambient-wrong-actor", messageChannel: "ambient-wrong-channel" }).execute("inventory", {
    provenance: { actor_id: "model-invented" },
  });
  expect(f.request).toHaveBeenCalledOnce();
  expect(f.request.mock.calls[0]).toEqual(["/api/openclaw/v1/tools/workstation-inventory", {
    provenance: expect.objectContaining({ actor_id: f.ticket.actor_id, channel_id: channel, account_id: f.ticket.account_id,
      session_key: f.ticket.session_key, agent_id: f.ticket.agent_id, message_id: f.ticket.message_id }),
  }]);
});

it.each(["gmail", "administrateme-portal"] as const)("does not turn a forged %s ticket into inventory authority", async (channel) => {
  const f = conversation(channel);
  expect((await f.prepare("0".repeat(64))).mock.calls[0]?.[0]).toBe(false);
  await expect(f.tool(f.context).execute("forged", { provenance: f.ticket })).rejects.toThrow();
  expect(f.request).not.toHaveBeenCalled();
});

it("does not retain Gmail tool identity after its current input ends or in another session", async () => {
  const f = conversation("gmail");
  await f.prepare();
  await expect(f.tool({ ...f.context, sessionKey: f.context.sessionKey + "-other" }).execute("other-session", {})).rejects.toThrow();
  await f.hooks.get("agent_end")!({ success: false, runId: f.context.runId, messages: [] }, f.context);
  await expect(f.tool(f.context).execute("ended-input", {})).rejects.toThrow();
  expect(f.request).not.toHaveBeenCalled();
});
