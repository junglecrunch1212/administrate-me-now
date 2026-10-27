import {
  OPENCLAW_COMMIT,
  OPENCLAW_VERSION,
  type IntegrationState,
  type PluginConfig,
} from "./contract.js";

export class TransportError extends Error {
  readonly status?: number;
  readonly state: IntegrationState;

  constructor(message: string, state: IntegrationState, status?: number) {
    super(message);
    this.name = "TransportError";
    this.state = state;
    if (status !== undefined) {
      this.status = status;
    }
  }
}

// Only closed local error codes are exposed. Never forward backend reason text,
// URLs, provider errors, private inputs, or arbitrary response fields to a model.
const REQUEST_FAILURES = {
  request_authorization_unavailable: "Request grounding could not be verified. No action was confirmed or executed; this does not establish an account choice or provider permission failure. This explicit verification failure is not a transport timeout: stop, report it faithfully, and do not blindly repeat the request or ask for another permission.",
  request_clarification_required: "The proposed action did not match a clear current request. Clarify only the ambiguous intent; no action was confirmed or executed.",
} as const;

const SOURCE_PROVENANCE_LIMIT_PATH = "/api/openclaw/v1/tools/service-private-work";
const SOURCE_PROVENANCE_LIMIT_CODE = "source_provenance_limit_exceeded";
const SOURCE_PROVENANCE_LIMIT_FAILURE = "This private record would retain more than 256 source authorities. No work was created. Start a fresh, narrowly scoped run and do not retry this request in the same accumulated session.";

async function safeRequestFailure(response: Response, path: string): Promise<string | undefined> {
  if (response.status !== 409) return undefined;
  try {
    const body: unknown = await response.json();
    if (!body || typeof body !== "object" || !("detail" in body)) return undefined;
    const detail = body.detail;
    if (!detail || typeof detail !== "object" || !("code" in detail)) return undefined;
    const code = detail.code;
    if (path === SOURCE_PROVENANCE_LIMIT_PATH && code === SOURCE_PROVENANCE_LIMIT_CODE) {
      return `${SOURCE_PROVENANCE_LIMIT_CODE}: ${SOURCE_PROVENANCE_LIMIT_FAILURE}`;
    }
    if (["/api/openclaw/v1/tools/confirm-apple-reminder", "/api/openclaw/v1/tools/cos-apple-reminders/confirm", "/api/openclaw/v1/tools/confirm-requested-email"].includes(path) &&
        (code === "request_authorization_unavailable" || code === "request_clarification_required")) {
      return `${code}: ${REQUEST_FAILURES[code]}`;
    }
  } catch { /* Keep unknown or malformed failures opaque. */ }
  return undefined;
}

export class AdministrateMeTransport {
  readonly config: PluginConfig;
  #state: IntegrationState = "degraded";
  #fetch: typeof fetch;

  constructor(config: PluginConfig, fetchImpl: typeof fetch = fetch) {
    this.config = config;
    this.#fetch = fetchImpl;
  }

  get state(): IntegrationState {
    return this.#state;
  }

  setState(state: IntegrationState): void {
    this.#state = state;
  }

  #secret(): string {
    const secret = process.env[this.config.sharedSecretEnv];
    if (!secret || secret.length < 24) {
      this.#state = "unavailable";
      throw new TransportError("AdministrateMe transport credential is unavailable", "unavailable");
    }
    return secret;
  }

  async request<T>(path: string, body: unknown): Promise<T> {
    if (!path.startsWith("/api/openclaw/v1/")) {
      throw new TransportError("AdministrateMe transport path is outside the narrow API", "incompatible");
    }
    const controller = new AbortController();
    // One explicit source-grounding model invocation has a 30s deadline plus
    // the existing 2s completion allowance. Other calls keep their bounded limit.
    const timeoutMs = (path === "/api/openclaw/v1/tools/confirm-apple-reminder" || path === "/api/openclaw/v1/tools/cos-apple-reminders/confirm" || path === "/api/openclaw/v1/tools/confirm-requested-email")
      ? Math.max(this.config.timeoutMs, 35_000) : this.config.timeoutMs;
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await this.#fetch(`${this.config.baseUrl}${path}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.#secret()}`,
          "Content-Type": "application/json",
          "User-Agent": `administrateme-openclaw/${OPENCLAW_VERSION}`,
          "X-OpenClaw-Commit": OPENCLAW_COMMIT,
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) {
        const safeFailure = await safeRequestFailure(response, path);
        const state: IntegrationState = response.status === 409 && !safeFailure ? "incompatible" : "degraded";
        this.#state = state;
        throw new TransportError(
          safeFailure ?? `AdministrateMe transport rejected request (${response.status})`,
          state,
          response.status,
        );
      }
      this.#state = "healthy";
      return (await response.json()) as T;
    } catch (error) {
      if (error instanceof TransportError) {
        throw error;
      }
      this.#state = "unavailable";
      const reason = error instanceof DOMException && error.name === "AbortError" ? "timed out" : "failed";
      throw new TransportError(`AdministrateMe transport ${reason}`, "unavailable");
    } finally {
      clearTimeout(timeout);
    }
  }

  async reportHealth(state: IntegrationState, reason?: string): Promise<void> {
    await this.request("/api/openclaw/v1/health/report", {
      state,
      reason,
      openclaw_version: OPENCLAW_VERSION,
      openclaw_commit: OPENCLAW_COMMIT,
      dm_scope: this.config.dmScope,
      member_agent_ids: this.config.memberAgentIds,
      binding_fingerprints: this.config.bindingFingerprints,
      unique_agents: this.config.uniqueAgents,
      unique_accounts: this.config.uniqueAccounts,
    });
    this.#state = state;
  }
}
