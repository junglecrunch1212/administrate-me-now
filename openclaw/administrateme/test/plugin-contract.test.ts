import { readFile } from "node:fs/promises";
import { createHmac } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AnyAgentTool,
  GatewayHandlerOptions,
  OpenClawPluginApi,
  OpenClawPluginCommandDefinition,
  OpenClawPluginService,
  OpenClawPluginToolContext,
} from "openclaw/plugin-sdk/plugin-entry";
import plugin from "../src/index.js";
import { OPENCLAW_COMMIT, OPENCLAW_VERSION } from "../src/contract.js";
import { AdministrateMeTransport, TransportError } from "../src/transport.js";
import { readPluginConfig } from "../src/config.js";

type ToolFactory = (context: OpenClawPluginToolContext) => AnyAgentTool | AnyAgentTool[];
type GatewayRegistration = {
  handler: (options: GatewayHandlerOptions) => void | Promise<void>;
  scope: string | undefined;
};
type HookRegistration = {
  handler: (event: Record<string, unknown>, context: Record<string, unknown>) => unknown;
};

function fakeApi(config: Record<string, unknown> = {}) {
  const tools = new Map<string, ToolFactory>();
  const commands = new Map<string, OpenClawPluginCommandDefinition>();
  const gateways = new Map<string, GatewayRegistration>();
  const hooks = new Map<string, HookRegistration[]>();
  const services: OpenClawPluginService[] = [];
  const api = {
    id: "administrateme",
    name: "AdministrateMe",
    version: "2.0.0-gate7",
    source: "fixture",
    registrationMode: "full",
    pluginConfig: config,
    logger: {
      debug: vi.fn(),
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    },
    registerTool(factory: AnyAgentTool | ToolFactory, opts?: { name?: string }) {
      if (typeof factory !== "function" || !opts?.name) throw new Error("expected named factory");
      tools.set(opts.name, factory);
    },
    registerCommand(command: OpenClawPluginCommandDefinition) {
      commands.set(command.name, command);
    },
    registerGatewayMethod(
      method: string,
      handler: GatewayRegistration["handler"],
      opts?: { scope?: "operator.read" | "operator.write" | "operator.admin" },
    ) {
      gateways.set(method, { handler, scope: opts?.scope });
    },
    registerService(service: OpenClawPluginService) {
      services.push(service);
    },
    on(hook: string, handler: HookRegistration["handler"]) {
      const registrations = hooks.get(hook) ?? [];
      registrations.push({ handler });
      hooks.set(hook, registrations);
    },
  } as unknown as OpenClawPluginApi;
  plugin.register(api);
  return { api, tools, commands, gateways, hooks, services };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestBody(fetchMock: ReturnType<typeof vi.fn>, index = 0): Record<string, unknown> {
  const init = fetchMock.mock.calls[index]?.[1] as RequestInit;
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

function toolContext(overrides: Partial<OpenClawPluginToolContext> = {}): OpenClawPluginToolContext {
  return {
    requesterSenderId: "actor-a",
    messageChannel: "discord",
    agentAccountId: "account-a",
    agentId: "agent-a",
    sessionKey: "adminme:tenant-a:member-a:dm",
    sessionId: "session-a",
    deliveryContext: { channel: "discord", accountId: "account-a", threadId: "thread-a" },
    ...overrides,
  };
}

describe("native OpenClaw contract fixture", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    process.env.ADMINME_OPENCLAW_SHARED_SECRET = "fixture-secret-at-least-24-characters";
    fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    delete process.env.ADMINME_OPENCLAW_SHARED_SECRET;
    vi.unstubAllGlobals();
  });

  it("pins the current native manifest and registers every declared surface", async () => {
    const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
    const manifest = JSON.parse(await readFile(path.join(root, "openclaw.plugin.json"), "utf8"));
    const pkg = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
    const runtime = fakeApi();
    expect(pkg.peerDependencies.openclaw).toBe(OPENCLAW_VERSION);
    expect(pkg.openclaw.build.openclawCommit).toBe(OPENCLAW_COMMIT);
    expect([...runtime.tools]).toHaveLength(9);
    expect([...runtime.commands]).toHaveLength(7);
    expect([...runtime.gateways]).toHaveLength(11);
    expect(runtime.services).toHaveLength(1);
    expect(manifest.contracts.tools).toEqual([...runtime.tools.keys()]);
  });

  it("sends a typed tool request with full trusted provenance and no operator token body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ state: "captured", event_id: "evt-1" }));
    const runtime = fakeApi();
    const factory = runtime.tools.get("administrateme_action");
    const tool = factory?.(toolContext()) as AnyAgentTool;
    const result = await tool.execute("call-1", {
      request_id: "request-1",
      mode: "capture",
      kind: "task",
      title: "Prepare the fixture",
    });
    const body = requestBody(fetchMock);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://127.0.0.1:3330/api/openclaw/v1/tools/action");
    expect(body.provenance).toMatchObject({
      actor_id: "actor-a",
      agent_id: "agent-a",
      session_key: "adminme:tenant-a:member-a:dm",
      session_id: "session-a",
      channel_id: "discord",
      account_id: "account-a",
      thread_id: "thread-a",
      call_kind: "tool",
      openclaw_version: OPENCLAW_VERSION,
      openclaw_commit: OPENCLAW_COMMIT,
    });
    expect(JSON.stringify(body)).not.toContain("fixture-secret");
    expect(result.details).toEqual({ state: "captured", event_id: "evt-1" });
  });

  it("derives member-portal tool authority only from a valid signed isolated session", async () => {
    const peer = "0123456789abcdefabcd";
    const signature = createHmac("sha256", process.env.ADMINME_OPENCLAW_SHARED_SECRET!)
      .update(`portal-session|${peer}`)
      .digest("hex")
      .slice(0, 32);
    const sessionKey = `agent:main:administrateme-portal-${peer}-${signature}`;
    const runtime = fakeApi();
    const factory = runtime.tools.get("administrateme_state");
    const context = toolContext({
      sessionKey,
      messageChannel: "gateway",
    });
    delete context.requesterSenderId;
    delete context.agentAccountId;
    delete context.deliveryContext;
    const tool = factory?.(context) as AnyAgentTool;
    await tool.execute("portal-call", {});
    expect(requestBody(fetchMock).provenance).toMatchObject({
      actor_id: `portal:${peer}`,
      session_key: sessionKey,
      channel_id: "administrateme-portal",
      account_id: "member-portal",
      call_kind: "tool",
    });
    expect(JSON.stringify(requestBody(fetchMock))).not.toContain(
      process.env.ADMINME_OPENCLAW_SHARED_SECRET,
    );

    const forgedContext = toolContext({
      sessionKey: `agent:main:administrateme-portal-${peer}-${"0".repeat(32)}`,
    });
    delete forgedContext.requesterSenderId;
    const forged = factory?.(forgedContext) as AnyAgentTool;
    await expect(forged.execute("forged-call", {})).rejects.toThrow("invalid");
  });

  it("adds one OpenClaw approval gate only to side-effect tools", async () => {
    const runtime = fakeApi();
    const hook = runtime.hooks.get("before_tool_call")?.[0]?.handler;
    const read = await hook?.({ toolName: "administrateme_state", params: {} }, {});
    const write = (await hook?.({ toolName: "administrateme_lifecycle", params: {} }, {})) as {
      requireApproval?: { allowedDecisions?: string[]; pluginId?: string };
    };
    expect(read).toBeUndefined();
    expect(write.requireApproval?.allowedDecisions).toEqual(["allow-once", "deny"]);
    expect(write.requireApproval?.pluginId).toBe("administrateme");
  });

  it("reports notification delivery through the typed result seam", async () => {
    const runtime = fakeApi();
    const factory = runtime.tools.get("administrateme_notification_result");
    const tool = factory?.(toolContext()) as AnyAgentTool;
    await tool.execute("attempt-a", {
      attempt_id: "attempt-a",
      decision_id: "decision-a",
      signal_id: "signal-a",
      outcome: "delivered",
      attempted_at: "2026-08-03T12:00:00Z",
      provider_receipt_id: "fixture-receipt",
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:3330/api/openclaw/v1/notifications/result",
    );
    expect(requestBody(fetchMock)).toMatchObject({
      attempt_id: "attempt-a",
      outcome: "delivered",
      provenance: { call_kind: "tool" },
    });
  });

  it("derives outbound approval from the resolved tool gate rather than model input", async () => {
    const runtime = fakeApi();
    const factory = runtime.tools.get("administrateme_review");
    const tool = factory?.(toolContext()) as AnyAgentTool;
    await tool.execute("review-1", {
      request_id: "review-1",
      action: "approve",
      commitment_id: "commitment-a",
      expected_last_event_id: "event-a",
      send_acknowledgement: true,
    });
    expect(requestBody(fetchMock)).toMatchObject({
      send_acknowledgement: true,
      openclaw_approved: true,
    });
  });

  it("does not let an operator scope substitute for dashboard member identity", async () => {
    const runtime = fakeApi();
    const today = runtime.gateways.get("administrateme.dashboard.today");
    const denied = vi.fn();
    await today?.handler({
      params: {},
      client: { connect: { scopes: ["operator.admin"] } },
      respond: denied,
    });
    expect(denied).toHaveBeenCalledWith(
      false,
      undefined,
      expect.objectContaining({ code: "ADMINISTRATEME_REJECTED" }),
    );
    expect(fetchMock).not.toHaveBeenCalled();

    const accepted = vi.fn();
    await today?.handler({
      params: {},
      client: { authenticatedUserId: "actor-a", pairedClientId: "dashboard-a" },
      respond: accepted,
    });
    expect(requestBody(fetchMock).provenance).toMatchObject({
      actor_id: "actor-a",
      account_id: "dashboard-a",
      channel_id: "openclaw-dashboard",
      call_kind: "dashboard",
    });
    fetchMock.mockClear();
    const review = runtime.gateways.get("administrateme.dashboard.review");
    await review?.handler({
      params: {
        requestId: "review-dashboard",
        action: "approve",
        commitmentId: "commitment-a",
        expectedLastEventId: "event-a",
      },
      client: { authenticatedUserId: "actor-a", pairedClientId: "dashboard-a" },
      respond: vi.fn(),
    });
    expect(requestBody(fetchMock)).toMatchObject({
      send_acknowledgement: false,
      openclaw_approved: false,
    });
  });

  it("registers authorized slash dispatch and rejects an untrusted sender before transport", async () => {
    const runtime = fakeApi();
    const command = runtime.commands.get("adminme-now");
    const base = {
      channel: "discord",
      commandBody: "/adminme-now",
      senderId: "actor-a",
      accountId: "account-a",
    };
    const denied = await command?.handler({ ...base, isAuthorizedSender: false });
    expect(denied?.text).toContain("rejected");
    expect(fetchMock).not.toHaveBeenCalled();
    await command?.handler({ ...base, isAuthorizedSender: true });
    expect(requestBody(fetchMock).provenance).toMatchObject({
      actor_id: "actor-a",
      call_kind: "slash_command",
    });
  });

  it("invokes standing orders with direct-route provenance and no plugin scheduler", async () => {
    const runtime = fakeApi();
    const factory = runtime.tools.get("administrateme_standing_order");
    const context = toolContext({
      deliveryContext: { channel: "discord", to: "actor-a", accountId: "account-a" },
    });
    delete context.requesterSenderId;
    const tool = factory?.(context) as AnyAgentTool;
    await tool.execute("standing-1", { program_id: "morning_digest", automation_id: "auto-1" });
    expect(requestBody(fetchMock)).toMatchObject({
      trigger_id: "standing-1",
      provenance: {
      actor_id: "actor-a",
      call_kind: "standing_order",
      },
    });
  });

  it("bridges only explicit allowlisted consent events and excludes generic or privileged content", async () => {
    const runtime = fakeApi({
      bridgeEnabled: true,
      bridgeChannels: ["discord"],
      bridgeEventKinds: ["household_action"],
    });
    const hook = runtime.hooks.get("message_received")?.[0]?.handler;
    const context = { channelId: "discord", accountId: "account-a", senderId: "actor-a" };
    await hook?.({ from: "actor-a", senderId: "actor-a", content: "generic chat" }, context);
    await hook?.(
      {
        from: "actor-a",
        senderId: "actor-a",
        content: "privileged text",
        metadata: { administrateme: { consent: true, eventKind: "household_action", sensitivity: "privileged" } },
      },
      context,
    );
    expect(fetchMock).not.toHaveBeenCalled();
    await hook?.(
      {
        from: "actor-a",
        senderId: "actor-a",
        messageId: "message-a",
        content: "capture this action",
        metadata: { administrateme: { consent: true, eventKind: "household_action", sensitivity: "sensitive" } },
      },
      context,
    );
    expect(requestBody(fetchMock)).toMatchObject({
      event_kind: "household_action",
      consent: true,
      sensitivity: "sensitive",
    });
  });

  it("records model metadata only for a tracked AdministrateMe session", async () => {
    const runtime = fakeApi();
    const stateFactory = runtime.tools.get("administrateme_state");
    await (stateFactory?.(toolContext()) as AnyAgentTool).execute("state-1", {});
    fetchMock.mockClear();
    const hook = runtime.hooks.get("model_call_started")?.[0]?.handler;
    await hook?.(
      {
        runId: "run-a",
        callId: "call-a",
        sessionKey: "adminme:tenant-a:member-a:dm",
        provider: "fixture",
        model: "deterministic-v1",
        prompt: "must never cross",
        response: "must never cross",
      },
      { sessionKey: "adminme:tenant-a:member-a:dm" },
    );
    const serialized = JSON.stringify(requestBody(fetchMock));
    expect(serialized).toContain("deterministic-v1");
    expect(serialized).not.toContain("must never cross");
    fetchMock.mockClear();
    await hook?.(
      { runId: "run-b", callId: "call-b", provider: "x", model: "y" },
      { sessionKey: "generic:conversation" },
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed on incompatible host responses without exposing the secret", async () => {
    const config = readPluginConfig(undefined);
    const transport = new AdministrateMeTransport(
      config,
      vi.fn(async () => jsonResponse({ private: "remote diagnostics" }, 409)) as typeof fetch,
    );
    let caught: unknown;
    try {
      await transport.request("/api/openclaw/v1/health/report", {});
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(TransportError);
    expect((caught as TransportError).state).toBe("incompatible");
    expect(String(caught)).not.toContain("fixture-secret");
    expect(String(caught)).not.toContain("remote diagnostics");
  });
});
