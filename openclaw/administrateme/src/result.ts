import type { ToolResult } from "./sdk-types.js";

export function toolResult(value: unknown): ToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify(value) }],
    details: value,
  };
}

export function safeError(error: unknown): string {
  return error instanceof Error ? error.message : "AdministrateMe request failed";
}
