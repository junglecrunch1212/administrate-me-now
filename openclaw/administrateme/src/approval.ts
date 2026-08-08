import { createHash, createHmac, randomUUID } from "node:crypto";
import { OPENCLAW_COMMIT, OPENCLAW_VERSION } from "./contract.js";
import type { PluginRuntime } from "./runtime.js";

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonical(record[key])}`)
    .join(",")}}`;
}

export function approvalRequestFingerprint(params: Record<string, unknown>): string {
  const clean = { ...params };
  delete clean.openclaw_approval_receipt_id;
  return createHash("sha256").update(canonical(clean)).digest("hex");
}

export function prepareApproval(
  runtime: PluginRuntime,
  toolName: string,
  params: Record<string, unknown>,
): {
  receiptId: string;
  params: Record<string, unknown>;
  resolve: (decision: "allow-once" | "allow-always" | "deny" | "timeout" | "cancelled") => Promise<void>;
} {
  const receiptId = `ocapr_${randomUUID()}`;
  const requestFingerprint = approvalRequestFingerprint(params);
  return {
    receiptId,
    params: { ...params, openclaw_approval_receipt_id: receiptId },
    async resolve(decision) {
      if (decision !== "allow-once") return;
      const secret = process.env[runtime.transport.config.sharedSecretEnv];
      if (!secret || secret.length < 24) throw new Error("AdministrateMe approval credential is unavailable");
      const issuedAtMs = Date.now();
      const expiresAtMs = issuedAtMs + 120_000;
      const material = [
        "administrateme-approval-v1",
        receiptId,
        toolName,
        requestFingerprint,
        String(issuedAtMs),
        String(expiresAtMs),
        "allow-once",
        OPENCLAW_VERSION,
        OPENCLAW_COMMIT,
      ].join("|");
      const signature = createHmac("sha256", secret).update(material).digest("hex");
      await runtime.transport.request("/api/openclaw/v1/approvals/resolve", {
        receipt_id: receiptId,
        tool_name: toolName,
        request_fingerprint: requestFingerprint,
        issued_at_ms: issuedAtMs,
        expires_at_ms: expiresAtMs,
        decision: "allow-once",
        openclaw_version: OPENCLAW_VERSION,
        openclaw_commit: OPENCLAW_COMMIT,
        signature,
      });
    },
  };
}
