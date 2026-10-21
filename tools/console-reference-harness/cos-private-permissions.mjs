// Explicit member permission consent through production routes in hosted,
// credential-free CI. The delayed-response boundary is synthetic only.
export async function auditCosPrivatePermissions({ page, otherPage, stalePage, baseUrl, record, screenshot, auditAccessibility, expectHandledFailure }) {
  const endpoint = "/api/member/v1/cos/delegations";
  const get = async () => {
    const response = await page.request.get(`${baseUrl}${endpoint}`);
    record("CoS permission list is available to its owner", response.ok());
    return response.json();
  };
  const gotoPermissions = async () => {
    await page.goto(`${baseUrl}/member/settings?section=cos-work`, { waitUntil: "networkidle" });
    await page.locator(".cos-private-permissions").getByRole("status").filter({ hasText: /Choose each permission|Your permissions are shown|last permission change/ }).waitFor();
  };
  const card = page.locator(".cos-private-permissions");
  const review = card.getByRole("button", { name: "Review permissions", exact: true });
  const read = card.getByRole("checkbox", { name: /^Read my private AdministrateMe context/ });
  const prepare = card.getByRole("checkbox", { name: /^Prepare external actions/ });
  const dialog = page.locator("#action-dialog");
  let browserPosts = 0;
  const countPost = (request) => { if (new URL(request.url()).pathname === endpoint && request.method() === "POST") browserPosts += 1; };
  page.on("request", countPost);
  const fixtureResponse = await page.request.post(`${baseUrl}/__gate7/prepare-cos-replies`);
  const fixture = await fixtureResponse.json();
  const policyResponse = await page.request.get(`${baseUrl}/api/member/v1/settings/cos-replies`);
  const policy = await policyResponse.json();
  const recipientResponse = await page.request.put(`${baseUrl}/api/member/v1/settings/cos-replies`, { data: { request_id: "visual-permission-recipient", expected_revision: policy.revision, entries: [{ member_id: fixture.member_id, channel: fixture.channel, address: fixture.recipient }] } });
  record("Permission browser fixture has one synthetic reply destination", fixtureResponse.ok() && recipientResponse.status() === 202);
  const before = await get();
  record("CoS permission setup starts without an implicit grant", before.items.length === 0);
  await page.goto(`${baseUrl}/member/settings?section=cos-replies`, { waitUntil: "networkidle" });
  const composer = page.locator(".cos-response-approval");
  await composer.getByRole("link", { name: "Review my CoS permissions", exact: true }).waitFor();
  record("Reply composer explains missing permission before text approval", await composer.getByRole("button", { name: "Approve this exact reply", exact: true }).count() === 0 && await composer.getByRole("textbox", { name: "Exact reply text", exact: true }).count() === 0);
  await composer.getByRole("link", { name: "Review my CoS permissions", exact: true }).click();
  await review.waitFor();
  record("CoS permission choices require separate explicit consent", !await read.isChecked() && !await prepare.isChecked() && await review.isDisabled() && await card.getByRole("combobox", { name: "Permission duration", exact: true }).inputValue() === "15" && await card.getByRole("combobox").count() === 1);
  record("Permission setup explains private scope and unchanged send gates", (await card.textContent()).includes("does not connect or monitor my personal inboxes") && (await card.textContent()).includes("Every actual send still needs my exact-text approval and a separate sending approval"));
  await stalePage.goto(`${baseUrl}/member/settings?section=cos-work`, { waitUntil: "networkidle" });
  const staleCard = stalePage.locator(".cos-private-permissions");
  await staleCard.getByRole("status").filter({ hasText: "Choose each permission" }).waitFor();
  await staleCard.getByRole("checkbox", { name: /^Read my private AdministrateMe context/ }).check();
  record("Both owner tabs load before either grants permission", !await staleCard.getByRole("button", { name: "Review permissions", exact: true }).isDisabled() && (await get()).items.length === 0);
  await read.check();
  await review.click();
  await dialog.getByRole("button", { name: "Grant these permissions", exact: true }).waitFor();
  record("Permission confirmation previews only selected permission and exact expiry", (await dialog.textContent()).includes("My private AdministrateMe context") && (await dialog.textContent()).includes("Read my private AdministrateMe context") && !(await dialog.textContent()).includes("Prepare external actions") && (await dialog.textContent()).includes("Expires") && browserPosts === 0 && (await get()).items.length === 0);
  await dialog.getByRole("button", { name: "Go back", exact: true }).click();
  record("Cancelling permission review makes no grant", browserPosts === 0 && (await get()).items.length === 0);
  await review.click();
  const requested = page.waitForResponse((response) => new URL(response.url()).pathname === endpoint && response.request().method() === "POST");
  await dialog.getByRole("button", { name: "Grant these permissions", exact: true }).click();
  const firstResponse = await requested;
  const firstBody = firstResponse.request().postDataJSON();
  const firstReceipt = await firstResponse.json();
  await card.getByRole("status").filter({ hasText: "Your permissions are shown" }).waitFor();
  const firstGrant = (await get()).items.find((item) => item.grant_id === firstReceipt.grant_id);
  const browserNow = await page.evaluate(() => Date.now());
  record("Only explicit own read permission is granted for fifteen minutes", firstResponse.status() === 201 && firstGrant.active && firstBody.context_scope === "member_private" && firstBody.context_member_id === firstGrant.context_member_id && firstGrant.context_member_id === firstGrant.requested_by_member_id && JSON.stringify(firstBody.permissions) === JSON.stringify(["read_context"]) && Date.parse(firstBody.expires_at) - browserNow === 15 * 60_000 && firstReceipt.provider_authority === false && firstReceipt.external_write_requests === 0, firstReceipt);
  record("An active private grant disables replacement creation", await review.isDisabled() && await read.isDisabled() && await prepare.isDisabled());
  await staleCard.getByRole("button", { name: "Review permissions", exact: true }).click();
  const staleDialog = stalePage.locator("#action-dialog");
  const staleResponsePromise = stalePage.waitForResponse((response) => new URL(response.url()).pathname === endpoint && response.request().method() === "POST");
  expectHandledFailure(409);
  await staleDialog.getByRole("button", { name: "Grant these permissions", exact: true }).click();
  const staleResponse = await staleResponsePromise;
  const staleError = await staleResponse.json();
  await staleCard.getByRole("status").filter({ hasText: "Your permissions are shown" }).waitFor();
  const afterStale = await get();
  const stalePendingCleared = await stalePage.evaluate((memberId) => sessionStorage.getItem(`administrateme:cos-permission-pending:${memberId}`) === null, fixture.member_id);
  record("Stale owner tab receives a definitive conflict and refreshes the one active grant", staleResponse.status() === 409 && staleError.detail.code === "private_delegation_already_active" && staleResponse.request().postDataJSON().require_no_active_private_grant === true && firstBody.require_no_active_private_grant === true && stalePendingCleared && afterStale.items.length === 1 && afterStale.items[0].grant_id === firstReceipt.grant_id && afterStale.items[0].active && await staleCard.getByRole("button", { name: "Review permissions", exact: true }).isDisabled() && (await stalePage.locator("#state-banner").textContent()).includes("permissions changed"), staleError);

  await page.goto(`${baseUrl}/member/settings?section=cos-replies`, { waitUntil: "networkidle" });
  await composer.getByRole("link", { name: "Review my CoS permissions", exact: true }).waitFor();
  record("Read context alone does not authorize reply preparation", await composer.getByRole("button", { name: "Approve this exact reply", exact: true }).count() === 0);
  await gotoPermissions();
  await card.getByRole("button", { name: "Revoke these permissions…", exact: true }).click();
  await dialog.getByRole("button", { name: "Keep permissions", exact: true }).click();
  record("Revocation requires its own confirmation", (await get()).items.find((item) => item.grant_id === firstReceipt.grant_id).active === true);
  await card.getByRole("button", { name: "Revoke these permissions…", exact: true }).click();
  await dialog.getByRole("button", { name: "Revoke these permissions", exact: true }).click();
  await card.getByRole("status").filter({ hasText: "Choose each permission" }).waitFor();
  const revoked = (await get()).items.find((item) => item.grant_id === firstReceipt.grant_id);
  record("Explicit revocation retains the original grant history", !revoked.active && Boolean(revoked.revoked_at) && (await card.textContent()).includes("Revoked"));

  await read.uncheck(); await prepare.check();
  await card.getByRole("combobox", { name: "Permission duration", exact: true }).selectOption("until_revoked");
  await review.click();
  record("Until-revoked permission requires an explicit unbounded review", (await dialog.textContent()).includes("Until revoked") && (await dialog.textContent()).includes("Prepare external actions") && !(await dialog.textContent()).includes("Read my private AdministrateMe context"));
  const prepareResponsePromise = page.waitForResponse((response) => new URL(response.url()).pathname === endpoint && response.request().method() === "POST");
  await dialog.getByRole("button", { name: "Grant these permissions", exact: true }).click();
  const prepareResponse = await prepareResponsePromise;
  const prepareReceipt = await prepareResponse.json();
  await card.getByRole("status").filter({ hasText: "Your permissions are shown" }).waitFor();
  record("Until revoked never silently includes reading context", prepareResponse.status() === 201 && prepareResponse.request().postDataJSON().expires_at === null && JSON.stringify(prepareResponse.request().postDataJSON().permissions) === JSON.stringify(["prepare_external_actions"]));
  await page.goto(`${baseUrl}/member/settings?section=cos-replies`, { waitUntil: "networkidle" });
  await composer.getByRole("button", { name: "Approve this exact reply", exact: true }).waitFor();
  record("iMessage approval remains blocked without result-reading permission", await composer.getByRole("combobox", { name: "Reply channel", exact: true }).inputValue() === "imessage" && await composer.getByRole("button", { name: "Approve this exact reply", exact: true }).isDisabled() && (await composer.getByRole("status").textContent()).includes("private context permission"));
  await gotoPermissions();
  await card.locator(`[data-grant-id="${prepareReceipt.grant_id}"]`).getByRole("button", { name: "Revoke these permissions…", exact: true }).click();
  await dialog.getByRole("button", { name: "Revoke these permissions", exact: true }).click();
  await card.getByRole("status").filter({ hasText: "Choose each permission" }).waitFor();

  const expired = await page.request.post(`${baseUrl}${endpoint}`, { data: { request_id: "visual-expired-private-permission", context_scope: "member_private", permissions: ["read_context"], expires_at: new Date(browserNow - 60_000).toISOString() } });
  record("Expired synthetic permission fixture is recorded through the real route", expired.status() === 201);
  await gotoPermissions();
  record("Expired permission is visible and does not grant current authority", (await card.textContent()).includes("Expired") && !await read.isDisabled() && (await get()).items.every((item) => !item.active));

  // Simulate a request whose response is lost before its durable result arrives.
  // The UI must never repeat POST, including after navigation or a status check.
  let delayedBody = null;
  const delayCreate = async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    delayedBody = route.request().postDataJSON();
    await route.fulfill({ status: 201, contentType: "application/json", body: "{}" });
  };
  await page.route(`**${endpoint}`, delayCreate);
  await read.check(); await prepare.check();
  await card.getByRole("combobox", { name: "Permission duration", exact: true }).selectOption("60");
  await review.click();
  record("Both permissions and bounded expiry are reviewed separately from sending", (await dialog.textContent()).includes("Read my private AdministrateMe context") && (await dialog.textContent()).includes("Prepare external actions") && (await dialog.textContent()).includes("Every actual reply still needs both approvals"));
  await dialog.getByRole("button", { name: "Grant these permissions", exact: true }).click();
  await card.getByRole("status").filter({ hasText: "last permission change is not confirmed" }).waitFor();
  const postsAfterUnknown = browserPosts;
  await card.getByRole("button", { name: "Check permission status", exact: true }).click();
  await page.waitForFunction(() => [...document.querySelectorAll(".cos-private-permissions button")].some((node) => node.textContent === "Check permission status" && !node.disabled));
  await card.getByRole("status").filter({ hasText: "last permission change is not confirmed" }).waitFor();
  await page.reload({ waitUntil: "networkidle" });
  await card.getByRole("status").filter({ hasText: "last permission change is not confirmed" }).waitFor();
  record("Unknown create stays locked across status checks and reload without retry", browserPosts === postsAfterUnknown && await review.isDisabled() && await read.isDisabled() && (await get()).items.every((item) => !item.active));
  await page.unroute(`**${endpoint}`, delayCreate);
  // Complete the original synthetic in-flight request exactly once. This is a
  // fixture transport action, not a browser retry or a production side effect.
  const delayedResponse = await page.request.post(`${baseUrl}${endpoint}`, { data: delayedBody });
  const delayedReceipt = await delayedResponse.json();
  record("Original delayed permission request is recorded once", delayedResponse.status() === 201, delayedReceipt);
  await card.getByRole("button", { name: "Check permission status", exact: true }).click();
  await card.getByRole("status").filter({ hasText: "Your permissions are shown" }).waitFor();
  const after = await get();
  const confirmed = after.items.find((item) => item.grant_id === delayedReceipt.grant_id);
  const confirmationResponse = await page.request.get(`${baseUrl}${endpoint}?request_id=${encodeURIComponent(delayedBody.request_id)}`);
  const confirmation = await confirmationResponse.json();
  record("Unknown response reconciles the exact request and exact typed grant", browserPosts === postsAfterUnknown && confirmation.request_confirmation.state === "recorded" && confirmation.request_confirmation.grant_id === confirmed.grant_id && confirmed.active && JSON.stringify(confirmed.permissions) === JSON.stringify(["prepare_external_actions", "read_context"]) && Date.parse(confirmed.expires_at) - browserNow === 60 * 60_000 && after.items.filter((item) => item.active).length === 1 && await review.isDisabled(), confirmation.request_confirmation);

  await otherPage.goto(`${baseUrl}/member/settings?section=cos-work`, { waitUntil: "networkidle" });
  const otherCard = otherPage.locator(".cos-private-permissions");
  await otherCard.getByRole("status").filter({ hasText: "Choose each permission" }).waitFor();
  const otherResponse = await otherPage.request.get(`${baseUrl}${endpoint}`);
  const other = await otherResponse.json();
  record("Another adult sees only their own permission setup", otherResponse.ok() && other.items.length === 0 && await otherCard.getByRole("button", { name: "Revoke these permissions…", exact: true }).count() === 0 && !(await otherCard.textContent()).includes(confirmed.grant_id) && await otherCard.getByRole("combobox").count() === 1);
  // This header belongs solely to the credential-free test ingress. It is
  // never a technique for live member acceptance or permission creation.
  let viewedPermissionReads = 0;
  const countViewedRead = (request) => { if (new URL(request.url()).pathname === endpoint) viewedPermissionReads += 1; };
  otherPage.on("request", countViewedRead);
  await otherPage.setExtraHTTPHeaders({ "Tailscale-User-Login": "visual-adult.ts.net", "X-AdminMe-View-Member": confirmed.context_member_id });
  await otherPage.reload({ waitUntil: "networkidle" });
  await otherCard.getByText("Only a principal in their own private view can review or change these permissions. Return to your own view to continue.", { exact: true }).waitFor();
  record("View-as offers no permission controls or private delegation fetch", await otherCard.getByRole("button").count() === 0 && await otherCard.getByRole("checkbox").count() === 0 && viewedPermissionReads === 0);
  otherPage.off("request", countViewedRead);
  const sharedResponse = await page.request.post(`${baseUrl}${endpoint}`, { data: { request_id: "visual-shared-permission-history", context_scope: "household_shared", permissions: ["read_context"], expires_at: null } });
  const sharedReceipt = await sharedResponse.json();
  record("Shared synthetic grant remains separately scoped and pending its quorum", sharedResponse.status() === 201 && sharedReceipt.active === false && sharedReceipt.required_approving_member_ids.length === 2);
  await gotoPermissions();
  const exactRecords = page.locator("details").filter({ has: page.locator("summary", { hasText: "Exact delegations" }) });
  record("Existing shared authority records remain visible without private mutation controls", (await exactRecords.textContent()).includes(sharedReceipt.grant_id) && (await exactRecords.textContent()).includes(confirmed.grant_id) && await card.locator(`[data-grant-id="${sharedReceipt.grant_id}"]`).count() === 0 && await card.getByRole("button", { name: "Revoke these permissions…", exact: true }).count() === 1);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await gotoPermissions();
    await auditAccessibility(page, `CoS private permissions ${width}`);
    record(`CoS private permission controls fit at ${width}`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await screenshot(page, `cos-private-permissions-${width}.png`);
  }
  page.off("request", countPost);
}
