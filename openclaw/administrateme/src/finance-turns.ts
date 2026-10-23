import type { PluginRuntime } from "./runtime.js";

export const FINANCE_TOOLS = new Set([
  "administrateme_finance_status", "administrateme_finance_query",
  "administrateme_finance_calculate", "administrateme_finance_job",
]);

type HostContext = { runId?: string | undefined; sessionKey?: string | undefined; agentId?: string | undefined };
type Binding = { runId: string; sessionKey: string; agentId: string; expiresAt: number };
const calls = new WeakMap<PluginRuntime, Map<string, Binding>>();

// The pinned tool factory has no turn ID. Bind the host's before_tool_call
// coordinates instead; never trust a model-supplied correlation/round field.
export function bindFinanceTurn(runtime: PluginRuntime, context: HostContext, callId: string | undefined): boolean {
  if (!callId || callId.length > 300 || !context.runId || context.runId.length > 200 || !context.sessionKey || !context.agentId) return false;
  let bindings = calls.get(runtime);
  if (!bindings) { bindings = new Map(); calls.set(runtime, bindings); }
  const now = Date.now();
  for (const [key, value] of bindings) if (value.expiresAt <= now) bindings.delete(key);
  if (bindings.size >= 256) return false;
  bindings.set(callId, { runId: context.runId, sessionKey: context.sessionKey, agentId: context.agentId, expiresAt: now + 30_000 });
  return true;
}

export function takeFinanceTurn(runtime: PluginRuntime, callId: string, context: HostContext): string {
  const binding = calls.get(runtime)?.get(callId);
  calls.get(runtime)?.delete(callId);
  if (!binding || binding.expiresAt <= Date.now() || binding.sessionKey !== context.sessionKey || binding.agentId !== context.agentId) {
    throw new Error("Finance requires a current host-bound private turn.");
  }
  return `finance-run:${binding.runId}`;
}
