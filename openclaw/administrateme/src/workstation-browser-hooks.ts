/** Installed native hook + fail-closed result middleware for one admitted read. */
import { createHash } from "node:crypto";
import type { OpenClawPluginApi, OpenClawPluginToolContext } from "openclaw/plugin-sdk/plugin-entry";
import type { PluginRuntime } from "./runtime.js";
import { provenanceFromServiceTool, serviceAgentId } from "./service-private.js";
import { toolResult } from "./result.js";
import { prepareBrowserRead, verifiedBrowserRead, type BrowserReadPolicy, type PreparedBrowserRead } from "./workstation-browser.js";

type WorkerPolicy = { bound: boolean; mode?: "read" | "complete"; resource?: BrowserReadPolicy & { route_sha256: string } };
const BASE = "/api/openclaw/v1/tools/workstation-browser";
const object = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

export function registerWorkstationBrowserHooks(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  const pending = new Map<string, { prepared: PreparedBrowserRead; route: string; started: number }>();
  const service = (context: Record<string, unknown>) => context.agentId === serviceAgentId();
  const provenance = (context: Record<string, unknown>) => provenanceFromServiceTool(context as OpenClawPluginToolContext,
    runtime.transport.config.sharedSecretEnv, runtime.transport.config.serviceWorkEnabled);
  const policy = (context: Record<string, unknown>) => runtime.transport.request<WorkerPolicy>(`${BASE}/policy`, { provenance: provenance(context) });
  const native = { currentConfig: () => api.runtime.config.current() };
  const key = (context: Record<string, unknown>, call: unknown) => {
    if (typeof context.sessionKey !== "string" || typeof call !== "string" || !call) throw new Error("Native tool call binding is unavailable.");
    return createHash("sha256").update(JSON.stringify([context.sessionKey, call])).digest("hex");
  };
  api.on("before_prompt_build", async (_event, context) => {
    if (!service(context)) return;
    try {
      const current = await policy(context);
      if (current.bound) return { toolsAllow: current.mode === "read" ? ["browser"] : [] };
    } catch { return { toolsAllow: [] }; }
  });
  api.on("before_tool_call", async (event, context) => {
    if (event.toolName !== "browser" && !service(context)) return;
    try {
      if (!service(context)) throw new Error("The browser requires its admitted CoS worker.");
      const current = await policy(context);
      if (event.toolName !== "browser") {
        if (current.bound) throw new Error("This worker may only perform its admitted browser read.");
        return;
      }
      if (!current.bound || current.mode !== "read" || !current.resource) throw new Error("No current browser read was admitted.");
      const id = key(context, context.toolCallId ?? event.toolCallId);
      const prepared = prepareBrowserRead(native, current.resource, event.params);
      while (pending.size >= 100) pending.delete(pending.keys().next().value!);
      pending.set(id, { prepared, route: current.resource.route_sha256, started: Date.now() });
      return { params: prepared.params };
    } catch {
      return { block: true, blockReason: "A current authenticated browser request, verified CoS tab and source policy are required." };
    }
  }, { priority: 100 });
  api.registerAgentToolResultMiddleware(async (event, context) => {
    try {
      if (!service(context)) throw new Error("Unexpected native browser principal.");
      const id = key(context, event.toolCallId);
      const admitted = pending.get(id);
      pending.delete(id);
      if (!admitted || event.isError || Date.now() - admitted.started > 60000) throw new Error("Native browser result has no current binding.");
      const current = await policy(context);
      if (!current.bound || !current.resource) throw new Error("Browser source authority was revoked.");
      const observed = verifiedBrowserRead(native, admitted.prepared, current.resource, event.result);
      const captured = object(await runtime.transport.request(`${BASE}/capture`, {
        provenance: provenance(context), route_sha256: admitted.route, native_target_id: observed.targetId,
        url: observed.url, actual_identity: observed.identity, excerpt: observed.text, truncated: observed.truncated,
      }));
      if (captured.status !== "verified" || typeof captured.observation_event_id !== "string" || captured.excerpt !== observed.text) throw new Error("Native browser source was not retained.");
      const result = toolResult(captured);
      if (Buffer.byteLength(JSON.stringify(result), "utf8") >= 9500) throw new Error("Native browser result exceeds its complete-read budget.");
      return { result };
    } catch {
      // The original result is never returned after capture, policy or identity
      // failure. The host's middleware runner also withholds on thrown failures.
      api.logger.warn("Native browser read was withheld because its source evidence could not be verified.");
      return { result: toolResult({ status: "blocked", reason: "browser_source_not_verified", provider_effect_count: 0 }) };
    }
  }, { matcher: ["browser"], runtimes: ["openclaw", "codex"] });
}
