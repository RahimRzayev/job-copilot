// SmartRecruiters "oneclick-ui" application form, page 1 (personal info, CV, education, experience, message),
// then "Next" to the screening page. NEVER clicks Submit. Returns a log + the screening questions found.
// Start state: the oneclick-ui form is open (https://jobs.smartrecruiters.com/oneclick-ui/company/...).
async (page) => {
  /*__DATA__*/
  const log = [];
  const esc = (s) => String(s || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Base name without extension: the upload list shows the file name (sometimes without ".pdf").
  const stem = (p) => String(p || '').split(/[\\/]/).pop().replace(/\.[^.]+$/, '');
  const step = async (name, fn) => {
    try { await fn(); log.push(`ok   ${name}`); }
    catch (e) { log.push(`FAIL ${name}: ${String(e.message || e).split('\n')[0]}`); }
  };
  const tb = (name) => page.getByRole('textbox', { name, exact: true });
  const pick = async (combo, text, optionName) => {
    await combo.click();
    await combo.pressSequentially(text, { delay: 40 });
    const opt = page.getByRole('option', { name: optionName }).first();
    await opt.waitFor({ timeout: 8000 });
    await opt.click();
  };
  // SmartRecruiters date pickers open on the current year: step back with "Previous year", click the month cell.
  const pickMonth = async (field, year, month, monthName) => {
    // Bare years in the profile have no month: never guess one, let Claude ask.
    if (!year || !month) throw new Error('date has no month in the profile (write YYYY-MM)');
    await field.click();
    const spin = page.getByRole('spinbutton', { name: 'Year' }).last();
    for (let i = 0; i < 15; i++) {
      const y = parseInt(await spin.inputValue().catch(() => spin.textContent()), 10);
      if (y === year) break;
      await page.getByRole('button', { name: y > year ? 'Previous year' : 'Next year' }).last().click();
    }
    await page.getByRole('gridcell', { name: `${monthName} 1, ${year}` }).click();
  };

  // Cookie banner blocks clicks.
  await page.getByRole('button', { name: 'Reject All' }).click({ timeout: 1500 }).catch(() => {});

  await step('first name', () => tb('First name').fill(DATA.first_name));
  await step('last name', () => tb('Last name').fill(DATA.last_name));
  await step('email', () => page.locator('[data-test="personal-info-email-input"]').getByRole('textbox', { name: 'Email' }).fill(DATA.email));
  await step('confirm email', () => tb('Confirm your email').fill(DATA.email));
  // Match city AND country: a bare city name can pick a namesake abroad.
  const cityOption = DATA.country ? new RegExp(`${esc(DATA.city_option)}.*${esc(DATA.country)}`, 'i') : DATA.city_option;
  await step('city', () => pick(page.getByRole('combobox', { name: 'City' }), DATA.city_search, cityOption));
  await step('phone', async () => {
    // The country code defaults to a guess (often another country): set it from DATA before typing the local
    // number. Type the phone's country name when known, else the dial code ("+<code>").
    const cc = page.getByRole('combobox', { name: 'Country code' });
    if ((await cc.count()) && !(await cc.innerText()).includes(DATA.phone_country)) {
      await cc.click();
      const name = DATA.phone_country_name;  // the phone's country; unknown -> type the dial code
      await page.keyboard.type(name || DATA.phone_country, { delay: 50 });
      const re = name ? new RegExp(esc(name), 'i') : new RegExp(esc(DATA.phone_country));
      await page.getByRole('option', { name: re }).first().click({ timeout: 8000 });
    }
    await tb('Phone number').fill(DATA.phone_local);
  });
  if (DATA.linkedin) await step('linkedin', () => tb('LinkedIn').fill(DATA.linkedin));
  if (DATA.website) await step('website', () => tb('Website').fill(DATA.website));
  await step('resume upload', async () => {
    await page.locator('[data-test="resume-upload"] input[type="file"]').setInputFiles(DATA.resume_path);
    await page.getByText(stem(DATA.resume_path)).first().waitFor({ timeout: 15000 });
  });

  const already = async (text) => (await page.getByText(text).count()) > 0;

  if (!(await already(new RegExp(esc(DATA.education.institution))))) {
    await step('education', async () => {
      const e = DATA.education;
      await page.locator('[data-test="add-education"]').getByRole('button', { name: 'Add' }).click();
      await pick(page.getByRole('combobox', { name: 'Institution' }), e.institution, e.institution);
      await page.getByRole('textbox', { name: 'Major' }).fill(e.major);
      await page.getByRole('textbox', { name: 'Degree' }).fill(e.degree_text);
      if (e.current) await page.getByRole('checkbox', { name: 'I currently attend' }).check();
      await pickMonth(page.getByRole('textbox', { name: 'From' }), e.start_year, e.start_month, e.start_month_name);
      if (e.current === false) await pickMonth(page.getByRole('textbox', { name: 'To' }), e.end_year, e.end_month, e.end_month_name);
      await page.getByRole('button', { name: 'Save' }).click();
      await page.getByText(new RegExp(`${esc(e.institution)} ${e.start_year}`)).waitFor({ timeout: 8000 });
    });
  } else log.push('skip education (already listed)');

  if (DATA.experience && !(await already(new RegExp(esc(DATA.experience.company))))) {
    await step('experience', async () => {
      const x = DATA.experience;
      await page.locator('[data-test="add-experience"]').getByRole('button', { name: 'Add' }).click();
      await pick(page.getByRole('combobox', { name: 'Title' }), x.title, x.title);
      await pick(page.getByRole('combobox', { name: 'Company' }), x.company, x.company);
      await pick(page.getByRole('combobox', { name: 'Office location' }), x.location_search, new RegExp(`^${esc(x.location_option_prefix)}`));
      await page.getByRole('textbox', { name: 'Description' }).fill(x.description);
      await pickMonth(page.getByRole('textbox', { name: 'From' }), x.start_year, x.start_month, x.start_month_name);
      if (x.current) await page.getByRole('checkbox', { name: /currently work here/i }).check();
      else await pickMonth(page.getByRole('textbox', { name: 'To' }), x.end_year, x.end_month, x.end_month_name);
      await page.getByRole('button', { name: 'Save' }).click();
      await page.getByText(new RegExp(`${esc(x.title)} ${x.start_year}`)).waitFor({ timeout: 8000 });
    });
  } else log.push('skip experience (already listed or none)');

  if (DATA.message) {
    await step('message', () => page.getByRole('textbox', { name: /Let the company know/ }).fill(DATA.message));
  }

  // Validation errors on page 1?
  const errors = await page.locator('[role="alert"]').allInnerTexts().catch(() => []);
  const realErrors = errors.map(t => t.trim()).filter(t => t && !/cookie/i.test(t));
  if (realErrors.length) return { stage: 'page1', log, errors: realErrors };

  await step('next -> screening', async () => {
    await page.getByRole('button', { name: 'Next' }).click();
    await page.getByRole('heading', { name: 'Preliminary questions' }).waitFor({ timeout: 15000 });
  });

  // Report the screening page so Claude can answer from the answer bank; nothing is submitted here.
  // SmartRecruiters renders in shadow DOM, so read the accessibility tree instead of the DOM.
  const snap = await page.locator('main').ariaSnapshot().catch(() => '');
  const questions = snap.split('\n')
    .filter(l => /^\s*- (radiogroup|combobox|checkbox|textbox) "/.test(l))
    .map(l => l.trim().replace(/:$/, '').slice(2, 320));
  return { stage: 'screening', log, questions };
}
