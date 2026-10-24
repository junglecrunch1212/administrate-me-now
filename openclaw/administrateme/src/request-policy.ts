/** Supported host-hook policy for an exact current human instruction.
 * It does not resolve an OpenClaw human approval or bypass later host policies.
 */
import { createHash, createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import type { OpenClawPluginToolContext } from "openclaw/plugin-sdk/plugin-entry";
import { OPENCLAW_COMMIT, OPENCLAW_VERSION, type Provenance } from "./contract.js";
import { approvalRequestFingerprint, canonical } from "./approval.js";
import { peekReminderInput } from "./self-replies.js";
import type { PluginRuntime } from "./runtime.js";

export const MEMBER_REMINDER_RULE = "current-human-request.apple-reminder-create.v1";
export const COS_REMINDER_RULE = "current-human-request.cos-reminder-create.v1";
export const COS_EMAIL_RULE = "current-human-request.cos-email-to-requester.v1";
type RequestRule = typeof MEMBER_REMINDER_RULE | typeof COS_REMINDER_RULE | typeof COS_EMAIL_RULE;
const CONTRACT = "authenticated-request-v1";
const TOOL = "administrateme_execute_pending_provider_action";
const EMAIL_TOOL = "administrateme_cos_response";
type Context = { runId?: string | undefined; sessionKey?: string | undefined; agentId?: string | undefined; toolCallId?: string | undefined };
type Input = { provenance: Provenance; source_message_event_id: string; instruction_text: string };
type Ticket = {
  contract: string; member_id: string; domain_approval_event_id: string;
  grounding_event_id: string; source_event_id: string; action_fingerprint: string;
  action: Record<string, unknown>; rule_id: string; issued_at: string; expires_at: string;
  evidence: Array<{ start: number; end: number; text: string }>; signature: string;
};
const tickets = new WeakMap<PluginRuntime, Map<string, Ticket>>();
const calls = new WeakMap<PluginRuntime, Map<string, { input: Input; callId: string; expires: number }>>();

function secret(runtime: PluginRuntime): string {
  const value = process.env[runtime.transport.config.sharedSecretEnv];
  if (!value || value.length < 24) throw new Error("Request policy credential unavailable");
  return value;
}

function emailActionValid(ticket: Ticket): boolean {
  const action = ticket.action;
  return action.provider === "gmail" && action.operation === "send_email" &&
    action.resource_owner === "service-principal:chief-of-staff" && action.operating_principal === "service-principal:chief-of-staff" &&
    action.recipient_member_id === ticket.member_id &&
    ["service_channel_id", "provider_account_id", "recipient", "body"].every((key) => typeof action[key] === "string" && action[key]) &&
    typeof action.subject === "string" && typeof action.sender_selected_by_policy === "boolean" && typeof action.recipient_selected_by_policy === "boolean" &&
    action.content_fingerprint === createHash("sha256").update(canonical(["cos-response-plain-text-v1", "email", action.provider_account_id, action.recipient, action.subject, action.body])).digest("hex");
}

function validateTicket(runtime: PluginRuntime, candidate: unknown, input: Input, expectedRule: RequestRule): Ticket {
  if (!candidate || typeof candidate !== "object") throw new Error("Request policy evidence unavailable");
  const ticket = candidate as Ticket;
  const { signature, ...body } = ticket;
  const expected = createHmac("sha256", secret(runtime)).update(`${CONTRACT}|${canonical(body)}`).digest("hex");
  const now = Date.now();
  const issued = Date.parse(ticket.issued_at), expires = Date.parse(ticket.expires_at);
  const agent = `adminme-${createHash("sha256").update(ticket.member_id ?? "").digest("hex").slice(0, 16)}`;
  const points = Array.from(input.instruction_text);
  if (typeof signature !== "string" || !/^[a-f0-9]{64}$/.test(signature) ||
      !timingSafeEqual(Buffer.from(expected), Buffer.from(signature)) ||
      !ticket.action || createHash("sha256").update(canonical(ticket.action)).digest("hex") !== ticket.action_fingerprint ||
      (expectedRule === COS_EMAIL_RULE ? !emailActionValid(ticket) :
        ticket.action.provider !== "apple_reminders" || ticket.action.operation !== "create_reminder" || ticket.action.resource_owner !== (expectedRule === COS_REMINDER_RULE ? "service-principal:chief-of-staff" : "requester")) ||
      (expectedRule === COS_REMINDER_RULE && (ticket.action.operating_principal !== "service-principal:chief-of-staff" ||
        !["provider_account_id", "source_id", "list_id"].every((key) => typeof ticket.action[key] === "string" && ticket.action[key]) ||
        !["device_identity_sha256", "selection_sha256", "write_policy_sha256"].every((key) => /^[a-f0-9]{64}$/.test(String(ticket.action[key]))) ||
        !Number.isInteger(ticket.action.os_uid) || Number(ticket.action.os_uid) < 0)) ||
      ticket.contract !== CONTRACT || ticket.rule_id !== expectedRule ||
      ticket.source_event_id !== input.source_message_event_id ||
      input.provenance.agent_id !== agent ||
      !Number.isFinite(issued) || !Number.isFinite(expires) || issued > now || expires <= now || expires - issued > 600_000 ||
      !ticket.domain_approval_event_id || !ticket.grounding_event_id || !/^[a-f0-9]{64}$/.test(ticket.action_fingerprint) ||
      !Array.isArray(ticket.evidence) || ticket.evidence.length < 1 || ticket.evidence.length > 8 ||
      ticket.evidence.some((span) => !Number.isInteger(span.start) || !Number.isInteger(span.end) ||
        span.start < 0 || span.end <= span.start || span.end > points.length || points.slice(span.start, span.end).join("") !== span.text)) {
    throw new Error("Current request policy evidence is stale or mismatched");
  }
  return ticket;
}

export function rememberRequestAuthorization(runtime: PluginRuntime, result: unknown, input: Input, requested: Record<string, unknown>, expectedRule: RequestRule = MEMBER_REMINDER_RULE): void {
  if (!result || typeof result !== "object" || !("request_authorization" in result)) return;
  const ticket = validateTicket(runtime, (result as Record<string, unknown>).request_authorization, input, expectedRule);
  const requestedRecipient = typeof requested.recipient === "string" ? requested.recipient.replace(/@([^@]+)$/, (_match, domain: string) => "@" + domain.toLowerCase()) : requested.recipient;
  const mismatch = expectedRule === COS_EMAIL_RULE ?
    ticket.action.subject !== requested.subject || ticket.action.body !== requested.body ||
    (requested.provider_account_id !== undefined && ticket.action.provider_account_id !== requested.provider_account_id) ||
    (requested.recipient !== undefined && ticket.action.recipient !== requestedRecipient) ||
    ticket.action.sender_selected_by_policy !== (requested.provider_account_id === undefined) ||
    ticket.action.recipient_selected_by_policy !== (requested.recipient === undefined) :
    ticket.action.title !== String(requested.title).trim() ||
    Date.parse(String(ticket.action.due_at)) !== Date.parse(String(requested.due_at)) ||
    ticket.action.notes !== (requested.notes ?? null) ||
    (requested.list_id !== undefined && ticket.action.list_id !== requested.list_id) ||
    ticket.action.selected_by_policy !== (requested.list_id === undefined);
  if (mismatch) {
    throw new Error("Grounded action differs from this tool's requested action");
  }
  let cache = tickets.get(runtime);
  if (!cache) { cache = new Map(); tickets.set(runtime, cache); }
  for (const [key, row] of cache) if (Date.parse(row.expires_at) <= Date.now()) cache.delete(key);
  if (cache.size >= 100) cache.delete(cache.keys().next().value!);
  cache.set(ticket.domain_approval_event_id, ticket);
}

export async function prepareRequestedApproval(runtime: PluginRuntime, toolName: string,
  params: Record<string, unknown>, context: Context): Promise<Record<string, unknown> | undefined> {
  if (toolName !== TOOL && toolName !== EMAIL_TOOL) return undefined;
  const email = toolName === EMAIL_TOOL;
  const ticket = tickets.get(runtime)?.get(String(email ? params.approval_event_id : params.domain_approval_event_id));
  if (!ticket) return undefined; // Legacy pending work retains its independent manual path.
  if (!context.toolCallId) throw new Error("Current native tool identity is unavailable");
  const input = await peekReminderInput(runtime, context);
  if (!input) throw new Error("Current trusted request is unavailable");
  if (email ? ticket.rule_id !== COS_EMAIL_RULE : ticket.rule_id !== MEMBER_REMINDER_RULE && ticket.rule_id !== COS_REMINDER_RULE) throw new Error("Unsupported current request rule for this tool");
  validateTicket(runtime, ticket, input, ticket.rule_id as RequestRule);
  const allowed = email ? ["approval_event_id", "request_id"] : ["domain_approval_event_id"];
  if (Object.keys(params).some((key) => !allowed.includes(key)) ||
      (email && (typeof params.request_id !== "string" || params.request_id.length < 8 || params.request_id.length > 120))) {
    throw new Error("Request approval parameters cannot be supplied by the model");
  }
  const updated = { ...params, request_authorization_event_id: ticket.grounding_event_id };
  const authorization = {
    source_event_id: ticket.source_event_id, request_authorization_event_id: ticket.grounding_event_id,
    domain_approval_event_id: ticket.domain_approval_event_id,
    action_fingerprint: ticket.action_fingerprint, rule_id: ticket.rule_id,
  };
  const receiptId = `ocreq_${randomUUID()}`;
  const fingerprint = approvalRequestFingerprint(updated);
  const issuedAtMs = Date.now(), expiresAtMs = Math.min(issuedAtMs + 120_000, Date.parse(ticket.expires_at));
  const signature = createHmac("sha256", secret(runtime)).update([
    "administrateme-request-approval-v1", receiptId, toolName, fingerprint,
    String(issuedAtMs), String(expiresAtMs), "authenticated_request", OPENCLAW_VERSION,
    OPENCLAW_COMMIT, canonical(authorization),
  ].join("|")).digest("hex");
  await runtime.transport.request("/api/openclaw/v1/approvals/authenticated-request", {
    receipt_id: receiptId, tool_name: toolName, request_fingerprint: fingerprint,
    issued_at_ms: issuedAtMs, expires_at_ms: expiresAtMs, decision: "authenticated_request",
    openclaw_version: OPENCLAW_VERSION, openclaw_commit: OPENCLAW_COMMIT, authorization, signature,
  });
  let pending = calls.get(runtime);
  if (!pending) { pending = new Map(); calls.set(runtime, pending); }
  pending.set(receiptId, { input, callId: context.toolCallId, expires: expiresAtMs });
  for (const [key, row] of pending) if (row.expires <= Date.now()) pending.delete(key);
  return { ...updated, openclaw_approval_receipt_id: receiptId };
}

export function requestExecutionProvenance(runtime: PluginRuntime, callId: string,
  params: Record<string, unknown>, context: OpenClawPluginToolContext): Provenance | undefined {
  const receiptId = params.openclaw_approval_receipt_id;
  if (typeof receiptId !== "string" || !receiptId.startsWith("ocreq_")) return undefined;
  const bound = calls.get(runtime)?.get(receiptId);
  if (!bound || bound.callId !== callId || bound.expires <= Date.now() ||
      bound.input.provenance.session_key !== context.sessionKey || bound.input.provenance.agent_id !== context.agentId) {
    throw new Error("Execution is not bound to this authenticated current request");
  }
  return bound.input.provenance;
}
