// Real member routes and UI; only the isolated Gateway conversation is a fixture.
export async function auditCosSendingApproval({ page, baseUrl, record, screenshot, auditAccessibility }) {
  const call = async (path, method = "GET", data = null) => {
    const response = await page.request.fetch(`${baseUrl}${path}`, { method, ...(data ? { data } : {}) });
    const value = await response.json();
    record(`CoS sending fixture ${method} ${path}`, response.ok(), value);
    return value;
  };
  const fixture = await call("/__gate7/prepare-cos-replies", "POST");
  const policy = await call("/api/member/v1/settings/cos-replies");
  await call("/api/member/v1/settings/cos-replies", "PUT", {
    request_id: "visual-cos-recipients",
    expected_revision: policy.revision,
    entries: [{ member_id: fixture.member_id, channel: fixture.channel, address: fixture.recipient }],
  });
  const endpoint = "/api/member/v1/chat/reply-approvals";
  let completed = 0;
  for (const [width, decision] of [[1440, "allow-once"], [390, "deny"]]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.goto(`${baseUrl}/member/settings?section=cos-replies`, { waitUntil: "networkidle" });
    const composer = page.locator(".cos-response-approval");
    const body = `Café at noon.  Bring the fixture tickets for ${width}.`;
    await composer.getByRole("textbox", { name: "Exact reply text", exact: true }).fill(body);
    record(`CoS composer has the exact own iMessage destination at ${width}`, await composer.getByRole("combobox", { name: "To", exact: true }).inputValue() === fixture.recipient && await composer.getByRole("combobox", { name: "Reply channel", exact: true }).inputValue() === "imessage");
    await auditAccessibility(page, `CoS reply composer ${width}`);
    const approvalResponse = page.waitForResponse((response) => response.url().endsWith("/api/member/v1/cos/responses/approvals") && response.request().method() === "POST");
    await composer.getByRole("button", { name: "Approve this exact reply", exact: true }).click();
    const approved = await approvalResponse;
    const approval = await approved.json();
    const continuation = composer.getByRole("button", { name: "Continue to sending approval", exact: true });
    await continuation.waitFor();
    record(`CoS domain confirmation is visibly unsent at ${width}`, approved.status() === 201 && approval.state === "awaiting_openclaw_approval" && approval.provider_effect_count === 0 && (await composer.textContent()).includes("It has not been sent"), approval);
    await screenshot(page, `cos-reply-confirmed-${width}.png`);
    const streamResponse = page.waitForResponse((response) => response.url().endsWith("/api/member/v1/chat/stream") && response.request().method() === "POST");
    await continuation.click();
    const stream = await streamResponse;
    const streamRequest = stream.request().postDataJSON();
    record(`Settings continues the exact domain approval through member chat at ${width}`, stream.ok() && streamRequest.message.includes(approval.approval_event_id) && streamRequest.message.includes("administrateme_cos_response") && (await page.locator("#chat-messages").textContent()).includes("Please send the exact reply I just approved.") && !(await page.locator("#chat-messages").textContent()).includes(approval.approval_event_id), streamRequest);
    const pending = page.locator(".cos-send-approval");
    await pending.getByRole("heading", { name: "Ready to send this reply?", exact: true }).waitFor();
    const rows = await call(endpoint);
    record(`Only the scoped exact reply reaches the approval card at ${width}`, rows.items.length === 1 && rows.items[0].can_allow === true && rows.items[0].recipient === fixture.recipient && rows.items[0].body === body && await pending.count() === 1 && (await pending.textContent()).includes(body) && (await pending.textContent()).includes(fixture.recipient), rows);
    const other = await page.request.get(`${baseUrl}${endpoint}`, { headers: { "Tailscale-User-Login": "visual-adult.ts.net" } });
    record(`Other member receives no private reply approval at ${width}`, other.ok() && (await other.json()).items.length === 0);
    const broad = await page.request.post(`${baseUrl}${endpoint}`, { data: { approval_id: rows.items[0].approval_id, revision: rows.items[0].revision, decision: "allow-always" } });
    record(`Broad sending approval is rejected at ${width}`, broad.status() === 422 && await pending.getByRole("button").count() === 2 && await pending.getByRole("button", { name: /always|all replies/i }).count() === 0);
    const before = await call("/__gate7/cos-reply-proof");
    record(`Viewing and invalid broad requests grant no sending decision at ${width}`, before.decisions.length === completed && before.provider_effect_count === 0 && before.pending === true, before);
    const button = pending.getByRole("button", { name: decision === "allow-once" ? "Allow this reply once" : "Don’t send", exact: true });
    await button.focus();
    record(`Exact sending decision is keyboard reachable at ${width}`, await button.evaluate((node) => node === document.activeElement) && await button.isEnabled());
    record(`CoS sending approval fits at ${width}`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await screenshot(page, `cos-reply-sending-approval-${width}.png`);
    const decisionResponse = page.waitForResponse((response) => response.url().endsWith(endpoint) && response.request().method() === "POST");
    await button.click();
    const decided = await decisionResponse;
    const decisionBody = decided.request().postDataJSON();
    const resolution = await decided.json();
    record(`Member records only the exact ${decision} decision at ${width}`, decided.status() === 200 && resolution.resolved === true && resolution.sent === false && JSON.stringify(Object.keys(decisionBody).sort()) === JSON.stringify(["approval_id", "decision", "revision"]) && decisionBody.approval_id === rows.items[0].approval_id && decisionBody.revision === rows.items[0].revision && decisionBody.decision === decision, { decisionBody, resolution });
    await page.locator("#chat-state").filter({ hasText: /^Ready$/ }).waitFor();
    await stream.finished();
    await page.waitForFunction(() => document.querySelectorAll(".cos-send-approval").length === 0 && !document.querySelector("#chat-send").disabled);
    completed += 1;
    const proof = await call("/__gate7/cos-reply-proof");
    record(`Terminal chat refresh removes stale approval controls at ${width}`, proof.decisions.length === completed && proof.completed.length === completed && proof.decisions.at(-1).decision === decision && proof.pending === false && proof.provider_effect_count === 0 && (await call(endpoint)).items.length === 0, proof);
    const expectedResult = decision === "allow-once" ? "This isolated browser fixture contacts no provider." : "Sending declined. No provider was contacted.";
    record(`Chat reports the bounded fixture result without claiming delivery at ${width}`, (await page.locator("#chat-messages").textContent()).includes(expectedResult));
    const replay = await page.request.post(`${baseUrl}${endpoint}`, { data: decisionBody });
    record(`The resolved sending decision cannot be replayed at ${width}`, replay.status() === 409 && (await call("/__gate7/cos-reply-proof")).decisions.length === completed);
    await screenshot(page, `cos-reply-decision-result-${width}.png`);
    await page.locator("#chat-close").click();
  }
}
