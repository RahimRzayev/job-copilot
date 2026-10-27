"""/mail-check: sort the unread Gmail inbox of the job-search address (settings.job_email).

Rows come from Gmail's list view (browser/gmail_rows_bodies.js on the `is:unread` search): sender, subject, snippet,
date, thread id, and `body` = the newest message's text from the print view (reading it does not mark the thread
read). Classification uses the body when present: a ~150-char snippet can hide a rejection that opens like an
"application received" note. Every row lands in one bucket:

- keep      : needs the user (interview, assessment/test, offer, a recruiter asking something, a deadline). Stays
              unread and is listed in the report.
- job_info  : application received / rejection / status update / account created / old codes. Recorded in the
              Gmail history (and job statuses synced), then marked read. Rejections are still named in the report.
- noise     : job alerts, newsletters, promotions, receipts, social notifications. Marked read.
- unknown   : rules can't tell. Claude reads it (subject/snippet, print view if needed) and decides; default keep.

Marking read happens in the browser (browser/gmail_mark_read.js). Nothing is ever deleted, archived, moved or sent.
Every run writes data/inbox/mailcheck-<date>.json (what was marked read, with thread ids) so mistakes can be undone.
"""

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from . import history, settings
from .db import ROOT, now

QUERY = "is:unread"
LOG_DIR = ROOT / "data" / "inbox"

# Questionnaire requests, Polish ones included: "Wypełnij kwestionariusz, aby dokończyć swoją aplikację" ("fill in the
# questionnaire to finish your application") must not be marked read as a confirmation.
ASK_EXTRA = r"|(wypełnij|uzupełnij|rozwiąż) (\w+ )?(kwestionariusz|formularz|test|zadani)|dokończ\w* (\w+ )?aplikacj|" \
            r"complete (the|a|our|this|short) (\w+ )?questionnaire"
# Direct requests in German, French, Spanish, Dutch, Italian and Portuguese ("please send / could you / missing
# documents / complete your application / fill in the questionnaire"). The "do not reply" footers ("Bitte antworten Sie
# nicht", "Veuillez ne pas répondre", "Ti preghiamo di non rispondere", "Por favor, não responda") are excluded.
ASK_INTL = (
    r"|bitte (\w+ )?(füllen|senden|schicken|laden|reichen|ergänzen|bestätigen|antworten|wählen|vervollständigen|"
    r"beantworten|teilen) (sie|du)\b(?! (nicht|uns nicht))|bitte melden sie sich|melden sie sich bitte|"
    r"(könnten|können) (sie|du) (uns |mir )?(bitte )?(\w+ ){0,4}(mitteilen|senden|schicken|bestätigen|nennen|zusenden|"
    r"zukommen lassen)|rückfrage|fehlende (unterlagen|angaben|informationen|dokumente)|unterlagen (nachreichen|fehlen)|"
    r"vervollständigen sie (ihre|deine) bewerbung|fragebogen (aus)?(zu )?füllen|"
    r"merci de (bien vouloir )?(compléter|remplir|nous (envoyer|transmettre|faire parvenir|indiquer|confirmer|"
    r"communiquer)|répondre|choisir|confirmer|sélectionner)|veuillez (\w+ )?(compléter|remplir|nous (envoyer|transmettre|"
    r"faire parvenir|indiquer|confirmer|communiquer)|choisir|sélectionner|confirmer|télécharger)|pourriez-vous|"
    r"pouvez-vous (nous )?(\w+ )?(envoyer|transmettre|indiquer|confirmer|communiquer)|(documents?|pièces?) manquante?s?|"
    r"informations? manquantes?|compl[ée]te[rz]? (votre candidature|le questionnaire|ce questionnaire|notre questionnaire)|"
    r"por favor,? (completa|complete|env[ií]a|env[ií]e|env[ií]anos|confirma|confirme|responde|responda|elige|elija|"
    r"selecciona|seleccione|rellena|rellene|sube|suba|ind[ií]canos|ind[ií]quenos)\b|(podr[ií]as|podr[ií]a|puedes|puede) "
    r"(usted )?(\w+ )?(indicarnos|enviarnos|confirmarnos|decirnos|compartirnos)|documentos? (faltantes|pendientes)|"
    r"informaci[oó]n (faltante|pendiente)|completa (tu|su) (candidatura|solicitud)|completa (el|este|nuestro) cuestionario|"
    r"(wil|kun|kunt|zou|wilt) (je|jij|u) (\w+ ){0,5}(invullen|sturen|opsturen|toesturen|bevestigen|aanleveren|laten weten|"
    r"reageren|kiezen|uploaden|doorgeven)|gelieve (\w+ )?(in te vullen|te sturen|te bevestigen|te reageren|te kiezen|"
    r"te uploaden)|ontbrekende (documenten|gegevens|informatie|stukken)|rond (je|uw) sollicitatie af|"
    r"vragenlijst in te vullen|vul (de|deze|onze) vragenlijst in|"
    r"(per favore|ti preghiamo di|la preghiamo di|si prega di|ti chiediamo di|le chiediamo di),? (?!non\b)(\w+ )?"
    r"(compila|completa|compilare|completare|invia|inviare|inviarci|conferma|confermare|rispondi|rispondere|scegli|"
    r"scegliere|seleziona|selezionare|carica|caricare|indica|indicarci)\b|potresti (\w+ )?(inviarci|indicarci|"
    r"confermarci|comunicarci|dirci)|potrebbe (\w+ )?(inviarci|indicarci|confermarci|comunicarci)|documenti mancanti|"
    r"informazioni mancanti|completa la tua candidatura|compila (il|questo|il nostro) questionario|"
    r"(por favor|pedimos que|solicitamos que),? (?!(n[aã]o|no)\b)(\w+ )?(preencha|complete|envie|nos envie|confirme|"
    r"responda|escolha|selecione|carregue|indique|informe)\b|(poderia|podes|pode) (\w+ )?(nos )?(enviar|informar|confirmar|"
    r"indicar|dizer)-?(nos)?|documentos? (pendentes|em falta|faltantes)|informa[cç][oõ]es (pendentes|em falta)|"
    r"(conclua|finalize) (a )?(sua|tua) candidatura|preencha (o|este|nosso) question[aá]rio|"
    r"date limite|fecha l[ií]mite|uiterste datum|termine ultimo|entro e non oltre|prazo (final|limite)|data limite")
# Interview, test, offer, invitation and "next steps" wording in the same languages (the "keep" side).
KEEP_INTL = history.wordstart(history.INTERVIEW_INTL + history.TEST_INTL + (
    r"|einladung|nächste[nr]? schritte?|"
    r"(haben sie|hast du) (noch )?interesse|freuen uns auf (ihre|deine) (antwort|rückmeldung)|verfügbarkeit|"
    r"vertragsangebot|"
    r"bis (montag|dienstag|mittwoch|donnerstag|freitag)|"
    r"invitation (à|a|pour)|convocation|prochaines? étapes?|disponibilit|êtes-vous (toujours )?intéressée?|"
    r"dans l['’]attente de (votre|ta) (réponse|retour)|promesse d['’]embauche|"
    r"avant (lundi|mardi|mercredi|jeudi|vendredi)|"
    r"invitaci[oó]n|convocatoria|pr[oó]ximos pasos|siguientes pasos|(sigues|sigue) interesad[oa]|"
    r"quedamos a la espera de (tu|su) respuesta|carta (de )?oferta|"
    r"antes del? (lunes|martes|mi[eé]rcoles|jueves|viernes)|"
    r"uitnodiging|volgende stap(pen)?|beschikbaarheid|"
    r"(ben je|bent u) (nog )?(ge[iï]nteresseerd)|(kijken|zien) (we )?(uit )?naar (je|jouw|uw) (reactie|antwoord)|"
    r"contractvoorstel|v[oó][oó]r (maandag|dinsdag|woensdag|donderdag|vrijdag)|"
    r"invito (a|al|per)|prossimi passi|prossime fasi|(sei|è) ancora interessat[oa]|"
    r"(attendiamo|in attesa di) (un )?(tuo|suo) (riscontro|risposta)|"
    r"proposta di assunzione|lettera di offerta|entro (lunedì|martedì|mercoledì|giovedì|venerdì)|"
    r"convite|pr[oó]ximos passos|pr[oó]ximas etapas|ainda (tem|está) interess|"
    r"aguardamos (o seu|seu|a sua|sua|o teu|teu) (retorno|resposta|contato)|"
    r"proposta de (emprego|trabalho|contrata[cç][aã]o)|at[eé] (segunda|ter[cç]a|quarta|quinta|sexta)"))

# Order matters: the first matching group wins. "keep" is checked before anything that would mark read.
KEEP = re.compile(
    r"(interview|rozmow[aęy] (rekrutacyjn|kwalifikacyjn)|zaproszenie|invit(e|ation) (you )?to|"
    r"schedule (a|an|your) |book (a|your) (slot|time)|calendly|availability|when are you available|"
    r"assessment|online test|technical test|coding (task|challenge|test)|hackerrank|codility|testgorilla|"
    r"hirevue|video interview|recorded interview|task for you|zadanie|"
    r"offer letter|pleased to offer|job offer|oferta zatrudnienia|"
    r"next steps?|following steps|action (required|needed)|please (complete|provide|send|submit|confirm|reply|"
    r"respond|choose|select|upload|fill)|complete your application|missing (information|documents?)|"
    r"documents? (required|needed)|deadline|by (monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"reference check|background check|onboarding|could you (possibly |please )?(specify|tell|send|confirm|share|let)|"
    r"can you (please )?(specify|tell|send|confirm|share|let)|would you be available|are you (still )?interested|"
    r"looking forward to your (reply|response|answer)" + ASK_EXTRA + history.wordstart(ASK_INTL) + KEEP_INTL + ")",
    re.I)
NOISE_SENDERS = re.compile(
    r"(jobalerts-noreply@linkedin|jobs-listings@linkedin|messages-noreply@linkedin|notifications-noreply@linkedin|"
    r"jobs-noreply@linkedin|news@|newsletter|marketing|promo|offers@|deals@|@email\.|@e\.|@mail\.|@news\.|"
    r"@info\.|alert|digest|noreply@medium|@substack|@quora|glassdoor|indeed\.com|jooble|careerjet|"
    r"jobs2web\.)", re.I)
NOISE_TEXT = re.compile(
    r"(job alert|jobs? for you|new jobs? (matching|for)|recommended jobs|\d+% match|top job picks|"
    r"oferty dla ciebie|nowe oferty|newsletter|webinar|unsubscribe|digest|weekly|%\s*off|discount|promo(tion)?|"
    r"sale\b|deal(s)?\b|black friday|your (order|receipt|invoice|purchase|payment|subscription|trial)|"
    r"order (confirmed|shipped)|has (shipped|been delivered)|delivery|faktura|zamówieni|"
    r"is hiring|people you may know|viewed your profile|new connection|endorse|congratulate|trending|"
    r"tips|career advice|learning|course|certificate earned|survey|feedback on|rate your|how did we do|"
    r"shared some google account data|help us match you|keep your (details|profile) up to date|"
    r"share your (recruitment|candidate) experience|"
    # German, French, Spanish, Dutch, Italian, Portuguese: unsubscribe footers, promotions, invoices/orders, surveys,
    # "X is hiring", profile views
    r"abmelden|abbestellen|désabonner|désinscri|darse de baja|cancelar (la )?suscripci[oó]n|afmelden|uitschrijven|"
    r"disiscriv|annulla(re)? l['’]iscrizione|cancelar (a )?(inscri[cç][aã]o|assinatura)|descadastr|"
    r"rabatt|gutschein|remise|réduction|soldes|code promo|descuento|rebajas|korting|uitverkoop|sconto|saldi|desconto|"
    r"promo[cç][aã]o|rechnung|facture|factura|factuur|fattura|fatura|nota fiscal|(ihre|deine) bestellung|votre commande|"
    r"(tu|su) pedido|(je|uw) bestelling|il tuo ordine|(seu|sua) (pedido|encomenda)|"
    r"umfrage|sondage|encuesta|enquête|questionario di (gradimento|soddisfazione)|pesquisa de satisfa[cç][aã]o|"
    r"(teilen sie|teile) (uns )?(ihre|deine) (erfahrung|meinung)|partagez votre (expérience|avis)|comparte tu "
    r"(experiencia|opini[oó]n)|deel (je|uw) (ervaring|mening)|condividi la tua esperienza|compartilhe (a )?sua "
    r"(experi[eê]ncia|opini[aã]o)|"
    r"\bstellt ein\b|\brecrute\b|est[aá] contratando|sta assumendo|"
    r"(ihr|dein) profil angesehen|a consulté votre profil|ha visto tu perfil|heeft (je|uw) profiel bekeken|"
    r"ha visualizzato il tuo profilo|visualizou (o )?seu perfil)", re.I)
JOB_INFO_KINDS = {"applied", "rejection", "status", "account"}
# With a full body, confirmations mention "interview", "next steps", "availability" while describing the process
# ("we'll be in touch ... if we'd like to meet you for an interview"). For confirmation-type mail only a direct
# request keeps it unread.
ASKS = re.compile(
    r"(please (complete|provide|send|submit|reply|respond|choose|select|upload|fill)|action (required|needed)|"
    r"complete your application|missing (information|documents?)|documents? (required|needed)|"
    r"could you (possibly |please )?(specify|tell|send|confirm|share|let)|can you (please )?(specify|tell|send|confirm|"
    r"share|let)|would you be available|are you (still )?interested|deadline" + ASK_EXTRA + history.wordstart(ASK_INTL) +
    ")", re.I)
# Confirmations the history classifier doesn't name (Polish and other corporate "we thank you for", account welcomes).
CONFIRMATION = re.compile(
    r"(podziękowanie za (zainteresowanie|aplikacj|przesłan)|dziękujemy za|aplikacj\w* (na|w odpowiedzi)|"
    r"creating an account|account (has been )?(created|activated)|welcome to (the )?\w+ (careers?|talent|candidate)|"
    r"registration (complete|successful)|profile (created|updated)|talent community|"
    # German, French, Spanish, Dutch, Italian, Portuguese
    r"wir bedanken uns|vielen dank für (ihre|deine|ihr|dein) (bewerbung|interesse)|nous vous remercions|merci (pour|de) "
    r"(votre|ta) (candidature|intérêt)|(te|le) agradecemos|agradecemos (tu|su)|gracias por (tu|su) (candidatura|"
    r"solicitud|inter[eé]s)|(wij|we) danken (je|u)|bedankt voor (je|jouw|uw) (sollicitatie|interesse)|(ti|la) "
    r"ringraziamo|grazie per (la tua|il tuo|la sua|il suo) (candidatura|interesse)|agradecemos (a sua|o seu|sua|seu|a "
    r"tua|o teu|pel[oa])|obrigad[oa] (por|pela|pelo)|"
    r"konto (wurde )?(erfolgreich )?(erstellt|angelegt|aktiviert)|registrierung (war )?(erfolgreich|abgeschlossen)|"
    r"willkommen (bei|im|in) (der |dem |unserem )?(\w+ )?(karriere|talent|bewerber)|compte (a été )?(bien )?(créé|"
    r"activé)|inscription (réussie|confirmée|terminée|validée)|bienvenue (sur|dans|chez) (\w+ )?(espace candidat|"
    r"carrières?|talent)|cuenta (ha sido )?(creada|activada)|registro (completado|exitoso|completo|realizado)|"
    r"bienvenid[oa] (a|al) (\w+ )?(portal|talento|empleo)|account (is )?(aangemaakt|geactiveerd)|registratie "
    r"(is )?(voltooid|gelukt|geslaagd)|welkom (bij|in) (\w+ )?(talent|carrière|kandida)|account (è stato )?(creato|"
    r"attivato)|registrazione (completata|riuscita|avvenuta)|benvenut[oa] (in|nel|su) (\w+ )?(talent|carriere|portale)|"
    r"conta (foi )?(criada|ativada)|(registo|cadastro) (conclu[ií]do|efetuado|realizado)|bem-vind[oa] (ao|à) (\w+ )?"
    r"(portal|talent|carreiras)|talent ?pool|vivier de talents|bolsa de talento|banco de talentos)", re.I)
CODE_TEXT = re.compile(r"(one[- ]time pass(code|word)|verification code|security code|kod weryfikacyjny|"
                       r"\botp\b|confirm your email|verify your email|activate your account|"
                       # German, French, Spanish, Dutch, Italian, Portuguese
                       r"bestätigungscode|verifizierungscode|sicherheitscode|einmal(passwort|code|kennwort)|"
                       r"e-?mail(-adresse)? (zu )?bestätigen|bestätigen sie ihre e-?mail|konto aktivieren|"
                       r"aktivieren sie ihr konto|code de (vérification|confirmation|sécurité)|mot de passe "
                       r"(à usage unique|temporaire)|confirme[zr]? (votre|ton) (adresse|e-?mail)|v[ée]rifiez votre "
                       r"(adresse|e-?mail)|activez votre compte|c[oó]digo de (verificaci[oó]n|seguridad|confirmaci[oó]n|"
                       r"verifica[cç][aã]o|seguran[cç]a|confirma[cç][aã]o)|confirma (tu|su) (correo|email|direcci[oó]n)|"
                       r"activa (tu|su) cuenta|verificatiecode|bevestigingscode|beveiligingscode|eenmalige (code|"
                       r"wachtwoord)|bevestig (je|jouw|uw) (e-?mail|emailadres)|activeer (je|uw) account|"
                       r"codice di (verifica|sicurezza|conferma)|password monouso|conferma (il tuo|il suo) (indirizzo|"
                       r"e-?mail)|attiva (il tuo|il suo) account|confirme (o seu|seu|o teu|teu) (e-?mail|endere[cç]o)|"
                       r"ative (a sua|sua) conta)", re.I)
SECURITY_TEXT = re.compile(r"(security alert|new sign-in|sign-in attempt|password (was )?(changed|reset)|"
                           r"suspicious|2-step|recovery (email|phone)|"
                           # German, French, Spanish, Dutch, Italian, Portuguese
                           r"sicherheitswarnung|sicherheitshinweis|neue anmeldung|anmeldeversuch|passwort (wurde )?"
                           r"(geändert|zurückgesetzt)|passwort zurücksetzen|verdächtig|alerte de sécurité|nouvelle "
                           r"connexion|tentative de connexion|mot de passe (a été )?(modifié|réinitialisé)|"
                           r"réinitialis\w* (de |votre )?(votre )?mot de passe|activité suspecte|alerta de seguridad|"
                           r"nuevo inicio de sesi[oó]n|intento de inicio de sesi[oó]n|contraseña (ha sido )?(cambiada|"
                           r"restablecida|modificada)|restablecer (tu |su )?contraseña|actividad sospechosa|"
                           r"beveiligingsmelding|nieuwe aanmelding|aanmeldpoging|wachtwoord (is )?(gewijzigd|gereset|"
                           r"opnieuw ingesteld)|verdachte activiteit|avviso di sicurezza|nuovo accesso|tentativo di "
                           r"accesso|password (è stata )?(modificata|reimpostata|cambiata)|attività sospetta|"
                           r"alerta de seguran[cç]a|novo (login|acesso)|tentativa de (login|acesso)|senha (foi )?"
                           r"(alterada|redefinida)|redefinir (a |sua )?senha|atividade suspeita|"
                           r"bestätigung in zwei schritten|validation en deux étapes|verificaci[oó]n en dos pasos|"
                           r"verificatie in twee stappen|verifica in due passaggi|verifica[cç][aã]o em duas etapas)",
                           re.I)


def classify(row: dict, today: date | None = None) -> tuple[str, str]:
    """-> (bucket, reason). Buckets: keep | job_info | noise | unknown."""
    today = today or date.today()
    subject = (row.get("subject") or "").replace("​", "")
    snippet = (row.get("snippet") or "").replace("​", "")
    body = (row.get("body") or "").replace("​", "")
    sender = f"{row.get('sender_name', '')} <{row.get('sender_email', '')}>"
    text = f"{subject} {body or snippet}"
    # Footers and boilerplate ("next steps", "security", "verify your email") sit deep in bodies: the keep, code and
    # security rules only look at the start, as they did with the snippet.
    head = f"{subject} {(body[:400] if body else snippet)}"
    short = f"{subject} {snippet}"  # security/code wording in a body is usually footer boilerplate (SmartRecruiters)
    kind = history.classify(subject, snippet, body)

    if SECURITY_TEXT.search(short):
        return "keep", "account security notice"
    if kind in ("interview", "test", "offer"):
        return "keep", kind
    if CODE_TEXT.search(short):
        when = history.parse_gmail_date(row.get("date", ""))
        try:
            age = (today - date.fromisoformat(when)).days
        except ValueError:
            age = 0
        return ("job_info", "old verification code") if age >= 1 else ("keep", "fresh verification code")
    if kind == "alert":
        return "noise", "job alert / newsletter sender"
    if kind == "rejection":  # before the sender check: some rejections come from a marketing-style address
        return "job_info", "rejection"
    if NOISE_SENDERS.search(sender):
        return "noise", "job alert / newsletter sender"
    if (ASKS if body and kind in ("applied", "status", "account") else KEEP).search(head):
        return "keep", "asks for something / next steps"
    if kind in JOB_INFO_KINDS:
        return "job_info", kind
    if CONFIRMATION.search(text):
        return "job_info", "confirmation / account"
    if NOISE_TEXT.search(text):
        return "noise", "promotion / notification"
    return "unknown", ""


def plan(rows: list[dict], today: date | None = None) -> dict:
    out = {"keep": [], "job_info": [], "noise": [], "unknown": []}
    seen = set()
    for r in rows:
        tid = r.get("thread_id") or ""
        if tid and tid in seen:
            continue
        seen.add(tid)
        bucket, reason = classify(r, today)
        out[bucket].append({**{k: r.get(k, "") for k in ("thread_id", "date", "sender_name", "sender_email",
                                                           "subject", "snippet")}, "reason": reason,
                            "has_body": bool(r.get("body"))})
    return out


def sync_job_statuses(conn: sqlite3.Connection) -> list[dict]:
    """Applied jobs whose company later sent an interview/test/offer or rejection for that role get their status
    updated (applied -> interview | rejected | offer). A mail that names no role counts only when it is the single
    open application at that company."""
    changes = []
    active = conn.execute("SELECT j.id, j.company, j.title, j.status, MAX(a.applied_at) AS applied_at FROM jobs j "
                          "JOIN applications a ON a.job_id = j.id WHERE j.status IN ('applied', 'interview') "
                          "GROUP BY j.id").fetchall()
    per_company: dict[str, int] = {}
    for job in active:
        core = history.company_core(job["company"])
        per_company[core] = per_company.get(core, 0) + 1
    # Every application ever made, closed ones too: once one of two applications at a company is rejected, the other
    # becomes "the only open one" and must not take the same rejection.
    applied_titles: dict[str, list[tuple[int, str]]] = {}
    for r in conn.execute("SELECT DISTINCT j.id, j.company, j.title FROM jobs j JOIN applications a ON a.job_id = j.id"):
        applied_titles.setdefault(history.company_core(r["company"]), []).append((r["id"], r["title"]))
    for job in active:
        since = (job["applied_at"] or "")[:10]
        only_one = per_company[history.company_core(job["company"])] == 1
        others = [t for i, t in applied_titles.get(history.company_core(job["company"]), []) if i != job["id"]]
        for h in history.for_company(conn, job["company"], job["title"]):
            if h["date"] < since:
                continue
            if not h["same_role"] and any(history._same_role(t, h["role_text"]) for t in others):
                continue  # about another application at this company
            new = {"offer": "offer", "interview": "interview", "test": "interview"}.get(h["kind"])
            # A rejection that names no role ("Regarding your application") can only mean the one open application
            # at that company; with several open ones it stays a note for Claude to resolve. A mail naming the title
            # without its qualifiers ("AI Engineer" for "AI Engineer (LLM automation…), part-time, remote") counts
            # the same way, only for the single open application.
            ours = h["same_role"] or (only_one and (not h["names_other_role"] or h["core_role"]))
            if h["kind"] == "rejection" and ours:
                new = "rejected"
            # Same guard for invites: an assessment for one role must not flip another open application at the same
            # company to interview.
            if new in ("offer", "interview") and not ours:
                new = None
            if new and new != job["status"] and not (job["status"] == "interview" and new == "interview"):
                conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (new, job["id"]))
                changes.append({"id": job["id"], "company": job["company"], "title": job["title"],
                                "from": job["status"], "to": new, "email": h["subject"][:80], "date": h["date"]})
                break
    changes += _sync_by_role_only(conn, [j for j in active if j["id"] not in {c["id"] for c in changes}], active)
    conn.commit()
    return changes


def _sync_by_role_only(conn: sqlite3.Connection, todo: list, active: list) -> list[dict]:
    """Mails sent through an ATS that name only the role, never the company ("Thank you for your interest in the
    Engineer, Integrations position"). Such a mail counts when its role matches exactly one open application and names
    none of the companies applied to."""
    changes = []
    rows = conn.execute("SELECT * FROM app_history WHERE kind IN ('rejection', 'interview', 'test', 'offer') "
                        "ORDER BY msg_date DESC").fetchall()
    # Every company ever applied to, not only the open ones: a rejection naming a closed application's company must
    # not flip a similar role at another company.
    applied_cores = {history.company_core(r["company"]) for r in conn.execute(
        "SELECT DISTINCT j.company FROM jobs j JOIN applications a ON a.job_id = j.id")}
    applied_cores.discard("")
    for row in rows:
        domain = (row["sender_email"] or "").split("@")[-1]
        if not any(p in domain for p in history.PLATFORM_DOMAINS):
            continue
        role_text = f"{row['subject']} {row['snippet']} {(row['body'] or '')[:600]}"
        hay = f"{history._haystack(row)} {history.fold(role_text)} "
        if any(f" {core} " in hay for core in applied_cores):
            continue  # names a company: the normal company match handles it (or it is about another company)
        matches = [j for j in active if history._same_role(j["title"], role_text)]
        if len(matches) != 1 or matches[0] not in todo or row["msg_date"] < (matches[0]["applied_at"] or "")[:10]:
            continue
        job = matches[0]
        new = {"rejection": "rejected", "offer": "offer"}.get(row["kind"], "interview")
        if new == job["status"]:
            continue
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (new, job["id"]))
        changes.append({"id": job["id"], "company": job["company"], "title": job["title"], "from": job["status"],
                        "to": new, "email": row["subject"][:80], "date": row["msg_date"], "matched_by": "role only"})
        todo = [j for j in todo if j["id"] != job["id"]]
    return changes


def run(conn: sqlite3.Connection, rows: list[dict], dry_run: bool = True) -> dict:
    """Classify rows; unless dry_run, record job mail in history, sync job statuses and write the mark-read list."""
    p = plan(rows)
    result = {"dry_run": dry_run, "counts": {k: len(v) for k, v in p.items()}, **p}
    if not dry_run:
        job_rows = [r for r in rows
                    if history.classify(r.get("subject", ""), r.get("snippet", ""), r.get("body", "")) != "alert"]
        result["history"] = history.import_rows(conn, job_rows)
        result["status_changes"] = sync_job_statuses(conn)
    to_mark = [r["thread_id"] for r in p["job_info"] + p["noise"] if r["thread_id"]]
    result["mark_read"] = to_mark
    log = LOG_DIR / f"mailcheck-{date.today().isoformat()}{'-dry' if dry_run else ''}.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"at": now(), **result}, ensure_ascii=False, indent=1))
    result["log"] = str(log)
    return result


def mark_script(thread_ids: list[str]) -> Path:
    """browser/gmail_mark_read.js with the thread ids filled in, saved under data/inbox for browser_run_code."""
    src = (ROOT / "browser" / "gmail_mark_read.js").read_text().replace("new Set(__THREADS__)", "new Set(" + json.dumps(thread_ids) + ")")
    path = LOG_DIR / "gmail-mark-read.js"
    path.write_text(src)
    return path


def gmail_base() -> str:
    """Gmail for the job inbox: the address in place of the account index opens that account even when the browser
    is signed in to several (/u/0/ is whichever signed in first)."""
    email = settings.get().job_email.strip()
    return f"https://mail.google.com/mail/u/{email or 0}/"


def gmail_url(query: str = QUERY) -> str:
    from urllib.parse import quote
    return gmail_base() + "#search/" + quote(query)


def load_rows(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        rows += history.load_rows(p)
    return rows
