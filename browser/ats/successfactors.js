// SAP SuccessFactors candidate application page (career<N>.successfactors.eu / .com). Fills what the profile knows,
// uploads CV + cover letter, then returns every field still empty or unanswered so Claude can answer them from the
// answer bank. NEVER clicks Apply/Submit and never touches passwords (some tenants create the account on the same
// form: the password is pasted by Claude with `jobhunt secret copy`, see /apply).
// Start state: signed in (or on a create-account+apply page), on "Apply for <job>" with sections visible.
// Lessons: picklists are inputs with role=combobox — open, type, click `[role=option]:visible`; old profiles keep a
// stale CV ("Edit document" replaces it); resume parsing writes wrong values (Industry, State) so the report lists
// every value for review. Country and region come from DATA (`country`, `address.region`); empty = skipped.
async (page) => {
  /*__DATA__*/
  const log = [];
  const esc = (s) => String(s || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const sleep = (ms) => page.waitForTimeout(ms);
  const step = async (name, fn) => {
    try { const r = await fn(); log.push(r === 'skip' ? `skip ${name}` : `ok   ${name}`); }
    catch (e) { log.push(`FAIL ${name}: ${String(e.message || e).split('\n')[0]}`); }
  };
  const tb = (re) => page.getByRole('textbox', { name: re }).first();
  const fillIfEmpty = async (re, value) => {
    const t = tb(re);
    if (!value || !(await t.count()) || !(await t.isVisible().catch(() => false))) return 'skip';
    if ((await t.inputValue()) && (await t.inputValue()) !== '') return 'skip';
    await t.fill(value);
  };
  const pick = async (re, typed, optRe) => {
    const c = page.getByRole('combobox', { name: re }).first();
    if (!(await c.count())) return 'skip';
    const tag = await c.evaluate(e => e.tagName);
    if (tag === 'SELECT') {
      const opts = await c.locator('option').allInnerTexts();
      const m = opts.find(o => optRe.test(o.trim()));
      if (!m) throw new Error('no option');
      await c.selectOption({ label: m });
      return;
    }
    const cur = await c.inputValue().catch(() => '');
    if (cur && optRe.test(cur.trim())) return 'skip';
    await c.click(); await sleep(600);
    if (typed) { await c.fill(''); await c.pressSequentially(typed, { delay: 50 }); await sleep(1000); }
    const opts = page.locator('[role=option]:visible');
    const texts = await opts.allInnerTexts();
    const i = texts.findIndex(t => optRe.test(t.trim()));
    if (i < 0) { await page.keyboard.press('Escape'); throw new Error('options: ' + texts.slice(0, 8).join('|')); }
    await opts.nth(i).click(); await sleep(300);
  };
  // A missing upload button is an error, not a skip: a tenant that only shows "Delete document <old CV>" would
  // otherwise send the old CV unnoticed.
  const upload = async (buttonRe, file) => {
    const b = page.getByRole('button', { name: buttonRe }).first();
    if (!file) return 'skip';
    if (!(await b.count())) throw new Error(`no upload button matching ${buttonRe}`);
    await b.click(); await sleep(1500);
    await page.getByText('Upload from Device').first().click({ timeout: 2000 }).catch(() => {});
    await sleep(800);
    const fi = page.locator('input[type=file]');
    if (!(await fi.count())) throw new Error('no file input');
    await fi.last().setInputFiles(file);
    await sleep(5000);
  };

  await page.getByRole('button', { name: /Accept All|Strictly Necessary|Accept Use Of Cookies/i }).first().click({ timeout: 1500 }).catch(() => {});
  await page.getByRole('button', { name: 'Expand all sections' }).click({ timeout: 2000 }).catch(() => {});
  await sleep(1000);

  // ---- documents: replace any old CV, attach the per-job cover letter ----
  // Only the CV section counts: the cover letter or other documents can share the file name prefix.
  const cvName = DATA.resume_path.split(/[\\/]/).pop();
  const cvSection = async () => {
    const t = await page.locator('body').innerText();
    const i = t.search(/CV\s*\/\s*Resume|^\s*(Upload\s+)?Resume\s*$/im);
    if (i < 0) return '';
    const end = t.indexOf('Cover Letter', i);
    return t.slice(i, end > i ? end : i + 400);
  };
  await step('resume', async () => {
    const before = await cvSection();
    // Same file name is not enough: an old CV can carry the same name. Skip only when the section shows today's
    // upload date.
    const d = new Date(), p = (n) => String(n).padStart(2, '0');
    const today = [`${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()}`, `${p(d.getMonth() + 1)}/${p(d.getDate())}/${d.getFullYear()}`];
    if (before.includes(cvName) && today.some(t => before.includes(t))) return 'skip';
    if (await page.getByRole('button', { name: /Resume.*Edit document/i }).count()) {
      await upload(/Resume.*Edit document/i, DATA.resume_path);
    } else {
      // Some tenants: "Delete document <old>.pdf" + confirm OK, then the upload button appears.
      const old = (before.match(/Delete document (\S+\.(?:pdf|docx?|txt))/i) || [])[1];
      if (old) {
        await page.getByRole('button', { name: new RegExp(`Delete document ${old.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) }).first().click();
        await sleep(1200);
        await page.locator('[role=dialog]:visible button, .globalDialog:visible button').filter({ hasText: /^OK$/ }).first().click();
        await sleep(2500);
      }
      await upload(/Upload a Resume|Attach a Resume|(Upload|Attach).*(CV|Resume)/i, DATA.resume_path);
    }
    if (!(await cvSection()).includes(cvName)) throw new Error(`CV section still shows: ${(await cvSection()).slice(0, 120)}`);
  });
  await step('cover letter', async () => {
    if (!DATA.cover_letter_path) return 'skip';
    const name = DATA.cover_letter_path.split(/[\\/]/).pop();
    if ((await page.locator('body').innerText()).includes(name)) return 'skip';
    return upload(/Cover Letter.*Attach a Cover Letter/i, DATA.cover_letter_path);
  });

  // ---- personal information (only empty fields are filled; existing profile data is reported, not overwritten) ----
  const a = DATA.address || {};
  const addressLine = [a.street, [a.house_number, a.apartment].filter(Boolean).join('/')].filter(Boolean).join(' ');
  await step('email', () => fillIfEmpty(/^\*?\s*Email Address|^\*?\s*Email$/i, DATA.email));
  await step('retype email', () => fillIfEmpty(/Retype Email/i, DATA.email));
  await step('first name', () => fillIfEmpty(/^\*?\s*First Name/i, DATA.first_name));
  await step('last name', () => fillIfEmpty(/^\*?\s*Last Name/i, DATA.last_name));
  await step('phone', () => fillIfEmpty(/Mobile Phone|Primary Phone|^\*?\s*Phone Number|^\*?\s*Phone$/i, DATA.phone_full));
  await step('address', () => fillIfEmpty(/Address Line 1|^\*?\s*Address$/i, addressLine));
  await step('city', () => fillIfEmpty(/^\*?\s*City/i, a.city));
  await step('postal code', () => fillIfEmpty(/Zip|Postal Code/i, a.postal_code));
  await step('linkedin', () => fillIfEmpty(/LinkedIn/i, (DATA.linkedin || '').replace('https://linkedin.com', 'https://www.linkedin.com')));
  const country = DATA.country || a.country || '';
  await step('country', () => country
    ? pick(/^\*?\s*Country:?$|Country of Residence|Country\/Region of Residence/i, country.slice(0, 3), new RegExp(`^${esc(country)}`, 'i'))
    : 'skip');
  await sleep(1000);
  // Region picklists use English or local names: type the first letters, match the whole name anywhere.
  await step('state', () => a.region
    ? pick(/State|Province|Region/i, a.region.slice(0, 3), new RegExp(esc(a.region), 'i'))
    : 'skip');

  // ---- report: every visible field with its value, so Claude can review parsed/stale values and answer the rest ----
  const fields = await page.evaluate(() => {
    const vis = (e) => !!(e.offsetParent || e.getClientRects().length);
    const lab = (e) => {
      const l = e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`);
      return (l?.innerText || e.getAttribute('aria-label') || e.title || e.name || '').replace(/\s+/g, ' ').trim().slice(0, 120);
    };
    const out = [];
    const radios = {};
    for (const e of document.querySelectorAll('input, select, textarea')) {
      if (['hidden', 'file', 'password', 'submit', 'button'].includes(e.type) || !vis(e)) continue;
      if (e.type === 'radio') {
        const g = e.closest('[role=radiogroup]')?.getAttribute('aria-label') || e.name;
        (radios[g] = radios[g] || { label: g, type: 'radio', options: [], value: '' }).options.push(lab(e));
        if (e.checked) radios[g].value = lab(e);
        continue;
      }
      const value = e.type === 'checkbox' ? (e.checked ? 'CHECKED' : '') : e.tagName === 'SELECT' ? (e.options[e.selectedIndex]?.text || '') : e.value;
      out.push({ label: lab(e), type: e.getAttribute('role') === 'combobox' ? 'picklist' : (e.type || e.tagName.toLowerCase()),
                 required: /^\*/.test(lab(e)) || e.getAttribute('aria-required') === 'true', value: String(value).slice(0, 80) });
    }
    // SF "globalRadio" widgets are spans with role=radio, not inputs: without this a required Yes/No ("right to
    // work") is missing from the report and only surfaces as an error after Apply. Click the `_anchor` span.
    for (const g of document.querySelectorAll('[role=radiogroup]')) {
      if (!vis(g) || g.querySelector('input[type=radio]')) continue;
      const label = (document.getElementById(g.getAttribute('aria-labelledby') || '')?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 120);
      const opts = [...g.querySelectorAll('.globalRadio')].map(o => o.innerText.trim());
      const sel = [...g.querySelectorAll('[role=radio]')].findIndex(r => r.getAttribute('aria-checked') === 'true');
      radios[label] = { label, type: 'radio', options: opts, value: sel >= 0 ? opts[sel] : '', required: /^\*/.test(label) };
    }
    return out.concat(Object.values(radios));
  });
  const empty = fields.filter(f => !f.value && f.type !== 'checkbox' || (f.type === 'checkbox' && f.required && !f.value));
  return { log, empty_or_unanswered: empty, all_fields: fields };
}
