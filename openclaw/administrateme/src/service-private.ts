import { createHash, createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { Type } from "typebox";
import type { AnyAgentTool, OpenClawPluginApi, OpenClawPluginToolContext } from "openclaw/plugin-sdk/plugin-entry";
import { OPENCLAW_COMMIT, OPENCLAW_VERSION, type Provenance } from "./contract.js";
import type { PluginRuntime } from "./runtime.js";
import { toolResult } from "./result.js";

export const COS_SERVICE_PRINCIPAL_ID = "service-principal:chief-of-staff";
export const SERVICE_PRIVATE_TOOL_NAMES = [
  "administrateme_service_private_work",
  "administrateme_service_private_work_state",
  "administrateme_service_private_state",
  "administrateme_service_context",
] as const;

export function serviceAgentId(principalId = COS_SERVICE_PRINCIPAL_ID): string {
  return `am-service-${createHash("sha256").update(principalId).digest("hex").slice(0, 16)}`;
}

export function serviceSessionKey(secret: string, principalId = COS_SERVICE_PRINCIPAL_ID, requestId?: string): string {
  if (secret.length < 24) throw new Error("Service session credential is unavailable");
  const digest = requestId === undefined ? undefined : createHash("sha256").update(requestId).digest("hex").slice(0, 32);
  const material = `service-session|${principalId}${digest ? `|${digest}` : ""}`;
  const signature = createHmac("sha256", secret).update(material).digest("hex").slice(0, 32);
  return `agent:${serviceAgentId(principalId)}:administrateme-service-${digest ? `${digest}-` : ""}${signature}`;
}

function validServiceSession(secret: string, candidate: string): boolean {
  const prefix = `agent:${serviceAgentId()}:administrateme-service-`;
  if (!candidate.startsWith(prefix)) return false;
  const suffix = candidate.slice(prefix.length);
  if (!/^(?:[0-9a-f]{32}-)?[0-9a-f]{32}$/.test(suffix)) return false;
  const digest = suffix.includes("-") ? suffix.split("-")[0] : undefined;
  const signature = createHmac("sha256", secret)
    .update(`service-session|${COS_SERVICE_PRINCIPAL_ID}${digest ? `|${digest}` : ""}`)
    .digest("hex").slice(0, 32);
  const expected = Buffer.from(`${prefix}${digest ? `${digest}-` : ""}${signature}`, "utf8");
  const supplied = Buffer.from(candidate, "utf8");
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

export function provenanceFromServiceTool(
  context: OpenClawPluginToolContext & { channelId?: unknown; requester?: unknown },
  sharedSecretEnv = "ADMINME_OPENCLAW_SHARED_SECRET",
  enabled = true,
): Provenance {
  if (!enabled) throw new Error("Service-private work is disabled");
  // Only native agent/session context supplies identity. Tool parameters, an
  // operator token or a member delivery route never become service authority.
  // Pinned OpenClaw uses webchat as its INTERNAL_MESSAGE_CHANNEL even for
  // non-delivery Gateway agent runs. It is safe only without any recipient.
  const requester = context.requester as { channel?: unknown; senderId?: unknown; accountId?: unknown; roleIds?: unknown } | undefined;
  const memberRequester = context.requester !== undefined && (
    !requester || typeof requester !== "object" || requester.channel !== "webchat" ||
    [requester.senderId, requester.accountId, requester.roleIds].some((value) => value !== undefined && value !== null)
  );
  const delivery = context.deliveryContext;
  const routed = delivery !== undefined && delivery !== null && (
    delivery.channel !== "webchat" ||
    Object.entries(delivery).some(([key, value]) => key !== "channel" && value !== undefined && value !== null)
  );
  if (routed || (context.messageChannel !== undefined && context.messageChannel !== "webchat") ||
    (context.channelId !== undefined && context.channelId !== "webchat") || memberRequester ||
    [context.requesterSenderId, context.nativeChannelId, context.agentAccountId]
      .some((value) => value !== undefined && value !== null)) {
    throw new Error("Service-private work cannot run in a member or delivery context");
  }
  const secret = process.env[sharedSecretEnv];
  if (!secret || context.agentId !== serviceAgentId() || !context.sessionKey) {
    throw new Error("Service-private work requires its dedicated native agent session");
  }
  if (secret.length < 24 || !validServiceSession(secret, context.sessionKey)) {
    throw new Error("Service-private native session binding is invalid");
  }
  return {
    actor_id: COS_SERVICE_PRINCIPAL_ID,
    agent_id: context.agentId,
    session_key: context.sessionKey,
    ...(context.sessionId ? { session_id: context.sessionId } : {}),
    channel_id: "administrateme-service",
    correlation_id: randomUUID(),
    call_kind: "tool",
    openclaw_version: OPENCLAW_VERSION,
    openclaw_commit: OPENCLAW_COMMIT,
  };
}

export function registerServicePrivateTools(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  const requestId = Type.String({ minLength: 8, maxLength: 120 });
  const definitions = [
    {
      name: SERVICE_PRIVATE_TOOL_NAMES[0],
      description: "Create internal work, notes or scratchpads. Sources consumed in this run are retained automatically alongside explicit references; current restrictions apply. No provider action or timer is authorized.",
      parameters: Type.Object({
        request_id: requestId,
        title: Type.String({ minLength: 1, maxLength: 500 }),
        description: Type.Optional(Type.String({ maxLength: 2000 })),
        source_event_ids: Type.Optional(Type.Array(Type.String({ minLength: 1, maxLength: 240 }), { maxItems: 20 })),
        record_kind: Type.Optional(Type.Union([Type.Literal("work"), Type.Literal("note"), Type.Literal("scratchpad")], { default: "work" })),
        service_work_type: Type.Optional(Type.Union([
          Type.Literal("operational_task"), Type.Literal("waiting_for"), Type.Literal("follow_up"),
          Type.Literal("clarification"), Type.Literal("exception"), Type.Literal("preparation"),
          Type.Literal("held_action"), Type.Literal("provider_failure"), Type.Literal("reconciliation"),
          Type.Literal("operational_calendar"), Type.Literal("reminder_like"),
        ])),
        due: Type.Optional(Type.String()),
        follow_up_at: Type.Optional(Type.String()),
      }, { additionalProperties: false }),
      path: "/api/openclaw/v1/tools/service-private-work",
    },
    {
      name: SERVICE_PRIVATE_TOOL_NAMES[1],
      description: "Advance one private internal CoS work item at its expected revision. Completion records work state, never provider delivery.",
      parameters: Type.Object({
        task_id: Type.String({ minLength: 1, maxLength: 240 }),
        request_id: requestId,
        expected_last_event_id: Type.String({ minLength: 1, maxLength: 240 }),
        action: Type.Union([Type.Literal("start"), Type.Literal("resume"), Type.Literal("hold"),
          Type.Literal("follow_up"), Type.Literal("complete"), Type.Literal("cancel")]),
        reason_code: Type.Union([Type.Literal("clarification"), Type.Literal("provider_failure"), Type.Literal("reconciliation"),
          Type.Literal("scheduled_hold"), Type.Literal("completed"), Type.Literal("cancelled"), Type.Literal("other")]),
        follow_up_at: Type.Optional(Type.String()),
      }, { additionalProperties: false }),
      path: (params: Record<string, unknown>) => `/api/openclaw/v1/tools/service-private-work/${encodeURIComponent(String(params.task_id))}/state`,
      pathParam: "task_id",
    },
    {
      name: SERVICE_PRIVATE_TOOL_NAMES[2],
      description: "Read this service principal's private internal work and bounded typed audit history. This grants no member or provider access.",
      parameters: Type.Object({
        task_id: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
        work_offset: Type.Optional(Type.Integer({ minimum: 0, maximum: 1000000, default: 0 })),
        after_event_id: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
        limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100, default: 50 })),
      }, { additionalProperties: false }),
      path: "/api/openclaw/v1/tools/service-private-state",
    },
    {
      name: SERVICE_PRIVATE_TOOL_NAMES[3],
      description: "Read authorized household facts and metadata plus your own acquired Google Calendar/Tasks current facts and Gmail, including unrouted mail. Effective policy permits headers or bounded plaintext snippets. Retain source references; no HTML, attachments or outside disclosure rights.",
      parameters: Type.Object({
        source_event_ids: Type.Optional(Type.Array(Type.String({ minLength: 1, maxLength: 240 }), { maxItems: 20 })),
        after_event_id: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
        limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 50, default: 20 })),
      }, { additionalProperties: false }),
      path: "/api/openclaw/v1/tools/service-context",
    },
  ];
  for (const definition of definitions) {
    api.registerTool((context): AnyAgentTool => ({
      name: definition.name,
      label: definition.name,
      description: definition.description,
      parameters: definition.parameters,
      async execute(_id, params) {
        const provenance = provenanceFromServiceTool(context, runtime.transport.config.sharedSecretEnv, runtime.transport.config.serviceWorkEnabled);
        const values = { ...params };
        const path = typeof definition.path === "function" ? definition.path(values) : definition.path;
        if (definition.pathParam) delete values[definition.pathParam];
        return toolResult(await runtime.transport.request(path, { ...values, provenance }));
      },
    }), { name: definition.name, optional: true });
  }
}

/** Start the configured service principal without exposing its session binding.
 * Operator permission admits a run; domain grants still decide every data read.
 */
export function registerServiceWorkGateway(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  api.registerGatewayMethod("administrateme.service.run", async ({ params, respond }) => {
    const allowedKeys = new Set(["message", "request_id"]);
    if (!runtime.transport.config.serviceWorkEnabled) {
      respond(false, undefined, { code: "UNAVAILABLE", message: "Service work is disabled" });
      return;
    }
    if (Object.keys(params).some((key) => !allowedKeys.has(key)) ||
        typeof params.message !== "string" || !params.message.trim() || params.message.length > 8000 ||
        typeof params.request_id !== "string" || params.request_id.length < 8 || params.request_id.length > 120) {
      respond(false, undefined, { code: "INVALID_REQUEST", message: "Expected bounded message and request_id only" });
      return;
    }
    try {
      const secret = process.env[runtime.transport.config.sharedSecretEnv];
      if (!secret || !api.runtime?.subagent) throw new Error("Native service runtime unavailable");
      const result = await api.runtime.subagent.run({
        // Core admission dedupe expires. A fresh nonce keeps a later miss from
        // reopening an old transcript after its source grants were revoked.
        sessionKey: serviceSessionKey(secret, COS_SERVICE_PRINCIPAL_ID, randomUUID()),
        message: params.message,
        idempotencyKey: `administrateme-service:${params.request_id}`,
        deliver: false,
      });
      // Never return the credential-derived session key or transcript here.
      respond(true, { status: "accepted", run_id: result.runId, service_principal_id: COS_SERVICE_PRINCIPAL_ID });
    } catch {
      respond(false, undefined, { code: "UNAVAILABLE", message: "Native service run could not be admitted" });
    }
  }, { scope: "operator.admin" });
}
