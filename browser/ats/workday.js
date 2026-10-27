// Workday candidate application: fills "My Information" and "My Experience", then stops on the next step
// (Application Questions) and returns its questions. NEVER submits.
// Start state: signed in, application started ("Apply Manually"), on the "My Information" step.
// Labels differ per tenant ("Given Name(s)" vs "First Name"), so each field tries variants.
// Every personal value (names, address, region, school, degree, languages, skills, file names) comes from DATA.
async (page) => {
  /*__DATA__*/
  const log = [];
  const esc = (s) => String(s || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const fileName = (p) => String(p || '').split(/[\\/]/).pop();
  const step = async (name, fn) => {
    try { const r = await fn(); log.push(r === 'skip' ? `skip ${name}` : `ok   ${name}`); }
    catch (e) { log.push(`FAIL ${name}: ${String(e.message || e).split('\n')[0]}`); }
  };
  const sleep = (ms) => page.waitForTimeout(ms);
  const tbx = (re) => page.getByRole('textbox', { name: re }).first();
  const fillIf = async (re, value) => {
    const t = tbx(re);
    if (!(await t.count()) || !value) return 'skip';
    if ((await t.inputValue()) === value) return 'skip';
    await t.fill(value);
  };
  // Workday listbox buttons (Degree, Language, Overall...): open, then click the option.
  const choose = async (button, optionRe) => {
    await button.click();
    const opt = page.getByRole('option', { name: optionRe }).first();
    await opt.waitFor({ timeout: 6000 });
    await opt.click();
  };
  // Workday search prompts (School, Field of Study, Skills, How did you hear): type + Enter, click the match.
  // Options are read only from the open popup: selected pills elsewhere on the page (school, skills) are also
  // "promptOption" nodes and get clicked by mistake otherwise. Typing key by key filters lists that ignore
  // fill()+Enter (some Field of Study lists).
  const closePopup = async () => {
    await page.keyboard.press('Escape').catch(() => {});
    // Escape does not always close the prompt popup; a leftover popup blocks later radio clicks.
    if (await page.locator('[data-automation-id="promptLeafNode"]').count()) await page.locator('h2').first().click().catch(() => {});
  };
  const prompt = async (input, text, labelRe) => {
    // An open list from a previous field can intercept the click: fall back to focus.
    await input.click({ timeout: 3000 }).catch(async () => { await page.keyboard.press('Escape').catch(() => {}); await input.focus(); });
    await input.fill('');
    await input.pressSequentially(text, { delay: 50 });
    await input.press('Enter');
    const popup = page.locator('[data-automation-id="activeListContainer"], [data-automation-id="promptLeafNode"]').first();
    await popup.waitFor({ timeout: 8000 }).catch(() => {});
    const opts = page.locator('[data-automation-id="promptLeafNode"] [data-automation-id="promptOption"], ' +
                              '[data-automation-id="activeListContainer"] [data-automation-id="promptOption"]');
    const opt = opts.filter({ hasText: labelRe }).first();
    try { await opt.waitFor({ timeout: 8000 }); } catch (e) { await closePopup(); throw e; }
    await opt.click();
    await closePopup();
  };
  const currentStep = async () => (await page.locator('[data-automation-id="progressBarActiveStep"]').innerText().catch(() => '')) ||
    (await page.getByText(/current step \d of \d/).first().innerText().catch(() => ''));
  // Every visible answer on the current step, recorded before leaving it. A source dropdown can silently get a wrong
  // value that only shows on the Review page: the report must show what the script chose.
  const answers = {};
  const recordAnswers = async () => {
    const name = (await currentStep()).replace(/^current step \d of \d\s*/i, '').trim() || 'step';
    answers[name] = await page.evaluate(() => {
      const vis = (e) => !!(e.offsetParent || e.getClientRects().length);
      const lab = (e) => (e.getAttribute('aria-label') || document.querySelector(`label[for="${CSS.escape(e.id || '')}"]`)?.innerText || e.name || '')
        .replace(/\s+/g, ' ').replace(/ Required$/, '').trim().slice(0, 70);
      const out = {};
      for (const b of document.querySelectorAll('main button[aria-haspopup="listbox"]')) if (vis(b)) out[lab(b)] = b.innerText.trim();
      for (const e of document.querySelectorAll('main input[type=text], main textarea, main input[type=tel], main input[type=email]'))
        if (vis(e) && e.value && !/^[0-9a-f]{20,}$/.test(e.value)) out[lab(e)] = e.value.slice(0, 60);
      for (const r of document.querySelectorAll('main input[type=radio]:checked')) {
        const g = r.closest('[role=radiogroup], fieldset');
        out[(g?.getAttribute('aria-label') || g?.querySelector('legend')?.innerText || r.name || 'radio').slice(0, 70)] =
          r.getAttribute('aria-label') || r.parentElement?.innerText?.trim() || r.value;
      }
      for (const p of document.querySelectorAll('main [data-automation-id="selectedItem"]')) if (vis(p)) out['selected: ' + p.innerText.trim().slice(0, 40)] = '✓';
      return out;
    }).catch(() => ({}));
  };
  const saveAndContinue = async () => {
    await recordAnswers();
    // Guest-apply tenants label the button "Next".
    await page.getByRole('button', { name: /^(Save and Continue|Next)$/ }).last().click();
    await sleep(2500);
    // Some tenants have no errorMessage automation id; the "Errors Found" heading is common to all tenants.
    const errs = await page.locator('[data-automation-id="errorMessage"]').allInnerTexts().catch(() => []);
    if (!errs.length && await page.getByRole('heading', { name: 'Errors Found' }).count())
      errs.push(...(await page.getByText(/^The field .* is required|^Invalid/).allInnerTexts()));
    if (errs.length) throw new Error('validation: ' + errs.join(' | '));
  };

  // Cookie banner
  await page.getByRole('button', { name: /^Decline$|Reject/ }).click({ timeout: 1500 }).catch(() => {});

  // ---------- My Information ----------
  if (/My Information/i.test(await currentStep())) {
    await step('how did you hear', async () => {
      // Some tenants use a plain listbox button, not a search prompt. Prefer the real source, then the company's own
      // career site, then a job board.
      const btn = page.getByRole('button', { name: /How Did You Hear About Us\? Select One/i });
      if (await btn.count()) {
        await btn.first().click(); await sleep(1000);
        // The real source first ("LinkedIn Job Site"); a loose fallback once picked an unrelated board.
        const opts = page.getByRole('option');
        const real = opts.filter({ hasText: new RegExp(`^${esc(DATA.source_label)}`, 'i') });
        const own = opts.filter({ hasText: /Career Site|Company Website|Website/i });
        const board = opts.filter({ hasText: /^Job Board/i });
        // No exact match: the nearest true category (a career-site job -> the website, anything else -> a job board).
        const order = /website/i.test(DATA.source_label) ? [real, own, board] : [real, board];
        let pickOpt = null;
        for (const o of order) if (!pickOpt && (await o.count())) pickOpt = o.first();
        if (!pickOpt) { await page.keyboard.press('Escape'); throw new Error('no matching source option'); }
        await pickOpt.click();
        return;
      }
      const box = page.getByRole('textbox', { name: /How Did You Hear/i });
      if (!(await box.count())) return 'skip';
      if (/1 item selected/.test(await box.locator('..').innerText().catch(() => ''))) return 'skip';
      // Flat list (most tenants) or a category menu (Website > <COMPANY>.COM, Job Board > ...).
      // The list can be virtualized (only ~5 rows rendered), so scroll to find a category. Categories open a
      // sub-list via their chevron; only a leaf is a valid source (a "Social Media" leaf can be rejected on Submit
      // with "should contain ... Recruiting Source"; Job Board > Job Board is accepted).
      const srcRe = new RegExp(esc(DATA.source_label), 'i');
      await prompt(box, DATA.source_label, srcRe).catch(async () => {
        // A single search match is selected on Enter without showing a list ("Linkedin Jobs"); the category
        // fallback below would then replace the real source with "Website > <COMPANY>.COM".
        if (await page.locator('main [data-automation-id="selectedItem"]').filter({ hasText: srcRe }).count()) return;
        await box.fill(''); await box.focus(); await page.keyboard.press('Enter'); await sleep(1200);
        const menu = page.locator('[data-automation-id="activeListContainer"]');
        const seen = new Set();
        for (let i = 0; i < 10; i++) {
          (await menu.locator('[data-automation-id="promptOption"]').allInnerTexts()).forEach(t => seen.add(t));
          await menu.evaluate(e => { e.scrollTop += 150; }); await sleep(250);
        }
        const cats = /website/i.test(DATA.source_label) ? ['Website', 'Job Board'] : ['Job Board', 'Website'];
        const cat = cats.find(c => seen.has(c)) || 'Job Board';
        await menu.evaluate(e => { e.scrollTop = 0; }); await sleep(250);
        for (let i = 0; i < 10 && !(await menu.getByText(cat, { exact: true }).count()); i++) {
          await menu.evaluate(e => { e.scrollTop += 150; }); await sleep(250);
        }
        const catOpt = menu.getByRole('option', { name: new RegExp(`^${cat}`) }).first();
        const chevron = catOpt.locator('svg.wd-icon-chevron-right-small');
        await ((await chevron.count()) ? chevron.first().click({ force: true }) : catOpt.click()); await sleep(1500);
        const leaves = menu.getByRole('option');
        const own = leaves.filter({ hasText: new RegExp(esc(DATA.job.company), 'i') });
        const same = leaves.filter({ hasText: new RegExp(`^${cat}$`) });
        await ((await own.count()) ? own.first() : (await same.count()) ? same.first() : leaves.first()).click();
        await page.keyboard.press('Escape').catch(() => {});
        await page.locator('h2').first().click().catch(() => {});
      });
    });
    await step('previous worker: No', async () => {
      const g = page.getByRole('group', { name: /(worked for|employed by|previous(ly)? (worked|employed)|previous worker|former employee|prior employment)/i }).first();
      if (!(await g.count())) return 'skip';
      // "No" only when the profile shows no job at this company; otherwise the user answers.
      const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
      const company = norm(DATA.job.company);
      const past = (DATA.past_employers || []).map(norm).find(c => c && company && (c.includes(company) || company.includes(c)));
      if (past) throw new Error(`profile lists a job at "${past}": ask the user`);
      // The input is visually hidden and a forced click on it may not register: click its label.
      const no = g.getByRole('radio', { name: 'No' });
      const id = await no.getAttribute('id');
      await (id ? page.locator(`label[for="${id}"]`).click() : no.click({ force: true }));
      if (!(await no.isChecked())) throw new Error('radio not set');
    });
    await step('first name', () => fillIf(/^(Given Name\(s\)|First Name)/, DATA.first_name));
    // Some tenants label the surname "Primary".
    await step('last name', () => fillIf(/^(Family Name|Last Name|Primary)/, DATA.last_name));
    const a = DATA.address || {};
    const addressLine = [a.street, [a.house_number, a.apartment].filter(Boolean).join('/')].filter(Boolean).join(' ');
    await step('address', async () => {
      if (await tbx(/^Street Name$/).count()) {
        await fillIf(/^Street Name$/, a.street);
        await fillIf(/^House Number$/, a.house_number);
        await fillIf(/^Apartment$/, a.apartment);
      } else {
        await fillIf(/^Address Line 1/, addressLine);
      }
      await fillIf(/^City$/, a.city);
      await fillIf(/^Postal Code/, a.postal_code);
      // Some country formats require Municipality and District: the city is used for both (right for large
      // cities; check them on the Review page).
      await fillIf(/^Municipality\*?$/, a.city);
      await fillIf(/^District\*?$/, a.city);
    });
    // Required "State / Province / Region" listbox: a mouse click may not open it, Enter on the focused button does.
    await step('province', async () => {
      const b = page.locator('#address--countryRegion');
      if (!(await b.count()) || !/Select One/.test(await b.innerText())) return 'skip';
      if (!a.region) throw new Error('required region but DATA.address.region is empty');
      await b.focus(); await page.keyboard.press('Enter'); await sleep(1000);
      const opts = page.locator('[role="option"]:visible');
      const exact = opts.filter({ hasText: new RegExp(`^${esc(a.region)}$`, 'i') });
      const loose = opts.filter({ hasText: new RegExp(esc(a.region), 'i') });
      const o = (await exact.count()) ? exact.first() : (await loose.count()) ? loose.first() : null;
      if (!o) {
        const seen = (await opts.allInnerTexts()).slice(0, 20).join('|');
        await page.keyboard.press('Escape').catch(() => {});
        throw new Error(`no option for region "${a.region}": ${seen}`);
      }
      await o.click();
    });
    await step('phone', () => fillIf(/^Phone Number/, DATA.phone_local));
    await step('phone device type', async () => {
      const b = page.locator('button[name="phoneType"]');
      if (!(await b.count()) || !/Select One/.test(await b.innerText())) return 'skip';
      await choose(b, /^(Mobile|Home Cellular|Cell)/);
    });
    await step('save -> My Experience', saveAndContinue);
  } else log.push('skip My Information (not on that step)');

  // ---------- My Experience ----------
  if (/My Experience/i.test(await currentStep())) {
    // File names shown in the upload list are the basenames of the uploaded files.
    const cvName = fileName(DATA.resume_path), letterName = fileName(DATA.cover_letter_path);
    await step('resume upload', async () => {
      if (cvName && await page.getByText(cvName).count()) return 'skip';
      await page.locator('input[type="file"]').first().setInputFiles(DATA.resume_path);
      await page.getByText(/successfully uploaded/i).first().waitFor({ timeout: 20000 });
    });
    await step('cover letter upload', async () => {
      if (!DATA.cover_letter_path || await page.getByText(letterName).count()) return 'skip';
      await page.locator('input[type="file"]').first().setInputFiles(DATA.cover_letter_path);
      await page.getByText(letterName).first().waitFor({ timeout: 20000 });
    });

    // CV parsing can add empty "Work Experience N" rows that later fail validation: delete them.
    await step('remove blank parsed rows', async () => {
      await sleep(1500);
      let removed = 0;
      for (const name of [/^Work Experience \d+$/, /^Education \d+$/]) {
        const groups = page.getByRole('group', { name });
        for (let i = (await groups.count()) - 1; i >= 0; i--) {
          const g = groups.nth(i);
          const first = g.getByRole('textbox').first();
          const val = await first.inputValue().catch(() => 'x');
          const pills = await g.locator('[data-automation-id="selectedItem"]').count();
          if (!val && !pills && (await g.getByRole('button', { name: 'Delete' }).count())) {
            await g.getByRole('button', { name: 'Delete' }).first().click(); removed++; await sleep(500);
          }
        }
      }
      if (!removed) return 'skip';
    });

    const x = DATA.experience;
    await step('work experience', async () => {
      if (!x) return 'skip';
      // A row with this title already exists (earlier application, CV parse): keep it, only fill what it lacks.
      let grp = null;
      for (const g of await page.getByRole('group', { name: /^Work Experience \d+$/ }).all()) {
        if ((await g.getByRole('textbox', { name: /^Job Title/ }).inputValue().catch(() => '')).includes(x.title)) { grp = g; break; }
      }
      let filled = false;
      if (!grp) {
        // Some tenants show an empty "Work Experience 1" without a Delete button: reuse it instead of adding a
        // second row, or the blank one fails validation.
        grp = page.getByRole('group', { name: /^Work Experience \d+$/ }).first();
        const blank = (await grp.count()) && !(await grp.getByRole('textbox', { name: /^Job Title/ }).inputValue().catch(() => 'x'));
        if (!blank) {
          await page.getByRole('group', { name: /^Work Experience$/ }).getByRole('button', { name: /^Add/ }).first().click();
          grp = page.getByRole('group', { name: /^Work Experience \d+$/ }).last();
        }
        await grp.getByRole('textbox', { name: /^Job Title/ }).fill(x.title_full);
        await grp.getByRole('textbox', { name: /^Company/ }).fill(x.company);
        const from = grp.getByRole('group', { name: /^From/ }), to = grp.getByRole('group', { name: /^To/ });
        // Months are never guessed: a bare year in the profile leaves the month for the user.
        const ym = async (grp, y, m, what) => {
          if (m) await grp.getByRole('spinbutton', { name: 'Month' }).fill(String(m).padStart(2, '0'));
          else log.push(`warn ${what} month unknown (profile has a bare year): ask the user`);
          if (y) await grp.getByRole('spinbutton', { name: 'Year' }).fill(String(y));
        };
        await ym(from, x.start_year, x.start_month, 'experience start');
        if (x.current) await grp.getByRole('checkbox', { name: /currently work here/i }).check().catch(() => log.push('warn no "I currently work here" box'));
        else await ym(to, x.end_year, x.end_month, 'experience end');
        filled = true;
      }
      // Some tenants require Location and Role Description.
      const loc = grp.getByRole('textbox', { name: /^Location/ });
      if ((await loc.count()) && !(await loc.inputValue())) {
        if (await loc.getAttribute('placeholder')) {
          await prompt(loc, x.location_search, new RegExp(`^${esc(x.location_option_prefix)}`, 'i')).catch(() => loc.fill(x.location_search));
        } else await loc.fill(x.location_search);
        filled = true;
      }
      const desc = grp.getByRole('textbox', { name: /^Role Description/ });
      if ((await desc.count()) && !(await desc.inputValue())) { await desc.fill(x.description); filled = true; }
      if (!filled) return 'skip';
    });

    const e = DATA.education || {};
    const aliases = (e.institution_aliases || [e.institution]).filter(Boolean);
    await step('education', async () => {
      if (!aliases.length) return 'skip';
      const existing = await page.locator('[data-automation-id="selectedItem"]').allInnerTexts().catch(() => []);
      if (existing.some(t => aliases.some(a => t.includes(a)))) return 'skip';
      // Some tenants render an empty "Education 1" block up front; reuse it instead of adding another.
      let grp = page.getByRole('group', { name: /^Education \d+$/ }).last();
      const blank = (await grp.count()) && !(await grp.getByRole('textbox', { name: /School|University|Institution/ }).inputValue().catch(() => 'x'));
      if (!blank) {
        await page.getByRole('group', { name: /^Education$/ }).getByRole('button', { name: /^Add/ }).first().click();
        grp = page.getByRole('group', { name: /^Education \d+$/ }).last();
      }
      const school = grp.getByRole('textbox', { name: /School|University|Institution/ });
      let picked = false;
      for (const alias of aliases) {
        try { await prompt(school, alias.split(' ').slice(0, 2).join(' '), new RegExp(esc(alias), 'i')); picked = true; break; }
        catch (_) { await school.fill(''); }
      }
      // Some tenants use a plain textbox for the school, not a search prompt.
      if (!picked && !(await school.getAttribute('placeholder'))) {
        await school.fill(aliases.length > 1 ? `${aliases[0]} (formerly ${aliases[1]})` : aliases[0]); picked = true;
      }
      // School not in the list: each tenant names the escape option differently.
      for (const [typed, re] of [['Other', /^Other( [-–] not listed)?$/i], ['Not', /^Not (in List|Listed|Available)$/i],
                                 ['School Not', /^School Not Listed$/i]]) {
        if (picked) break;
        try { await prompt(school, typed, re); picked = true; log.push(`warn school not listed: picked "${typed}..."`); }
        catch (_) { await school.fill(''); }
      }
      if (!picked) throw new Error('school: no matching or "not listed" option');
      // Degree: exact "<Level>(s)" first (a longer "Bachelors of Management and Engineering" can sit above plain
      // "Bachelors"), then "<Level> of <subject>" from degree_text ("of Arts" is often listed before "of Science"),
      // any "<Level>...", short codes ("BS", "MSc"), and generic or local names ("Undergraduate", "Licencjat").
      const degree = grp.getByRole('button', { name: /^Degree/ });
      if (!e.degree_level) throw new Error('no degree in the profile: ask the user');
      const level = String(e.degree_level).trim().replace(/'?s$/i, '');
      const lv = esc(level);
      const subject = (/\bof ([A-Za-z ]+?)\s*(\(|,|$)/.exec(e.degree_text || '') || [])[1] || '';
      const short = { science: 'Sc?', arts: 'A', engineering: 'Eng' }[subject.toLowerCase()] || 'Sc?';
      const codes = /doctor|ph\.?d/i.test(level) ? /^(Ph\.?\s?D\.?|DPhil|Doctorate)$/i
        : new RegExp(`^${esc(level[0])}\\.?\\s?${short}\\.?$`, 'i');
      const generic = { bachelor: /^(Undergraduate|University degree|Licencjat|Licence)/i,
                        master: /^(Graduate degree|Postgraduate|Magister)/i }[level.toLowerCase()];
      const degreeRes = [new RegExp(`^${lv}'?s?( Degree)?$`, 'i'), subject && new RegExp(`^${lv}('?s)? of ${esc(subject)}`, 'i'),
                         new RegExp(`^${lv}`, 'i'), codes, generic].filter(Boolean);
      let degreeSet = false;
      for (const re of degreeRes) {
        try { await choose(degree, re); degreeSet = true; break; }
        catch (_) { await page.keyboard.press('Escape').catch(() => {}); }
      }
      if (!degreeSet) throw new Error(`degree: no option for "${level}"`);
      const field = grp.getByRole('textbox', { name: /Field of Study/ });
      // Some lists only have a combined option ("<Major> or <Related>"): fall back to an option containing the major.
      if (await field.count()) await prompt(field, e.major, new RegExp(`^${esc(e.major)}$`, 'i'))
        .catch(() => prompt(field, e.major, new RegExp(esc(e.major), 'i')));
      const gpa = grp.getByRole('textbox', { name: /GPA|Overall Result/ });
      if (await gpa.count()) await gpa.fill(e.gpa);
      const from = grp.getByRole('group', { name: /^From/ }), to = grp.getByRole('group', { name: /^To/ });
      if (e.start_year) await from.getByRole('spinbutton', { name: 'Year' }).fill(String(e.start_year));
      if (e.end_year) await to.getByRole('spinbutton', { name: 'Year' }).fill(String(e.end_year));
      else log.push('warn education end year unknown: ask the user');
    });

    await step('languages', async () => {
      const groups = page.getByRole('group', { name: /^Languages \d+$/ });
      if (!(await groups.count())) return 'skip';
      for (let i = 0; i < DATA.languages.length; i++) {
        const L = DATA.languages[i];
        if (i >= (await groups.count())) {
          await page.getByRole('group', { name: /^Languages$/ }).getByRole('button', { name: /^Add/ }).first().click();
          await sleep(800);
          if (i >= (await groups.count())) break;  // tenant allows one language only
        }
        const g = groups.nth(i);
        const langBtn = g.getByRole('button', { name: /^Language/ });
        // A prefilled language can still have "Overall: Select One" (kept from an earlier application without its
        // level), so only the language pick is skipped, never the level.
        if (!(await langBtn.innerText({ timeout: 3000 }).catch(() => '')).includes(L.name)) {
          await choose(langBtn, new RegExp(`^${esc(L.name)}$`, 'i'));
        }
        // check() on "I am fluent" can fail silently: verify and warn.
        const fluentBox = g.getByRole('checkbox', { name: /fluent/i });
        if (L.fluent && (await fluentBox.count()) && !(await fluentBox.isChecked().catch(() => true))) {
          await fluentBox.check().catch(() => fluentBox.click({ force: true }).catch(() => {}));
          if (!(await fluentBox.isChecked().catch(() => true))) log.push(`warn fluent box not ticked: ${L.name}`);
        }
        const overall = g.getByRole('button', { name: /^Overall/ });
        if ((await overall.count()) && /Select One/.test(await overall.innerText().catch(() => 'Select One'))) {
          await choose(overall, new RegExp(`^${esc(L.cefr)}`, 'i'));
        }
      }
    });

    await step('skills', async () => {
      const box = page.getByRole('textbox', { name: /Type to Add Skills|Skills/ });
      if (!(await box.count())) return 'skip';
      // Multi-select list: options are "<Skill> not checked"; Escape + fill('') between skills can drop all but
      // the last pick, so close the list by clicking the heading instead. Adds every skill in DATA.skills.
      for (const s of DATA.skills) {
        await box.focus();
        await page.keyboard.type(s, { delay: 40 });
        await page.keyboard.press('Enter');
        await sleep(2200);
        const o = page.locator('[data-automation-id="activeListContainer"]')
          .getByRole('option', { name: new RegExp(`^${esc(s)}( \\(.*\\))? not checked$`, 'i') }).first();
        if (await o.count()) await o.click();
        await sleep(600);
        await page.locator('h2').first().click().catch(() => {});
        await sleep(400);
      }
    });

    await step('linkedin', async () => {
      const li = page.getByRole('textbox', { name: /LinkedIn/i });
      if (!(await li.count()) || await li.inputValue()) return 'skip';
      if (!DATA.linkedin) return 'skip';
      await li.fill(DATA.linkedin.replace('https://linkedin.com', 'https://www.linkedin.com').replace(/\/?$/, '/'));  // Workday rejects the short form
    });
    await step('save -> next step', saveAndContinue);
  } else log.push('skip My Experience (not on that step)');

  // ---------- report the next step (questions are answered by Claude from the answer bank) ----------
  const stepName = await currentStep();
  const snap = await page.locator('main, [data-automation-id="applyFlowPage"]').first().ariaSnapshot().catch(() => '');
  const questions = snap.split('\n')
    .filter(l => /^\s*- (radiogroup|group|combobox|checkbox|textbox|button) "/.test(l) && /\?|\*|Required/.test(l))
    .map(l => l.trim().replace(/:$/, '').slice(2, 320));
  return { step: stepName, log, answers, questions };
}
