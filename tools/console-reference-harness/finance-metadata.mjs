// Real candidate assets and metadata endpoints on a disposable loopback fixture.
export async function auditFinanceMetadata({ page, stalePage, otherPage, baseUrl, record, screenshot, auditAccessibility, expectHandledFailure, sourceVersion }) {
  const endpoint = "/api/member/v1/settings/finance-metadata";
  const card = (target = page) => target.locator(".finance-metadata-settings");
  const status = (target = page) => card(target).getByRole("status");
  const visit = async (target = page) => {
    await target.goto(`${baseUrl}/member/settings?section=cos-work`, { waitUntil: "networkidle" });
    await card(target).waitFor();
  };
  const ready = async (target = page) => {
    await visit(target);
    await status(target).filter({ hasText: /Your metadata access is (enabled|disabled)/ }).waitFor();
  };
  await ready();
  const versions = await page.locator("link[href*='/member-assets/member.css'], script[src*='/member-assets/member.js']").evaluateAll(nodes => nodes.map(node => new URL(node.href || node.src).searchParams.get("v")));
  record("Metadata assets match the candidate", versions.length === 2 && versions.every(value => value === sourceVersion));
  record("Metadata starts disabled with one direct-owner control", await card().getByRole("checkbox").count() === 1 && !await card().getByRole("checkbox").isChecked() && await card().getByRole("button", { name: "Inspect retained bindings" }).isDisabled());
  const before = await (await page.request.get(`${baseUrl}${endpoint}`)).json();
  await ready(stalePage);
  await card().getByRole("checkbox").check();
  await card().getByRole("button", { name: "Save metadata access" }).click();
  await status().filter({ hasText: "Your metadata access is enabled." }).waitFor();
  const enabled = await (await page.request.get(`${baseUrl}${endpoint}`)).json();
  record("Owner enablement preserves the exact CoS ceiling", enabled.owner_read_enabled === true && enabled.cos_read_enabled === before.cos_read_enabled && enabled.revision === before.revision + 1);
  await card().getByRole("button", { name: "Inspect retained bindings" }).click();
  await card().locator('.finance-metadata-result[data-state="verified_empty"]').waitFor();
  record("Real empty retained history is explicitly verified", (await card().textContent()).includes("Verified Empty"));
  await card(stalePage).getByRole("checkbox").check();
  expectHandledFailure(409);
  await card(stalePage).getByRole("button", { name: "Save metadata access" }).click();
  await status(stalePage).filter({ hasText: "Save could not be verified" }).waitFor();
  record("A stale save blocks inspection until authoritative refresh", await card(stalePage).getByRole("button", { name: "Inspect retained bindings" }).isDisabled());
  await card(stalePage).getByRole("button", { name: "Check metadata access" }).click();
  await status(stalePage).filter({ hasText: "Your metadata access is enabled." }).waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await card().scrollIntoViewIfNeeded();
    await screenshot(page, `finance-metadata-enabled-${width}.png`);
    await auditAccessibility(page, `finance-metadata-${width}`);
    record(`Metadata fits viewport ${width}`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  await card().getByRole("checkbox").uncheck();
  await card().getByRole("button", { name: "Save metadata access" }).click();
  await status().filter({ hasText: "History is preserved." }).waitFor();
  const disabled = await (await page.request.get(`${baseUrl}${endpoint}`)).json();
  record("Revocation disables inspection and clears prior output", !disabled.owner_read_enabled && disabled.cos_read_enabled === before.cos_read_enabled && await card().getByRole("button", { name: "Inspect retained bindings" }).isDisabled() && await card().locator(".finance-metadata-result").textContent() === "");
  expectHandledFailure(503);
  await page.route(`**${endpoint}`, route => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic unavailable metadata store" }) }), { times: 1 });
  await card().getByRole("button", { name: "Check metadata access" }).click();
  await status().filter({ hasText: "Access could not be verified" }).waitFor();
  record("Unavailable access never becomes verified empty", await card().getByRole("checkbox").isDisabled() && await card().getByRole("button", { name: "Inspect retained bindings" }).isDisabled() && await card().locator(".finance-metadata-result").textContent() === "");
  await screenshot(page, "finance-metadata-unavailable-390.png");
  await otherPage.goto(`${baseUrl}/member/settings?section=cos-work`, { waitUntil: "networkidle" });
  const otherSettings = await (await otherPage.request.get(`${baseUrl}/api/member/v1/surfaces/settings`)).json();
  record("Denied-view fixture is actually a child", otherSettings.control.identity.authenticated_role === "child", otherSettings.control.identity);
  const absent = await card(otherPage).count() === 0;
  record("Nonprincipal view exposes no metadata controls", absent || (await card(otherPage).getByRole("checkbox").count() === 0 && (await card(otherPage).textContent()).includes("own authenticated principal view")));
  const denied = await otherPage.request.get(`${baseUrl}${endpoint}`);
  record("Actual child metadata endpoint remains forbidden", denied.status() === 403, denied.status());
}
