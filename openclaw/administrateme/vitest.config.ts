import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  resolve: {
    alias: {
      "openclaw/plugin-sdk/global-singleton": fileURLToPath(
        new URL("./test/fixtures/openclaw-2026.10.8/global-singleton.ts", import.meta.url),
      ),
      "openclaw/plugin-sdk/agent-harness-runtime": fileURLToPath(new URL("./test/fixtures/openclaw-2026.10.8/agent-harness-runtime.ts", import.meta.url)),
      "openclaw/plugin-sdk/approval-runtime": fileURLToPath(
        new URL("./test/fixtures/openclaw-2026.10.8/approval-runtime.ts", import.meta.url),
      ),
      "openclaw/plugin-sdk/plugin-entry": fileURLToPath(
        new URL("./test/fixtures/openclaw-2026.10.8/plugin-entry-runtime.ts", import.meta.url),
      ),
    },
  },
  test: {
    environment: "node",
    include: ["test/**/*.test.ts"],
    restoreMocks: true,
  },
});
