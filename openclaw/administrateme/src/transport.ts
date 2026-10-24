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
    const timeoutMs = path === "/api/openclaw/v1/tools/confirm-apple-reminder"
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
        const state: IntegrationState = response.status === 409 ? "incompatible" : "degraded";
        this.#state = state;
        throw new TransportError(
          `AdministrateMe transport rejected request (${response.status})`,
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
