import { randomUUID } from "node:crypto";
import type {
  OpenClawPluginApi,
  OpenClawPluginCommandDefinition,
  PluginCommandContext,
} from "openclaw/plugin-sdk/plugin-entry";
import { provenanceFromCommand } from "./provenance.js";
import { safeError } from "./result.js";
import { trackSession, type PluginRuntime } from "./runtime.js";

async function invoke(
  runtime: PluginRuntime,
  context: PluginCommandContext,
  path: string,
  body: Record<string, unknown> = {},
): Promise<{ text: string }> {
  try {
    if (!context.isAuthorizedSender) {
      return { text: "OpenClaw command authorization rejected this sender." };
    }
    const provenance = provenanceFromCommand(context);
    trackSession(runtime, provenance);
    const result = await runtime.transport.request(path, { ...body, provenance });
    return { text: JSON.stringify(result) };
  } catch (error) {
    return { text: safeError(error) };
  }
}

function readLifecycleArgs(context: PluginCommandContext): Record<string, unknown> | null {
  const parts = (context.args ?? "").trim().split(/\s+/).filter(Boolean);
  if (parts.length < 3) return null;
  const [itemKind, itemId, expectedLastEventId, value] = parts;
  if (!itemKind || !itemId || !expectedLastEventId) return null;
  return {
    request_id: randomUUID(),
    item_kind: itemKind,
    item_id: itemId,
    expected_last_event_id: expectedLastEventId,
    value,
  };
}

function command(definition: OpenClawPluginCommandDefinition): OpenClawPluginCommandDefinition {
  return { ...definition, requireAuth: true };
}

export function registerCommands(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  api.registerCommand(
    command({
      name: "adminme-forward",
      description: "Submit selected text to the CoS for extraction and private Triage review.",
      acceptsArgs: true,
      async handler(context) {
        const text = context.args ?? "";
        if (!text.trim()) return { text: "Usage: /adminme-forward <selected message or obligation>" };
        if (text.length > 32_000) return { text: "This submission is too large. Submit a smaller selection for review." };
        return invoke(runtime, context, "/api/openclaw/v1/submissions/forward", {
          request_id: randomUUID(), text, consent: true,
        });
      },
    }),
  );
  api.registerCommand(
    command({
      name: "adminme-now",
      description: "Show my authorized What Now ranking.",
      handler: (context) => invoke(runtime, context, "/api/openclaw/v1/tools/what-now"),
    }),
  );
  api.registerCommand(
    command({
      name: "adminme-triage",
      description: "Show my authorized Triage queue.",
      handler: (context) => invoke(runtime, context, "/api/openclaw/v1/dashboard/triage"),
    }),
  );
  api.registerCommand(
    command({
      name: "adminme-inbox",
      description: "Deprecated alias for /adminme-triage.",
      handler: (context) => invoke(runtime, context, "/api/openclaw/v1/dashboard/triage"),
    }),
  );

  for (const action of ["done", "defer", "snooze", "block", "unblock"] as const) {
    api.registerCommand(
      command({
        name: `adminme-${action}`,
        description: `${action} an authorized task or commitment.`,
        acceptsArgs: true,
        async handler(context) {
          const parsed = readLifecycleArgs(context);
          if (!parsed) {
            return {
              text: `Usage: /adminme-${action} <task|commitment> <id> <expected-last-event-id> [until|reason]`,
            };
          }
          const value = parsed.value;
          delete parsed.value;
          return invoke(runtime, context, "/api/openclaw/v1/tools/lifecycle", {
            ...parsed,
            action: action === "done" ? "complete" : action,
            ...(action === "defer" || action === "snooze" ? { until: value } : {}),
            ...(action === "block" ? { reason: value } : {}),
          });
        },
      }),
    );
  }
}
