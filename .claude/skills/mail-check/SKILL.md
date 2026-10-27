---
name: mail-check
description: Sort the unread inbox of the job-search Gmail (settings job_email) — mark job alerts, confirmations, account emails, rejections and promotions as read, keep interview/assessment/offer/recruiter-request emails unread and report them. Use when the user says "mail check", "check my mail", "/mail-check", or after applications to see what came back.
---

# Mail check

Run from the project root; commands print JSON. Browser: Playwright MCP, Gmail signed in as the job inbox:
`uv run jobhunt settings` → `job_email`. Empty → ask the user to run /setup first and stop. If the browser has
several Google accounts and the job Gmail is not the first, replace `/u/0/` in every Gmail URL below with
`/u/<job_email>/`.

Allowed: read any email in this inbox, mark emails as read. **Never** send, delete, archive, move, label, star or
change settings. Argument `apply` = act. The first run ever (no `data/inbox/mailcheck-*.json` log yet) is a dry run;
after the user has seen one dry run, normal runs act.

## 1. Collect every unread row
1. `uv run jobhunt mail query` → `url` (`is:unread`, whole backlog). `browser_navigate` there, wait ~4 s.
   "No messages matched" → report "inbox clean" and stop.
2. Run `browser/gmail_rows_bodies.js` with `browser_evaluate` (file content as `function`), `filename:
   data/inbox/mail-unread-p1.json`. Gmail shows 50 per page: while the page has 50 rows, open `<url>/p2`, `/p3`…
   and save `mail-unread-p2.json`…. Each row carries `body` = the newest message's text from the print view
   (fetching the print view does NOT mark anything read; LinkedIn alert digests are skipped). Classification uses
   the body, so "thank you for applying … we decided not to move forward" is a rejection, not a confirmation
   (list snippets cut off before the decision).

## 2. Classify
`uv run jobhunt mail plan data/inbox/mail-unread-p*.json` (dry run) or `... --apply` (records job mail in the Gmail
history and syncs job statuses: applied → interview / rejected / offer). Buckets:
- `keep` — interview, assessment/test, offer, "please complete/provide…", next steps, deadlines, security notices,
  fresh codes. Stays unread.
- `job_info` — application received, rejections, status updates, account created, old codes → mark read.
- `noise` — LinkedIn/board job alerts, newsletters, promotions, receipts, social → mark read.
- `unknown` — the rules know English, Polish, German, French, Spanish, Dutch, Italian and Portuguese wording; mail
  in other languages mostly lands here. YOU decide each
  one from subject + `body`, and for a rejection / interview / offer about a tracked job also set its status
  (`uv run jobhunt status <id> --set rejected|interview|offer`) (already in the rows file; `has_body` in the plan output).
  Rows without a body (fetch error): open the print view (`/mail/u/0/?ik=<GLOBALS[9]>&view=pt&search=all&th=<id>`
  via fetch — it does not mark the thread read). Put it in keep / job_info / noise. **When in doubt: keep.** Anything
  written by a real person to the user (recruiter, hiring manager) is keep unless it is a plain rejection.
Also sanity-check the rule buckets: a "keep" that is obviously a newsletter, or a "job_info" that actually asks for
something, gets moved. A rejection "valid only for this position" does not block other jobs at that company. Write
the final list of thread ids to mark (job_info + noise after your corrections) into `data/inbox/mail-mark.json` as a
JSON list.

## 3. Act (skip on dry run)
1. `uv run jobhunt mail mark-script --ids-file data/inbox/mail-mark.json` writes `data/inbox/gmail-mark-read.js`.
   (`--from-log data/inbox/mailcheck-<date>.json` uses the rule plan as is.)
2. On the `is:unread` search page: `browser_run_code_unsafe(filename=data/inbox/gmail-mark-read.js)`. It ticks only
   those threads and clicks Gmail's own "Mark as read". Marked rows vanish from `is:unread`, so reload page 1 and run
   again until it returns `marked: 0` (max ~10 rounds). `still_unread` non-empty → report it, don't force.
3. Re-run `gmail_rows.js` on `is:unread` once and confirm only `keep` threads remain.

## 4. Report (chat, compact)
- **Needs you** (keep): one line each — date, company/sender, what is asked, deadline if any, and the related job id
  (`uv run jobhunt history show "<company>"` / job list). Interviews and tests first.
- **Status changes**: from `status_changes` (e.g. "#123 <Company> → rejected").
- **Marked read**: counts per bucket + reason (e.g. "41 marked read: 18 job alerts, 9 application confirmations,
  4 account emails, 3 rejections (<companies>), 7 promotions"). Name every rejection.
- Dry run: say nothing was marked, show the full proposed lists, and ask (AskUserQuestion) whether to apply.
- The log `data/inbox/mailcheck-<date>.json` keeps every thread id that was marked read (undo: open the thread in
  Gmail and use "Mark as unread").
