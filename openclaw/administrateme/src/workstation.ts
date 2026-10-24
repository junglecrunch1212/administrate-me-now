import { Type } from "typebox";
import type { AnyAgentTool, OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { provenanceFromTool } from "./provenance.js";
import { provenanceFromServiceTool, serviceAgentId } from "./service-private.js";
import type { PluginRuntime } from "./runtime.js";
import { toolResult } from "./result.js";

export const WORKSTATION_INVENTORY_TOOL = "administrateme_workstation_inventory";

export function registerWorkstationInventory(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  api.registerTool((context): AnyAgentTool => ({
    name: WORKSTATION_INVENTORY_TOOL,
    label: WORKSTATION_INVENTORY_TOOL,
    description: "Read the current private account, device and tool map. Distinguishes configured, connected, authenticated, authorized, accessible, executable and verified outcomes. Consult before diagnosing an unavailable account or route; this map grants no access and sends nothing.",
    parameters: Type.Object({}, { additionalProperties: false }),
    async execute() {
      const provenance = context.agentId === serviceAgentId()
        ? provenanceFromServiceTool(context, runtime.transport.config.sharedSecretEnv, runtime.transport.config.serviceWorkEnabled)
        : provenanceFromTool(context, "tool", runtime.transport.config.sharedSecretEnv);
      return toolResult(await runtime.transport.request("/api/openclaw/v1/tools/workstation-inventory", { provenance }));
    },
  }), { name: WORKSTATION_INVENTORY_TOOL, optional: true });
}
