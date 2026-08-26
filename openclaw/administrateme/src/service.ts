import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { OPENCLAW_COMMIT, OPENCLAW_VERSION } from "./contract.js";
import type { PluginRuntime } from "./runtime.js";
import { TransportError } from "./transport.js";

const HEALTH_RETRY_DELAYS_MS = [1_000, 2_000, 5_000, 10_000, 30_000] as const;
const HEALTH_HEARTBEAT_MS = 30_000;

export function registerService(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  let stopped = true;
  let attempt = 0;
  let healthTimer: ReturnType<typeof setTimeout> | undefined;
  let inFlight: Promise<void> | undefined;
  let readyReported = false;

  const scheduleHealth = (delay: number): void => {
    if (stopped) return;
    healthTimer = setTimeout(() => {
      healthTimer = undefined;
      inFlight = reconcileHealth();
    }, delay);
  };

  const reconcileHealth = async (): Promise<void> => {
    try {
      await runtime.transport.reportHealth("healthy", "native plugin started");
      attempt = 0;
      if (!readyReported) {
        readyReported = true;
        api.logger.info(
          `AdministrateMe plugin ready for OpenClaw ${OPENCLAW_VERSION} (${OPENCLAW_COMMIT.slice(0, 12)})`,
        );
      }
      scheduleHealth(HEALTH_HEARTBEAT_MS);
    } catch (error) {
      runtime.transport.setState("unavailable");
      if (stopped) return;
      const state = error instanceof TransportError ? error.state : "unavailable";
      const status = error instanceof TransportError && error.status ? ` status=${error.status}` : "";
      const delay = HEALTH_RETRY_DELAYS_MS[
        Math.min(attempt, HEALTH_RETRY_DELAYS_MS.length - 1)
      ] ?? 30_000;
      const message = `AdministrateMe health report unavailable; retrying in ${delay}ms (state=${state}${status})`;
      if (attempt === 0) api.logger.warn(message);
      else api.logger.debug(message);
      attempt += 1;
      scheduleHealth(delay);
    }
  };

  api.registerService({
    id: "administrateme-runtime-health",
    start() {
      stopped = false;
      attempt = 0;
      readyReported = false;
      inFlight = reconcileHealth();
    },
    async stop() {
      stopped = true;
      if (healthTimer !== undefined) {
        clearTimeout(healthTimer);
        healthTimer = undefined;
      }
      if (inFlight !== undefined) {
        await inFlight;
        inFlight = undefined;
      }
      try {
        await runtime.transport.reportHealth("unavailable", "native plugin stopped");
      } catch {
        runtime.transport.setState("unavailable");
      }
    },
  });
}
