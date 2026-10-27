<div align="center">

# Job Copilot

**Your job hunt on autopilot, with you in charge of every Submit.**

Finds internship, graduate and junior jobs in the roles you choose every day, filters out the ones you can't take,
scores the rest against your CV, tailors your CV and cover letter, and fills the application forms for you, all from
[Claude Code](https://claude.com/claude-code), on your own machine.

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
![Claude Code](https://img.shields.io/badge/runs%20in-Claude%20Code-D97757)
![macOS | Linux | Windows](https://img.shields.io/badge/os-macOS%20%7C%20Linux%20%7C%20Windows-555)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

</div>

---

## What a day looks like

| Step | Command | What happens |
|:---:|---|---|
| 1 | `/mail-check` | Sorts your job inbox. Interview and test invites come first; confirmations and rejections update your applications. |
| 2 | `/find-jobs` | Scans LinkedIn, company career sites and local job boards, drops jobs that don't fit your rules, scores the rest and builds a shortlist. |
| 3 | `/tailor-cv` | Makes a tailored CV for each shortlisted job (only with skills you confirm you have). |
| 4 | `/apply` | Opens each application, fills every field from your profile and answer bank, and **stops for your OK before Submit**. |
| 5 | `/shortlist` | Shows the progress table any time: ✅ applied · ⏳ in queue · ✋ needs you · ❌ skipped. |

## Features

- 🔎 **Wide search.** LinkedIn (no login), the career sites of companies you pick (Workday, Greenhouse, Lever,
  SmartRecruiters, SuccessFactors…) and country-specific job boards. Duplicates across sites are merged.
- 🧹 **Your rules, applied before anything else.** The roles you want (any field, not only IT), job titles you never
  want, city, remote scope, languages you can work in, max years of experience, seniority. Jobs requiring another language or living in another country are dropped.
- 🎯 **Honest scoring.** Claude reads each remaining posting against your CV and explains the score; closed and
  already-applied jobs are flagged.
- 🧠 **Answer bank.** Every form question you answer is remembered, so you're never asked twice. Setup pre-fills
  the 50 most common ones.
- ✍️ **Tailored CV and cover letter** per job, built only from facts in your profile.
- 🤖 **Form filling** in a real browser, with ready-made scripts for the most common application systems. Account
  sign-up and sign-in use one default password from your OS keychain, pasted so Claude never sees it.
- 📬 **Inbox tracking.** Claude reads your job Gmail: confirmations, rejections, tests and interview invites update
  each application's status, and verification codes are found for you.
- 🔔 **LinkedIn alerts.** Jobs LinkedIn emails you are imported, filtered and scored like the rest.

## Safety and privacy

- **You approve every Submit.** Claude fills, reads every field back, shows you a summary and waits.
- **Nothing about you is uploaded or committed.** Your CV, profile, answers and database live in `profile/` and
  `data/`, which git ignores.
- **Facts come from you.** Claude never invents experience or skills; an unknown question is asked once and saved.
- **No LinkedIn automation.** LinkedIn is only read (public pages and alert emails); Easy Apply stays manual.
- **Your inbox is read-only.** Claude reads emails and marks them read. It never sends, deletes, archives, moves or
  labels anything.
- **CAPTCHAs are yours** to solve; Claude stops and waits.

## Requirements

| | |
|---|---|
| [Claude Code](https://claude.com/claude-code) | with a Claude plan that includes it |
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (Windows: see the uv docs) |
| [Node.js](https://nodejs.org) 18+ | runs the Playwright browser server (`npx`) |
| Google Chrome | the browser Claude drives |
| Microsoft Word **or** [LibreOffice](https://www.libreoffice.org) | optional, only for tailored CV PDFs |

## Quick start

```bash
git clone https://github.com/RahimRzayev/job-copilot.git
cd job-copilot
uv sync
claude
```

Then, inside Claude Code:

1. Approve the `playwright` MCP server when Claude Code asks (it comes with the project in `.mcp.json`).
2. Run **`/setup`**. It takes about 20 minutes, once.
3. Run **`/find-jobs`**.

### What `/setup` asks you for

- 📄 **Your CV as a PDF.** Drop it in `profile/`. Add the Word version too if you want tailored CVs.
- 🌍 **Where you search.** Country, the city where you'd work on-site, remote preference (your country, your
  region, or worldwide), relocation.
- 🎯 **The roles you want**, in your own words ("data analyst", "marketing assistant"), the levels, and job titles
  you never want. Nothing is pre-set: the search is built around your answers.
- 🗣️ **Languages you can work in** and the most years of experience a job may ask for.
- 📬 **A job-search inbox.** A separate Gmail is best: sign-ups, codes and replies go there (see below).
- 🏢 **Companies to watch** (optional). The watchlist starts empty; name the companies you want and Claude adds them.
- 🔑 **One default password** for job-site accounts, typed into your own terminal (`uv run jobhunt secret set`) and
  kept in your OS keychain.
- 📝 **About 50 common form questions** (salary, start date, work permit, notice period…). Skip any; they get asked
  when a form needs them.

Run `/setup` again any time to change something, or edit `profile/profile.yaml` directly.

## Gmail and LinkedIn alerts

Optional, but most of the tracking needs it. About 5 minutes, once.

1. **Use a Gmail only for the job search** and put it on your CV. Only Gmail works: the scripts read Gmail's web pages.
2. **Sign in once in Claude's browser.** The first time Claude opens Gmail (`/setup` step 5 or `/mail-check`), a
   Chrome window opens: sign in there yourself. The login stays in that browser profile; Claude never types your
   Google password.
3. **LinkedIn alerts:** on linkedin.com, search a role + your city, turn on **Set alert**, choose daily + email. One
   alert per role. `/linkedin-alerts` then reads the alert emails; Claude never logs in to LinkedIn.

What Claude does with the inbox:

| | |
|---|---|
| `/mail-check` | sorts unread mail: interview / test / offer emails stay unread and are reported first; job alerts, confirmations, rejections and promotions are marked read; application statuses update |
| `/apply` | finds sign-up links and verification codes while it registers on job sites |
| `/find-jobs` | checks your past applications so you don't apply twice |
| `/linkedin-alerts` | imports every job LinkedIn emailed you since the last run |
| Never | sends, deletes, archives, moves or labels anything, or changes settings |

The inbox rules know English and Polish emails; Claude sorts emails in other languages itself.

## Commands

**In Claude Code**

| Command | Use it to… |
|---|---|
| `/setup` | create or update your profile, search settings, password and common answers |
| `/find-jobs` | run the daily scan and get a ranked shortlist |
| `/linkedin-alerts` | import only the jobs LinkedIn emailed you |
| `/tailor-cv` | build tailored CVs for the shortlist |
| `/apply` | apply to the shortlist (or `/apply <job id>`) |
| `/shortlist` | see the apply queue as a table |
| `/mail-check` | sort new email and update application statuses |
| `/answer-bank` | review or correct your saved answers |

**In a terminal** (`uv run jobhunt <command>`)

| Command | |
|---|---|
| `doctor` | check that everything is set up (✅ / ❌ with fixes) |
| `settings` | show your search settings |
| `queue --watch` | live apply-queue table in a second terminal |
| `qa list` | list saved answers |
| `secret set` / `secret check` | store / check the default job-site password |

## Job sources

| Where | Sources |
|---|---|
| Everywhere | LinkedIn public job search · LinkedIn alert emails · career sites of the companies in your `companies.yaml` |
| Poland | Pracuj.pl · englishjobs.pl · for IT roles also JustJoin.it, No Fluff Jobs, Bulldogjob |

`companies.yaml` starts empty. Add the companies you want to work for: tell Claude "add <company> to my watchlist" and
it finds the career site and adds the entry (format at the top of the file).
Adding a job board for your country is a small module in `jobhunt/sources/`; see [docs/how-it-works.md](docs/how-it-works.md).

## Good to know

- **Languages:** postings written in Polish, German, French, Spanish, Dutch, Italian or Portuguese are recognised
  and dropped if you don't work in that language; for other languages, explicit "fluent X required" wording is caught.
- **Cost:** everything runs in your Claude Code session; scoring and form filling use your plan's usage.
- **Sites change.** Job boards and application systems change their pages; when a script breaks, Claude falls back
  to filling by hand and you can fix the script.
- **Be a good citizen.** Scans are paced and stop when a site rate-limits. Use it for your own job search, and check
  each site's terms.
- Not affiliated with LinkedIn, Anthropic or any job board.

## License

[MIT](LICENSE)
