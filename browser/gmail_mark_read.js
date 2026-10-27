// Mark the given Gmail threads as read — nothing else. Run with Playwright browser_run_code_unsafe on a Gmail
// search-results page (the `is:unread` search used by /mail-check or the LinkedIn-alert search).
// __THREADS__ is replaced by a JSON list of legacy thread ids (gmail_rows.js `thread_id`, linkedin_alerts.js rows).
// Ticks only the rows whose id is in the list, then clicks Gmail's own "Mark as read" toolbar button.
// Never deletes, archives, moves, labels or sends. Returns how many it marked and which ids were not on this page
// (marked rows drop out of an `is:unread` search, so re-run on page 1 until `remaining_on_page` is 0).
async (page) => {
  const ids = new Set(__THREADS__);
  // Gmail keeps hidden copies of other views (inbox) in the DOM: only rows visible in the current list count.
  await page.waitForSelector('div[role="main"] tr.zA', { timeout: 15000 }).catch(() => {});
  const rows = page.locator('div[role="main"] tr.zA:visible');
  const n = await rows.count();
  let ticked = 0;
  const seen = [];
  const alreadyRead = [];
  for (let i = 0; i < n; i++) {
    const row = rows.nth(i);
    const tid = await row.locator('[data-legacy-thread-id]').first().getAttribute('data-legacy-thread-id').catch(() => null);
    if (!tid) continue;
    seen.push(tid);
    if (!ids.has(tid)) continue;
    // Read rows (no bold class zE) are skipped: if every target is read Gmail only offers "Mark as unread",
    // and waiting for "Mark as read" times out with the rows left ticked.
    if (!(await row.evaluate(r => r.classList.contains('zE')))) { alreadyRead.push(tid); continue; }
    const box = row.locator('div[role="checkbox"]').first();
    if ((await box.getAttribute('aria-checked')) !== 'true') await box.click();
    ticked++;
  }
  if (!ticked) return { marked: 0, already_read: alreadyRead.length, remaining_on_page: 0, rows_on_page: seen.length };
  const btn = page.locator('div[role="button"][aria-label="Mark as read"]:visible').first();
  await btn.waitFor({ timeout: 5000 });
  await btn.click();
  await page.waitForTimeout(2500);
  // Verify: rows still unread (bold, class zE) among the targeted ids.
  const stillUnread = await page.evaluate((list) => [...document.querySelectorAll('div[role="main"] tr.zA.zE')]
    .filter(r => r.offsetParent)
    .map(r => r.querySelector('[data-legacy-thread-id]')?.getAttribute('data-legacy-thread-id'))
    .filter(t => t && list.includes(t)), [...ids]);
  return { marked: ticked - stillUnread.length, already_read: alreadyRead.length, still_unread: stillUnread,
           rows_on_page: seen.length,
           remaining_on_page: stillUnread.length };
}
