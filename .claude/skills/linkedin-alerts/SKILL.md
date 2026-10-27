---
name: linkedin-alerts
description: Daily check of the user's LinkedIn job-alert emails in Gmail — extract every job LinkedIn emailed, fetch descriptions, filter, score and report ONLY those jobs. Use when the user says "check my LinkedIn alerts", "linkedin alerts", "what did LinkedIn send me", "/linkedin-alerts".
---

# LinkedIn alerts

A small, fast alternative to `/find-jobs`: only the jobs LinkedIn emailed to the job Gmail from the user's saved
LinkedIn job alerts. No LinkedIn login, no scraping LinkedIn search. Run from the project root; commands print JSON.

First `uv run jobhunt settings` → `job_email` (the Gmail the alerts go to), city, country, languages, max_years. No
profile or no country/city → tell the user to run /setup first and stop. No LinkedIn alerts set up yet → suggest
creating some on LinkedIn (one alert per settings `roles` entry, optionally with a level word, location = your
city / country, daily, delivered by email to `job_email`) and stop.

Argument: optional number of days. Default (no argument): every alert email since the last successful import
(stored in the DB, `meta.alerts_last_read`, minus a 1-hour overlap), so the time of day and weekends don't matter and
alerts that arrived after the previous run are always read, even if `/mail-check` already marked them read.
A number N overrides this with the last N days.

## 1. Read the emails (browser)
1. `uv run jobhunt alerts query` (or `--days <N>` if the user gave N) → `url`, `days`, and `since` when incremental.
2. `browser_navigate` to that url (Gmail signed in as `job_email`; if the browser has several Google accounts and
   the job Gmail is not the first, replace `/u/0/` with `/u/<job_email>/`). Wait ~4 s for the list. If Gmail shows
   "No messages matched", tell the user there were no alert emails in that window and stop.
3. Run `browser/linkedin_alerts.js` with `browser_evaluate` (pass the file content as `function`,
   `filename: data/inbox/linkedin-alerts.json`). It opens nothing: it fetches each thread's print view and
   returns `{stats: {emails, cards, jobs, errors}, jobs: [...]}`.
   More than 50 alert emails (one Gmail page)? Also open `<url>/p2`, run again into `linkedin-alerts-p2.json`,
   and import both files.

## 2. Import
`uv run jobhunt alerts import data/inbox/linkedin-alerts.json` (add `--days <N>` only if the user gave N; default =
days since the last import). A successful import records the file's write time as the new `alerts_last_read`.
- expands every alert's "See all jobs" link (an email shows only ~6 matches) by running the same search
  (keywords, geoId, workplace/experience filters) on LinkedIn's public guest endpoint for the last N days,
- fetches each new job's description from LinkedIn's public guest page (404 = removed → stored as `closed`),
- runs the normal prefilter per your settings (languages outside settings `languages`, senior, more than
  `max_years`, non-tech when `tech_only`, `exclude_titles`, on-site outside your city, remote outside settings `remote`),
- skips the "See all" expansion of alerts searching Europe/EU/EMEA/worldwide (`WIDE_GEO` in alerts.py; their remote
  results are mostly pinned to other countries and all get filtered); the email's own cards still count,
- revives jobs the daily scan had dropped only for age ("older than 24h"),
- pre-scores everything. Output: `counts`, `filtered_reasons` (count per reason), and `jobs` grouped by status
  (`new`, `scored`, `applied`, `closed`…; `filtered` lists only the email's own cards), each with its `alert` keyword.

## 2b. Mark the alert emails as read
Only after the import succeeded: put the `threads` list from `data/inbox/linkedin-alerts.json` (and `-p2`) into
`data/inbox/mail-mark.json`, `uv run jobhunt mail mark-script --ids-file data/inbox/mail-mark.json`, then on the
alert search page from step 1 run `browser_run_code_unsafe(filename=data/inbox/gmail-mark-read.js)` until it returns
`marked: 0` (reload between rounds). Never anything but "Mark as read".

## 3. Check history + score
1. Gmail history for these companies: `uv run jobhunt history match` (uses the history already imported; if the
   last import is older than a few days, refresh it as in `/find-jobs` step 6 first).
2. Read every job in `jobs.new` in detail (`uv run jobhunt show <id>`) — alert batches are small, so no auto-low.
   Score them exactly as `/find-jobs` §2 describes (same tiers, honest reasons, history red flags), write
   `data/inbox/scores-alerts.json`, `uv run jobhunt score data/inbox/scores-alerts.json`.
3. Liveness + company page for the ones scored medium/high: `uv run jobhunt resolve <ids…>`, then
   `uv run jobhunt verify <ids…>`, and follow "Company-page check before any shortlist question" in `/find-jobs`
   (incl. its `easy_apply` list: Easy Apply-only jobs are `manual` before the question, not after).

## 4. Report
In chat, a compact table of the scored alert jobs: id, score, title, company, location, alert keyword, one-line
reason, ⚠ history. Below it, one line with what was dropped (e.g. "9 filtered: 5 language, 2 non-tech, 2 years;
4 removed by LinkedIn; 6 already seen in the daily scan" — list already-applied ones by name).
Then ask which to apply to (AskUserQuestion, multiSelect, best 4 as options) and mark them
`uv run jobhunt status <ids…> --set shortlisted`; applying follows `/tailor-cv` and the `/apply` skill. LinkedIn
Easy Apply-only jobs are never automated: mark them `manual` and say so.

## Tuning the alerts
If many alert jobs get filtered for the same reason (e.g. lots of senior or other-language roles from one keyword),
suggest to the user which LinkedIn alert to narrow or delete; the keyword per job is in the `alert` field.
