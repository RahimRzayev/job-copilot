// Like gmail_rows.js, plus `body`: the text of the newest message in each thread, read from Gmail's print view
// (same-origin fetch; opening the print view does NOT mark the thread read). Run via Playwright browser_evaluate
// on a Gmail search page (the `is:unread` search for /mail-check), with `filename:`.
// Why: the list snippet stops after ~150 chars, so "Thank you for applying ... unfortunately we decided not to move
// forward" looks like a confirmation. The body decides instead.
// LinkedIn job-alert digests are skipped (no body needed, and there can be many). Gmail enforces Trusted Types,
// so HTML is reduced to text with regexes, not DOMParser.
// Works for whichever Google account the page is signed in as: the account path (/mail/u/N/) is read from the URL.
async () => {
  const ik = (window.GLOBALS || [])[9];
  const base = (location.pathname.match(/^\/mail\/u\/[^/]+\//) || ['/mail/u/0/'])[0];
  const text = (html) => html
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<br\s*\/?>|<\/(p|div|tr|li|h\d)>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&#39;|&#x27;/g, "'").replace(/&quot;/g, '"')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/[ \t ​]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim();
  const rows = [...document.querySelectorAll('div[role="main"] tr.zA')].filter(r => r.offsetParent).map(r => {
    const sender = r.querySelector('.yW span[email]');
    const date = r.querySelector('td.xW span[title], .xW span[title]');
    const subj = r.querySelector('span.bog');
    const snip = r.querySelector('span.y2');
    const thread = r.querySelector('[data-legacy-thread-id]');
    return {
      sender_name: sender ? sender.getAttribute('name') || sender.innerText : '',
      sender_email: sender ? sender.getAttribute('email') : '',
      subject: subj ? subj.innerText.trim() : '',
      snippet: snip ? snip.innerText.replace(/^\s*-\s*/, '').trim() : '',
      date: date ? date.getAttribute('title') : '',
      thread_id: thread ? thread.getAttribute('data-legacy-thread-id') : '',
    };
  });
  for (const row of rows) {
    if (!ik || !row.thread_id || /jobalerts-noreply@linkedin\.com/i.test(row.sender_email)) continue;
    try {
      const html = await (await fetch(`${base}?ik=${ik}&view=pt&search=all&th=${row.thread_id}`,
                                      { credentials: 'include' })).text();
      const parts = html.split(/<table[^>]*class="?message"?/i);
      // Newest message only (threads keep the older "application received" mail too); skip its header table.
      const last = parts[parts.length - 1];
      const start = last.search(/<table width=100% cellpadding=12/i);
      row.body = text(start >= 0 ? last.slice(start) : last).slice(0, 3000);
    } catch (e) {
      row.body_error = String(e);
    }
    await new Promise(r => setTimeout(r, 300));  // be gentle with Gmail
  }
  return rows;
}
