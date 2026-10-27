// FULL mode for it.pracuj.pl: every level, the configured city + remote, newest first (sc=0), page by page until
// offers are older than __HOURS__ hours. Run on any pracuj.pl tab via Playwright browser_evaluate (same-origin
// fetch passes Cloudflare). Replace __HOURS__ with a number and __CITY__ with the city's Pracuj slug as a JSON
// string (pipeline.pracuj_city(), e.g. "krakow"), __KEYWORDS__ with a JSON list. Output: Job-shaped list for `jobhunt ingest`.
async () => {
  const hours = __HOURS__;
  const city = __CITY__;
  const cutoff = Date.now() - hours * 3600 * 1000;
  // it.pracuj.pl = the IT section. On a www.pracuj.pl tab the same script runs keyword searches over every
  // category (__KEYWORDS__: the user's roles and search keywords, pipeline.pracuj_keywords()). A page can only fetch
  // its own host (cross-origin fetch fails), so run the script once on each tab.
  const host = location.host;
  const KEYWORDS = __KEYWORDS__;
  const searches = host === 'www.pracuj.pl'
    ? KEYWORDS.flatMap(kw => [`https://www.pracuj.pl/praca/${encodeURIComponent(kw)};kw/${city};wp?sc=0`,
                              `https://www.pracuj.pl/praca/${encodeURIComponent(kw)};kw/praca%20zdalna;wm,home-office?sc=0`])
    : [`https://it.pracuj.pl/praca/${city};wp?sc=0`, 'https://it.pracuj.pl/praca/praca%20zdalna;wm,home-office?sc=0'];
  const level = (levels) => {
    const s = (levels || []).join(' ').toLowerCase();
    if (s.includes('praktykant') || s.includes('stażyst') || s.includes('trainee')) return 'intern';
    if (s.includes('junior') || s.includes('asystent')) return 'junior';
    if (s.includes('mid') || s.includes('regular')) return 'mid';
    if (s.includes('senior') || s.includes('ekspert')) return 'senior';
    if (s.includes('kierownik') || s.includes('menedżer') || s.includes('dyrektor')) return 'manager';
    return '';
  };
  const out = new Map();
  const stats = [];
  for (const base of searches) {
    let pages = 0;
    // Stops by itself once a page has nothing inside the window; 25 pages was too few for 72h after a weekend
    // (a big city hit the cap with fresh offers still coming).
    for (let pn = 1; pn <= 80; pn++) {
      const url = pn === 1 ? base : `${base}&pn=${pn}`;
      const res = await fetch(url, { credentials: 'include' });
      const html = await res.text();
      if (res.status !== 200) { stats.push({ search: base, blocked: res.status, page: pn }); break; }
      const m = html.match(/<script id="__NEXT_DATA__" type="application\/json">([\s\S]*?)<\/script>/);
      if (!m) break;
      const qs = JSON.parse(m[1]).props.pageProps.dehydratedState.queries;
      const groups = qs.flatMap(q => (q.state.data && q.state.data.groupedOffers) || []);
      if (!groups.length) break;
      pages = pn;
      let fresh = 0;
      for (const g of groups) {
        if (Date.parse(g.lastPublicated || 0) < cutoff) continue;
        fresh++;
        for (const o of g.offers) {
          out.set(String(o.partitionId), {
            source: 'pracuj', source_id: String(o.partitionId), title: g.jobTitle, company: g.companyName,
            url: o.offerAbsoluteUri, apply_url: o.offerAbsoluteUri, location: (o.displayWorkplace || '').split(',')[0],
            remote: base.includes('zdalna;wm') || (g.workModes || []).some(w => /zdaln|remote|home/i.test(w)),
            seniority: level(g.positionLevels),
            description: [g.jobDescription, (g.aiSummary || '').replace(/<[^>]+>/g, ' ')].join('\n'),
            skills: g.technologies || [], salary: g.salaryDisplayText || '', posted_at: g.lastPublicated || '',
            expires_at: g.expirationDate || '',
            // Pracuj adds a Polish AI summary to every offer; judge the language from the original text.
            languages: /^(Twój zakres|Twoje zadania|Zakres obowiązków)/.test(g.jobDescription || '') ? ['Polish:listing'] : [],
          });
        }
      }
      if (fresh === 0) break;  // newest-first: a page with nothing fresh means we are past the window
      await new Promise(r => setTimeout(r, 1500));
    }
    stats.push({ search: decodeURIComponent(base.split('/praca/')[1]), pages, hit_cap: pages >= 80 });
  }
  // window_hours lets `jobhunt ingest` use the same window without a --hours flag.
  return { window_hours: hours, host, stats, jobs: [...out.values()] };
}
