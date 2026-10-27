// Run on a www.pracuj.pl page (NOT it.pracuj.pl — cross-subdomain fetches are blocked) via Playwright
// browser_evaluate. Cloudflare rate-limits fast bursts (429 "Access denied"): keep the 2.5 s pace, and the
// script stops at the first block instead of hammering on.
// Replace __URLS__ with a JSON array of Pracuj offer URLs. Returns [{source, source_id, employer_url, expires_at}]
// for `jobhunt update-details`. employer_url is the employer's own ATS form ("formUrl"); empty when the
// application happens on Pracuj itself.
async () => {
  const urls = __URLS__;
  const out = [];
  for (const url of urls) {
    try {
      const res = await fetch(url, { credentials: 'include' });
      const html = await res.text();
      if (res.status !== 200 || !html.includes('__NEXT_DATA__')) {
        out.push({ source: 'pracuj', url, error: `blocked or missing (HTTP ${res.status})` });
        break;  // Cloudflare limit hit: stop, retry later
      }
      const m = html.match(/<script id="__NEXT_DATA__" type="application\/json">([\s\S]*?)<\/script>/);
      const raw = m ? m[1] : '';
      const form = raw.match(/"formUrl":"(https?:[^"]+)"/);
      const exp = raw.match(/"expirationDate":"([^"]+)"/);
      const id = (url.match(/oferta,(\d+)/) || [])[1];
      out.push({ source: 'pracuj', source_id: id, employer_url: form ? JSON.parse('"' + form[1] + '"') : '',
                 expires_at: exp ? exp[1] : '', expired: /wygasł|nieaktualn/i.test(html.slice(0, 200000)) && !raw });
    } catch (e) {
      out.push({ source: 'pracuj', url, error: String(e) });
    }
    await new Promise(r => setTimeout(r, 2500));
  }
  return out;
}
