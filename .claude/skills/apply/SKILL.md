---
name: apply
description: Apply to a tracked job (by jobhunt id) through the browser — open the posting, follow it to the real application form, fill everything from profile + answer memory, ask the user only for unknowns, and stop before Submit for approval. Use when the user says "apply to <id>", "apply to the shortlisted ones", "/apply".
---

# Apply

Input: one or more job ids (`uv run jobhunt list --status shortlisted` if none given).
Browser: Playwright MCP (`mcp__playwright__*`). Files you upload or save must be inside the project directory.

Before the first job: `uv run jobhunt settings` (country, city, languages, currency, salary_monthly, job_email,
password_service) and read `profile/profile.yaml`. No profile, or settings without country/city → tell the user to
run /setup first and stop.

## Hard rules (also in CLAUDE.md)
- **Never click the final Submit/Apply/Send without explicit approval in this conversation** — one AskUserQuestion per
  application, with a summary preview. "Next"/"Continue" buttons that only change page are fine.
- Never invent facts. Order of truth: `profile/profile.yaml` → `uv run jobhunt qa lookup "<question>" --company <X>` →
  ask the user (then `qa save`).
- **Read back every field** before the approval question: `browser_evaluate` over the inputs/selects/textareas, not
  the screenshot alone (placeholders and merged autofill values only show up this way).
- **Never set `skipped` or `manual` on your own judgment** — ask first. Facts need no question but are still reported:
  the posting is closed, or it is LinkedIn Easy Apply only.
- CAPTCHA → set `needs_input`, ask the user to solve it in the window.
- LinkedIn: never automate Easy Apply, never log in to LinkedIn or Glassdoor. Easy Apply only → answers sheet + `manual`.

## Accounts and passwords
Every ATS account uses the job email (settings `job_email`, else `personal.email`; forms get the same address) +
the user's default password (OS keychain entry settings `password_service`). Existing account → sign in; none →
register, then verify via that Gmail. Do it yourself; never ask the user for the password.
- "Sign in with Google" first when offered (the job Gmail). If the session is not active the user completes the
  Google login; the automated browser cannot use passkeys, so they pick "Try another way".
- Before registering, search the job Gmail for the ATS domain (`from:<domain> in:anywhere`) and check the portal's
  "Sign In": an account may exist. A second registration can be silently rejected while the form says "Success".
- Paste the password through the clipboard so it never enters the conversation:
  1. `uv run jobhunt secret copy` (prints only the length; add the site rule, e.g. `--max 12`, if one is saved).
  2. Click the password field, Playwright `ControlOrMeta+V`; same in the confirm field.
  3. Read back only the field's length (`el.value.length`) and compare it with the printed length.
  4. `uv run jobhunt secret clear`.
- Site rules: Taleo and some others cap passwords (often 12 characters) → `--max 12`; a site that demands more
  length or another character class → `--suffix <S>`. If a site rejects the default, ask the user once how to adapt
  it, then save the rule, never the password: `uv run jobhunt qa save "password rule <domain>" "--max 12" --type text
  --tags password-rule`. Look it up with `qa lookup "password rule <domain>"` before every sign-in.
- Sign-in fails → "Forgot password" → reset link from the job Gmail → set the default again (same 4 steps).
- `uv run jobhunt secret check` says whether the default exists. Missing → ask the user to run
  `uv run jobhunt secret set` in their own terminal.
- Never print the password, put it in a tool argument, write it to a file, or ask for it in chat.
- Personal logins (Google, LinkedIn, the user's own job-board accounts) stay the user's own: they sign in themselves.

## Queue status
Keep every job's status current — the user watches it with /shortlist or `jobhunt queue --watch` instead of
interrupting you. One short `--note` each time:
- starting a job: `uv run jobhunt status <id> --set applying --note "<step, e.g. checking company page / filling Workday>"`;
  update the note at big steps (registering, cover letter, filling page 2…).
- you need the user (Submit approval, CAPTCHA, unknown answers): `--set needs_input --note "<what exactly>"`
  right BEFORE asking; back to `--set applying` when they answered.
- not applying (after the user agreed, or a fact: closed / Easy Apply only): `--set skipped --note "<reason>"` or
  `--set manual --note "<reason>"`.
- `jobhunt applied` sets `applied` itself. Never leave a job in `applying` when moving to the next one.

## Flow per job
1. `uv run jobhunt show <id>` → title, company, url, apply_url, employer_url, `employer_note`, `history_note`.
2. **Still open, not applied yet?** `uv run jobhunt verify <id>` (board + employer ATS), then confirm on the company's
   own site before filling anything (aggregator listings can be stale). Search the job Gmail for the company name +
   application/candidate keywords: catches prior applications and pending tests/interviews. Closed →
   `status <id> --set skipped --note "closed: <where>"` and tell the user. Prior application or pending test → ask.
   The employer page asks more years or a language outside settings `languages` → say so and ask before filling.
3. **Find the real form**: start from `employer_url`. Empty → `uv run jobhunt resolve <id>`; still empty → WebSearch
   `"<title>" "<company>"` (+ "myworkdayjobs", "smartrecruiters", "successfactors", "greenhouse"…), then
   `uv run jobhunt set-employer <id> <url>` so it is never searched twice.
   - LinkedIn-only jobs often have an employer posting: search the company careers site (`<careers>?q=<title>`) before
     calling it LinkedIn-only. LinkedIn may list a job under an agency or another brand name: search the real
     company's board too. LinkedIn guest page: `apply-link-offsite` = employer site exists; `apply-link-onsite` =
     Easy Apply (→ `manual`, a fact). Offsite but no employer posting found → ask the user (`manual`?). LinkedIn
     alert descriptions can be truncated: re-read the employer posting for years/stack.
   - Board ads that need a board login usually also exist on the employer's careers site (pick the page for your
     country / remote).
   - Platform quirks: see **ATS notes** below.
4. **Which CV**: `personal.cv_file` by default. If `personal.cv_variants` has a variant for this role type (e.g. a
   data-analyst CV for pure data/BI roles), use it (`formfill ... --cv <variant>`). One CV per application, never
   two. Say which CV in the approval summary.
5. **Tailored CV (skill facts)**: `uv run jobhunt cv plan <id>` → `unknown` = skills the job names that were never
   asked about. Ask them in one AskUserQuestion round (Yes working / Yes basic / No) and save each with
   `uv run jobhunt cv fact-save "<skill>" --status yes|no --level working|basic --display "<as on CV>"` — never ask
   the same skill twice. Then `uv run jobhunt cv build <id>`: `built: true` → `formfill` uploads the tailored copy;
   `blocked` → say why and upload the base CV. Never add a skill with `no` or one the user has not confirmed. The
   approval summary says "CV tailored: +<skills>" or "base CV".
6. **Cover letter** — one letter PER JOB, ~180-250 words, profile facts only:
   1. "Dear <Company> <team>," + one sentence: who the user is and the role applied for. Still studying
      (`education.status` "in progress", or `expected_graduation` ahead): degree, year of study, university, expected
      graduation. Graduated: degree, university, graduation year, and the current or latest role from `experience`.
      All from the profile; compute the year of study, never guess.
   2. The 2-3 projects / skills / experience entries from the profile that match THIS posting; name the matching tech.
   3. Honest gaps for this posting's must-haves ("I have not used Java commercially yet …") + readiness to learn.
   4. Work authorization (`work_authorization.statement`), language levels relevant to the job, availability
      (`availability`); work model only as the posting states it — no commitments the user hasn't confirmed (step 8).
   5. "Kind regards, <personal.full_name>".
   Save to `data/inbox/msg-<id>.txt`, render `uv run jobhunt cover <id> --text-file data/inbox/msg-<id>.txt`. Its
   `unverified` list = tools/names/numbers the profile doesn't back up (gap sentences are skipped): remove each one,
   or ask the user and add the fact to profile.yaml. Never send a letter with unexplained `unverified` terms. Attach
   the PDF it prints (never a .txt). Use the same text for message boxes (shorten to the box's limit).
7. **One-shot fill scripts** (SmartRecruiters, Workday, SuccessFactors): run them AFTER `cover` so they attach the
   letter. `uv run jobhunt formfill <smartrecruiters|workday|successfactors> <id> --message-file
   data/inbox/msg-<id>.txt [--source "LinkedIn"] [--cv <variant>]`, then
   `mcp__playwright__browser_run_code_unsafe(filename=<script>)` once the form is open (SmartRecruiters: the
   oneclick-ui page; Workday: signed in, "Apply Manually", on "My Information"; SuccessFactors: "Apply for <job>").
   They return a per-field `log` (ok/skip/FAIL), `answers` / `all_fields` (every value they chose or found) and the
   next page's `questions`. **Script answers are proposals, not facts**: read every value (wrong source labels,
   parsed Industry/State, stale entries), fix FAILs by hand, patch `browser/ats/<ats>.js` when a tenant differs.
   Other ATS by hand: prefer `browser_find` with a regex over full snapshots (pages are huge); `browser_fill_form`
   for plain text boxes.
8. **Questions**: for every question the profile doesn't cover, `qa lookup`; no confident match → collect all
   unknowns for this form and ask in one AskUserQuestion round (max 4), then `qa save` each answer.
   - *Salary*: `qa lookup` first (tag `salary`: the user's own rule for ranges, internship vs junior figures, other
     currencies); no saved answer → settings `salary_monthly` in settings `currency`, gross per month. Another
     currency → convert at the current rate and say so in the preview. A rate per contract type (contractor, hourly)
     → answer bank or ask. Never invent a figure.
   - *Work authorization / visa / permit type / citizenship status*: `work_authorization` in the profile, else the
     answer bank, else ask.
   - *Languages*: levels from the profile's `languages`; never claim a language that is not listed.
   - *Personal commitments* (shift work, nights/weekends, travel %, relocation, on-site days, start date other than
     the profile's `availability`): only from the answer bank; absent → ask in the unknowns round and `qa save`.
     Never write them into a cover letter as a fact.
   - *Internships that require student status for the whole period*: compare with `expected_graduation`; ask if it
     ends earlier.
   - *Opinion / about-the-company fields* ("3 words that describe us", "why us"): draft from the posting and mark
     them **[drafted by Claude]** in the approval preview.
   - *Knockout questions*: an honest "No" on a must-have can grey out the whole form → stop and ask the user.
9. **Old documents**: portals with an existing account (Avature, SuccessFactors, Workday "Use My Last Application")
   keep earlier CVs and cover letters. Before approval open the profile/attachments list and make sure only THIS
   job's CV and letter will be sent; delete stale ones, upload the new ones, verify by file size if names match.
10. **Before Submit**: read back every field (hard rule), `browser_take_screenshot` to
    `data/inbox/<company>-presubmit.png`, `status --set needs_input --note "Submit approval"`, then AskUserQuestion
    "Submit?" with a preview of every filled value, the CV and letter used, and anything marked [drafted by Claude]
    (Submit / Edit first / Don't apply).
11. **After Submit**: confirm the success page/text AND the confirmation email in the job Gmail, then write
    `data/inbox/app-<id>.json` (`{answers: {...}, cover_letter, notes}`) and
    `uv run jobhunt applied <id> data/inbox/app-<id>.json --ats <name>`.
12. Close finished tabs. Report: submitted / skipped (why) / waiting on the user (what).

## ATS notes
### Everywhere
- **Upload first, then type**: on many forms (Ashby, iCIMS, Dayforce, SuccessFactors, Avature) an upload re-renders
  the form, wipes typed values or gives fields new ids. Upload CV + letter, then fill, then answer by label text.
- **Resume parsing** fills fields wrongly (last name in "Preferred First Name", past volunteering as the current job,
  wrong major/industry/state, a language the user doesn't speak in skills): check every field it touched.
- **File uploads in `browser_run_code_unsafe`**: prefer `input[type=file].setInputFiles()`. Some widgets ignore it
  → click the button and finish with `browser_file_upload` (the call ends in "Modal state: File chooser").
- **Dropdown overlays / cookie banners** intercept clicks: reject cookies ("Reject all" / "Continue without
  accepting") or remove the banner node; press Escape and focus the input with `evaluate` before typing.
- **react-select**: the input is a hidden dummy — `document.getElementById('react-select-N-input').focus()`,
  ArrowDown, click `#react-select-N-option-K`; searchable ones: type slowly + Enter or click the option inside the
  input's own `aria-controls` listbox.
- **Checkboxes with a link in the label**: never `check({force:true})` (the forced click can hit the link and lose the
  form) — click the label text.
- **Email OTP / verification links**: open a new tab
  `https://mail.google.com/mail/u/0/#search/from%3A<company>+newer_than%3A1h` (use `/u/<job_email>/` if the job Gmail
  is not the browser's first account), read only that message's code, close the tab. OTP boxes (`[id="0-otp"]`,
  `#pin-code-1`…): click the first and type, focus advances itself. A "Verify and submit" button needs approval first.
- **LinkedIn URL**: `https://www.linkedin.com/in/<id>/` — several validators reject the short form.
- **Hidden length limits**: some text boxes cut or reject silently (~255 chars Avature, 500 Pracuj, 1000 JustJoin).
- **Consents**: required ones ticked; "contact me about this application" ticked; future-recruitment and talent
  community boxes follow the answer bank (tag `consent`; no answer → unticked and ask once); marketing and
  account-creation boxes left unticked unless the form makes them required.
- **Source / "How did you hear"**: `qa lookup "how did you hear about us"` for the user's own rule first; else
  where the job was found ("LinkedIn", "Company website" for career-site jobs, "Job board" for other boards).
- **Already applied**: portals that show earlier applications → record with `jobhunt applied` + note, never re-apply.
  "Duplicate candidate" errors → stop and ask; never retry with different contact details. Some companies allow one
  pending application per candidate. A rejection "valid only for this position" does not block other jobs there.
- **Invisible reCAPTCHA** rejecting the automated click: fill everything, get approval, ask the user to click Submit
  (a retry may be needed). Some forms show the reCAPTCHA only on the first Submit click: the user ticks it, the
  second click submits.
- **School not listed**: try `education.former_name` first, then the form's "Other" / "Not listed" option (type the
  name if a free-text box appears). Virtualized lists only render a few rows: type in their search box.
- **No confirmation** (server error after Submit): check the job Gmail; no email within ~15 min = not submitted →
  tell the user and resubmit (the approval stands).
- Company sites with their own Google sign-in: the user signs in. Internship forms may want a transcript — ask the
  user for the file and keep it in `profile/`. Talent platforms that end in an AI video interview: fill the profile
  (levels from profile.yaml, not their defaults), the user does the interview.

### By platform
- **Ashby** (`jobs.ashbyhq.com/<co>/<id>/application`; board API `api.ashbyhq.com/posting-api/job-board/<co>`):
  upload first (re-render empties Name/Email/Phone and radios); the top "autofill from resume" input also fills
  Resume — check the Resume entry. Type with `pressSequentially`. Radios: `check({force:true})` misses React state —
  click `label[for=<id>]` once, only if unchecked (labels toggle). Yes/No are buttons (`_active_` class). Location =
  combobox, pick the option. Success text "successfully submitted" / "Thank you for applying" on the same URL.
- **Avature** (`*.avature.net` or company domains): use the "APPLY" link (`/jobs/ApplicationForm?folderId=`), not a
  one-click "APPLY MY PROFILE". CV upload = click the button + `browser_file_upload` (setInputFiles is ignored).
  The Register page parses the CV — fix values; CONTINUE there can be the submit. Stale documents: Delete opens a
  browser `confirm` (`page.once('dialog', d => d.accept())`); a cookie banner can block Delete links. Hidden
  `<select multiple>` skills: `selectOption` + change event. A voluntary EEO survey page's "APPLY" submits. Success
  `/Success` + confirmation email (always check it).
- **Darwinbox**: Google sign-in; resume autofill → check every step; date picker: navigate months, click day; submit
  opens a confirm modal; 422 "Duplicate candidate" = prior application → stop, ask.
- **Dayforce** (`jobs.dayforcehcm.com/.../CANDIDATEPORTAL/jobs/<id>`): "Apply without an Account". "Import Resume"
  wipes personal fields → upload first, refill by id (`jobPostingApplication_personalInfo_*`); Education/Employment
  via "Edit Record" → "Update". Ant selects: click `input[id=...]` with force, type, click `.ant-select-item-option`.
  Last step "Candidate Acknowledgement" (background checks etc.) goes in the preview; submit
  `button[test-id=application-submit]` → `/apply/success`.
- **eRecruiter** (`form.erecruiter.pl/form/<id>`; ASP.NET `*.erecruiter.pl/FormTemplates/RecruitmentForm.aspx`):
  custom selects = button + `[role=option]`; `input[type=date]` → `fill('YYYY-MM-DD')`; ASP.NET: pick country first
  (postback), multi-select `#ms..._lstOptions-title` + checkbox, uploads post back (wait for networkidle). Success
  `/thank-you` or `ThankYou.aspx`; some redirect to a candidate-account promo → confirm by email.
- **Element** (`*.elementapp.ai/application/v2/...`): one page; questions may be in the local language (answer in
  a language from settings `languages`, the posting's language when the user works in it); success `/application/confirmation`.
- **Greenhouse** (`job-boards.greenhouse.io`, `job-boards.eu.greenhouse.io`, embed `.../embed/job_app?for=<co>&token=<id>`):
  "Apply Now" is a button. Question ids differ per job → match by label. Comboboxes and Yes/No are react-select:
  click the `.select__control` holding the input and pick the option from its `aria-controls` listbox (the phone
  `iti` list is always in the DOM, so a bare `[role=option]` hits it). Phone "Country" = dial code. Files `#resume`,
  `#cover_letter` (embed forms may have no letter field); sometimes a required `#preferred_name`. Success `/confirmation`.
- **HR Manager / Talentech** (`candidate.hr-manager.net`): find the role in `vacancies/list.aspx?customer=<co>`.
  Single ASP.NET page: document category select → hidden file input (posts back): CV as "CV", letter as "Other
  documents". Account is created on submit.
- **iCIMS** (`*.icims.com`): email + consent + hCaptcha (user), then a password page. Uploading the resume reloads the
  page and wipes fields → upload first, then fill, then password. It keeps the OLD file name → compare sizes via the
  download link. Dropdowns: click combobox, type, `[role=option]:visible` inside the `in_iframe=1` frame. "Phone
  linked to an account" → tick "Use this phone anyway".
- **Jobvite** (`jobs.jobvite.com/<co>/job/<id>/apply`): residence country, "I ACCEPT", plain inputs;
  success `/applyConfirmation`.
- **Oracle Recruiting Cloud** (`*.fa.*.oraclecloud.com/.../CandidateExperience`): email + terms → form; Preferred
  First/Last Name can be required (copy the legal name); postal code is a lookup (type, click gridcell) and wipes
  City; conditional follow-ups appear after answers. Submit sends a 6-digit email code (`#pin-code-1..6`). Success:
  My Applications "Application Submitted".
- **Phenom** career sites (often in front of Workday): email OTP first; uploads fire `alert()` → auto-accept;
  year-only education picker; school search not listing it → "Not Available". Success `/applythankyou`.
- **SmartRecruiters** (`jobs.smartrecruiters.com`): "I'm interested" → oneclick-ui form. Comboboxes keep only a value
  **picked from the dropdown** (type slowly, click the option; Escape clears). No `;` in text. Dates: picker,
  "Previous year", click month. Upload via the **Resume** section button, not the autofill box. Phone country code
  can default to another country (the script sets it). Page 2: screening questions (click radios by snapshot ref;
  options may contain en dashes) + privacy checkbox. Success URL ends `/success` (check `location.href` + body).
  Liveness: `https://api.smartrecruiters.com/v1/companies/<Company>/postings?city=<city>&limit=100` lists only open
  postings (`q` is ignored); expired pages say "Sorry, this job has expired".
- **SuccessFactors** (`career<N>.successfactors.eu|com`): check "Job Management → Jobs Applied" first. Job page
  "Apply now" → menu "Apply Now" (a direct `/talentcommunity/apply/<req>` link redirects home; a forced click may be
  needed). Some tenants create the account on the apply page (reCAPTCHA → user). Picklists `[id="N:_input"]` + type +
  `[role=option]:visible`; Yes/No `globalRadio` spans → click `[id="N:_anchor"]`. Old CV: "Edit document" → "Upload
  from Device", or Delete → in-page Confirm → OK. Every upload re-renders with new ids and wipes Additional
  Information → upload first, answer by label. No region in the list → "No state available" / "Not Applicable".
  "Notification" (about this application) ticked, "Talent Community" per the answer bank (tag `consent`); privacy dialog → Accept. Apply may
  send a one-time passcode; a failed Apply keeps the values. Success "Successfully Applied to …" / confirmation email.
- **Taleo** (`*.taleo.net/careersection/...`, often opened in a new tab from another board): user name = email;
  password usually capped at 12 → `secret copy --max 12`. Steps: GDPR selects + consent dates via the calendar link
  → "Today"; Source Type; Resume ("I want to upload" radio, file input, then the multipart form's Save and Continue —
  button ids change to `editTemplateMultipart-…`); Attachments (file + comment + "Attach"); Personal Information
  (Country first; calendar "›" next month, click a day cell without `othermonth`); Education/Experience fields are
  Select/Search pop-up pages (type in the filter, Refresh, tick the row radio `tr.list-evenrows/oddrows`, Select —
  filter with the exact text, a plain "Other" picks the wrong first row). Success "Process completed. Thank you for
  your job application."
- **Teamtailor** and similar: success URL contains `/thanks/`; a 503 after Submit → check Gmail (see Everywhere).
  Knockout questions grey out the form.
- **TestedRecruits**: reCAPTCHA failure wipes the resume input; the user must not click upload buttons (Playwright
  captures the native picker). A skills-test invite follows quickly — record the application right after submit.
- **Traffit** (`<tenant>.traffit.com`): `public/an/<id>` is the ad, its "Apply" opens `public/form/a/<id>`; open jobs
  list `https://<tenant>.traffit.com/public/job_posts/published`. Selectize: type into the `-selectized` input, click
  `.selectize-dropdown .option`, click `h1` to close. Consent: `check({force:true})` (label intercepts); some tenants
  make the future-recruitment consent required. Success `/public/form/thankyou/`.
- **Workable** (`apply.workable.com/<co>/j/<id>/apply`): resume import pre-fills Address — check the value before
  submit (it can merge two addresses). Currency list may lack yours. Cover letter = plain textarea.
- **Workday** (`<tenant>.wd<N>.myworkdayjobs.com/<site>`):
  - "Apply" → "Apply Manually". "Sign in with Google" when offered; otherwise "Sign In" check, then Create Account
    with the clipboard password (the button can sit under a `[data-automation-id="click_filter"]` overlay → click the
    overlay; the terms checkbox needs its label clicked). Guest-apply tenants have "Next" instead of "Save and Continue".
  - Jobs API: `POST https://<tenant>.wd<N>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs {"searchText": "..."}`;
    `remoteType` tells the work mode.
  - Dropdowns: `[data-automation-id="promptOption"]` (click by `[data-automation-label="..."]`), some searches show
    `menuItem` instead; plain listbox buttons → `role=option`; some open only with focus + Enter. Search fields:
    type + Enter. A prompt popup can block clicks → Escape, focus the input.
  - "How did you hear": flat list, listbox, or virtualized category tree. Only a leaf is valid; "Social Media"
    leaves can fail at Submit or open a required referral-name field → prefer the exact LinkedIn leaf or Job Board >
    Job Board. Read the source pill on the Review page every time.
  - Address: some country formats need Municipality/District and a Province listbox; phone device type and phone code
    are picklists.
  - Education: school is a search or a plain textbox; not listed → "Not in List" / "School Not Listed" / "Other"
    (a former name from `education.former_name` may be listed). Some tenants leave education groups unlabelled (the
    script fails: fill by hand). Degree lists may be short codes (BS, MS). Dates are MM/YYYY or year-only
    spinbuttons — type into them (off-viewport → focus + fill).
  - Languages: five 1–5 levels + "I am fluent". Fill languages once: after Back/Save cycles one can vanish from the
    form but stay in the backend ("Duplicate language entries are not allowed").
  - Questions: previous-worker wording varies; "right to work" / visa opens follow-ups (permit type, copy of visa);
    citizenship status = country search. Many tenants have no cover-letter field → say the letter is not sent.
  - Validation errors after Submit can lose the draft → reload. Success: "Application Received" on Candidate Home or
    `jobTasks/completed/application`; status may show "Assessment In Process" at once; confirmation email.
- **Yello / recsolu** boards: "APPLY" → "CONTINUE" may open the real ATS (Taleo) in a new tab.
- **Zoho Recruit**: cookie layer → remove the `career-cookie-consent` node; City is a combobox (`#inputId` in
  "Address Information"), Country a text box; language levels are `lyte-drop-item` lists. Image CAPTCHA → the user
  types it; Enter in the CAPTCHA box submits (approval first) and may redirect elsewhere → confirm by email.
- **Custom career pages / webforms**: a failed submit can re-render with a new id suffix but keep values; custom
  selects `.select-selected` → `.select-items > div`; validators can fire while typing (letters/digits/hyphen only).

### Country job boards (only when that source is enabled for your country)
- **Pracuj** (Poland): read the apply href with `browser_evaluate` (`[data-test="anchor-apply"]`) and navigate to it
  (clicking is often blocked by a terms modal). Login is the user's own: "Witaj ponownie" → ask the user to type the
  password, `browser_wait_for textGone: "Witaj ponownie"`. "Pracodawca prosi o wypełnienie swojego formularza" →
  "Kontynuuj aplikowanie"; the employer form opens in a **new tab** (`browser_tabs`); the Pracuj "Dziękujemy" page
  after that is NOT a submission. Native form ("Formularz aplikacyjny"): it pre-attaches an old CV and a generic
  Polish message — replace both (`#cvFilesChooseFileFromDriveInput` is the real input). The account holds at most 6
  CVs: pick an identical stored copy via "Wybierz CV z Mojego Konta", or ask the user which one to delete. Cover
  letter: "Dodatkowe załączniki" → "prześlij z urządzenia" + `browser_file_upload` → type "list motywacyjny".
  "Wiadomość do pracodawcy" is cut at 500 characters (write ≤ 480). The questionnaire after "Wyślij formularz" comes
  AFTER submission.
- **JustJoin** (Poland) quick apply: invisible reCAPTCHA rejects the automated click → fill everything, ask the user to
  click Apply (a retry may be needed). The message box opens through an unnamed checkbox
  (`form input[type=checkbox]:not([name])`) and allows 1000 characters with no counter (~800 incl. links). A sign-up
  popover can block Apply → Escape. Success "Applied - DD.MM.YYYY" on the offer + "You applied for" email.
- **NoFluffJobs** (Poland): "email" apply = modal `#apply-modal` (`#applyButton`; Escape wipes it). CV via "Wybierz
  plik" + `browser_file_upload` (setInputFiles does not register). A login popover blocks `#applyButton` → clear
  `.cdk-overlay-container`. Tick the language checkbox for your language; leave "Załóż konto" unticked.
  `applybtnexternal` = employer site in a new tab; NoFluff's `/success` then is NOT a submission.
- **Bulldogjob** (Poland): ads need a Bulldogjob login → find the same role on the employer's careers site.
