# Job Application Copilot

Helps the user find and apply to the roles they chose (settings `roles`), from internship to junior level. The Python package `jobhunt` does the
deterministic work (database, scraping, filtering, form-fill scripts); Claude does the reasoning (scoring, answering
form questions, driving the browser through the Playwright MCP).

**First run:** if `profile/profile.yaml` is missing or `uv run jobhunt settings` shows no country/city, run `/setup`
before anything else.

## Hard rules
- **Never press the final Submit / Apply / Send button without the user's explicit approval in this conversation.**
  Fill everything, read every field back from the page, take a screenshot, show a summary of all answers, then wait.
- **Never invent facts** about the user. Sources of truth, in order: `profile/profile.yaml`, the answer bank
  (`uv run jobhunt qa lookup`), the user. Unknown → ask via AskUserQuestion, then `uv run jobhunt qa save` so it is
  never asked again.
- Never mark a job `skipped` or `manual` on your own judgment: show the evidence from the job's own posting and ask.
- Do not automate LinkedIn Easy Apply or log in to LinkedIn / Glassdoor. LinkedIn is read-only discovery.
- CAPTCHA → stop and ask the user to solve it.
- **Passwords** live only in the OS keychain, never in files, the database, memory or the chat. Job-site (ATS)
  accounts use the job email from settings + the default password: `uv run jobhunt secret copy`, click the password
  field, Playwright `ControlOrMeta+V`, check only the field's length, then `uv run jobhunt secret clear`. Never print
  it, never put it in a tool argument, never ask for it in chat. Prefer "Sign in with Google" when offered. The user's
  own Google / LinkedIn logins stay theirs.
- The job inbox (`job_email` in settings): Claude may read emails (list view first, the full message only when needed)
  and mark them read. Never send, delete, archive, move, label or change settings.

## Answer bank workflow
1. `uv run jobhunt qa lookup "<question as written on the form>" [--company X]` → JSON candidates with `similarity`.
2. Decide whether a candidate truly answers the question (same meaning, not just shared words). If yes, use it
   (adapt to the field type / options / length) and run `uv run jobhunt qa variant <id> "<form phrasing>"`.
3. No good candidate: ask the user, then `uv run jobhunt qa save "<question>" "<answer>" --type ... --tags ...`.
   Use `--company` only for company-specific answers ("why us").
4. Skill questions ("Do you know Docker?") → check `skills` in the profile first; save new skill facts with tag
   `skill` and offer to add them to the profile.

## Filtering policy (from `search:` in the profile, see `uv run jobhunt settings`)
- Drop jobs requiring a language the user can't work in.
- Drop on-site / hybrid jobs outside the user's city (unless `relocate`); remote follows `remote`
  (country / region / worldwide), never remote limited to another country's residents.
- Drop jobs requiring more than `max_years` of experience, and senior titles.
- Titles: drop `exclude_titles`; drop non-tech titles only when `tech_only`. Rank by `roles` and `levels`.
- Never drop or down-score on salary; the salary in settings is only typed into forms.

## Layout
- `jobhunt/settings.py` search settings; `jobhunt/db.py` schema; `jobhunt/qa.py` answer bank; `jobhunt/cli.py` CLI (JSON out).
- `jobhunt/sources/` one module per job board (LinkedIn, company career sites, and boards enabled per country);
  `jobhunt/pipeline.py` scrape → dedupe → prefilter → store; `jobhunt/prefilter.py` filter rules; `jobhunt/prescore.py`.
- `jobhunt/liveness.py` + `jobhunt verify`: is the job still open on the board and the employer's site; stores
  `employer_note` (years / language requirements from the employer's own posting); check it before shortlisting.
- `jobhunt/history.py` + `jobhunt history ...`: application history from the job inbox; flags earlier applications.
- `jobhunt/mailcheck.py` + `/mail-check`; `jobhunt/alerts.py` + `/linkedin-alerts` (LinkedIn job-alert emails).
- `jobhunt/formfill.py` + `browser/ats/*.js`: one-shot fill scripts for common ATSs (never submit).
  `jobhunt cover <id>` per-job cover letter PDF; `jobhunt/cvtailor.py` + `/tailor-cv` tailored CVs.
- `jobhunt/queue.py` + `jobhunt queue` + `/shortlist`: apply-queue table; `/apply` keeps statuses current.
- `companies.yaml` career-site watchlist (the user's own, gitignored; `jobhunt init` creates it from
  `companies.example.yaml`), empty until the user adds companies. "Add <company> to my watchlist":
  find its career-site system and slug (format at the top of the file), add the entry, then
  `uv run jobhunt watchlist "<company>"`: it must load and find jobs (0 jobs usually means a wrong slug).
- `profile/` your CV + profile (gitignored). `data/` database and scratch files (gitignored). `reports/<date>.html` ranked report.
- Skills: `/setup`, `/find-jobs`, `/linkedin-alerts`, `/tailor-cv`, `/apply`, `/shortlist`, `/answer-bank`, `/mail-check`.
- Checks: `uv run jobhunt doctor`. Tests: `uv run pytest -q`.
