/** Narrow the SDK's unknown input before consuming current-turn authority.
 * Existing native schemas and domain validators retain field-level validation.
 */
export function assertToolInput(value: unknown): asserts value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("AdministrateMe tool arguments must be an object");
  }
}
