// Hosted credential-free production composition. The lost response is a
// synthetic transport boundary; no household instance or Apple data is used.
export async function auditBridgeEnrollment({ page, otherPage, stalePage, baseUrl, record, screenshot, auditAccessibility, expectHandledFailure, sourceVersion }) {
  const endpoint = "/api/member/v1/settings/bridges";
  const settingsPath = "/api/member/v1/surfaces/settings";
  const gotoBridges = async () => {
    await page.goto(`${baseUrl}/member/settings?section=bridges`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "Connect Apple Bridge", exact: true }).waitFor();
  };
  const readSettings = async () => {
    const response = await page.request.get(`${baseUrl}${settingsPath}`);
    record("Bridge owner Settings remains available", response.ok());
    return response.json();
  };
  let posts = 0;
  const countPost = (request) => { if (new URL(request.url()).pathname === endpoint && request.method() === "POST") posts += 1; };
  page.on("request", countPost);
  await gotoBridges();
  const preparationTarget = await page.locator(".bridge-prepared-confirmation").boundingBox();
  record("Mac preparation confirmation has a usable pointer target", preparationTarget && preparationTarget.width >= 24 && preparationTarget.height >= 24, preparationTarget);
  const versions = await page.locator("link[href*='/member-assets/member.css'], script[src*='/member-assets/member.js']").evaluateAll((nodes) => nodes.map((node) => new URL(node.href || node.src).searchParams.get("v")));
  record("Bridge browser exercises source-bound production assets", versions.length === 2 && versions.every((version) => version === sourceVersion));
  record("Pairing is disabled until software preparation is confirmed", await page.getByRole("button", { name: "Prepare enrollment", exact: true }).isDisabled());
  await page.locator('input[name="mac_prepared"]').check();
  record("Verified helper is available without starting enrollment", !await page.getByRole("button", { name: "Download Mac helper", exact: true }).isDisabled() && posts === 0);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download Mac helper", exact: true }).click();
  const helperDownload = await downloadPromise;
  record("Settings downloads the verified program separately from pairing", helperDownload.suggestedFilename() === "AdministrateMe-Bridge-Helper.zip" && posts === 0);
  await page.route("**/api/member/v1/settings/bridges/helper/download", (route) => route.fulfill({ status: 200, contentType: "application/zip", body: "damaged fixture archive" }));
  await page.getByRole("button", { name: "Download Mac helper", exact: true }).click();
  await page.locator(".bridge-mac-preparation [role=status]").filter({ hasText: "failed verification" }).waitFor();
  record("Damaged helper download is rejected before saving or pairing", posts === 0 && (await page.locator(".bridge-mac-preparation [role=status]").textContent()).includes("was not saved"));
  await page.unroute("**/api/member/v1/settings/bridges/helper/download");
  const before = await readSettings();
  record("Bridge fixture starts with no enrollment", before.control.bridge_wizard.current === null);
  await stalePage.goto(`${baseUrl}/member/settings?section=bridges`, { waitUntil: "networkidle" });
  await stalePage.getByRole("button", { name: "Prepare enrollment", exact: true }).waitFor();
  await stalePage.locator('input[name="mac_prepared"]').check();
  const armed = await page.request.post(`${baseUrl}/__gate7/bridge-preparation?unavailable=true`);
  record("Bridge production preparation failure is armed in the disposable fixture", armed.ok());
  await page.locator('.bridge-form input[value="apple_notes"]').check();
  const failedResponsePromise = page.waitForResponse((response) => new URL(response.url()).pathname === endpoint && response.request().method() === "POST");
  expectHandledFailure(409);
  await page.getByRole("button", { name: "Prepare enrollment", exact: true }).click();
  const failedResponse = await failedResponsePromise;
  const failedBody = failedResponse.request().postDataJSON();
  const failedError = await failedResponse.json();
  await page.locator(".bridge-enrollment-failure").waitFor();
  const failureText = await page.locator(".bridge-enrollment-failure").textContent();
  const failedSettings = await readSettings();
  const failed = failedSettings.control.bridge_wizard.current;
  record("HTTP refusal reconciles the actual recorded Failed enrollment", failedResponse.status() === 409 && failedError.detail.error === "release_unavailable" && failed.request_id === failedBody.request_id && failed.state === "failed" && failed.reason === "release_unavailable" && failureText.includes("was recorded as failed") && failureText.includes("A reviewed release commit is required") && posts === 1, failed);
  record("Failed preparation does not mark later enrollment stages complete", await page.locator('.bridge-progress-list [data-complete="true"]').count() === 0);
  record("Failure feedback never claims no request exists or exposes handoff secrets", !(await page.locator("#surface").textContent()).includes("request was not created") && !(await page.locator("#state-banner").textContent()).includes("view is unavailable") && await page.getByRole("button", { name: "Download secure handoff", exact: true }).count() === 0 && !JSON.stringify(failedSettings).includes('"transfer_key"'));
  const memberId = before.control.identity.authenticated_member_id;
  record("Recorded failure clears only its matching pending reference", await page.evaluate((id) => sessionStorage.getItem(`administrateme:bridge-enrollment-pending:${id}`) === null, memberId));
  const staleResponsePromise = stalePage.waitForResponse((response) => new URL(response.url()).pathname === endpoint && response.request().method() === "POST");
  expectHandledFailure(409);
  await stalePage.getByRole("button", { name: "Prepare enrollment", exact: true }).click();
  const staleResponse = await staleResponsePromise;
  const staleError = await staleResponse.json();
  await stalePage.locator(".bridge-enrollment-failure").waitFor();
  record("A definitive stale Settings refusal refreshes without an unknown-request lock", staleResponse.status() === 409 && staleError.detail.error === "enrollment_not_started" && staleError.detail.request_recorded === false && staleError.detail.request_id === staleResponse.request().postDataJSON().request_id && await stalePage.locator(".bridge-enrollment-pending").count() === 0 && await stalePage.getByRole("button", { name: "Prepare enrollment", exact: true }).isDisabled() && await stalePage.evaluate((id) => sessionStorage.getItem(`administrateme:bridge-enrollment-pending:${id}`) === null, memberId), staleError);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.reload({ waitUntil: "networkidle" });
    await page.locator(".bridge-enrollment-failure").waitFor();
    record(`Recorded failure remains useful after reload at ${width}`, (await page.locator(".bridge-enrollment-failure").textContent()).includes("A reviewed release commit is required") && posts === 1);
    await auditAccessibility(page, `Bridge enrollment failure ${width}`);
    await screenshot(page, `bridge-enrollment-failed-${width}.png`);
  }

  await otherPage.goto(`${baseUrl}/member/settings?section=bridges`, { waitUntil: "networkidle" });
  record("Another adult cannot see the failed private enrollment", await otherPage.locator(".bridge-enrollment-failure").count() === 0 && !(await otherPage.locator("#surface").textContent()).includes(failedBody.request_id));

  const restored = await page.request.post(`${baseUrl}/__gate7/bridge-preparation?unavailable=false`);
  record("Bridge fixture restores the real preparation implementation", restored.ok());
  await page.locator('input[name="mac_prepared"]').check();
  let delayedBody = null;
  const loseResponse = async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    delayedBody = route.request().postDataJSON();
    await route.fulfill({ status: 202, contentType: "application/json", body: "{}" });
  };
  await page.route(`**${endpoint}`, loseResponse);
  await page.locator('.bridge-form input[value="apple_notes"]').check();
  await page.getByRole("button", { name: "Prepare enrollment", exact: true }).click();
  await page.locator(".bridge-enrollment-pending").getByRole("status").waitFor();
  const pendingPosts = posts;
  const pendingText = await page.locator(".bridge-enrollment-pending").textContent();
  record("Unknown response retains exact immutable request and selected sources", delayedBody && delayedBody.request_id !== failedBody.request_id && JSON.stringify(delayedBody.capabilities) === JSON.stringify(["apple_calendar", "apple_notes", "apple_reminders"]) && pendingText.includes(delayedBody.request_id) && pendingText.includes("Do not repeat it") && await page.getByRole("button", { name: "Prepare enrollment", exact: true }).isDisabled() && await page.evaluate((body) => sessionStorage.getItem(`administrateme:bridge-enrollment-pending:${body.member_id}`) === JSON.stringify(body), delayedBody));
  await page.getByRole("button", { name: "Check enrollment status", exact: true }).click();
  await page.locator(".bridge-enrollment-pending").getByRole("status").waitFor();
  await page.reload({ waitUntil: "networkidle" });
  await page.locator(".bridge-enrollment-pending").getByRole("status").waitFor();
  record("Status checks and reload never repeat an uncertain enrollment", posts === pendingPosts && await page.getByRole("button", { name: "Prepare enrollment", exact: true }).isDisabled() && await page.getByRole("button", { name: "Retry", exact: true }).isDisabled() && (await readSettings()).control.bridge_wizard.current.request_id === failedBody.request_id);
  await screenshot(page, "bridge-enrollment-pending-390.png");
  await page.unroute(`**${endpoint}`, loseResponse);
  // Complete the original synthetic in-flight write once with exactly the
  // captured body. The UI performs GET reconciliation only, never this POST.
  const completed = await page.request.post(`${baseUrl}${endpoint}`, { data: delayedBody });
  record("Original delayed request completes through production preparation", completed.status() === 202, await completed.json());
  await page.getByRole("button", { name: "Check enrollment status", exact: true }).click();
  await page.getByRole("button", { name: "Download secure handoff", exact: true }).waitFor();
  const recovered = (await readSettings()).control.bridge_wizard.current;
  const proofResponse = await page.request.get(`${baseUrl}/__gate7/bridge-enrollment-proof`);
  const proof = await proofResponse.json();
  record("Exact delayed request reconciles without duplicate enrollment or provider effect", posts === pendingPosts && recovered.request_id === delayedBody.request_id && recovered.state === "package_ready" && await page.locator(".bridge-enrollment-pending").count() === 0 && proofResponse.ok() && JSON.stringify(proof.started_request_ids) === JSON.stringify([failedBody.request_id, delayedBody.request_id]) && proof.provider_effect_count === 0 && recovered.writes_authorized === false, proof);
  page.off("request", countPost);
}
