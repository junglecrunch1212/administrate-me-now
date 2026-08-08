import { createHash, createHmac, randomUUID } from "node:crypto";
import { OPENCLAW_COMMIT, OPENCLAW_VERSION } from "./contract.js";
import type { PluginRuntime } from "./runtime.js";

function pythonJsonString(value: string): string {
  // Python's approval verifier uses json.dumps() with ensure_ascii=True.
  // Escape every non-ASCII UTF-16 code unit so both runtimes hash the same
  // bytes, including surrogate pairs for emoji.
  return JSON.stringify(value).replace(
    /[\u0080-\uffff]/g,
    (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`,
  );
}

function pythonJsonNumber(value: number): string {
  const magnitude = Math.abs(value);
  const serialized =
    magnitude !== 0 && magnitude >= 1e-6 && magnitude < 1e-4
      ? value.toExponential()
      : JSON.stringify(value);
  if (serialized === undefined) throw new Error("approval parameters are not JSON serializable");
  // Python pads single-digit JSON exponents (1e-07); JavaScript does not.
  return serialized.replace(/e([+-]?)(\d+)$/i, (_match, sign: string, exponent: string) => {
    return `e${sign || "+"}${exponent.padStart(2, "0")}`;
  });
}

function canonical(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return pythonJsonString(value);
  if (typeof value === "number") return pythonJsonNumber(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value !== "object") {
    throw new Error("approval parameters are not JSON serializable");
  }
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${pythonJsonString(key)}:${canonical(record[key])}`)
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
