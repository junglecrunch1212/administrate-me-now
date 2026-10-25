/** The native SDK accepts unknown arguments; only object arguments cross our seam.
 * Field/schema and domain authorization checks remain with their existing owners.
 */
export function assertToolInput(value: unknown): asserts value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("AdministrateMe tool arguments must be an object");
  }
}
