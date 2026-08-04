import type { GatewayHandlerOptions, OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { provenanceFromDashboard } from "./provenance.js";
import { safeError } from "./result.js";
import { trackSession, type PluginRuntime } from "./runtime.js";

async function dispatch(
  runtime: PluginRuntime,
  options: GatewayHandlerOptions,
  path: string,
  transform: (params: Record<string, unknown>) => Record<string, unknown> = (params) => params,
): Promise<void> {
  try {
    const provenance = provenanceFromDashboard(options.client);
    trackSession(runtime, provenance);
    const result = await runtime.transport.request(path, {
      ...transform(options.params),
      provenance,
    });
    options.respond(true, result);
  } catch (error) {
    options.respond(false, undefined, { code: "ADMINISTRATEME_REJECTED", message: safeError(error) });
  }
}

export function registerDashboard(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  const reads = [
    ["administrateme.dashboard.today", "/api/openclaw/v1/dashboard/today"],
    ["administrateme.dashboard.inbox", "/api/openclaw/v1/dashboard/inbox"],
    ["administrateme.dashboard.people", "/api/openclaw/v1/dashboard/people"],
    ["administrateme.dashboard.knowledge", "/api/openclaw/v1/dashboard/knowledge"],
    ["administrateme.dashboard.finance", "/api/openclaw/v1/dashboard/finance"],
    ["administrateme.dashboard.calendar", "/api/openclaw/v1/dashboard/calendar"],
    ["administrateme.dashboard.scoreboard", "/api/openclaw/v1/dashboard/scoreboard"],
    ["administrateme.dashboard.settings", "/api/openclaw/v1/dashboard/settings"],
    ["administrateme.dashboard.health", "/api/openclaw/v1/dashboard/health"],
  ] as const;
  for (const [method, path] of reads) {
    api.registerGatewayMethod(method, (options) => dispatch(runtime, options, path), {
      scope: "operator.read",
    });
  }

  api.registerGatewayMethod(
    "administrateme.dashboard.review",
    (options) =>
      dispatch(runtime, options, "/api/openclaw/v1/tools/review", (params) => ({
        request_id: params.requestId,
        action: params.action,
        commitment_id: params.commitmentId,
        expected_last_event_id: params.expectedLastEventId,
        description: params.description,
        due_at: params.dueAt,
        reason: params.reason,
        send_acknowledgement: false,
        openclaw_approved: false,
      })),
    { scope: "operator.write" },
  );
  api.registerGatewayMethod(
    "administrateme.dashboard.lifecycle",
    (options) =>
      dispatch(runtime, options, "/api/openclaw/v1/tools/lifecycle", (params) => ({
        request_id: params.requestId,
        item_kind: params.itemKind,
        item_id: params.itemId,
        action: params.action,
        expected_last_event_id: params.expectedLastEventId,
        until: params.until,
        reason: params.reason,
        note: params.note,
      })),
    { scope: "operator.write" },
  );
}
