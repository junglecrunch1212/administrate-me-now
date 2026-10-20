import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { createChannelNativeApprovalRuntime, type PluginApprovalRequest, type PluginApprovalResolved } from "openclaw/plugin-sdk/approval-runtime";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { approvalRequestFingerprint } from "./approval.js";
import type { PluginRuntime } from "./runtime.js";

const TOOL = "administrateme_cos_response";
const CONTRACT = "cos-member-approval-v1";
type Binding = {
  session_key: string; agent_id: string; domain_approval_id: string;
  request_id: string; request_fingerprint: string; receipt_id: string;
  tool_call_id: string; created_at_ms: number;
};
type Pending = Binding & { approval_id: string; expires_at_ms: number; revision: string };
export type CoSApprovalRelay = {
  remember: (params: Record<string, unknown>, context: Record<string, unknown>, receiptId: string) => void;
};

export function registerCosApprovalRelay(api: OpenClawPluginApi, runtime: PluginRuntime): CoSApprovalRelay {
  const bindings = new Map<string, Binding>();
  const pending = new Map<string, Pending>();
  const used = new Map<string, number>();
  const prune = () => {
    const now = Date.now();
    for (const [key, value] of bindings) if (now - value.created_at_ms > 130_000) bindings.delete(key);
    for (const [key, value] of pending) if (value.expires_at_ms <= now) pending.delete(key);
    for (const [key, value] of used) if (value <= now) used.delete(key);
  };
  const matching = (record: PluginApprovalRequest): Binding | undefined => {
    prune();
    const request = record.request;
    const binding = bindings.get(request.toolCallId ?? "");
    if (!binding || request.pluginId !== "administrateme" || request.toolName !== TOOL ||
        request.sessionKey !== binding.session_key || request.agentId !== binding.agent_id ||
        !/^plugin:[a-f0-9-]{36}$/.test(record.id) ||
        !Number.isSafeInteger(record.createdAtMs) || !Number.isSafeInteger(record.expiresAtMs) ||
        record.createdAtMs < binding.created_at_ms - 1000 || record.createdAtMs > Date.now() + 1000 ||
        record.expiresAtMs <= Date.now() || record.expiresAtMs > record.createdAtMs + 120_000 ||
        JSON.stringify(request.allowedDecisions) !== '["allow-once","deny"]') return;
    return binding;
  };
  // Public pinned SDK: native subscription and resolution stay inside Gateway.
  // The virtual target is this authenticated dashboard, never a provider channel.
  const createNative = () => createChannelNativeApprovalRuntime<string, string, string, PluginApprovalRequest, PluginApprovalResolved>({
    label: "administrateme/cos-approvals",
    clientDisplayName: "AdministrateMe CoS reply approvals",
    cfg: api.config,
    eventKinds: ["plugin"],
    isConfigured: () => true,
    shouldHandle: (record) => Boolean(matching(record)),
    nativeAdapter: {
      describeDeliveryCapabilities: () => ({
        enabled: true, preferredSurface: "origin", supportsOriginSurface: true, supportsApproverDmSurface: false,
      }),
      resolveOriginTarget: ({ request }) => ({ to: request.request.sessionKey ?? "" }),
    },
    buildPendingContent: ({ request }) => request.id,
    prepareTarget: ({ request }) => ({ dedupeKey: request.id, target: request.id }),
    deliverTarget: async ({ request }) => {
      const binding = matching(request);
      if (!binding) return null;
      const row = { ...binding, approval_id: request.id, expires_at_ms: request.expiresAtMs };
      pending.set(request.id, {
        ...row, revision: createHash("sha256").update(JSON.stringify(row)).digest("hex"),
      });
      return request.id;
    },
    finalizeResolved: async ({ request }) => { pending.delete(request.id); },
    finalizeExpired: async ({ request }) => { pending.delete(request.id); },
    onStopped: () => { pending.clear(); bindings.clear(); used.clear(); },
  });
  let native: ReturnType<typeof createNative> | undefined;
  const authorize = (params: Record<string, unknown>, operation: "list" | "resolve"): Record<string, string> => {
    prune();
    const fields = ["session_key", "agent_id", "member_id", "approval_id", "revision", "decision", "nonce"];
    if (Object.keys(params).sort().join(",") !== [...fields, "expires_at_ms", "signature"].sort().join(",") ||
        fields.some((key) => typeof params[key] !== "string" || String(params[key]).includes("|"))) {
      throw new Error("Invalid member approval authorization");
    }
    const row = params as Record<string, string>;
    const expires = Number(params.expires_at_ms);
    const now = Date.now();
    if (!Number.isSafeInteger(expires) || expires <= now || expires > now + 10_000 ||
        !row.session_key || !row.agent_id || !row.member_id || !/^[a-f0-9]{32}$/.test(row.nonce ?? "") ||
        used.has(row.nonce ?? "")) throw new Error("Expired member approval authorization");
    const secret = process.env[runtime.transport.config.sharedSecretEnv];
    if (!secret || secret.length < 24) throw new Error("Approval authorization unavailable");
    const material = [CONTRACT, operation, ...fields.map((key) => row[key]), String(expires)].join("|");
    const expected = createHmac("sha256", secret).update(material).digest("hex");
    const actual = String(params.signature);
    if (!/^[a-f0-9]{64}$/.test(actual) ||
        !timingSafeEqual(Buffer.from(expected), Buffer.from(actual))) throw new Error("Member approval authorization refused");
    used.set(row.nonce!, expires);
    return row;
  };
  api.registerGatewayMethod("administrateme.cosApprovals.list", async ({ params, respond }) => {
    try {
      const ticket = authorize(params, "list");
      const rows = [...pending.values()].filter((row) =>
        row.session_key === ticket.session_key && row.agent_id === ticket.agent_id);
      respond(true, { items: rows.map(({ receipt_id: _receipt, tool_call_id: _call, created_at_ms: _created, ...row }) => row) });
    } catch { respond(false, undefined, { code: "APPROVAL_UNAVAILABLE", message: "Reply approval unavailable" }); }
  }, { scope: "operator.read" });
  api.registerGatewayMethod("administrateme.cosApprovals.resolve", async ({ params, respond }) => {
    try {
      const ticket = authorize(params, "resolve");
      const row = pending.get(ticket.approval_id ?? "");
      if (!row || row.session_key !== ticket.session_key || row.agent_id !== ticket.agent_id ||
          row.revision !== ticket.revision || row.expires_at_ms <= Date.now() ||
          !["allow-once", "deny"].includes(ticket.decision ?? "")) throw new Error("Reply approval changed");
      // Consume before awaiting. Neither a timeout nor a repeated UI request can retry.
      pending.delete(row.approval_id);
      if (!native) throw new Error("Approval runtime unavailable");
      await native.request("plugin.approval.resolve", { id: row.approval_id, decision: ticket.decision });
      respond(true, { resolved: true });
    } catch { respond(false, undefined, { code: "APPROVAL_UNAVAILABLE", message: "Reply approval unavailable" }); }
  }, { scope: "operator.write" });
  api.registerService({
    id: "administrateme-cos-approval-relay",
    async start() {
      const gateway = api.config?.gateway as Record<string, unknown> | undefined;
      if (gateway?.mode === "remote" || process.env.OPENCLAW_GATEWAY_URL) {
        throw new Error("CoS approval relay requires the configured local Gateway");
      }
      native = createNative(); await native.start();
    },
    async stop() { await native?.stop(); native = undefined; },
  });
  return {
    remember(params, context, receiptId) {
      prune();
      if (typeof context.sessionKey !== "string" || typeof context.agentId !== "string" ||
          typeof context.toolCallId !== "string" || !context.toolCallId ||
          typeof params.approval_event_id !== "string" || typeof params.request_id !== "string" ||
          Object.keys(params).some((key) => !["request_id", "approval_event_id", "openclaw_approval_receipt_id"].includes(key))) return;
      bindings.set(context.toolCallId, {
        session_key: context.sessionKey, agent_id: context.agentId,
        domain_approval_id: params.approval_event_id, request_id: params.request_id,
        request_fingerprint: approvalRequestFingerprint(params), receipt_id: receiptId,
        tool_call_id: context.toolCallId, created_at_ms: Date.now(),
      });
    },
  };
}
