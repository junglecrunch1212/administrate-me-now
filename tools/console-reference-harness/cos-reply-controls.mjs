// Credential-free browser proof against the real disposable application.
export async function auditCosReplyControls({ page, baseUrl, record, screenshot, auditAccessibility, mutate, label }) {
  await page.getByRole("button", { name: "CoS replies", exact: true }).click();
  const card = page.locator(".cos-reply-settings");
  await card.getByRole("heading", { name: "CoS replies", exact: true }).waitFor();
  record(`CoS replies has a URL-backed section ${label}`, new URL(page.url()).searchParams.get("section") === "cos-replies", page.url());
  record(`CoS replies explains the recipient ceiling ${label}`, (await card.textContent()).includes("An empty list blocks all replies") && (await card.textContent()).includes("does not turn on sending"), await card.textContent());
  await card.getByRole("button", { name: "Add recipient", exact: true }).click();
  const row = card.locator(".cos-reply-recipient").last();
  await row.getByRole("combobox", { name: "Channel", exact: true }).selectOption("email");
  await row.getByRole("textbox", { name: "Email address or international phone number", exact: true }).fill("fixture.reply@example.test");
  record(`CoS reply recipient binds a visible member and channel ${label}`, await row.getByRole("combobox", { name: "Member", exact: true }).inputValue() !== "" && await row.getByRole("combobox", { name: "Channel", exact: true }).inputValue() === "email");
  await auditAccessibility(page, `CoS reply recipients ${label}`);
  await screenshot(page, `surface-settings-cos-replies-${label}.png`);
  if (!mutate) return;
  const savedResponse = page.waitForResponse((response) => response.url().endsWith("/api/member/v1/settings/cos-replies") && response.request().method() === "PUT");
  await card.getByRole("button", { name: "Save recipients", exact: true }).click();
  const saved = await savedResponse;
  const receipt = await saved.json();
  record("CoS recipient Settings save records no provider effect", saved.status() === 202 && receipt.provider_effects === 0 && receipt.grants_provider_authority === false, receipt);
  await page.locator(`.cos-reply-settings[data-policy-revision="${receipt.revision}"]`).waitFor();
  await page.reload({ waitUntil: "networkidle" });
  await card.locator(".cos-reply-recipient").waitFor();
  record("CoS recipient survives page reload", await card.getByRole("textbox", { name: "Email address or international phone number", exact: true }).inputValue() === "fixture.reply@example.test");
  const revokedResponse = page.waitForResponse((response) => response.url().endsWith("/api/member/v1/settings/cos-replies") && response.request().method() === "PUT");
  await card.getByRole("button", { name: "Revoke all replies", exact: true }).click();
  const revoked = await revokedResponse;
  const revocation = await revoked.json();
  record("CoS recipient revocation records no provider effect", revoked.status() === 202 && revocation.revision === receipt.revision + 1 && revocation.provider_effects === 0, revocation);
  await page.locator(`.cos-reply-settings[data-policy-revision="${revocation.revision}"]`).waitFor();
  const currentResponse = await page.request.get(`${baseUrl}/api/member/v1/settings/cos-replies`);
  const current = await currentResponse.json();
  record("CoS empty list revokes every reply recipient", currentResponse.ok() && current.entries.length === 0 && current.revision === revocation.revision, current);
  await page.reload({ waitUntil: "networkidle" });
  await card.getByRole("heading", { name: "CoS replies", exact: true }).waitFor();
  record("CoS revoked recipients stay absent on reload", await card.locator(".cos-reply-recipient").count() === 0);
}
