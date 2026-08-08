import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const expectedCommit = "ce67ffb70e83e5ebb56ea02e814e577153f84f61";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function fail(message) {
  console.error(`LIVE MAC GATE NOT RUN: ${message}`);
  process.exit(2);
}

if (process.platform !== "darwin") fail("requires the disposable supported macOS lab");
if (process.env.ADMINME_LIVE_GATE !== "1") fail("set ADMINME_LIVE_GATE=1 after reviewing the lab target");
if (!process.env.OPENCLAW_SOURCE_DIR) fail("OPENCLAW_SOURCE_DIR must identify the reviewed OpenClaw checkout");
const [nodeMajor, nodeMinor, nodePatch] = process.versions.node.split(".").map(Number);
const supportedNode =
  (nodeMajor === 22 && (nodeMinor > 22 || (nodeMinor === 22 && nodePatch >= 3))) ||
  (nodeMajor === 24 && nodeMinor >= 15) ||
  (nodeMajor === 25 && nodeMinor >= 9) ||
  nodeMajor > 25;
if (!supportedNode) {
  fail(`Node ${process.versions.node} is outside the pinned OpenClaw source requirement`);
}
const sourceCommit = execFileSync("git", ["-C", process.env.OPENCLAW_SOURCE_DIR, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
if (sourceCommit !== expectedCommit) fail(`OpenClaw checkout is ${sourceCommit}, expected ${expectedCommit}`);

execFileSync("npm", ["run", "check"], { cwd: root, stdio: "inherit" });
execFileSync("npm", ["run", "build"], { cwd: root, stdio: "inherit" });
execFileSync("openclaw", ["plugins", "install", "--link", root], { stdio: "inherit" });
execFileSync("openclaw", ["plugins", "inspect", "administrateme", "--runtime", "--json"], { stdio: "inherit" });
execFileSync("openclaw", ["gateway", "restart"], { stdio: "inherit" });
execFileSync("openclaw", ["gateway", "status", "--deep", "--require-rpc"], { stdio: "inherit" });
console.log(`LIVE MAC GATE PASS: native plugin loaded at OpenClaw ${expectedCommit}.`);
