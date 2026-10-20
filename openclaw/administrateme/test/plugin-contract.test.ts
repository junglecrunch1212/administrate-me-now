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
import { approvalRequestFingerprint } from "../src/approval.js";
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

function fakeApi(
  config: Record<string, unknown> = {},
  registrationMode = "full",
) {
  const tools = new Map<string, ToolFactory>();
  const commands = new Map<string, OpenClawPluginCommandDefinition>();
  const gateways = new Map<string, GatewayRegistration>();
  const hooks = new Map<string, HookRegistration[]>();
  const services: OpenClawPluginService[] = [];
  const api = {
    id: "administrateme",
    name: "AdministrateMe",
    version: "2.0.0-gate8",
    source: "fixture",
    registrationMode: registrationMode as OpenClawPluginApi["registrationMode"],
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
  it("matches Python approval fingerprints for Unicode and exponent values", () => {
    expect(approvalRequestFingerprint({ text: "member-fixture’s café ☕" })).toBe(
      "560b5f9157897df23b027a410d40b2b93d8586b6bd3a8ed62891b29d20f473c6",
    );
    expect(approvalRequestFingerprint({ text: "emoji 😀" })).toBe(
      "b63e44cf7a757490e5fc318b0140a209ebc306955926e0f608d86c612f7ec2d8",
    );
    expect(approvalRequestFingerprint({ value: 1e-7 })).toBe(
      "aece37dfda4992947222ea73b79996bba4aa181345a1fbb7f8c2b56b3fbd6a44",
    );
    expect(approvalRequestFingerprint({ value: 1e-6 })).toBe(
      "40eb230f5af7b02c83c7be700ea8d95c3f2862ff1cbac50d0da498597e9cfaad",
    );
  });
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    process.env.ADMINME_OPENCLAW_SHARED_SECRET = "fixture-secret-at-least-24-characters";
    fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    delete process.env.ADMINME_OPENCLAW_SHARED_SECRET;
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("pins the current native manifest and registers every declared surface", async () => {
    const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
    const manifest = JSON.parse(await readFile(path.join(root, "openclaw.plugin.json"), "utf8"));
    const pkg = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
    const runtime = fakeApi();
    expect(pkg.peerDependencies.openclaw).toBe(OPENCLAW_VERSION);
    expect(pkg.openclaw.build.openclawCommit).toBe(OPENCLAW_COMMIT);
    expect([...runtime.tools]).toHaveLength(23);
    expect([...runtime.commands]).toHaveLength(9);
    expect([...runtime.gateways]).toHaveLength(14);
    expect(runtime.gateways.has("administrateme.dashboard.reminders")).toBe(true);
    expect(runtime.gateways.has("administrateme.dashboard.notes")).toBe(true);
    expect(runtime.services).toHaveLength(1);
    expect(manifest.contracts.tools).toEqual([...runtime.tools.keys()]);
  });

  it("cancels ordinary CoS replies and blocks generic delivery tool bypasses", async () => {
    const runtime = fakeApi({
      forwardIntakeAccounts: [{ channelId: "imessage", accountId: "cos-fixture" }],
    });
    const sending = runtime.hooks.get("message_sending")?.[0]?.handler;
    expect(sending?.({ to: "+12025550101", content: "unapproved" }, {
      channelId: "imessage", accountId: "cos-fixture",
    })).toMatchObject({ cancel: true });
    expect(sending?.({ to: "+12025550101", content: "unapproved" }, {
      channelId: "imessage",
    })).toMatchObject({ cancel: true });
    expect(sending?.({ to: "fixture", content: "ordinary" }, {
      channelId: "discord", accountId: "other-fixture",
    })).toBeUndefined();
    const before = runtime.hooks.get("before_tool_call")?.[0]?.handler;
    expect(await before?.({ toolName: "message", params: { channel: "imessage" } }, {}))
      .toMatchObject({ block: true });
    expect(await before?.({ toolName: "sessions_send", params: {} }, {}))
      .toMatchObject({ block: true });
    expect(await before?.({
      toolName: "administrateme_cos_response",
      params: { request_id: "fixture-response", approval_event_id: "ev_fixture" },
    }, {})).toMatchObject({ requireApproval: { allowedDecisions: ["allow-once", "deny"] } });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([false, true])("privately dispatches one admitted iMessage without transport retry (failure=%s)", async (failure) => {
    const runtime = fakeApi({
      forwardIntakeAccounts: [{ channelId: "imessage", accountId: "cos-fixture" }],
    });
    const sendText = vi.fn(async () => {
      if (failure) throw new Error("ambiguous native timeout");
      return { channel: "imessage", messageId: "fixture-native" };
    });
    runtime.api.config = { channels: { imessage: { accounts: { "cos-fixture": {} } } } };
    runtime.api.runtime = {
      channel: { outbound: { loadAdapter: vi.fn(async () => ({ sendText })) } },
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ transport_dispatch: {
        channel: "imessage", account_id: "cos-fixture",
        recipient: "+12025550101", body: "Exact private response",
        approval_event_id: "ev_response",
      } }))
      .mockResolvedValueOnce(jsonResponse({
        status: failure ? "unknown" : "sent", automatic_retry_allowed: false,
      }));
    const factory = runtime.tools.get("administrateme_cos_response");
    const made = factory?.(toolContext());
    const tool = Array.isArray(made) ? made[0] : made;
    const result = await tool?.execute("call-fixture", {
      request_id: "response-fixture", approval_event_id: "ev_response",
    });
    expect(sendText).toHaveBeenCalledTimes(1);
    expect(sendText).toHaveBeenCalledWith({
      cfg: runtime.api.config, to: "+12025550101", text: "Exact private response",
      accountId: "cos-fixture",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(requestBody(fetchMock, 1)).toMatchObject({ approval_event_id: "ev_response" });
    expect(JSON.stringify(result)).not.toContain("Exact private response");
    expect(JSON.stringify(result)).not.toContain("+12025550101");
    expect(JSON.stringify(result)).toContain(failure ? "unknown" : "sent");
  });

  it("registers dashboard ownership during read-only OpenClaw discovery", () => {
    const runtime = fakeApi({}, "discovery");
    expect([...runtime.gateways]).toHaveLength(14);
    expect(runtime.tools).toHaveLength(0);
    expect(runtime.commands).toHaveLength(0);
    expect(runtime.hooks).toHaveLength(0);
    expect(runtime.services).toHaveLength(0);
  });

  it("reconciles plugin health after the runtime starts later", async () => {
    vi.useFakeTimers();
    const healthFetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("loopback runtime unavailable"))
      .mockResolvedValue(
        jsonResponse({ event_id: "ev_health", state: "healthy" }),
      );
    vi.stubGlobal("fetch", healthFetch);
    const runtime = fakeApi();
    const service = runtime.services[0];
    expect(service).toBeDefined();

    const started = service?.start();
    expect(started).toBeUndefined();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(healthFetch).toHaveBeenCalledTimes(1);
    expect(runtime.api.logger.warn).toHaveBeenCalledWith(
      expect.stringContaining("retrying in 1000ms"),
    );

    await vi.advanceTimersByTimeAsync(1_000);
    expect(healthFetch).toHaveBeenCalledTimes(2);
    expect(requestBody(healthFetch, 1)).toMatchObject({
      state: "healthy",
      openclaw_version: OPENCLAW_VERSION,
      openclaw_commit: OPENCLAW_COMMIT,
    });
    expect(runtime.api.logger.info).toHaveBeenCalledWith(
      expect.stringContaining("AdministrateMe plugin ready"),
    );

    await vi.advanceTimersByTimeAsync(30_000);
    expect(healthFetch).toHaveBeenCalledTimes(3);
    expect(requestBody(healthFetch, 2)).toMatchObject({ state: "healthy" });

    await service?.stop();
    expect(healthFetch).toHaveBeenCalledTimes(4);
    expect(requestBody(healthFetch, 3)).toMatchObject({ state: "unavailable" });
  });

  it("cancels pending health reconciliation when the plugin stops", async () => {
    vi.useFakeTimers();
    const healthFetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("loopback runtime unavailable"))
      .mockResolvedValue(jsonResponse({ event_id: "ev_stopped", state: "unavailable" }));
    vi.stubGlobal("fetch", healthFetch);
    const runtime = fakeApi();
    const service = runtime.services[0];

    service?.start();
    await Promise.resolve();
    await Promise.resolve();
    await service?.stop();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(healthFetch).toHaveBeenCalledTimes(2);
    expect(requestBody(healthFetch, 1)).toMatchObject({ state: "unavailable" });
  });

  it("exposes tools without starting services during tool discovery", () => {
    const runtime = fakeApi({}, "tool-discovery");
    expect([...runtime.gateways]).toHaveLength(14);
    expect([...runtime.tools]).toHaveLength(23);
    expect(runtime.services).toHaveLength(0);
  });

  it("sends a typed tool request with full trusted provenance and no operator token body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ state: "pending_review", intent_event_id: "evt-1" }));
    const runtime = fakeApi();
    const factory = runtime.tools.get("administrateme_add_for_review");
    const tool = factory?.(toolContext()) as AnyAgentTool;
    const result = await tool.execute("call-1", {
      request_id: "request-1",
      text: "Prepare the fixture",
      kind_hint: "task",
    });
    const body = requestBody(fetchMock);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://127.0.0.1:3330/api/openclaw/v1/tools/add-for-review");
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
    expect(result.details).toEqual({ state: "pending_review", intent_event_id: "evt-1" });
  });

  it("routes typed CoS work creation and lifecycle through the native plugin seam", async () => {
    const runtime = fakeApi();
    const createFactory = runtime.tools.get("administrateme_service_work");
    const createTool = createFactory?.(toolContext()) as AnyAgentTool;
    await createTool.execute("work-call", {
      request_id: "fixture-work-request",
      service_assignment_role: "internal",
      service_work_type: "reconciliation",
      title: "Prepare fixture reconciliation",
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:3330/api/openclaw/v1/tools/service-work",
    );
    expect(requestBody(fetchMock)).toMatchObject({
      request_id: "fixture-work-request",
      service_assignment_role: "internal",
      service_work_type: "reconciliation",
    });

    const stateFactory = runtime.tools.get("administrateme_service_work_state");
    const stateTool = stateFactory?.(toolContext()) as AnyAgentTool;
    await stateTool.execute("state-call", {
      task_id: "cos-task:fixture/value",
      request_id: "fixture-state-request",
      expected_last_event_id: "ev_fixture",
      action: "complete",
      reason_code: "completed",
    });
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://127.0.0.1:3330/api/openclaw/v1/tools/service-work/cos-task%3Afixture%2Fvalue/state",
    );
    const stateBody = requestBody(fetchMock, 1);
    expect(stateBody.task_id).toBeUndefined();
    expect(stateBody).toMatchObject({
      request_id: "fixture-state-request",
      expected_last_event_id: "ev_fixture",
      action: "complete",
      reason_code: "completed",
    });
  });

  it("routes the catalog, compiled context, capability request, and policy proposal tools", async () => {
    const runtime = fakeApi();
    const catalog = runtime.tools.get("administrateme_capabilities")?.(
      toolContext(),
    ) as AnyAgentTool;
    await catalog.execute("catalog-call", {
      capability_id: "planned.apple_messages_reply",
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:3330/api/openclaw/v1/tools/capabilities",
    );
    expect(requestBody(fetchMock)).toMatchObject({
      capability_id: "planned.apple_messages_reply",
      provenance: { call_kind: "tool", account_id: "account-a" },
    });

    const context = runtime.tools.get("administrateme_settings_context")?.(
      toolContext(),
    ) as AnyAgentTool;
    await context.execute("context-call", {});
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://127.0.0.1:3330/api/openclaw/v1/tools/settings-context",
    );

    const capabilityRequest = runtime.tools.get("administrateme_capability_request")?.(
      toolContext(),
    ) as AnyAgentTool;
    await capabilityRequest.execute("capability-request-call", {
      request_id: "capability-request-a",
      expected_revision: 0,
      capability_id: "planned.apple_messages_observation",
      action: "request",
      requested_scope: "member_private",
    });
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "http://127.0.0.1:3330/api/openclaw/v1/tools/capability-request",
    );

    const proposal = runtime.tools.get("administrateme_policy_proposal")?.(
      toolContext(),
    ) as AnyAgentTool;
    await proposal.execute("proposal-call", { action: "inspect" });
    expect(fetchMock.mock.calls[3]?.[0]).toBe(
      "http://127.0.0.1:3330/api/openclaw/v1/tools/policy-proposal",
    );
    expect(requestBody(fetchMock, 3)).toMatchObject({
      action: "inspect",
      provenance: { actor_id: "actor-a", call_kind: "tool" },
    });
  });

  it("derives member-portal tool authority only from a valid signed isolated session", async () => {
    const peer = "0123456789abcdefabcd";
    const signature = createHmac("sha256", process.env.ADMINME_OPENCLAW_SHARED_SECRET!)
      .update(`portal-session|${peer}`)
      .digest("hex")
      .slice(0, 32);
    const agentId = "adminme-0123456789abcdef";
    const sessionKey = `agent:${agentId}:administrateme-portal-${peer}-${signature}`;
    const runtime = fakeApi();
    const factory = runtime.tools.get("administrateme_state");
    const context = toolContext({
      sessionKey,
      messageChannel: "gateway",
      agentId,
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
      agent_id: agentId,
      call_kind: "tool",
    });
    expect(JSON.stringify(requestBody(fetchMock))).not.toContain(
      process.env.ADMINME_OPENCLAW_SHARED_SECRET,
    );

    const forgedContext = toolContext({
      sessionKey: `agent:${agentId}:administrateme-portal-${peer}-${"0".repeat(32)}`,
      agentId,
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

    const capabilityRequest = (await hook?.(
      {
        toolName: "administrateme_capability_request",
        params: {
          request_id: "capability-request-a",
          expected_revision: 0,
          capability_id: "planned.apple_messages_observation",
          action: "request",
        },
      },
      {},
    )) as { requireApproval?: { allowedDecisions?: string[] } };
    expect(capabilityRequest.requireApproval?.allowedDecisions).toEqual([
      "allow-once",
      "deny",
    ]);

    const inspect = await hook?.(
      {
        toolName: "administrateme_policy_proposal",
        params: { action: "inspect" },
      },
      {},
    );
    expect(inspect).toBeUndefined();
    const decide = (await hook?.(
      {
        toolName: "administrateme_policy_proposal",
        params: { action: "decide" },
      },
      {},
    )) as { requireApproval?: { allowedDecisions?: string[] } };
    expect(decide.requireApproval?.allowedDecisions).toEqual(["allow-once", "deny"]);
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

  it("binds outbound approval to a signed allow-once resolution rather than model input", async () => {
    const runtime = fakeApi();
    const params = {
      request_id: "review-1",
      action: "approve",
      commitment_id: "commitment-a",
      expected_last_event_id: "event-a",
      send_acknowledgement: true,
    };
    const hook = runtime.hooks.get("before_tool_call")?.[0]?.handler;
    const gated = (await hook?.(
      { toolName: "administrateme_review", params },
      {},
    )) as {
      params: Record<string, unknown>;
      requireApproval: { onResolution: (decision: "allow-once") => Promise<void> };
    };
    await gated.requireApproval.onResolution("allow-once");
    const resolution = requestBody(fetchMock);
    expect(resolution).toMatchObject({
      tool_name: "administrateme_review",
      decision: "allow-once",
      openclaw_version: OPENCLAW_VERSION,
      openclaw_commit: OPENCLAW_COMMIT,
    });
    expect(resolution.signature).toMatch(/^[0-9a-f]{64}$/);
    fetchMock.mockResolvedValueOnce(jsonResponse({ accepted: true }));
    const factory = runtime.tools.get("administrateme_review");
    const tool = factory?.(toolContext()) as AnyAgentTool;
    await tool.execute("review-1", gated.params);
    const reviewBody = requestBody(fetchMock, 1);
    expect(reviewBody).toMatchObject({
      send_acknowledgement: true,
      openclaw_approval_receipt_id: resolution.receipt_id,
    });
    expect(reviewBody).not.toHaveProperty("openclaw_approved");
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
    });
    expect(requestBody(fetchMock)).not.toHaveProperty("openclaw_approved");
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

  it("captures a native forward only on a reviewed dedicated CoS endpoint", async () => {
    const runtime = fakeApi({
      forwardIntakeAccounts: [{ channelId: "imessage", accountId: "cos-fixture" }],
    });
    const hook = runtime.hooks.get("message_received")?.[0]?.handler;
    const inbound = { from: "actor-a", senderId: "actor-a", messageId: "native-forward-1", content: "Forwarded obligation: submit the form by Friday." };
    await hook?.(inbound, { channelId: "imessage", accountId: "conversation-fixture", senderId: "actor-a" });
    await hook?.(inbound, { channelId: "discord", accountId: "cos-fixture", senderId: "actor-a" });
    expect(fetchMock).not.toHaveBeenCalled();
    const context = { channelId: "imessage", accountId: "cos-fixture", senderId: "actor-a" };
    await hook?.(inbound, context);
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/submissions/forward");
    const first = requestBody(fetchMock);
    expect(first).toMatchObject({
      text: inbound.content, consent: true, submission_gesture: "dedicated_intake",
      unsupported_attachment_count: 0,
      provenance: { channel_id: "imessage", account_id: "cos-fixture", actor_id: "actor-a", message_id: "native-forward-1", call_kind: "bridge" },
    });
    await hook?.(inbound, context);
    expect(requestBody(fetchMock, 1).request_id).toBe(first.request_id);
    await hook?.({ ...inbound, messageId: "native-attachment", metadata: { attachments: [{ mimeType: "image/png" }] } }, context);
    expect(requestBody(fetchMock, 2).unsupported_attachment_count).toBe(1);
  });

  it("never downgrades declared sensitivity on a dedicated intake endpoint", async () => {
    const runtime = fakeApi({ forwardIntakeAccounts: [{ channelId: "imessage", accountId: "cos-fixture" }] });
    const hook = runtime.hooks.get("message_received")?.[0]?.handler;
    const context = { channelId: "imessage", accountId: "cos-fixture", senderId: "actor-a" };
    const inbound = { from: "actor-a", senderId: "actor-a", messageId: "native-sensitive", content: "Selected material" };
    for (const value of ["privileged", "unknown", null, 1]) {
      await expect(hook?.({ ...inbound, metadata: { administrateme: { sensitivity: value } } }, context)).rejects.toThrow("sensitivity");
      await expect(hook?.({ ...inbound, metadata: { sensitivity: value, administrateme: { sensitivity: "normal" } } }, context)).rejects.toThrow("sensitivity");
    }
    expect(fetchMock).not.toHaveBeenCalled();
    await hook?.({ ...inbound, metadata: { sensitivity: "sensitive", administrateme: { sensitivity: "normal" } } }, context);
    expect(requestBody(fetchMock).sensitivity).toBe("sensitive");
    await hook?.({ ...inbound, metadata: { administrateme: { sensitivity: "sensitive" } } }, context);
    expect(requestBody(fetchMock, 1).sensitivity).toBe("sensitive");
  });

  it("does not silently accept lost, oversized, or unidentified dedicated submissions", async () => {
    const runtime = fakeApi({ forwardIntakeAccounts: [{ channelId: "imessage", accountId: "cos-fixture" }] });
    const hook = runtime.hooks.get("message_received")?.[0]?.handler;
    const context = { channelId: "imessage", accountId: "cos-fixture", senderId: "actor-a" };
    await expect(hook?.({ from: "actor-a", content: "missing native receipt" }, context)).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
    await expect(hook?.({ from: "actor-a", messageId: "native-large", content: "x".repeat(32_001) }, context)).rejects.toThrow();
    fetchMock.mockRejectedValue(new TypeError("unavailable"));
    await expect(hook?.({ from: "actor-a", senderId: "actor-a", messageId: "native-failed", content: "selected obligation" }, context)).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(runtime.api.logger.error).toHaveBeenCalledWith(expect.stringContaining("not acknowledged"));
  });

  it("requires explicit authorization for the optional forward slash command", async () => {
    const runtime = fakeApi();
    const command = runtime.commands.get("adminme-forward");
    const context = { channel: "imessage", accountId: "cos-fixture", senderId: "actor-a", isAuthorizedSender: false, commandBody: "/adminme-forward selected text", args: "selected text" };
    await command?.handler(context);
    expect(fetchMock).not.toHaveBeenCalled();
    await command?.handler({ ...context, isAuthorizedSender: true });
    expect(requestBody(fetchMock)).toMatchObject({ text: "selected text", consent: true, provenance: { actor_id: "actor-a", call_kind: "slash_command" } });
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
