// Disposable production-composition UI proof; no member-device or live state.
export async function auditSettingsChannelStatus({ page, baseUrl, record, screenshot, auditAccessibility }) {
  page.setDefaultTimeout(10_000);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    for (const active of [false, true]) {
      const changed = await page.request.post(`${baseUrl}/__gate7/settings-observation?active=${active}`, { timeout: 10_000 });
      const receipt = await changed.json();
      record(`Disposable observation ${active} has no provider effect at ${width}`, changed.ok() && receipt.observation_active === active && receipt.provider_effect_count === 0, receipt);
      await page.goto(`${baseUrl}/member/settings?section=overview`, { waitUntil: "networkidle" });
      await page.locator(".settings-service-channels").waitFor();
      const bootstrap = await page.request.get(`${baseUrl}/api/member/v1/bootstrap`).then((response) => response.json());
      const banner = await page.locator("#observation-banner").evaluate((node) => ({ hidden: node.hidden, display: getComputedStyle(node).display, rects: node.getClientRects().length }));
      const pill = await page.locator("#observation-pill").textContent();
      const stats = await page.locator(".settings-hero-stats").textContent();
      record(`Observation ${active} agrees across server, banner, and pill at ${width}`, bootstrap.observation_mode === active && banner.hidden === !active && (active ? banner.display === "flex" && banner.rects > 0 && pill === "Observation mode" : banner.display === "none" && banner.rects === 0 && pill === "Member-safe"), { banner, pill, active });
      record(`Settings distinguishes personal zero counts from CoS enrollment at ${width}/${active}`, stats.includes("Your connected accounts0") && stats.includes("Your syncs running0") && stats.includes(active ? "Writes executeNo" : "Writes executeGated") && await page.locator(".settings-service-channel").count() === 2 && await page.locator(".settings-service-channel").getByText("Enrolled", { exact: true }).count() === 2, stats);
      const channels = page.locator(".settings-service-channels");
      record(`CoS status does not infer live connection or sending authority at ${width}/${active}`, await channels.getByText("Not available here", { exact: true }).count() === 2 && await channels.getByRole("button").count() === 0 && (await channels.textContent()).includes(active ? "Paused for observation" : "Off"), await channels.textContent());
      record(`Inactive personal connections do not create repair notices at ${width}/${active}`, (await page.locator(".settings-attention").textContent()).includes("Nothing needs intervention"));
      await auditAccessibility(page, `Settings observation ${active} ${width}`);
      record(`Settings observation ${active} fits at ${width}`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await screenshot(page, `settings-observation-${active ? "on" : "off"}-${width}.png`);
    }
    await page.getByRole("button", { name: "Connections", exact: true }).click();
    const content = await page.locator("#surface").textContent();
    record(`Personal connections stay private and inactive at ${width}`, content.includes("My disabled fixture inbox") && !content.includes("Other member fixture inbox") && await page.locator(".connection-card").count() === 1 && await page.locator(".connection-card").getByRole("button").count() === 0 && content.includes("Sync controls are unavailable"), content);
    await screenshot(page, `settings-cos-connections-${width}.png`);
  }
}
