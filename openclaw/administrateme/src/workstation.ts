import { Type } from "typebox";
import type { AnyAgentTool, OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { provenanceFromTool } from "./provenance.js";
import { provenanceFromServiceTool, serviceAgentId } from "./service-private.js";
import type { PluginRuntime } from "./runtime.js";
import { toolResult } from "./result.js";
import { assertToolInput } from "./tool-input.js";

export const WORKSTATION_INVENTORY_TOOL = "administrateme_workstation_inventory";

export function registerWorkstationInventory(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  api.registerTool((context): AnyAgentTool => ({
    name: WORKSTATION_INVENTORY_TOOL,
    label: WORKSTATION_INVENTORY_TOOL,
    description: "Read the current private account, device and tool map. Distinguishes configured, connected, authenticated, authorized, accessible, executable and verified outcomes. Consult before diagnosing an unavailable account or route. Default overview gives complete authorized counts and compact rows; follow page.next_offset with identical selection and returned snapshot until null (409 means restart at offset 0). For evidence/account details use view=details, section and optional exact binding_id from the overview; details are paginated field paths. Each response fits native output limits; this map grants no access and sends nothing.",
    parameters: Type.Object({
      view: Type.Optional(Type.Union([Type.Literal("overview"), Type.Literal("details")])),
      section: Type.Optional(Type.Union([Type.Literal("identities"), Type.Literal("providers"), Type.Literal("bridges"), Type.Literal("tools")])),
      binding_id: Type.Optional(Type.String({ minLength: 1, maxLength: 500 })),
      offset: Type.Optional(Type.Integer({ minimum: 0, maximum: 100000 })),
      snapshot: Type.Optional(Type.String({ pattern: "^[0-9a-f]{64}$" })),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 40 })),
    }, { additionalProperties: false }),
    async execute(_id, params) {
      assertToolInput(params);
      const provenance = context.agentId === serviceAgentId()
        ? provenanceFromServiceTool(context, runtime.transport.config.sharedSecretEnv, runtime.transport.config.serviceWorkEnabled)
        : provenanceFromTool(context, "tool", runtime.transport.config.sharedSecretEnv, runtime);
      const result = toolResult(await runtime.transport.request("/api/openclaw/v1/tools/workstation-inventory", { ...params, provenance }));
      if (Buffer.byteLength(JSON.stringify(result), "utf8") >= 9500) {
        throw new Error("Inventory exceeded its native output budget; no partial map was returned.");
      }
      return result;
    },
  }), { name: WORKSTATION_INVENTORY_TOOL, optional: true });
}
