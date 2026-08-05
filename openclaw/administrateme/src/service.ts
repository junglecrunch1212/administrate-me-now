import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { OPENCLAW_COMMIT, OPENCLAW_VERSION } from "./contract.js";
import type { PluginRuntime } from "./runtime.js";

export function registerService(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  api.registerService({
    id: "administrateme-runtime-health",
    async start() {
      try {
        await runtime.transport.reportHealth("healthy", "native plugin started");
        api.logger.info(
          `AdministrateMe plugin ready for OpenClaw ${OPENCLAW_VERSION} (${OPENCLAW_COMMIT.slice(0, 12)})`,
        );
      } catch {
        runtime.transport.setState("unavailable");
        api.logger.warn("AdministrateMe is unavailable; household state remains durable and unchanged");
      }
    },
    async stop() {
      try {
        await runtime.transport.reportHealth("unavailable", "native plugin stopped");
      } catch {
        runtime.transport.setState("unavailable");
      }
    },
  });
}
