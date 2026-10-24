/** Independent host checks for an immutable offered reminder and a fresh answer.
 * The caller must additionally authenticate the entire signed ticket and the
 * actual native turn; this module never turns a model argument into evidence.
 */
import { createHash } from "node:crypto";
import { canonical } from "./approval.js";

export const REMINDER_CHOICE_RULE = "current-human-request.apple-reminder-list-choice.v1";
const hash = (value: unknown) => createHash("sha256").update(canonical(value)).digest("hex");
const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Reminder choice evidence is unavailable");
  return value as Record<string, unknown>;
};
const exactKeys = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));

function spansMatch(text: string, raw: unknown): boolean {
  const points = Array.from(text);
  return Array.isArray(raw) && raw.length >= 1 && raw.length <= 8 && raw.every((item) => {
    const span = object(item);
    return exactKeys(span, ["start", "end", "text"]) && Number.isInteger(span.start) && Number.isInteger(span.end) &&
      Number(span.start) >= 0 && Number(span.end) > Number(span.start) && Number(span.end) <= points.length &&
      points.slice(Number(span.start), Number(span.end)).join("") === span.text;
  });
}

/** Invoked at cache admission AND immediately before the host authorizes execution. */
export function validateReminderChoice(
  raw: unknown, input: { source_message_event_id: string; instruction_text: string },
  requestedTaskId?: unknown,
): void {
  const ticket = object(raw), continuation = object(ticket.continuation), offered = object(continuation.offered), action = object(ticket.action);
  const original = continuation.original_instruction;
  const due = Date.parse(String(offered.due_at)), expiry = Date.parse(String(offered.pending_expires_at));
  const issued = Date.parse(String(ticket.issued_at)), ticketExpiry = Date.parse(String(ticket.expires_at));
  const submitted = Date.parse(String(offered.original_submitted_at)), offeredAt = Date.parse(String(offered.offered_at));
  if (typeof original !== "string" || !original || original.length > 32_000 ||
      !exactKeys(continuation, ["offered_event_id", "offered", "original_instruction", "original_evidence"]) ||
      ![continuation.offered_event_id, offered.task_id, offered.action_id, offered.request_id,
        offered.original_source_event_id, offered.provider_account_id].every((value) => typeof value === "string" && value) ||
      requestedTaskId !== undefined && requestedTaskId !== offered.task_id ||
      ticket.rule_id !== REMINDER_CHOICE_RULE || ticket.member_id !== offered.member_id ||
      ticket.source_event_id !== input.source_message_event_id || ticket.source_event_id === offered.original_source_event_id ||
      offered.provider !== "apple_reminders" || offered.operation !== "create_reminder" || offered.resource_owner !== "requester" ||
      offered.expiry_reason !== "requested_reminder_due_time" || offered.provider_effect_count !== 0 ||
      ![due, expiry, issued, ticketExpiry, submitted, offeredAt].every(Number.isFinite) ||
      due % 1000 !== 0 || due !== expiry || expiry <= Date.now() ||
      submitted > offeredAt || offeredAt > issued || issued >= ticketExpiry || ticketExpiry > expiry ||
      hash(original) !== offered.original_source_fingerprint || !spansMatch(original, continuation.original_evidence) ||
      !spansMatch(input.instruction_text, ticket.evidence) ||
      !Array.isArray(offered.choices) || offered.choices.length < 1 || offered.choices.length > 100) {
    throw new Error("Original reminder task or fresh answer is stale or mismatched");
  }
  const choices = offered.choices.map(object);
  if (choices.some((choice) => !exactKeys(choice, ["list_id", "label"]) ||
      typeof choice.list_id !== "string" || !choice.list_id || typeof choice.label !== "string" || !choice.label) ||
      new Set(choices.map((choice) => choice.list_id)).size !== choices.length) {
    throw new Error("Recorded reminder destinations are invalid");
  }
  const answer = input.instruction_text.trim().toLowerCase();
  const candidates = ["yes", "yes please"].includes(answer) ? (choices.length === 1 ? choices : []) :
    choices.filter((choice) => [String(choice.list_id).toLowerCase(), String(choice.label).trim().toLowerCase()].includes(answer));
  const draft = { title: offered.title, due_at: new Date(due).toISOString().replace(/\.000Z$/, "+00:00"), notes: offered.notes ?? null };
  if (candidates.length !== 1 || candidates[0]!.list_id !== action.list_id ||
      hash(draft) !== offered.draft_fingerprint ||
      !exactKeys(action, ["provider", "operation", "resource_owner", "title", "due_at", "notes", "list_id", "selected_by_policy"]) ||
      action.provider !== offered.provider || action.operation !== offered.operation || action.resource_owner !== "requester" ||
      action.title !== offered.title || Date.parse(String(action.due_at)) !== due || action.notes !== (offered.notes ?? null) ||
      action.selected_by_policy !== false) {
    throw new Error("Fresh answer does not uniquely select the unchanged reminder draft");
  }
}
