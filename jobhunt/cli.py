"""Command line entry point. Output is JSON so Claude Code can parse it reliably."""

import argparse
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

from . import alerts, cvtailor, formfill, history, jobs, liveness, pipeline, prescore, qa, queue, report, settings
from .db import ROOT, connect, set_meta


def _print(data) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def cmd_qa(args: argparse.Namespace) -> None:
    conn = connect()
    if args.qa_cmd == "lookup":
        _print(qa.lookup(conn, args.question, company=args.company))
    elif args.qa_cmd == "save":
        tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
        qa_id = qa.save(conn, args.question, args.answer, args.type, tags, args.company)
        _print({"saved": qa_id})
    elif args.qa_cmd == "variant":
        qa.add_variant(conn, args.id, args.phrasing)
        _print({"ok": True})
    elif args.qa_cmd == "delete":
        qa.delete(conn, args.id)
        _print({"deleted": args.id})
    elif args.qa_cmd == "list":
        _print(qa.list_all(conn, tag=args.tag))


def cmd_cv(args: argparse.Namespace) -> None:
    conn = connect()
    if args.cv_cmd == "plan":
        _print(cvtailor.plan(conn, jobs.get(conn, args.id), cv=args.cv))
    elif args.cv_cmd == "build":
        _print(cvtailor.build(conn, jobs.get(conn, args.id), cv=args.cv))
    elif args.cv_cmd == "fact-save":
        _print(cvtailor.save_fact(conn, args.skill, args.status, display=args.display,
                                  level=args.level, section=args.section))
    elif args.cv_cmd == "fact-list":
        _print(cvtailor.list_facts(conn))


SCRAPE_LOCK = ROOT / "data" / "scrape.lock"


def _lock_holder() -> int | None:
    """PID of a live scrape, or None. A stale lock (process gone) is ignored."""
    try:
        pid = int(SCRAPE_LOCK.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _refuse_during_scrape(command: str) -> None:
    """Writers must not run while a scrape is still writing (an ingest during a long scrape mixes both runs)."""
    pid = _lock_holder()
    if pid:
        _print({"error": f"{command} refused: scrape pid {pid} is still running; wait for it to finish", "pid": pid})
        raise SystemExit(2)


def cmd_scrape(args: argparse.Namespace) -> None:
    sources = tuple(args.source) if args.source else pipeline.ALL_SOURCES
    if _lock_holder():
        _refuse_during_scrape("scrape")
    SCRAPE_LOCK.write_text(str(os.getpid()))
    try:
        conn = connect()
        started = time.time()
        hours = args.hours or pipeline.window_hours(conn, pipeline.SCRAPE_META)
        out = pipeline.scrape(conn, sources, hours=hours, max_years=args.max_years, mode=args.mode,
                              enrich_limit=args.enrich_limit).to_dict()
        # Only a full scan moves the window start; a one-source run must not hide the others' gap.
        if not args.source:
            set_meta(conn, pipeline.SCRAPE_META, str(started))
        _print({"window_hours": hours, **out})
    finally:
        SCRAPE_LOCK.unlink(missing_ok=True)


def cmd_history(args: argparse.Namespace) -> None:
    conn = connect()
    if args.history_cmd == "import":
        _print(history.import_rows(conn, history.load_rows(args.file)))
    elif args.history_cmd == "match":
        _print(history.annotate(conn, tuple(args.status.split(","))))
    elif args.history_cmd == "show":
        _print(history.for_company(conn, args.company, args.title or ""))
    elif args.history_cmd == "query":
        print(history.GMAIL_QUERY if not args.days else history.GMAIL_QUERY.replace("newer_than:2y", f"newer_than:{args.days}d"))
    elif args.history_cmd == "stats":
        _print(history.counts(conn))


def cmd_alerts(args: argparse.Namespace) -> None:
    if args.alerts_cmd == "query":
        _print(alerts.query(connect(), args.days))
    elif args.alerts_cmd == "import":
        _print(alerts.run(connect(), Path(args.file), days=args.days, max_years=args.max_years))


def cmd_enrich(args: argparse.Namespace) -> None:
    _refuse_during_scrape("enrich")
    _print(pipeline.enrich_missing(connect(), limit=args.limit))


def cmd_prescore(args: argparse.Namespace) -> None:
    conn = connect()
    out = prescore.run(conn, tuple(args.status.split(",")))
    if args.auto_low_below is not None:
        out["auto_low"] = prescore.auto_low(conn, args.auto_low_below)
    _print(out)


def cmd_formfill(args: argparse.Namespace) -> None:
    message = Path(args.message_file).read_text() if args.message_file else ""
    path = formfill.write_script(connect(), args.ats, args.id, message, args.source or "", args.cv, args.force)
    _print({"script": str(path), "run_with": "mcp__playwright__browser_run_code_unsafe(filename=<script>)"})


def cmd_verify(args: argparse.Namespace) -> None:
    statuses = tuple(args.status.split(","))
    _print(liveness.verify(connect(), statuses=statuses, ids=args.ids or None))


def cmd_browser_script(args: argparse.Namespace) -> None:
    """Fill a browser script's placeholders and write the bare function to data/inbox/<name>-fn.js: pass that file's
    content to browser_evaluate as `function` (with `filename:` for the output). No more hand-editing __URLS__."""
    from .db import ROOT
    src = (ROOT / "browser" / f"{args.name.replace('-', '_')}.js").read_text()
    info: dict = {}
    if "__CITY__" in src:
        src = src.replace("__CITY__", json.dumps(pipeline.pracuj_city()))
        info["city"] = pipeline.pracuj_city()
    if "__KEYWORDS__" in src:
        src = src.replace("__KEYWORDS__", json.dumps(pipeline.pracuj_keywords(), ensure_ascii=False))
    if "__HOURS__" in src:
        hours = args.hours or pipeline.pracuj_window_hours(connect())
        src = src.replace("__HOURS__", str(hours))
        info["hours"] = hours
    if "__URLS__" in src:
        conn = connect()
        urls = [r["url"] for r in conn.execute(
            "SELECT url FROM jobs WHERE status = 'new' AND source = 'pracuj' AND COALESCE(employer_url, '') = '' "
            "ORDER BY id LIMIT ?", (args.limit,))]
        src = src.replace("__URLS__", json.dumps(urls))
        info["urls"] = len(urls)
    body = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("//")).strip()
    out = ROOT / "data" / "inbox" / f"{args.name}-fn.js"
    out.write_text(body + "\n")
    _print({"script": str(out), **info})


def cmd_update_details(args: argparse.Namespace) -> None:
    _print(pipeline.update_details(connect(), Path(args.file)))


def cmd_refilter(args: argparse.Namespace) -> None:
    _refuse_during_scrape("refilter")
    _print(pipeline.refilter(connect(), hours=args.hours, max_years=args.max_years, mode=args.mode,
                             all_roles=args.all_roles))


def cmd_resolve(args: argparse.Namespace) -> None:
    from . import resolve
    _print(resolve.resolve(connect(), ids=args.ids or None, min_score=args.min_score))


def cmd_set_employer(args: argparse.Namespace) -> None:
    from . import resolve
    _print(resolve.set_employer(connect(), args.id, args.url))


def cmd_cover(args: argparse.Namespace) -> None:
    from . import cover
    from .jobs import get
    body = Path(args.text_file).read_text()
    conn = connect()
    # Terms the profile doesn't back up: fix the letter or confirm the fact with the user before attaching it.
    confirmed = {f["skill"] for f in cvtailor.list_facts(conn) if f["status"] == "yes"}
    confirmed |= {(f["display"] or "").lower() for f in cvtailor.list_facts(conn) if f["status"] == "yes"}
    unverified = cover.check_claims(body, get(conn, args.id), confirmed=confirmed)
    _print({"pdf": str(cover.render(conn, args.id, body)), "unverified": unverified})


def cmd_mail(args: argparse.Namespace) -> None:
    from . import mailcheck
    if args.mail_cmd == "query":
        _print({"query": mailcheck.QUERY, "url": mailcheck.gmail_url()})
    elif args.mail_cmd == "plan":
        _print(mailcheck.run(connect(), mailcheck.load_rows(args.files), dry_run=not args.apply))
    elif args.mail_cmd == "mark-script":
        ids = (args.ids or (json.loads(Path(args.ids_file).read_text()) if args.ids_file else None)
               or json.loads(Path(args.from_log).read_text())["mark_read"])
        _print({"script": str(mailcheck.mark_script(ids)), "threads": len(ids)})


def cmd_dedupe(args: argparse.Namespace) -> None:
    _print(pipeline.dedupe_existing(connect(), dry_run=args.dry_run))


def cmd_ingest(args: argparse.Namespace) -> None:
    _refuse_during_scrape("ingest")
    _print(pipeline.ingest(connect(), Path(args.file), hours=args.hours, max_years=args.max_years, mode=args.mode).to_dict())


def cmd_list(args: argparse.Namespace) -> None:
    _print(jobs.list_jobs(connect(), args.status.split(","), limit=args.limit, min_score=args.min_score, order=args.order,
                          desc_chars=None if args.full_description else 1500))


def cmd_show(args: argparse.Namespace) -> None:
    _print(jobs.get(connect(), args.id))


def cmd_score(args: argparse.Namespace) -> None:
    scores = json.loads(Path(args.file).read_text())
    _print({"scored": jobs.set_scores(connect(), scores)})


def cmd_status(args: argparse.Namespace) -> None:
    conn = connect()
    if args.ids:
        jobs.set_status(conn, args.ids, args.set, note=args.note)
    _print(jobs.counts(conn))


def cmd_queue(args: argparse.Namespace) -> None:
    if args.json:
        _print(queue.batch(queue.connect_ro()))
        return
    if args.md:
        sys.stdout.write(queue.markdown(queue.batch(queue.connect_ro())))
        return
    while True:
        text = queue.render(queue.batch(queue.connect_ro()))
        if not args.watch:
            sys.stdout.write(text)
            return
        sys.stdout.write("\033[H\033[2J" + text + f"\n(refreshes every {args.watch} s, Ctrl+C to stop)\n")
        sys.stdout.flush()
        try:
            time.sleep(args.watch)
        except KeyboardInterrupt:
            return


def cmd_applied(args: argparse.Namespace) -> None:
    data = json.loads(Path(args.file).read_text())
    app_id = jobs.record_application(connect(), args.id, args.ats, data.get("answers", {}),
                                     data.get("cover_letter", ""), data.get("notes", ""))
    _print({"application": app_id, "job": args.id, "status": "applied"})


def cmd_report(args: argparse.Namespace) -> None:
    _print({"report": str(report.build(connect(), min_score=args.min_score))})


def cmd_init(args: argparse.Namespace) -> None:
    from . import doctor
    _print(doctor.init())


def cmd_settings(args: argparse.Namespace) -> None:
    s = settings.get()
    known = sorted({*settings.GLOBAL_SOURCES, *(x for v in settings.COUNTRY_SOURCES.values() for x in v)})
    _print({**dataclasses.asdict(s), "configured": s.configured, "enabled_sources": [x for x in known if s.enabled(x)],
            "profile": str(settings.PROFILE)})


def cmd_watchlist(args: argparse.Namespace) -> None:
    from .sources import ats_boards
    from .sources.base import Http
    companies = pipeline._companies()
    wanted = {n.lower() for n in args.names}
    if wanted:
        companies = [c for c in companies if str(c.get("name", "")).lower() in wanted]
    http = Http()
    try:
        rows = ats_boards.check(http, companies)
    finally:
        http.client.close()
    found = {str(c.get("name", "")).lower() for c in companies}
    _print({"companies": rows, "not_in_watchlist": sorted(wanted - found)})


def cmd_doctor(args: argparse.Namespace) -> None:
    from . import doctor
    text, ok = doctor.run(export_test=args.export_test)
    sys.stdout.write(text)
    if not ok:
        raise SystemExit(1)


def cmd_secret(args: argparse.Namespace) -> None:
    """Never prints the password: copy says how many characters went to the clipboard, check says set + length."""
    from . import secret
    if args.secret_cmd == "set":
        print(f"saved ({secret.prompt_and_set()} characters) under {secret.service()!r}")
    elif args.secret_cmd == "copy":
        print(f"copied {secret.copy(args.max, args.suffix)} characters")
    elif args.secret_cmd == "clear":
        secret.clear()
        print("clipboard cleared")
    elif args.secret_cmd == "check":
        _print(secret.check())


CV_HELP = "main (personal.cv_file) or a personal.cv_variants name from the profile"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobhunt")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="first run: profile/profile.yaml from the example, data folders, database")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("settings", help="the effective search settings (profile `search:` section) as JSON")
    p.set_defaults(func=cmd_settings)
    p = sub.add_parser("doctor", help="check the setup; one line per check with a fix (exit 1 if a required one fails)")
    p.add_argument("--export-test", action="store_true",
                   help="also export a test page to PDF with Word / LibreOffice (opens Word on macOS)")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("watchlist", help="check that companies.yaml boards load: jobs found per company, or the error")
    p.add_argument("names", nargs="*", help="only these companies (as named in companies.yaml)")
    p.set_defaults(func=cmd_watchlist)

    p_s = sub.add_parser("secret", help="default job-site password in the OS keychain (never printed)")
    s_sub = p_s.add_subparsers(dest="secret_cmd", required=True)
    s_sub.add_parser("set", help="store it (asks twice; run in your own terminal)")
    q = s_sub.add_parser("copy", help="put it on the clipboard for Ctrl/Cmd+V; prints only the length")
    q.add_argument("--max", type=int, help="sites with a length cap: cut to N characters (the suffix included)")
    q.add_argument("--suffix", default="", help="sites wanting another symbol: append this")
    s_sub.add_parser("clear", help="empty the clipboard")
    s_sub.add_parser("check", help="whether it is set, and its length")
    p_s.set_defaults(func=cmd_secret)

    p_qa = sub.add_parser("qa", help="answer memory")
    qa_sub = p_qa.add_subparsers(dest="qa_cmd", required=True)

    p = qa_sub.add_parser("lookup", help="find stored answers for a question")
    p.add_argument("question")
    p.add_argument("--company")

    p = qa_sub.add_parser("save", help="store or overwrite an answer")
    p.add_argument("question")
    p.add_argument("answer")
    p.add_argument("--type", default="text", choices=["text", "yesno", "number", "choice"])
    p.add_argument("--tags", help="comma separated, e.g. skill,work-auth")
    p.add_argument("--company", help="only use this answer for this company")

    p = qa_sub.add_parser("variant", help="attach a new phrasing to an existing answer")
    p.add_argument("id", type=int)
    p.add_argument("phrasing")

    p = qa_sub.add_parser("delete", help="remove a saved answer by id")
    p.add_argument("id", type=int)

    p = qa_sub.add_parser("list", help="every saved answer (optionally one tag)")
    p.add_argument("--tag")

    p_qa.set_defaults(func=cmd_qa)

    p_cv = sub.add_parser("cv", help="tailored CV: skill facts and per-job CV copies")
    cv_sub = p_cv.add_subparsers(dest="cv_cmd", required=True)
    p = cv_sub.add_parser("plan", help="which of the job's skills are on the CV, confirmed, denied or unknown")
    p.add_argument("id", type=int)
    p.add_argument("--cv", default="main", help=CV_HELP)
    p = cv_sub.add_parser("build", help="write a per-job CV copy (refuses unless the source CV is present)")
    p.add_argument("id", type=int)
    p.add_argument("--cv", default="main", help=CV_HELP)
    p = cv_sub.add_parser("fact-save", help="remember a yes/no skill answer (asked once)")
    p.add_argument("skill")
    p.add_argument("--status", required=True, choices=list(cvtailor.STATUSES))
    p.add_argument("--display", help="how it is written on the CV, e.g. 'Java'")
    p.add_argument("--level", choices=list(cvtailor.LEVELS))
    p.add_argument("--section", help="Skills line key from `cv plan` (sections), e.g. 'backend'")
    cv_sub.add_parser("fact-list", help="all remembered skill answers")
    p_cv.set_defaults(func=cmd_cv)

    p = sub.add_parser("scrape", help="fetch, prefilter and store new jobs")
    p.add_argument("--source", action="append", choices=pipeline.ALL_SOURCES)
    p.add_argument("--hours", type=int, help="max posting age (default: since the last full scan + 2 h, min 24)")
    p.add_argument("--max-years", type=int, help="drop jobs requiring more years (default: settings max_years; narrow: at most 2)")
    p.add_argument("--mode", choices=["full", "narrow"], default="full",
                   help="full: every listing in the window, filter ourselves; narrow: intern/junior searches only")
    p.add_argument("--enrich-limit", type=int, default=300, help="max detail pages to fetch per run")
    p.set_defaults(func=cmd_scrape)

    p_h = sub.add_parser("history", help="application history imported from Gmail")
    h_sub = p_h.add_subparsers(dest="history_cmd", required=True)
    p = h_sub.add_parser("import", help="import rows saved by browser/gmail_rows.js")
    p.add_argument("file")
    p = h_sub.add_parser("match", help="annotate jobs with prior applications/tests/interviews")
    p.add_argument("--status", default="new,scored,shortlisted")
    p = h_sub.add_parser("show", help="history for one company")
    p.add_argument("company")
    p.add_argument("--title")
    p = h_sub.add_parser("query", help="print the Gmail search query to run")
    p.add_argument("--days", type=int, help="incremental: only the last N days")
    h_sub.add_parser("stats", help="imported history rows per kind (applied, rejected, test...)")
    p_h.set_defaults(func=cmd_history)

    p_a = sub.add_parser("alerts", help="LinkedIn job-alert emails from Gmail as a job source")
    a_sub = p_a.add_subparsers(dest="alerts_cmd", required=True)
    p = a_sub.add_parser("query", help="print the Gmail search (and URL) for alert emails since the last import")
    p.add_argument("--days", type=int, help="override: the last N days instead of 'since the last import'")
    p = a_sub.add_parser("import", help="load browser/linkedin_alerts.js output: enrich, prefilter, pre-score")
    p.add_argument("file")
    p.add_argument("--days", type=int, help="posting-age window; default: days since the last import")
    p.add_argument("--max-years", type=int)
    p_a.set_defaults(func=cmd_alerts)

    p = sub.add_parser("enrich", help="fetch missing descriptions for new jobs and re-check language/years rules")
    p.add_argument("--limit", type=int, default=300)
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser("prescore", help="cheap 0-100 ranking of unscored jobs; optionally auto-mark the tail low")
    p.add_argument("--status", default="new")
    p.add_argument("--auto-low-below", type=int, help="mark status=new jobs under this pre-score as scored-low")
    p.set_defaults(func=cmd_prescore)

    p = sub.add_parser("formfill", help="write a one-shot Playwright fill script for an ATS form")
    p.add_argument("ats", help="smartrecruiters | workday | successfactors")
    p.add_argument("id", type=int)
    p.add_argument("--message-file", help="cover message text file")
    p.add_argument("--source", help="'how did you hear' label override")
    p.add_argument("--cv", default="main", help=CV_HELP)
    p.add_argument("--force", action="store_true",
                   help="write it although the job record has no link to this ATS (form on a custom domain)")
    p.set_defaults(func=cmd_formfill)

    p = sub.add_parser("verify", help="check jobs are still open on the board AND the employer ATS")
    p.add_argument("ids", type=int, nargs="*")
    p.add_argument("--status", default="new,scored,shortlisted")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("browser-script", help="fill a browser/*.js script (pracuj-full: --hours; pracuj-details: "
                                              "new Pracuj jobs without employer link) -> data/inbox/<name>-fn.js")
    p.add_argument("name", choices=["pracuj-full", "pracuj-details"])
    p.add_argument("--hours", type=int, help="pracuj-full window (default: since the last Pracuj ingest + 2 h, min 24)")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_browser_script)
    p = sub.add_parser("update-details", help="merge employer_url/expires_at JSON by (source, source_id)")
    p.add_argument("file")
    p.set_defaults(func=cmd_update_details)

    p = sub.add_parser("refilter", help="re-run current filters on filtered jobs (after rule/window changes)")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--max-years", type=int)
    p.add_argument("--mode", choices=["full", "narrow"], default="full")
    p.add_argument("--all-roles", action="store_true",
                   help="apply --hours to every job, not only internship/graduate programmes")
    p.set_defaults(func=cmd_refilter)

    p = sub.add_parser("resolve", help="find the employer's own application page (employer_url) for aggregator jobs")
    p.add_argument("ids", nargs="*", type=int)
    p.add_argument("--min-score", type=int, default=55)
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("set-employer", help="store an employer application URL found by hand")
    p.add_argument("id", type=int)
    p.add_argument("url")
    p.set_defaults(func=cmd_set_employer)

    p = sub.add_parser("cover", help="render a per-job cover letter PDF from a text file")
    p.add_argument("id", type=int)
    p.add_argument("--text-file", required=True)
    p.set_defaults(func=cmd_cover)

    p = sub.add_parser("mail", help="/mail-check: sort unread Gmail, mark noise read, keep what needs the user")
    m_sub = p.add_subparsers(dest="mail_cmd", required=True)
    m_sub.add_parser("query", help="the Gmail search (is:unread) and its URL")
    q = m_sub.add_parser("plan", help="classify gmail_rows.js output; --apply records history + status sync")
    q.add_argument("files", nargs="+")
    q.add_argument("--apply", action="store_true", help="not a dry run: import job mail into history, sync statuses")
    q = m_sub.add_parser("mark-script", help="write the mark-as-read browser script for these thread ids")
    q.add_argument("ids", nargs="*")
    q.add_argument("--ids-file", help="JSON list of thread ids (Claude's final list)")
    q.add_argument("--from-log", help="mailcheck-<date>.json: use its mark_read list")
    p.set_defaults(func=cmd_mail)

    p = sub.add_parser("dedupe", help="mark stored duplicates (same board id or company+title) as filtered")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_dedupe)

    p = sub.add_parser("ingest", help="load jobs from a JSON file (browser scrapes, other job sources)")
    p.add_argument("file")
    p.add_argument("--hours", type=int, help="age window; default: the file's window_hours, else 24")
    p.add_argument("--max-years", type=int)
    p.add_argument("--mode", choices=["full", "narrow"], default="full")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("list", help="jobs as compact JSON")
    p.add_argument("--status", default="new", help="comma separated")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--min-score", type=int)
    p.add_argument("--order", choices=["score", "prescore"], default="score")
    p.add_argument("--full-description", action="store_true", help="do not cut descriptions at 1500 chars")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="one job with full description")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("score", help="write scores from a JSON file [{id, score, tier, matched, missing, red_flags, reason}]")
    p.add_argument("file")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("status", help="counts per status; with ids + --set, change status")
    p.add_argument("ids", type=int, nargs="*")
    p.add_argument("--set", choices=jobs.STATUSES)
    p.add_argument("--note", help="shown next to the job in `jobhunt queue` (what is happening / why skipped)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("queue", help="apply queue of the latest shortlist (read-only, safe while /apply runs)")
    p.add_argument("--watch", type=int, nargs="?", const=5, default=0, metavar="SECONDS",
                   help="redraw every SECONDS (default 5) until Ctrl+C")
    p.add_argument("--json", action="store_true")
    p.add_argument("--md", action="store_true", help="markdown table with status icons (what /shortlist shows)")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("applied", help="record a submitted application from JSON {answers, cover_letter, notes}")
    p.add_argument("id", type=int)
    p.add_argument("file")
    p.add_argument("--ats", required=True, help="e.g. smartrecruiters, greenhouse, pracuj")
    p.set_defaults(func=cmd_applied)

    p = sub.add_parser("report", help="write ranked HTML report")
    p.add_argument("--min-score", type=int, default=0)
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
