import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { access, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, extname, join, normalize, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const HARNESS_VERSION = "1.0.0";
const REFERENCE_SHA256 = "98ac268f39c0d367f5c904ab2af5e0e35389eae8ff6bffd4cede7ef8bfdd0a3b";
const HOST = "127.0.0.1";
const PORT = Number.parseInt(process.env.CONSOLE_REFERENCE_PORT ?? "4173", 10);
const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "..", "..");
const referencePath = join(repoRoot, "ADMINISTRATEME_CONSOLE_REFERENCE.html");
const evidenceDir = join(repoRoot, "docs", "refactor", "evidence", "console-reference");
const referenceUrl = `http://${HOST}:${PORT}/ADMINISTRATEME_CONSOLE_REFERENCE.html`;
const desktopViewport = { width: 1440, height: 1000 };
const narrowViewport = { width: 390, height: 844 };
const fixedInstant = "2026-04-21T13:42:00.000Z";

const assertions = [];
const interactions = [];
const consoleMessages = [];
const pageErrors = [];
const requestFailures = [];

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function referenceDigest(bytes) {
  // The constitutional hash was recorded from the original CRLF artifact on
  // Windows. Git stores the same text with LF, so normalize line endings before
  // checking content identity on macOS/Linux.
  return sha256(Buffer.from(bytes.toString("utf8").replace(/\r?\n/g, "\r\n"), "utf8"));
}

async function fileDigest(path) {
  return sha256(await readFile(path));
}

function serialize(value) {
  if (value === undefined) return null;
  if (value instanceof Error) return { name: value.name, message: value.message, stack: value.stack };
  return value;
}

function check(interactionId, assertionId, description, actual, expected) {
  const pass = JSON.stringify(serialize(actual)) === JSON.stringify(serialize(expected));
  assertions.push({
    interaction_id: interactionId,
    assertion_id: assertionId,
    description,
    status: pass ? "PASS" : "FAIL",
    actual: serialize(actual),
    expected: serialize(expected),
  });
  return pass;
}

async function exercise(id, name, action) {
  const firstAssertion = assertions.length;
  let thrown = null;
  try {
    await action();
  } catch (error) {
    thrown = error;
    check(id, `${id}-unexpected-error`, "interaction completes without an exception", error, "no exception");
  }
  const ownAssertions = assertions.slice(firstAssertion);
  const status = !thrown && ownAssertions.length > 0 && ownAssertions.every((item) => item.status === "PASS")
    ? "PASS"
    : "FAIL";
  interactions.push({
    id,
    name,
    status,
    assertion_ids: ownAssertions.map((item) => item.assertion_id),
    error: thrown ? serialize(thrown) : null,
  });
}

function mimeType(path) {
  return {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
  }[extname(path).toLowerCase()] ?? "application/octet-stream";
}

function createStaticServer() {
  return createServer(async (request, response) => {
    try {
      const requestUrl = new URL(request.url ?? "/", `http://${HOST}:${PORT}`);
      if (requestUrl.pathname === "/favicon.ico") {
        response.writeHead(204);
        response.end();
        return;
      }
      const decodedPath = decodeURIComponent(requestUrl.pathname);
      const relativePath = decodedPath === "/" ? "ADMINISTRATEME_CONSOLE_REFERENCE.html" : decodedPath.slice(1);
      const requestedPath = normalize(resolve(repoRoot, relativePath));
      const relativeToRoot = relative(repoRoot, requestedPath);
      if (relativeToRoot.startsWith("..") || resolve(requestedPath) === resolve(repoRoot)) {
        response.writeHead(403, { "content-type": "text/plain; charset=utf-8" });
        response.end("forbidden");
        return;
      }
      const metadata = await stat(requestedPath);
      if (!metadata.isFile()) throw new Error("not a file");
      response.writeHead(200, {
        "cache-control": "no-store",
        "content-length": metadata.size,
        "content-type": mimeType(requestedPath),
      });
      if (request.method === "HEAD") {
        response.end();
        return;
      }
      createReadStream(requestedPath).pipe(response);
    } catch {
      response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      response.end("not found");
    }
  });
}

async function listen(server) {
  await new Promise((resolvePromise, reject) => {
    server.once("error", reject);
    server.listen(PORT, HOST, resolvePromise);
  });
}

async function closeServer(server) {
  if (!server.listening) return;
  await new Promise((resolvePromise, reject) => {
    server.close((error) => error ? reject(error) : resolvePromise());
  });
}

async function launchInstalledEdge() {
  try {
    return await chromium.launch({ channel: "msedge", headless: true });
  } catch (channelError) {
    const candidates = [
      "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
      "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    ];
    for (const executablePath of candidates) {
      try {
        await access(executablePath);
        return await chromium.launch({ executablePath, headless: true });
      } catch {
        // Try the next installed-Edge location.
      }
    }
    throw new Error(`Unable to launch the installed Microsoft Edge channel: ${channelError.message}`);
  }
}

async function installDeterminism(page) {
  await page.addInitScript(({ instant }) => {
    const RealDate = Date;
    const fixedTime = new RealDate(instant).getTime();
    class FixedDate extends RealDate {
      constructor(...args) {
        super(...(args.length === 0 ? [fixedTime] : args));
      }
      static now() {
        return fixedTime;
      }
    }
    Object.defineProperty(globalThis, "Date", { configurable: true, value: FixedDate });
    Math.random = () => 0.5;
  }, { instant: fixedInstant });

  await page.route("https://fonts.googleapis.com/**", (route) => route.fulfill({
    status: 200,
    contentType: "text/css; charset=utf-8",
    body: "/* external fonts intentionally disabled by deterministic evidence harness */",
  }));
  await page.route("https://fonts.gstatic.com/**", (route) => route.fulfill({ status: 204, body: "" }));
}

async function stabilizePage(page) {
  await page.addStyleTag({ content: `
    :root {
      --font-head: Georgia, serif !important;
      --font-body: Arial, sans-serif !important;
      --font-mono: Consolas, monospace !important;
    }
    *, *::before, *::after {
      animation: none !important;
      caret-color: transparent !important;
      transition: none !important;
    }
  ` });
  await page.locator("#crm-list .crm-entry").first().waitFor({ state: "attached" });
  await page.locator("#clock").waitFor({ state: "visible" });
  await page.evaluate(() => window.scrollTo(0, 0));
}

async function resetPage(page, viewport = desktopViewport) {
  await page.setViewportSize(viewport);
  await page.goto(referenceUrl, { waitUntil: "domcontentloaded" });
  await stabilizePage(page);
}

async function visible(page, selector) {
  return page.locator(selector).isVisible();
}

async function textOf(page, selector) {
  return (await page.locator(selector).innerText()).trim();
}

async function clearToasts(page) {
  await page.locator("#toast-layer").evaluate((element) => element.replaceChildren());
}

async function activateNavTab(page, pageName) {
  const tab = page.locator(`.nav-tab[data-page="${pageName}"]`);
  await tab.focus();
  await tab.press("Enter");
}

async function run() {
  await mkdir(evidenceDir, { recursive: true });
  const referenceBytes = await readFile(referencePath);
  const actualReferenceSha = referenceDigest(referenceBytes);
  if (actualReferenceSha !== REFERENCE_SHA256) {
    throw new Error(`Reference SHA-256 mismatch: expected ${REFERENCE_SHA256}, got ${actualReferenceSha}`);
  }

  const server = createStaticServer();
  let browser;
  try {
    await listen(server);
    browser = await launchInstalledEdge();
    const browserVersion = browser.version();
    const context = await browser.newContext({
      colorScheme: "light",
      deviceScaleFactor: 1,
      locale: "en-US",
      reducedMotion: "reduce",
      timezoneId: "America/New_York",
      viewport: desktopViewport,
    });
    const page = await context.newPage();
    page.setDefaultTimeout(5000);
    await installDeterminism(page);
    page.on("console", (message) => {
      if (message.type() === "error") {
        consoleMessages.push({ type: message.type(), text: message.text(), location: message.location() });
      }
    });
    page.on("pageerror", (error) => pageErrors.push(serialize(error)));
    page.on("requestfailed", (request) => requestFailures.push({
      url: request.url(),
      method: request.method(),
      failure: request.failure(),
    }));

    await exercise("01", "carousel, compressed, and child modes", async () => {
      await resetPage(page);
      check("01", "01-carousel-active", "carousel mode is initially active", await visible(page, "#today-view-carousel"), true);
      await page.locator('#view-mode-toggle [data-mode="compressed"]').click();
      const compressedRows = await page.locator("#compressed-list .compressed-row").count();
      check("01", "01-compressed-active", "compressed mode becomes visible", await visible(page, "#today-view-compressed"), true);
      check("01", "01-compressed-populated", "compressed mode renders a decision queue", compressedRows > 0, true);
      await page.locator("#view-as-select").selectOption({ index: 2 });
      check("01", "01-child-page", "child mode activates the scoreboard page", await visible(page, "#page-scoreboard"), true);
      check("01", "01-child-chrome", "child mode applies full-bleed scoreboard chrome", await page.locator(".app").evaluate((element) => element.classList.contains("scoreboard-mode")), true);
    });

    await exercise("02", "reward toast", async () => {
      await resetPage(page);
      await page.locator('#view-mode-toggle [data-mode="compressed"]').click();
      const checkbox = page.locator("#compressed-list .compressed-check").first();
      await checkbox.click();
      check("02", "02-task-complete", "task checkbox enters done state", await checkbox.evaluate((element) => element.classList.contains("done")), true);
      check("02", "02-reward-visible", "task completion renders a reward toast", await visible(page, "#toast-layer .toast.tier-done"), true);
      const rewardSub = await textOf(page, "#toast-layer .toast-sub");
      check("02", "02-reward-tier", "reward toast reports the deterministic tier with task provenance", rewardSub.startsWith("tier: done") && rewardSub.includes("task"), true);
    });

    await exercise("03", "observation banner", async () => {
      await resetPage(page);
      check("03", "03-observation-initial", "observation banner is initially visible", await visible(page, "#observation-banner"), true);
      await activateNavTab(page, "settings");
      await page.locator("#observation-toggle").click();
      check("03", "03-observation-off", "settings toggle hides observation banner", await visible(page, "#observation-banner"), false);
      await page.locator("#observation-toggle").click();
      check("03", "03-observation-on", "settings toggle restores observation banner", await visible(page, "#observation-banner"), true);
      check("03", "03-observation-shift", "capture bar shifts below the active banner", await page.locator("#quick-capture").evaluate((element) => element.classList.contains("shifted")), true);
      await activateNavTab(page, "today");
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: join(evidenceDir, "desktop.png"), fullPage: true });
    });

    await exercise("04", "quick-capture prefix routing", async () => {
      await resetPage(page);
      const cases = [
        ["grocery: milk", "Captured to list"],
        ["call: follow up", "Captured to task"],
        ["idea: small experiment", "Captured to capture"],
        ["recipe: soup", "Captured to recipe"],
      ];
      for (const [input, expectedTitle] of cases) {
        await page.locator("#capture-input").fill(input);
        await page.locator("#capture-input").press("Enter");
        const toastTitle = await page.locator("#toast-layer .toast-title").last().innerText();
        check("04", `04-${expectedTitle.toLowerCase().replaceAll(" ", "-")}`, `${input.split(":")[0]} prefix routes to the declared destination`, toastTitle, expectedTitle);
        check("04", `04-${input.split(":")[0]}-cleared`, "quick-capture input clears after submission", await page.locator("#capture-input").inputValue(), "");
        await clearToasts(page);
      }
    });

    await exercise("05", "CRM party detail", async () => {
      await resetPage(page);
      await activateNavTab(page, "crm");
      const selectedParty = page.locator("#crm-list .crm-entry.active");
      const expectedName = await selectedParty.locator(".crm-name").innerText();
      await selectedParty.click();
      check("05", "05-party-active", "clicked CRM party remains selected", await selectedParty.evaluate((element) => element.classList.contains("active")), true);
      check("05", "05-party-name", "party detail matches the clicked party", await textOf(page, "#crm-detail-pane .party-detail h2") === expectedName.trim(), true);
      const sectionNames = await page.locator("#crm-detail-pane .party-section h4").allInnerTexts();
      check("05", "05-party-sections", "party detail renders identifiers, cadence, relationships, and interactions", sectionNames, ["IDENTIFIERS", "STATS & CADENCE", "RELATIONSHIPS", "RECENT INTERACTIONS"]);
    });

    await exercise("06", "Inbox approval and slide-out", async () => {
      await resetPage(page);
      await activateNavTab(page, "inbox");
      const cards = page.locator("#inbox-items .inbox-card");
      const before = await cards.count();
      const approve = page.locator('#inbox-items [data-action="approve"]').first();
      const card = approve.locator("xpath=ancestor::div[contains(@class,'inbox-card')]");
      await approve.click();
      check("06", "06-slide-out-class", "approved proposal enters slide-out state", await card.evaluate((element) => element.classList.contains("slide-out")), true);
      check("06", "06-approval-toast", "approval renders a commitment confirmation toast", (await textOf(page, "#toast-layer .toast-title")).includes("Commitment confirmed"), true);
      await page.waitForTimeout(450);
      check("06", "06-card-removed", "approved proposal is removed after slide-out", await cards.count(), before - 1);
    });

    await exercise("07", "calendar privacy redaction", async () => {
      await resetPage(page);
      await activateNavTab(page, "calendar");
      const privilegedEvents = page.locator("#cal-grid .cal-event.ev-work-privileged");
      const count = await privilegedEvents.count();
      const titles = await privilegedEvents.locator(".ev-title").allInnerTexts();
      const subtitles = await privilegedEvents.locator("div:last-child").allInnerTexts();
      check("07", "07-private-count", "calendar renders every declared privileged block", count, 3);
      check("07", "07-private-titles", "privileged titles are redacted to busy", titles.every((title) => title.trim() === "[busy]"), true);
      check("07", "07-private-subtitles", "privileged blocks expose only the privileged marker", subtitles.every((subtitle) => subtitle.trim() === "privileged"), true);
    });

    await exercise("08", "child scoreboard", async () => {
      await resetPage(page, narrowViewport);
      await page.locator("#view-as-select").selectOption({ index: 2 });
      const star = page.locator("#star-today");
      const before = Number.parseInt(await star.innerText(), 10);
      const chore = page.locator("#chore-grid .chore-card:not(.done)").first();
      const choreId = await chore.getAttribute("data-id");
      await chore.click();
      const completedChore = page.locator(`#chore-grid .chore-card[data-id="${choreId}"]`);
      check("08", "08-chore-complete", "unfinished child chore becomes complete", await completedChore.evaluate((element) => element.classList.contains("done")), true);
      check("08", "08-star-increment", "child completion increments today's stars", Number.parseInt(await star.innerText(), 10), before + 1);
      check("08", "08-child-reward", "child completion renders the warm reward", await visible(page, "#toast-layer .toast.tier-warm"), true);
      await page.screenshot({ path: join(evidenceDir, "narrow.png"), fullPage: true });
    });

    await exercise("09", "view-as selector", async () => {
      await resetPage(page);
      const initialGreeting = await textOf(page, "#today-greet");
      const initialSelection = await page.locator("#view-as-select option:checked").innerText();
      await page.locator("#view-as-select").selectOption({ index: 1 });
      const selectedText = await page.locator("#view-as-select option:checked").innerText();
      const viewedGreeting = await textOf(page, "#today-greet");
      check("09", "09-selector-change", "view-as selector changes to the second principal", selectedText !== initialSelection, true);
      check("09", "09-greeting-change", "view-as changes the Today greeting", viewedGreeting !== initialGreeting, true);
      check("09", "09-profile-mode", "second-principal view activates compressed mode", await visible(page, "#today-view-compressed"), true);
    });

    await exercise("10", "rate-limit toast", async () => {
      await resetPage(page);
      await activateNavTab(page, "settings");
      await page.locator('.settings-nav-item[data-pane="demo"]').click();
      const row = page.locator("#pane-demo .settings-row").filter({ hasText: "Fire rate-limit toast" });
      await row.locator("button").click();
      check("10", "10-rate-toast", "demo control renders the rate-limit toast", await visible(page, "#toast-layer .toast.tier-ratelimit"), true);
      const rateText = await textOf(page, "#toast-layer .toast.tier-ratelimit");
      check("10", "10-rate-details", "rate-limit toast includes retry and limiter provenance", rateText.includes("retry_after: 4s") && rateText.includes("writes_per_minute"), true);
    });

    await exercise("11", "degraded-mode banner", async () => {
      await resetPage(page);
      await activateNavTab(page, "settings");
      await page.locator('.settings-nav-item[data-pane="demo"]').click();
      const row = page.locator("#pane-demo .settings-row").filter({ hasText: "Toggle degraded banner" });
      await row.locator("button").click();
      check("11", "11-degraded-on", "demo control reveals the degraded banner", await visible(page, "#degraded-banner"), true);
      check("11", "11-double-shift", "capture bar shifts below both active banners", await page.locator("#quick-capture").evaluate((element) => element.classList.contains("double-shifted")), true);
      await row.locator("button").click();
      check("11", "11-degraded-off", "second activation hides the degraded banner", await visible(page, "#degraded-banner"), false);
    });

    await exercise("12", "all named Settings panes", async () => {
      await resetPage(page);
      await activateNavTab(page, "settings");
      const expectedPanes = ["observation", "persona", "members", "plaid", "reminders", "packs", "governance", "demo"];
      const navItems = page.locator("#settings-nav .settings-nav-item");
      const actualPanes = await navItems.evaluateAll((items) => items.map((item) => item.dataset.pane));
      check("12", "12-pane-inventory", "settings navigation exposes every named pane", actualPanes, expectedPanes);
      for (const pane of expectedPanes) {
        const navItem = page.locator(`.settings-nav-item[data-pane="${pane}"]`);
        await navItem.click();
        check("12", `12-${pane}-nav`, `${pane} navigation item becomes active`, await navItem.evaluate((element) => element.classList.contains("active")), true);
        check("12", `12-${pane}-pane`, `${pane} pane becomes visible`, await visible(page, `#pane-${pane}`), true);
        check("12", `12-${pane}-single`, `${pane} is the sole active settings pane`, await page.locator(".settings-pane.active").count(), 1);
      }
    });

    check("run", "run-console-errors", "reference emits no JavaScript console errors", consoleMessages.length, 0);
    check("run", "run-page-errors", "reference emits no uncaught page errors", pageErrors.length, 0);
    check("run", "run-request-failures", "reference emits no failed resource requests", requestFailures.length, 0);

    const allInteractionsPass = interactions.length === 12 && interactions.every((item) => item.status === "PASS");
    const allAssertionsPass = assertions.every((item) => item.status === "PASS");
    const overallStatus = allInteractionsPass && allAssertionsPass ? "PASS" : "FAIL";

    const screenshots = {};
    for (const filename of ["desktop.png", "narrow.png"]) {
      const path = join(evidenceDir, filename);
      const metadata = await stat(path);
      screenshots[filename] = { bytes: metadata.size, sha256: await fileDigest(path) };
    }

    const consoleReport = {
      status: consoleMessages.length === 0 && pageErrors.length === 0 ? "PASS" : "FAIL",
      console_errors: consoleMessages,
      page_errors: pageErrors,
      failed_requests: requestFailures,
    };
    const interactionReport = {
      status: overallStatus,
      count: interactions.length,
      passed: interactions.filter((item) => item.status === "PASS").length,
      failed: interactions.filter((item) => item.status === "FAIL").length,
      interactions,
    };
    const manifest = {
      status: overallStatus,
      harness_version: HARNESS_VERSION,
      source: {
        file: "ADMINISTRATEME_CONSOLE_REFERENCE.html",
        sha256: actualReferenceSha,
        url: referenceUrl,
      },
      runtime: {
        node: process.version,
        browser: "Microsoft Edge",
        browser_version: browserVersion,
        playwright_core: "1.62.0",
      },
      determinism: {
        desktop_viewport: desktopViewport,
        narrow_viewport: narrowViewport,
        device_scale_factor: 1,
        fixed_instant: fixedInstant,
        locale: "en-US",
        timezone: "America/New_York",
        reduced_motion: true,
        random_value: 0.5,
        external_fonts: "fulfilled with empty deterministic stylesheet",
      },
      results: {
        interactions: interactionReport.status,
        dom_assertions: allAssertionsPass ? "PASS" : "FAIL",
        javascript_console: consoleReport.status,
      },
      screenshots,
    };

    const markdownRows = interactions.map((item) => `| ${item.id} | ${item.name} | ${item.status} |`).join("\n");
    const interactionMarkdown = `# Console reference interaction results\n\nOverall: **${overallStatus}**\n\n| # | Declared behavior | Result |\n|---:|---|---|\n${markdownRows}\n`;
    const readme = `# Rendered console-reference evidence\n\nStatus: **${overallStatus}**\n\nRun from the repository root:\n\n\`\`\`powershell\nnpm.cmd ci --prefix tools/console-reference-harness --ignore-scripts\nnpm.cmd test --prefix tools/console-reference-harness\n\`\`\`\n\nThe harness serves the repository root only on \`${HOST}:${PORT}\`, launches installed Microsoft Edge headlessly through pinned \`playwright-core@1.62.0\`, freezes time/randomness, disables motion and remote fonts, exercises all twelve behaviors declared by the reference HTML, and exits nonzero on any failed interaction, DOM assertion, JavaScript console error, uncaught page error, or failed request.\n\nReference SHA-256: \`${actualReferenceSha}\`\n\nBrowser: Microsoft Edge \`${browserVersion}\`\n\nArtifacts:\n\n- \`desktop.png\` — ${desktopViewport.width}×${desktopViewport.height}, SHA-256 \`${screenshots["desktop.png"].sha256}\`\n- \`narrow.png\` — ${narrowViewport.width}×${narrowViewport.height}, SHA-256 \`${screenshots["narrow.png"].sha256}\`\n- \`interaction-results.md\` / \`interaction-results.json\` — twelve behavior results\n- \`dom-assertions.json\` — individual selector/state assertions\n- \`javascript-console.json\` — console errors, uncaught page errors, and request failures\n- \`run-manifest.json\` — source/runtime/determinism metadata and artifact hashes\n`;

    await Promise.all([
      writeFile(join(evidenceDir, "dom-assertions.json"), `${JSON.stringify({ status: allAssertionsPass ? "PASS" : "FAIL", count: assertions.length, assertions }, null, 2)}\n`),
      writeFile(join(evidenceDir, "interaction-results.json"), `${JSON.stringify(interactionReport, null, 2)}\n`),
      writeFile(join(evidenceDir, "interaction-results.md"), interactionMarkdown),
      writeFile(join(evidenceDir, "javascript-console.json"), `${JSON.stringify(consoleReport, null, 2)}\n`),
      writeFile(join(evidenceDir, "run-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`),
      writeFile(join(evidenceDir, "README.md"), readme),
    ]);

    await context.close();
    console.log(`CONSOLE_REFERENCE_HARNESS: ${overallStatus}`);
    console.log(`interactions: ${interactionReport.passed}/${interactionReport.count} passed`);
    console.log(`DOM assertions: ${assertions.filter((item) => item.status === "PASS").length}/${assertions.length} passed`);
    console.log(`JavaScript console errors: ${consoleMessages.length}; page errors: ${pageErrors.length}; request failures: ${requestFailures.length}`);
    console.log(`evidence: ${evidenceDir}`);
    if (overallStatus !== "PASS") process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    await closeServer(server);
  }
}

run().catch((error) => {
  console.error(`CONSOLE_REFERENCE_HARNESS: FAIL\n${error.stack ?? error.message}`);
  process.exitCode = 1;
});
