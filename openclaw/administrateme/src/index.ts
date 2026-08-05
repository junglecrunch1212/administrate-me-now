import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { readPluginConfig } from "./config.js";
import { PLUGIN_ID } from "./contract.js";
import { registerCommands } from "./commands.js";
import { registerDashboard } from "./dashboard.js";
import { registerHooks } from "./hooks.js";
import { registerService } from "./service.js";
import { registerTools } from "./tools.js";
import { AdministrateMeTransport } from "./transport.js";

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "AdministrateMe",
  description: "Authorized household state and action seam for AdministrateMe v2.",
  register(api) {
    if (api.registrationMode !== "full") return;
    const config = readPluginConfig(api.pluginConfig);
    const runtime = {
      transport: new AdministrateMeTransport(config),
      trackedSessions: new Map(),
    };
    registerDashboard(api, runtime);
    registerTools(api, runtime);
    registerCommands(api, runtime);
    registerHooks(api, runtime);
    registerService(api, runtime);
  },
});
