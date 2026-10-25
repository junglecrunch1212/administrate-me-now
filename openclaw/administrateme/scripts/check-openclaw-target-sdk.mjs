/** Compile the production plugin against the installed target's public SDK.
 * Unit fixtures are intentionally unavailable here. No Gateway/model/provider runs.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

assert.equal(process.env.GITHUB_ACTIONS, "true", "Use isolated hosted CI");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted", "Use a disposable hosted runner");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const target = path.resolve(process.argv[2]);
const pin = JSON.parse(await readFile(path.join(root, "../../bootstrap/versions.json"), "utf8")).openclaw;
assert.equal(path.basename(target), pin.commit);
assert.equal(JSON.parse(await readFile(path.join(target, "package.json"), "utf8")).version, pin.version);
const temporary = await mkdtemp(path.join(tmpdir(), "adminme-target-sdk-"));
try {
  await cp(path.join(root, "src"), path.join(temporary, "src"), { recursive: true });
  await mkdir(path.join(temporary, "node_modules/@types"), { recursive: true });
  for (const [name, source] of [["openclaw", target], ["typebox", path.join(root, "node_modules/typebox")], ["@types/node", path.join(root, "node_modules/@types/node")]]) {
    await symlink(source, path.join(temporary, "node_modules", name), "dir");
  }
  await writeFile(path.join(temporary, "package.json"), JSON.stringify({ type: "module" }));
  const config = JSON.parse(await readFile(path.join(root, "tsconfig.build.json"), "utf8"));
  const base = JSON.parse(await readFile(path.join(root, "tsconfig.json"), "utf8"));
  const options = { ...base.compilerOptions, ...config.compilerOptions, noEmit: true, declaration: false, sourceMap: false };
  delete options.paths;
  delete options.baseUrl;
  delete options.outDir;
  // Check our source against actual SDK types, without type-checking OpenClaw's
  // dependencies a second time. The target source build checks those separately.
  options.skipLibCheck = true;
  await writeFile(path.join(temporary, "tsconfig.json"), JSON.stringify({ compilerOptions: options, include: ["src/**/*.ts"] }));
  const require = createRequire(path.join(root, "package.json"));
  execFileSync(process.execPath, [require.resolve("typescript/bin/tsc"), "-p", path.join(temporary, "tsconfig.json")], { stdio: "inherit" });
  const targetRequire = createRequire(path.join(target, "package.json"));
  const { createJiti } = targetRequire("jiti");
  const jiti = createJiti(path.join(target, "package.json"), { tsconfigPaths: path.join(target, "tsconfig.json") });
  const supervision = await jiti.import(path.join(target, "src/infra/gateway-supervision.ts"));
  const external = { OPENCLAW_SUPERVISOR_MODE: "external" };
  assert.equal(supervision.isGatewayExternallySupervised(external), true);
  assert.throws(() => supervision.assertGatewayServiceMutationAllowed("restart", external), /external supervisor/);
  assert.match(supervision.formatExternalSupervisorUpdateRequired(), /self-update is disabled/);
  console.log(`Actual public target SDK compile and external-supervisor boundary passed: ${pin.version} ${pin.commit}`);
} finally {
  await rm(temporary, { recursive: true, force: true });
}
