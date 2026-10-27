---
name: shortlist
description: Show the apply queue of the latest shortlist — which jobs are applied, being applied now, waiting for the user, still in the queue, done manually or skipped. Use when the user says "shortlist", "queue", "where are we", "what's applied", or runs /shortlist.
---

# Shortlist (apply queue)

Read-only status view. It changes nothing, opens no browser, and never interrupts an `/apply` run.

1. Run `uv run jobhunt queue --md` from the project root.
2. Reply with its output exactly as printed: plain markdown, NOT inside a code block, so it renders as a table.
   Add nothing else except, when a row is ✋ Needs you, one line naming what that job needs from the user.

Status icons (statuses come from `/apply`, which updates them as it works; rows are in this order):
- 🔄 **Applying now** (`applying`): the job `/apply` is filling right now.
- ✋ **Needs you** (`needs_input`): Submit approval, a CAPTCHA, an unknown answer.
- ⏳ **In queue** (`shortlisted`): not started; in the order `/apply` takes them (score).
- ✅ **Applied** (`applied`); later ⭐ Interview / ⛔ Rejected / 🎉 Offer from `/mail-check`.
- 👤 **You apply** (`manual`): LinkedIn Easy Apply, blocked forms.
- ❌ **Skipped / Closed** (`skipped`, `closed`): with the reason in Note.

The batch is the latest shortlist: everything shortlisted within 12 h before the oldest job still pending (or before
the newest shortlisting when nothing is pending). Empty queue → suggest `/find-jobs` or `/linkedin-alerts`.

Live view without touching the Claude session: in another terminal tab run
`cd <project root> && uv run jobhunt queue --watch` (same table as plain text, redraws every 5 s; Ctrl+C stops).
