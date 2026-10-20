/** Content-free prerequisites for the disposable hosted Gateway probe. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, realpath, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

assert.equal(process.env.GITHUB_ACTIONS, "true", "Hosted CI is required");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted", "Disposable hosted runner required");

export async function checkPermissionPins(environment, rows) {
  for (const name of ["HELPER", "DATABASE_HELPER", "PYTHON"]) {
    const value = environment[`ADMINISTRATEME_PERMISSION_${name}`];
    const expected = environment[`ADMINISTRATEME_PERMISSION_${name}_SHA256`];
    const row = { name, readable: false };
    rows.push(row);
    try {
      const canonical = await realpath(value);
      const metadata = await stat(value);
      Object.assign(row, {
        readable: true,
        absolute: path.isAbsolute(value),
        canonical: canonical === value,
        regular_file: metadata.isFile(),
        mode: (metadata.mode & 0o777).toString(8),
        safe_owner: metadata.uid === 0 || metadata.uid === process.getuid(),
        non_writable_by_others: (metadata.mode & 0o022) === 0,
        valid_hash: typeof expected === "string" && /^[a-f0-9]{64}$/.test(expected),
        hash_matches: createHash("sha256").update(await readFile(value)).digest("hex") === expected,
      });
    } catch {
      throw new Error(`Permission fixture unavailable: ${name}`);
    }
    if (!row.absolute || !row.canonical || !row.regular_file || !row.safe_owner ||
        !row.non_writable_by_others || !row.valid_hash || !row.hash_matches) {
      throw new Error(`Permission fixture pin refused: ${name}`);
    }
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const artifact = path.join(await realpath(process.env.RUNNER_TEMP), "adminme-approval-gateway-evidence");
  await mkdir(artifact, { recursive: true, mode: 0o700 });
  const result = { contract: "cos-permission-fixture-pins-v1", status: "failed", permission_pins: [] };
  try {
    const environment = JSON.parse(await readFile(process.argv[2], "utf8"));
    await checkPermissionPins(environment, result.permission_pins);
    result.status = "passed";
  } catch {
    // Each failed predicate is already recorded without paths or file content.
    process.exitCode = 1;
  }
  await writeFile(path.join(artifact, "permission-pins.json"), `${JSON.stringify(result, null, 2)}\n`, { mode: 0o600 });
  console.log(JSON.stringify(result));
}
