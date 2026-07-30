import type { Provenance } from "./contract.js";
import type { AdministrateMeTransport } from "./transport.js";

export type PluginRuntime = {
  transport: AdministrateMeTransport;
  trackedSessions: Map<string, Provenance>;
};

export function trackSession(runtime: PluginRuntime, provenance: Provenance): void {
  if (provenance.session_key) {
    runtime.trackedSessions.set(provenance.session_key, provenance);
  }
}
