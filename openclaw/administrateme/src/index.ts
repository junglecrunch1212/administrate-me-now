import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { readPluginConfig } from "./config.js";
import { PLUGIN_ID } from "./contract.js";
import { registerCommands } from "./commands.js";
import { registerDashboard } from "./dashboard.js";
import { registerHooks } from "./hooks.js";
import { registerService } from "./service.js";
import { registerTools } from "./tools.js";
import { registerWorkstationInventory } from "./workstation.js";
import { registerWorkstationWorkTool, registerWorkstationWorkHooks } from "./workstation-work.js";
import { registerMemberWorkTool } from "./member-work.js";
import { registerAppleReminderTools } from "./apple-reminders.js";
import { registerSelfReminderTools } from "./self-reminders.js";
import { registerServicePrivateTools, registerServiceWorkGateway } from "./service-private.js";
import { AdministrateMeTransport } from "./transport.js";
import { registerCosApprovalRelay } from "./cos-approval-relay.js";
import { registerCosPermissionPreflight } from "./cos-permission-preflight.js";
import type { PluginRuntime } from "./runtime.js";

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "AdministrateMe",
  description: "Authorized household state and action seam for AdministrateMe v2.",
  register(api) {
    // The pinned runtime uses discovery/tool-discovery while its published SDK
    // type still names the equivalent non-activating modes setup/snapshot.
    const registrationMode = String(api.registrationMode);
    if (
      registrationMode !== "full" &&
      registrationMode !== "discovery" &&
      registrationMode !== "tool-discovery"
    ) {
      return;
    }
    const config = readPluginConfig(api.pluginConfig);
    const runtime: PluginRuntime = {
      transport: new AdministrateMeTransport(config),
      trackedSessions: new Map(),
    };
    registerDashboard(api, runtime);
    if (registrationMode === "discovery") return;
    registerTools(api, runtime);
    registerMemberWorkTool(api, runtime);
    registerAppleReminderTools(api, runtime);
    registerSelfReminderTools(api, runtime);
    registerServicePrivateTools(api, runtime);
    registerWorkstationInventory(api, runtime);
    registerWorkstationWorkTool(api, runtime);
    if (registrationMode === "tool-discovery") return;
    registerServiceWorkGateway(api, runtime);
    registerWorkstationWorkHooks(api, runtime);
    registerCommands(api, runtime);
    registerHooks(api, runtime);
    registerService(api, runtime);
    runtime.cosApprovals = registerCosApprovalRelay(api, runtime);
    registerCosPermissionPreflight(api, runtime);
  },
});
