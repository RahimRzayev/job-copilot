// LinkedIn job-alert emails -> Job-shaped list for `jobhunt alerts import`.
// Run via Playwright browser_evaluate on the Gmail search page printed by `jobhunt alerts query`
// (from:jobalerts-noreply@linkedin.com newer_than:Nd). Reads each thread through Gmail's print view (same-origin
// fetch, full message even when Gmail would clip it). Gmail enforces Trusted Types, so HTML is parsed with regex.
// Works for whichever Google account the page is signed in as: the account path (/mail/u/N/) is read from the URL.
// Read-only itself; /linkedin-alerts marks the threads read afterwards with browser/gmail_mark_read.js.
async () => {
  const ik = (window.GLOBALS || [])[9];
  if (!ik) return { error: 'not on a Gmail page (GLOBALS missing)' };
  const base = (location.pathname.match(/^\/mail\/u\/[^/]+\//) || ['/mail/u/0/'])[0];
  const clean = (s) => s.replace(/<[^>]+>/g, '\n')
    .replace(/&amp;/g, '&').replace(/&#39;|&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&middot;/g, '·')
    .split('\n').map(x => x.replace(/[\s͏‌]+/g, ' ').trim()).filter(x => x && !x.startsWith('<'));
  const SALARY = /(PLN|EUR|USD|GBP|CHF|zł|€|£|\$).*|\/\s*(hour|month|year)/i;

  // Only visible rows of the results list: Gmail keeps hidden copies of other views (inbox) in the DOM.
  const rows = [...document.querySelectorAll('div[role="main"] tr.zA')].filter(r => r.offsetParent).map(r => ({
    thread: r.querySelector('[data-legacy-thread-id]')?.getAttribute('data-legacy-thread-id'),
    subject: r.querySelector('span.bog')?.innerText.trim() || '',
    date: r.querySelector('td.xW span[title], .xW span[title]')?.getAttribute('title') || '',
  })).filter(r => r.thread);

  const jobs = new Map();
  const searches = new Map();  // "See all jobs" links: the full alert search, expanded by `jobhunt alerts import`
  const stats = { emails: rows.length, cards: 0, errors: [] };
  for (const row of rows) {
    // '“python”: Company - Title and more' (daily digest) or '<Name>: your job alert for Intern in <City>… has been created'
    let keyword = (row.subject.match(/^[“"]([^”"]+)[”"]/) || row.subject.match(/job alert for (.+?) in /i) || [])[1] || '';
    const parsed = Date.parse(row.date.replace(/ /g, ' '));
    const sent = isNaN(parsed) ? new Date().toISOString() : new Date(parsed).toISOString();
    let html;
    try {
      html = await (await fetch(`${base}?ik=${ik}&view=pt&search=all&th=${row.thread}`, { credentials: 'include' })).text();
    } catch (e) { stats.errors.push(`${row.thread}: ${e}`); continue; }
    // "See all jobs": /comm/jobs/search?… in older emails, /comm/jobs/search-results/?… in newer ones.
    const links = [...html.matchAll(/href="(https:\/\/www\.linkedin\.com\/comm\/jobs\/search(?:-results)?\/?\?[^"]+)"/g)]
      .map(sm => sm[1].replace(/&amp;/g, '&'));
    // A subject without a quoted keyword ("<Title> at <Company>"): take the alert's keyword from its link instead.
    if (!keyword && links.length) {
      try { keyword = (new URL(links[0]).searchParams.get('keywords') || '').trim(); } catch (e) { /* keep '' */ }
    }
    for (const url of links) {
      const key = url.replace(/&(lipi|midToken|midSig|trk|trkEmail|eid|otpToken|refId|originToLandingJobPostings)=[^&]*/g, '');
      if (!searches.has(key)) searches.set(key, { url, alert: keyword });
    }
    // Each card: <a href=".../jobs/view/<id>/...">Title</a> followed by "Company · Location (Remote)" and an
    // optional salary/"Actively recruiting" line. Logo links have no text and are skipped.
    const re = /<a[^>]+href="[^"]*jobs\/view\/(\d+)[^"]*"[^>]*>([\s\S]*?)<\/a>/g;
    let m;
    while ((m = re.exec(html))) {
      const title = clean(m[2]).join(' ');
      if (!title || jobs.has(m[1])) continue;
      const after = clean(html.slice(re.lastIndex, re.lastIndex + 2500)).slice(0, 3);
      const [company = '', place = ''] = (after[0] || '').split(' · ');
      stats.cards++;
      jobs.set(m[1], {
        source: 'linkedin', source_id: m[1], title, company, place,
        url: `https://www.linkedin.com/jobs/view/${m[1]}`,
        salary: after.slice(1).find(x => SALARY.test(x)) || '',
        posted_at: sent, alert: keyword,
      });
      await new Promise(r => setTimeout(r, 0));
    }
    await new Promise(r => setTimeout(r, 400));  // be gentle with Gmail
  }
  // thread ids let /linkedin-alerts mark these alert emails as read after a successful import.
  return { stats: { ...stats, jobs: jobs.size, searches: searches.size }, jobs: [...jobs.values()],
           searches: [...searches.values()], threads: rows.map(r => r.thread) };
}
