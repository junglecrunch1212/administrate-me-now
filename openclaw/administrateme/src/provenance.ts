import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { conversationToolProvenance } from "./conversation-input.js";
import type { PluginRuntime } from "./runtime.js";
import type {
  OpenClawPluginToolContext,
  PluginCommandContext,
  PluginHookMessageReceivedEvent,
} from "openclaw/plugin-sdk/plugin-entry";
import type { GatewayClient, PluginHookMessageContext } from "./sdk-types.js";
import {
  OPENCLAW_COMMIT,
  OPENCLAW_VERSION,
  type CallKind,
  type Provenance,
} from "./contract.js";

function required(value: string | undefined, label: string): string {
  const normalized = value?.trim();
  if (!normalized) {
    throw new Error(`AdministrateMe requires trusted ${label} provenance`);
  }
  return normalized;
}

function base(callKind: CallKind, actorId: string, channelId: string): Provenance {
  return {
    actor_id: required(actorId, "actor"),
    channel_id: required(channelId, "channel"),
    correlation_id: randomUUID(),
    call_kind: callKind,
    openclaw_version: OPENCLAW_VERSION,
    openclaw_commit: OPENCLAW_COMMIT,
  };
}

export function provenanceFromTool(
  context: OpenClawPluginToolContext,
  callKind: "tool" | "standing_order" = "tool",
  sharedSecretEnv = "ADMINME_OPENCLAW_SHARED_SECRET",
  runtime?: PluginRuntime,
): Provenance {
  const conversation = runtime && callKind === "tool" ? conversationToolProvenance(runtime, context) : undefined;
  if (conversation) return conversation;
  const sessionKey = context.sessionKey ?? "";
  if (context.agentId?.startsWith("am-service-") || sessionKey.includes(":administrateme-service-")) {
    throw new Error("Service-private sessions cannot invoke member tools");
  }
  const portal = /^agent:(adminme-[0-9a-f]{16}):administrateme-portal-([0-9a-f]{20})-([0-9a-f]{32})$/.exec(sessionKey);
  if (portal && callKind === "tool") {
    const agentId = portal[1]!;
    const peer = portal[2]!;
    const signature = portal[3]!;
    const secret = process.env[sharedSecretEnv];
    if (!secret || secret.length < 24) {
      throw new Error("AdministrateMe portal provenance credential is unavailable");
    }
    const expected = createHmac("sha256", secret)
      .update(`portal-session|${peer}`)
      .digest("hex")
      .slice(0, 32);
    const suppliedBuffer = Buffer.from(signature, "utf8");
    const expectedBuffer = Buffer.from(expected, "utf8");
    if (!timingSafeEqual(suppliedBuffer, expectedBuffer)) {
      throw new Error("AdministrateMe portal session provenance is invalid");
    }
    const result = base("tool", `portal:${peer}`, "administrateme-portal");
    result.account_id = "member-portal";
    result.session_key = sessionKey;
    result.agent_id = agentId;
    if (context.sessionId) result.session_id = context.sessionId;
    if (context.agentId && context.agentId !== agentId) {
      throw new Error("AdministrateMe portal agent provenance is contradictory");
    }
    return result;
  }
  const delivery = context.deliveryContext;
  const actor =
    context.requesterSenderId ?? (callKind === "standing_order" ? delivery?.to : undefined);
  const result = base(
    callKind,
    required(actor, callKind === "standing_order" ? "requester or direct route" : "requester"),
    context.messageChannel ?? delivery?.channel ?? "",
  );
  if (context.agentId) result.agent_id = context.agentId;
  if (context.sessionKey) result.session_key = context.sessionKey;
  if (context.sessionId) result.session_id = context.sessionId;
  const accountId = context.agentAccountId ?? delivery?.accountId;
  if (accountId) result.account_id = accountId;
  if (delivery?.threadId !== undefined) result.thread_id = String(delivery.threadId);
  return result;
}

export function provenanceFromCommand(context: PluginCommandContext): Provenance {
  const result = base("slash_command", required(context.senderId, "command sender"), context.channel);
  if (context.agentId) result.agent_id = context.agentId;
  if (context.sessionKey) result.session_key = context.sessionKey;
  if (context.sessionId) result.session_id = context.sessionId;
  if (context.accountId) result.account_id = context.accountId;
  if (context.messageThreadId !== undefined) result.thread_id = String(context.messageThreadId);
  return result;
}

export function provenanceFromDashboard(client: GatewayClient | null): Provenance {
  if (!client?.authenticatedUserId) {
    throw new Error("AdministrateMe dashboard requires an authenticated user identity");
  }
  const result = base("dashboard", client.authenticatedUserId, "openclaw-dashboard");
  if (client.pairedClientId) result.account_id = client.pairedClientId;
  return result;
}

export function provenanceFromMessage(
  event: PluginHookMessageReceivedEvent,
  context: PluginHookMessageContext,
): Provenance {
  if (event.senderId && context.senderId && event.senderId !== context.senderId) {
    throw new Error("AdministrateMe message sender provenance is contradictory");
  }
  if (event.messageId && context.messageId && event.messageId !== context.messageId) {
    throw new Error("AdministrateMe native message provenance is contradictory");
  }
  const result = base("bridge", required(event.senderId ?? context.senderId, "message sender"), context.channelId);
  result.correlation_id = event.runId ?? context.runId ?? randomUUID();
  if (event.sessionKey && context.sessionKey && event.sessionKey !== context.sessionKey) {
    throw new Error("AdministrateMe native session provenance is contradictory");
  }
  const sessionKey = event.sessionKey ?? context.sessionKey;
  if (sessionKey) result.session_key = sessionKey;
  // Native message hooks expose no agentId. Current-input binding derives it
  // only from the existing exact, allowlisted private iMessage session.
  if (context.accountId) result.account_id = context.accountId;
  const messageId = event.messageId ?? context.messageId;
  if (messageId) result.message_id = messageId;
  if (event.threadId !== undefined) result.thread_id = String(event.threadId);
  return result;
}

export function withCorrelation(provenance: Provenance, correlationId: string): Provenance {
  return { ...provenance, correlation_id: correlationId };
}
