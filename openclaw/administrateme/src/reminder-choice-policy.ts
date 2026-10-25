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

export function spansMatch(text: string, raw: unknown): boolean {
  const points = Array.from(text);
  return Array.isArray(raw) && raw.length >= 1 && raw.length <= 8 && raw.every((item) => {
    const span = object(item);
    return exactKeys(span, ["start", "end", "text"]) && Number.isInteger(span.start) && Number.isInteger(span.end) &&
      Number(span.start) >= 0 && Number(span.end) > Number(span.start) && Number(span.end) <= points.length &&
      points.slice(Number(span.start), Number(span.end)).join("") === span.text;
  });
}

type NativeInput = {
  source_message_event_id: string; instruction_text: string;
  provenance?: { channel_id: string; account_id?: string | undefined; actor_id: string; thread_id?: string | undefined } | undefined;
};

function conversation(raw: unknown): Record<string, unknown> | null {
  if (raw === null) return null;
  const value = object(raw);
  if (!exactKeys(value, ["channel_id", "account_id", "actor_id", "thread_id"]) || value.channel_id !== "gmail" ||
      ![value.account_id, value.actor_id, value.thread_id].every((field) => typeof field === "string" && field)) {
    throw new Error("Reminder conversation selection is invalid");
  }
  return value;
}

/** A task_id argument may agree with the decision, but cannot make it. */
export function taskAnswer(raw: unknown, input: NativeInput, offered: Record<string, unknown>, offerId: unknown, issued: number): string {
  const snapshot = object(raw);
  const submitted = Date.parse(String(snapshot.answer_submitted_at));
  const native = input.provenance;
  const nativeThread = native?.channel_id === "gmail" && native.account_id && native.thread_id ? {
    channel_id: "gmail", account_id: native.account_id, actor_id: native.actor_id, thread_id: native.thread_id,
  } : null;
  const answerThread = conversation(snapshot.answer_conversation);
  if (!exactKeys(snapshot, ["answer_source_event_id", "answer_submitted_at", "answer_conversation", "eligible_tasks"]) ||
      snapshot.answer_source_event_id !== input.source_message_event_id || !Number.isFinite(submitted) || submitted > issued ||
      canonical(answerThread) !== canonical(nativeThread) || !Array.isArray(snapshot.eligible_tasks) ||
      snapshot.eligible_tasks.length < 1 || snapshot.eligible_tasks.length > 1000) {
    throw new Error("Reminder task selection is not bound to the actual native answer");
  }
  const tasks = snapshot.eligible_tasks.map(object);
  if (tasks.some((task) => !exactKeys(task, ["offered_event_id", "task_id", "title_fingerprint", "pending_expires_at", "conversation"]) ||
      ![task.offered_event_id, task.task_id].every((value) => typeof value === "string" && value) ||
      !/^[a-f0-9]{64}$/.test(String(task.title_fingerprint)) ||
      !Number.isFinite(Date.parse(String(task.pending_expires_at))) || Date.parse(String(task.pending_expires_at)) <= submitted) ||
      new Set(tasks.map((task) => task.offered_event_id)).size !== tasks.length ||
      new Set(tasks.map((task) => task.task_id)).size !== tasks.length ||
      canonical(tasks.map((task) => task.offered_event_id)) !== canonical(tasks.map((task) => task.offered_event_id).sort())) {
    throw new Error("Retained eligible reminder tasks are invalid");
  }
  tasks.forEach((task) => conversation(task.conversation));
  const answer = input.instruction_text.trim();
  let selected: Record<string, unknown> | undefined;
  let remainder = answer;
  let referenced = false;
  const ids = tasks.flatMap((task) => {
    const prefix = String(task.task_id) + ":";
    return answer.startsWith(prefix) ? [{ task, rest: answer.slice(prefix.length).trim() }] : [];
  });
  const prefixes = Array.from(answer.matchAll(/:/g)).map((match) => ({
    digest: hash(answer.slice(0, match.index!).trim().toLowerCase()), rest: answer.slice(match.index! + 1).trim(),
  }));
  const titles = tasks.flatMap((task) => prefixes.filter((prefix) => prefix.digest === task.title_fingerprint).map((prefix) => ({ task, rest: prefix.rest })));
  const matches = ids.length ? ids : titles;
  if (matches.length) {
    referenced = true;
    if (matches.length === 1) { selected = matches[0]!.task; remainder = matches[0]!.rest; }
  }
  if (!referenced) {
    const sameThread = answerThread === null ? [] : tasks.filter((task) => canonical(task.conversation) === canonical(answerThread));
    const candidates = sameThread.length ? sameThread : tasks;
    if (candidates.length === 1) selected = candidates[0];
  }
  if (!selected || selected.offered_event_id !== offerId || selected.task_id !== offered.task_id || selected.title_fingerprint !== hash(String(offered.title).trim().toLowerCase()) ||
      Date.parse(String(selected.pending_expires_at)) !== Date.parse(String(offered.pending_expires_at))) {
    throw new Error("The actual member answer does not uniquely select this reminder task");
  }
  return remainder;
}

/** Invoked at cache admission AND immediately before the host authorizes execution. */
export function validateReminderChoice(
  raw: unknown, input: NativeInput,
  requestedTaskId?: unknown,
): void {
  const ticket = object(raw), continuation = object(ticket.continuation), offered = object(continuation.offered), action = object(ticket.action);
  const original = continuation.original_instruction;
  const due = Date.parse(String(offered.due_at)), expiry = Date.parse(String(offered.pending_expires_at));
  const issued = Date.parse(String(ticket.issued_at)), ticketExpiry = Date.parse(String(ticket.expires_at));
  const submitted = Date.parse(String(offered.original_submitted_at)), offeredAt = Date.parse(String(offered.offered_at));
  if (typeof original !== "string" || !original || original.length > 32_000 ||
      !exactKeys(continuation, ["offered_event_id", "offered", "original_instruction", "original_evidence", "selection"]) ||
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
  const answer = taskAnswer(continuation.selection, input, offered, continuation.offered_event_id, issued).trim().toLowerCase();
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
