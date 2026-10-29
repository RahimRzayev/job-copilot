---
name: tailor-cv
description: Build a tailored CV PDF for every shortlisted job (after the shortlist, before /apply). Asks once about each skill a shortlisted job names that the CV lacks, remembers the answers, adds confirmed skills to the matching Skills bullet, and writes one PDF per job to data/inbox/cvs/<job_id>/. Use when the user says "tailor cv", "/tailor-cv", "make the tailored CVs", or after the shortlist is set and before /apply.
---

# Tailor CV

Run from the project root. All `jobhunt` commands print JSON. Runs between the shortlist (find-jobs / linkedin-alerts)
and `/apply`. It never submits or uploads anything.

Prerequisites (`uv run jobhunt doctor` checks all of them and prints the expected paths):
- `profile/profile.yaml` (no profile → tell the user to run /setup first and stop).
- The editable CV as a DOCX in `profile/cv-source/` with the PDF's file name (`cv.pdf` → `cv.docx`; a variant may
  also sit in `profile/cv-source/<variant>/`), one per CV (`personal.cv_file` and each `personal.cv_variants` entry).
  The DOCX is never edited; copies are.
- A PDF exporter: Microsoft Word (macOS or Windows) or LibreOffice (any OS, `soffice` on the PATH). Use the program
  the original PDF came from when both exist, so fonts and layout match.
If doctor reports one missing, say what is missing and stop; `/apply` then uses the base CV. First run on this
machine: `uv run jobhunt doctor --export-test` proves Word / LibreOffice really exports before any job is built.

## 1. Shortlisted jobs
`uv run jobhunt list --status shortlisted` → the ids. If none, say so and stop.

## 2. Plan every job, collect unknown skills
For each id: `uv run jobhunt cv plan <id>`. Collect the union of `unknown` across all jobs (one entry per skill,
keep which job ids named it). `known_add`, `denied`, `on_cv` need no question.

If the union is empty, skip to step 3.

Otherwise ask every unknown skill once, in AskUserQuestion rounds of at most 4 questions each. Each question:
"<Skill>: do you know it? (asked by <job ids>)" with options "Yes, working" / "Yes, basic" / "No".
Save each answer right away:
- Yes, working → `uv run jobhunt cv fact-save "<skill>" --status yes --level working --display "<as on CV>"`
- Yes, basic → same with `--level basic`
- No → `uv run jobhunt cv fact-save "<skill>" --status no --display "<skill>"`

Never ask a skill that already has a fact (`uv run jobhunt cv fact-list`). Never change a fact without the user saying so.

## 3. Build each job's CV
For each shortlisted id: `uv run jobhunt cv build <id>`. Read the result:
- `built: true` → PDF written to `data/inbox/cvs/<id>/` under the same file name as the base CV; note `added` and
  `placed`.
  A `placed` key starting with `new:` (e.g. `new:AI tools`) is a Skills line added after the last one, because the CV
  had no line for that skill family; it is only on that job's copy.
- `reason: no confirmed skill with a bullet on this CV` → no tailored copy; the base CV is used. Not an error.
- `blocked: ...` → no file written; report the reason verbatim and use the base CV for that job.

Build one job at a time (the exporter handles one document at a time).

## 4. Check the upload path per job
For each shortlisted id, confirm the upload path is its own folder or the base CV:
`uv run python -c "from jobhunt import formfill,jobs; from jobhunt.db import connect; c=connect(); print(formfill.build_data(jobs.get(c,<id>))['resume_path'])"`
A job must never resolve to another job's folder. Check that each path contains the id it was built for.

## 5. Report (chat, compact)
One table: job id, company, title, CV uploaded (tailored or base), added skills, placement, pages. Then one line:
"Next: /apply <id> for each. Tailored CVs are per job; base CV for the rest." Do not apply anywhere in this skill.
