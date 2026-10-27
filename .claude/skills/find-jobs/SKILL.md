---
name: find-jobs
description: Daily job hunt — scrape all job boards, prefilter, score every new job against the user's profile, and produce a ranked report. Use when the user says "find jobs", "what's new today", "run the search", or similar.
---

# Find jobs

Run from the project root. All `jobhunt` commands print JSON.

## 0. Settings
`uv run jobhunt settings` → country, city, languages, remote, max_years, keywords, sources, job_email. No
`profile/profile.yaml`, or no country/city → tell the user to run /setup first and stop. Every filter below works
per these settings; `uv run jobhunt doctor` explains anything missing.

## 1. Collect (full mode = default)

Full mode fetches EVERY listing posted in the window and filters it ourselves (no intern/junior keyword or level
filters at the source). The narrow search is still available with `--mode narrow` if the user wants it.

1. **HTTP sources**: `uv run jobhunt scrape` fetches every enabled source (settings `sources`; empty = LinkedIn +
   company career boards + the boards known for your country):
   - LinkedIn guest search with NO keyword for your city + the whole country. The guest search ignores the
     remote/level/job-type filters and stops at ~990 results; a search that reaches the limit is re-run per keyword
     (settings `keywords`, entry-level words first) with no time limit. `fetched["linkedin searches"]`
     `slices skipped` must stay empty.
   - Company career boards from `companies.yaml` (Workday, Greenhouse, SmartRecruiters, SuccessFactors, Oracle,
     Eightfold, Phenom and a few big-tech custom boards; fetched 8 at a time). Their jobs are the employer's own
     posting, so `employer_url` is set and `/apply` goes straight to the form.
   - Country boards, only when enabled for your country (Poland: englishjobs.pl — English-only jobs in your city +
     remote in the country, plus its whole `student` tag when settings `levels` include students; with settings
     `tech_only` also JustJoin all levels newest-first, NoFluff all pages city + remote, Bulldogjob all pages; summaries only, its links are paid redirects, so open one only for a shortlisted job).
   No `--hours`: the window is everything since the last full scan + 2 h, at least 24 h, so a skipped day is never
   lost; the output starts with `window_hours`. Takes ~15-40 minutes (a long window after a missed day can take 2 h+)
   — run it with `run_in_background` and do step 2 meanwhile (but don't write the DB concurrently: ingest after it
   ends). Detail pages are fetched for the 300 most promising survivors (`--enrich-limit`); everything gets a
   pre-score. Report `429 waits` / `429 wait minutes` if they are large.
2. **Pracuj.pl** — only when the `pracuj` source is enabled for your country (Poland). Cloudflare → browser:
   `uv run jobhunt browser-script pracuj-full` writes `data/inbox/pracuj-full-fn.js` (window since the last Pracuj
   ingest). Run that file's content with `browser_evaluate` (`function`): on an `it.pracuj.pl` tab →
   `filename: data/inbox/pracuj-full.json` (IT section; only when settings `tech_only`), and on a `www.pracuj.pl` tab →
   `filename: data/inbox/pracuj-www.json` (keyword searches over every category with settings `keywords` and `roles`). A page can only fetch its own host. After the scrape
   finished: `uv run jobhunt ingest data/inbox/pracuj-full.json` and `uv run jobhunt ingest data/inbox/pracuj-www.json`
   (each reads its `window_hours` and records its host's run).

If a source errors, report it and continue; never stop the run for one source.

4. **Older internships/graduate programmes**: `uv run jobhunt refilter --hours 168` re-checks filtered jobs with
   current rules. The longer window applies ONLY to internship/graduate/trainee/student titles; other jobs keep the
   scan window (`--all-roles` revives everything, mostly noise).
   Duplicates (same board id, or same company + title in another city/board) are dropped at scrape time as
   `duplicate of #id`; `uv run jobhunt dedupe [--dry-run]` cleans ones already stored.
   Then `uv run jobhunt enrich --limit 300`: fetches descriptions for every `new` job still missing one (refilter
   revivals, ingested browser rows) and re-runs the language/years rules, so no job reaches scoring with an empty text.
5. **Pracuj employer links** — only when `pracuj` is enabled. Pracuj shows employer ATS links only on offer pages,
   which live on `www.pracuj.pl`. Open a **`https://www.pracuj.pl/`** tab first (from `it.pracuj.pl` every fetch fails
   cross-origin), then run `uv run jobhunt browser-script pracuj-details` (every new Pracuj job without an employer
   link) and pass `data/inbox/pracuj-details-fn.js` as `function` to `browser_evaluate`,
   `filename: data/inbox/pracuj-details.json`, then `uv run jobhunt update-details data/inbox/pracuj-details.json`.
6. **Still open?** `uv run jobhunt verify` — checks the board AND the employer ATS (SmartRecruiters `active`,
   Workday canApply, Greenhouse/Lever 404, Ashby board, "expired/closed" page text). Closed jobs get status `closed`
   and are never scored. Report how many were closed and why. Default scope = new + shortlisted + scored >= 55
   (re-checking every scored job takes long and locks the DB); pass ids for anything else.
7. **Gmail history (new mail only)**: `uv run jobhunt history query --days 14` → open that Gmail search in the job
   Gmail (settings `job_email`; URL-encode the query after `https://mail.google.com/mail/u/0/#search/`, and use
   `/u/<job_email>/` if it is not the browser's first account), run `browser/gmail_rows.js`
   (`filename: data/inbox/gmail-new.json`), `uv run jobhunt history import data/inbox/gmail-new.json`,
   then `uv run jobhunt history match`. Full rebuild: `history query` without `--days`, then page through `/p2`,
   `/p3`… (one file per page, import each) until a page has fewer than 50 rows.

## 2. Score (two stages)

Full mode leaves a few hundred jobs, too many to read in detail. Stage 1 is automatic: every job has a `prescore`
(skill overlap, entry-level wording, years asked, role family, foreign-stack penalty) — see `jobhunt/prescore.py`.

Stage 2 is you. Read `profile/profile.yaml` once (skills, projects, experience, certifications) and `uv run jobhunt settings` (`roles`, `levels`),
then `uv run jobhunt list --status new --order prescore --limit 50 --full-description` (without the flag
descriptions stop at 1500 chars and long LinkedIn ads lose their requirements) and read/score the top ~40-60 in
detail (repeat with the next batch while prescores stay >= ~45). Then park the tail with an honest reason:
`uv run jobhunt prescore --auto-low-below <cut>` (marks remaining new jobs under the cut as scored-low,
reason "pre-score only"). Report how many were read in detail vs auto-parked.

**Work mode first**: hybrid/on-site outside your city is dropped automatically when the description says so (unless
settings `relocate`), and a LinkedIn job pinned to another city needs explicit remote wording (`work_mode_reason` in
prefilter.py). Remote is accepted per settings `remote` (country / region / worldwide), never when pinned to another
country or limited to its residents. If a job you are about to score medium/high is in another city and the text is
vague, check the employer's posting (Workday `remoteType`, career-site "Work mode") BEFORE scoring — never shortlist
an unverified job outside your city (unless settings `relocate`).

Roles asking up to settings `max_years` go in the normal list when the user has a realistic chance — no separate
stretch tier. Senior/lead, more than `max_years`, a required language outside settings `languages`, on-site outside
your city, non-tech titles (only when settings `tech_only`) and settings `exclude_titles` are already dropped by
the rules.

| Score | Meaning |
|---|---|
| 80–100 **high** | One of settings `roles` at one of settings `levels` (or a mid role within `max_years` that fits the stack); most required skills are in the profile; required languages are all in settings `languages`; no hard blockers. Realistic interview chance. |
| 55–79 **medium** | Entry level but the stack is partly different from the profile's, or the role is adjacent to settings `roles`. Worth applying. |
| 0–54 **low** | Hidden blockers: a language outside settings `languages` required, more experience than the user has, a completed degree required while the profile's education is still in progress (or enrolment required while it is completed), mid-level in disguise, a role far from settings `roles`, on-site outside your city (unless settings `relocate`). |

Student roles: when settings `levels` include student or working student, these roles get a prescore bonus
("student role"); read them all in stage 2. Student roles need enrolment: a user whose education is completed
can't take them, so score those low.

Strong positives: the profile's skills at level working/strong, the stacks of its projects and experience, its
certifications, and settings `roles`. A skill the profile doesn't list is a gap, never a match.

Write scores to `data/inbox/scores.json` as
`[{"id": 12, "score": 84, "tier": "high", "matched": ["Python","Django"], "missing": ["Kubernetes"], "red_flags": [], "reason": "one sentence why"}]`
and run `uv run jobhunt score data/inbox/scores.json`. Batches of ~30 are fine.

**Salary never counts**: low, unpaid or unlisted pay is fine — never a red flag or a lower score. Judge seniority
from years/level, not from the pay. Saved amounts are only for "expected salary" form fields.

Be honest: inflated scores waste the user's applications. A reason must name the concrete fit or blocker.

`history_note` / `history_warn` from step 7 must shape the score: same role already applied or a test/interview
pending → put it in `red_flags` and tell the user (they may need to finish a test instead of re-applying; some ATS
block a re-application while one is pending). Recent rejections from the company → mention them.

**Employer links**: after scoring run `uv run jobhunt resolve` (all shortlisted + scored >= 55 without an employer
link, skipping jobs it already failed on in the last 7 days; pass ids to force a retry). It checks the companies.yaml
watchlist and probes Greenhouse/Lever/Ashby/SmartRecruiters/Workable/Recruitee by company slug. For each
`unresolved` medium/high job, use WebSearch `"<title>" "<company>"` (+ "myworkdayjobs", "smartrecruiters",
"successfactors", "icims", or aggregator pages with a "View original posting" link), then save it:
`uv run jobhunt set-employer <id> <url>`. Note in the report which jobs have no employer link (LinkedIn-only).

**Company-page check before any shortlist question:** for every job you are about to offer (medium/high, or any id
list the user asks to apply to), first `uv run jobhunt resolve <ids…>` (WebSearch the unresolved ones as above),
then `uv run jobhunt verify <ids…>`. `verify` reads the employer's own posting (Workday/Greenhouse/Lever/
SmartRecruiters API or the page) and stores `employer_note`, e.g. "company page: 3+ years, German required". Board
texts often differ from the employer's, and many jobs otherwise fall out only at apply time (closed, no company form,
language required, duplicate, already applied, more years). So, before asking:
- drop closed jobs; `verify` returns `easy_apply` (LinkedIn guest page shows Easy Apply, no employer link) and
  stores the note "LinkedIn Easy Apply only": search the company's careers site once (see /apply "LinkedIn-only
  jobs often have an employer posting"); nothing found → mark `manual` BEFORE the shortlist question and say so
  (Easy Apply is never automated). `apply_unknown` = LinkedIn showed no Apply button and no employer link is known:
  treat it like `easy_apply`;
- show `employer_note` and `history_note` in the table; more years on the company page than the user has → say it
  plainly and recommend skipping (the user decides); a required language outside settings `languages` → `skipped`
  with that note (a settings rule) and say so;
- a company page you could not read → write "company page not checked" in the row.

## 3. Report

1. `uv run jobhunt report` → `reports/<date>.html`; open it (`open <path>` on macOS, `xdg-open` on Linux,
   `start` on Windows).
2. In chat, show the top ~10 as a compact table: id, score, title, company, one-line reason, and a ⚠ history column.
3. Ask which to apply to (AskUserQuestion, multiSelect, best 4 high-tier as options; user can type other ids).
   Mark chosen ones: `uv run jobhunt status <ids...> --set shortlisted`; mark ones the user rejects `--set skipped`.
4. Suggest `/tailor-cv`, then `/apply <id>` for the shortlisted ones. LinkedIn Easy Apply jobs: the user applies
   manually — offer an answers sheet.
