// Hosted-only plugin: real production relay, real Gateway services and registry.
// This fixture adds no production API and never registers a tool or channel.
import { readFile, writeFile } from "node:fs/promises";
import { registerCosApprovalRelay } from "./cos-approval-relay.js";
import { registerCosPermissionPreflight } from "./cos-permission-preflight.js";

export default {
  id: "administrateme",
  name: "AdministrateMe isolated approval relay probe",
  register(api) {
    const runtime = {
      transport: {
        config: { sharedSecretEnv: "ADMINME_APPROVAL_PROBE_SECRET" },
      },
    };
    const relay = registerCosApprovalRelay(api, runtime);
    registerCosPermissionPreflight(api, runtime);
    api.registerService({
      id: "administrateme-approval-probe-seeds",
      async start() {
        const rows = JSON.parse(
          await readFile(process.env.ADMINME_APPROVAL_PROBE_SEEDS, "utf8"),
        );
        for (const row of rows) {
          relay.remember(row.params, row.context, row.receipt);
        }
        // Registered after the production service: this marker proves its
        // startup hook returned. The hosted probe independently requires
        // normal Gateway readiness, cron RPCs, and the native subscription.
        await writeFile(process.env.ADMINME_APPROVAL_PROBE_READY, "ready\n", {
          mode: 0o600,
        });
      },
    });
  },
};
