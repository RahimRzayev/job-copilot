---
name: setup
description: First-run setup for a new user — read their CV, fill profile/profile.yaml, ask where and how they search (country, city, languages, remote, experience), set the default password for job-site accounts, and answer the common application questions once. Use when the user says "setup", "get started", "first run", runs /setup, or when any other skill finds no profile.
---

# Setup

Goal: after one session the user can run `/find-jobs` and `/apply` without being asked the same things again.
Work in the order below. Say which step you're on ("Step 3 of 8: search settings") and keep each question short.
Re-running `/setup` later updates only what the user wants to change: ask which steps to redo.

Rules (same as everywhere in this project):
- Never invent a fact about the user. Everything comes from their CV or their answers; unknown → ask.
- Never ask for a password in chat, never print one.
- Use AskUserQuestion for questions with options (up to 4 per call); free-text questions go in a short numbered list
  the user answers in one message.

## 1. Install check
1. `uv run jobhunt init` (creates `profile/profile.yaml` and `companies.yaml` from their examples, `data/` and the
   database; safe to re-run).
2. `uv run jobhunt doctor`. Missing items are fixed in the later steps; if `uv` itself is missing, point to the README.

## 2. CV
1. Ask the user to put their CV as a PDF in `profile/` (e.g. `profile/cv.pdf`) and say the file name. If they also
   want tailored CVs per job (`/tailor-cv`), ask for the Word (.docx) source too and put it in `profile/cv-source/`
   under the PDF's name (`profile/cv.pdf` → `profile/cv-source/cv.docx`). Google Docs CV: File → Download → Microsoft
   Word. With a Word file, run `uv run jobhunt doctor --export-test` once: it exports a test page the way
   `/tailor-cv` does (on macOS, Word may ask the user to allow access; tell them to click OK).
   No Word file (Canva, LaTeX) → fine: tailoring is skipped and the plain PDF is sent. Cover letters need nothing
   extra; they are written from profile.yaml, so the fuller the profile, the better the letters.
2. Read the PDF. Fill `personal`, `education`, `experience`, `languages`, `skills`, `projects`, `certifications` in
   `profile/profile.yaml` with what the CV states, nothing more. Set `personal.cv_file`. Dates as YYYY-MM (forms ask
   for the month); a CV that gives only a year → ask for the month. Education `status`: in progress or completed
   (ask if the CV doesn't say). A given name of several words → also fill `personal.first_name` / `last_name`.
3. Show a compact table of what you took from the CV and ask for corrections.

## 3. Personal details the CV doesn't have
Ask only for the gaps: phone with country code, home address (some forms require it), LinkedIn / GitHub / portfolio,
citizenship, work authorization (countries where you may work without sponsorship → `can_work_in`; do you need
sponsorship for the country you search in? a one-sentence statement for forms), availability
(start date, hours per week, notice period, accepted contract types), skill levels (basic / working / strong) for the
skills the CV lists. Write them to the profile.

## 4. Search settings (`search:` section)
Ask:
1. Country you're job hunting in, and the one city where on-site / hybrid work is fine.
2. Remote: only within that country / also remote open to your whole region / worldwide remote. Region → which
   region, as job ads write it (Europe: "europe", "emea", "eu"; Latin America: "latam", "latin america"...) →
   `region_names`.
3. Willing to relocate within the country (on-site anywhere)?
4. Languages you can WORK in (jobs requiring any other language get dropped).
5. Which roles do you want? Their own words, most important first ("data analyst, marketing assistant"). Never
   suggest roles from the CV as defaults: the user decides. Then levels (internship / working student / trainee /
   graduate / junior / mid), job
   titles they never want (optional: "sales, call center"), and the most years of experience a job may require
   before it's dropped (juniors: 2-3).
6. Expected gross monthly salary and currency (only typed into forms; never used to filter).
7. The job-search email inbox (a separate Gmail is best: registrations, verification codes and replies go there).

Then fill the rest yourself and show it for confirmation:
- `country_code` (ISO), `country_names` (English + local spellings, lower case: `["germany", "deutschland"]`),
  `city_names` (English, local and common foreign spellings), `other_cities` (lower-case prefixes of the country's
  other big cities, so on-site jobs there are dropped unless remote), `roles` and `levels` (lower case, as the user said them),
  `exclude_titles` (each word the user gave plus the other words job ads in that country use for it, shown for
  confirmation: "sales" → account executive, business development, BDR, SDR, plus the local word such as
  "vertrieb"), `tech_only` (true only when every role is an IT role: it drops sales / HR / legal / finance
  titles and adds IT-only job boards; mixed or non-IT roles → false; unsure → ask), `keywords` (level words searched
  before the roles: "working student", "intern"... student words first for students), `sources: []` (auto: LinkedIn + company career sites + the job boards
  known for that country; today only Poland has extra boards).
- Run `uv run jobhunt settings` and show the result.

## 5. Browser and email
1. The project's `.mcp.json` starts the Playwright browser Claude uses. Ask the user to open it once (any `/find-jobs`
   or this step: navigate to https://mail.google.com) and sign in to their job inbox and, optionally, LinkedIn. Logins
   stay in that browser profile; Claude never types those passwords.
2. Optional, recommended: in LinkedIn, create job alerts (role + location, daily) sent to the job inbox; `/linkedin-alerts`
   reads them from Gmail without logging in to LinkedIn.

## 6. Default password for job-site accounts
Most companies' application systems (Workday, SuccessFactors, Taleo...) need an account. Claude registers and signs in
with the job email + ONE default password stored in the OS keychain, pasted through the clipboard so Claude never
sees it.
1. Ask the user to run, in their own terminal (not in this chat): `uv run jobhunt secret set` — it asks for the password twice.
   Suggest a password used ONLY for job sites, 10-12 characters (some systems cap at 12), letters + digits + one symbol.
2. Check: `uv run jobhunt secret check` (prints only whether it's set and its length).

## 7. Common application questions
1. Read `setup/starter-questions.yaml`. Replace `{country}`, `{city}`, `{currency}` from the settings. Skip
   questions whose `when` doesn't fit the user (meanings at the top of the file).
2. Skip questions the answer bank already has (`uv run jobhunt qa lookup "<question>"`, similarity >= 0.85 and same
   meaning) and questions the profile already answers (citizenship, start date...: save those from the profile after
   showing them).
3. Go group by group. Questions with `options` → AskUserQuestion (4 per call); the rest → one numbered list per group.
   The user may skip any; skipped ones get asked later by `/apply` when a form needs them.
4. Save each answer: `uv run jobhunt qa save "<question>" "<answer>" --type <type> --tags <tag1,tag2>`.
5. Show a compact table: question → answer, saved count, skipped count.

## 8. Company watchlist (optional)
`companies.yaml` lists employers whose career sites are searched directly (Workday, Greenhouse, Lever,
SmartRecruiters...). It starts empty. Ask which companies the user wants to work for (now or later: "add <company>
to my watchlist" works any time). Only add companies the user names. For each, find the career-site system and slug
with WebSearch, add an entry in the format at the top of the file, and check it loads: `uv run jobhunt watchlist
"<company>"` → jobs found, or the error. 0 jobs usually means a wrong slug (some systems return an empty list
instead of an error): compare with the company's career site, or say it has no openings in the country now.
Without a watchlist, LinkedIn and the country's job boards still cover the search.

## Done
Run `uv run jobhunt doctor` again and show the result as a table (✅ / ❌ per check). Then tell the user the daily
loop, one line each:
1. `/mail-check`: sort new email, see interview / test invites first.
2. `/find-jobs`: scan, filter and score new jobs, shortlist the good ones.
3. `/tailor-cv`: tailored CV per shortlisted job (optional).
4. `/apply`: fill the applications; you approve every Submit.
5. `/shortlist`: progress table any time.
