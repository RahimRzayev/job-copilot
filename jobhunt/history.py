"""Application history from Gmail: what the user already applied to, and what is pending.

Rows come from Gmail's search-results list (browser/gmail_rows.js) — sender, subject, preview
snippet, date. /mail-check rows (browser/gmail_rows_bodies.js) also carry `body`, the newest message's text from
the print view; when present it is stored and classified instead of the ~150-char snippet. Each row is classified (applied / test / interview /
rejection / offer / other) and matched to jobs by company, so the ranked list can warn
"already applied" or "test pending" before the user picks.
"""

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from functools import lru_cache

from . import settings
from .db import now

# Gmail search used to (re)build the history. Restrict with newer_than:14d for incremental runs. Subject words in
# English, Polish, German, French, Spanish/Italian/Portuguese ("candidatura" is shared) and Dutch; Gmail allows about
# 2,000 characters, so only the strongest words are listed. cli.py swaps the trailing "newer_than:2y".
GMAIL_QUERY = (
    "(subject:(application OR applying OR applied OR candidate OR candidacy OR interview OR assessment OR "
    "\"skills test\" OR \"thank you for your interest\" OR aplikacja OR aplikację OR kandydat OR rekrutacja OR rozmowa OR "
    "Bewerbung OR Eingangsbestätigung OR Vorstellungsgespräch OR Absage OR candidature OR entretien OR recrutement OR "
    "candidatura OR entrevista OR postulación OR colloquio OR sollicitatie OR sollicitatiegesprek OR kandidatuur) OR "
    "from:(smartrecruiters OR myworkday OR myworkdayjobs OR greenhouse OR lever OR ashby OR darwinbox OR "
    "teamtailor OR pracuj OR justjoin OR nofluffjobs OR erecruiter OR workable OR recruitee OR peopleforce OR "
    "testedrecruits OR icims OR jobvite OR traffit OR successfactors OR personio OR softgarden OR stepstone OR "
    "taleo)) "
    "newer_than:2y"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_history (
    id           INTEGER PRIMARY KEY,
    thread_id    TEXT,
    msg_date     TEXT,              -- ISO date
    sender_name  TEXT,
    sender_email TEXT,
    subject      TEXT,
    snippet      TEXT,
    kind         TEXT,              -- applied | test | interview | rejection | offer | status | other
    body         TEXT,              -- newest message text (print view), when the importer had it
    imported_at  TEXT,
    UNIQUE (thread_id, msg_date, subject)
);
"""

# The same kinds in German, French, Spanish, Dutch, Italian and Portuguese (formal and informal "you"), appended to
# the English/Polish alternatives in KINDS through wordstart(). Rejection words that are everyday words ("leider",
# "malheureusement", "helaas"...) only count with a rejection outcome in the same sentence and outside a conditional
# (weak_rejection): "Leider ist unser Büro geschlossen" in an interview invite is no rejection.


def wordstart(fragment: str) -> str:
    """'|a|b|c' -> '|(?<!\\w)(?=[^\\W\\d_])(?:a|b|c)': every alternative starts a word (all of them start with a letter).
    Two cheap tests per position instead of trying ~70 alternatives at every character keep the long multilingual
    patterns fast (the same as a leading \\b here, about twice as fast)."""
    return r"|(?<!\w)(?=[^\W\d_])(?:" + fragment.lstrip("|") + ")"


ALERT_INTL = (
    r"|jobs für (sie|dich)\b|\bneue (jobs|stellen|stellenangebote)\b|passende (jobs|stellen)|job-?agent|jobalarm|"
    r"könnten? (sie|dich) interessieren|(sie|dich) interessieren könnten|"
    r"offres d['’]emploi pour vous|nouvelles offres|alerte[- ]emploi|pourrai(en)?t vous intéresser|"
    r"empleos para (ti|usted)|nuevas ofertas|alerta de empleo|te (pueden|podr[ií]an) interesar|"
    r"vacatures voor (jou|u)\b|nieuwe vacatures|vacature-?alert|"
    r"offerte (di lavoro )?per te|nuove offerte|potrebbero interessarti|"
    r"vagas para (voc[eê]|ti)|novas vagas|alerta de vagas|(podem|pode) (te )?interessar")
OFFER_INTL = (  # positive wording only: "kein Vertragsangebot" / a negated offer is no offer
    r"|(?<!kein )(?<!keinen )vertragsangebot|freuen (uns|mich),? (\w+ ){0,3}(ihnen|dir) (\w+ ){0,3}"
    r"\b(eine zusage|ein angebot|einen arbeitsvertrag|die stelle) (\w+ ){0,3}(anbieten|machen|geben|unterbreiten|zu)|"
    r"herzlichen glückwunsch[^.]{0,60}(zusage|vertragsangebot|arbeitsvertrag)|"
    r"(heureux|heureuse|ravis?|ravies?|plaisir) de vous (proposer|offrir|faire) (le poste|un poste|ce poste|un contrat|"
    r"une proposition d['’]embauche|une offre)|promesse d['’]embauche|lettre d['’]offre|"
    r"félicitations[^.]{0,60}(offre|proposition d['’]embauche)|"
    r"carta (de )?oferta|(complace|alegra|encanta|agrada) (\w+ ){0,2}(ofrecerte|ofrecerle|hacerte|hacerle|extenderte|"
    r"extenderle) (el puesto|la posici[oó]n|un contrato|una oferta|una propuesta)|(enhorabuena|felicidades)[^.]{0,60}"
    r"(oferta|propuesta)|"
    r"contractvoorstel|(je|jou|u) (graag )?een (aanbod|contract|contractvoorstel) (te )?(doen|aanbieden|aan te bieden|sturen)|"
    r"gefeliciteerd[^.]{0,60}(aanbod|contract)|"
    r"lettera di offerta|proposta di assunzione|(lieti|felici|piacere) di (offrirti|offrirle|proporti|proporle) "
    r"(la posizione|il ruolo|il posto|un contratto|una proposta)|congratulazioni[^.]{0,60}(offerta|proposta di assunzione)|"
    r"(prazer|felizes|satisfa[cç][aã]o|contentes) (em|de) (lhe |te )?(oferecer|apresentar|fazer) (a vaga|a posi[cç][aã]o|"
    r"o cargo|um contrato|uma proposta|uma oferta)|parab[eé]ns[^.]{0,60}(proposta|oferta)")
INTERVIEW_INTL = (  # "agent d'entretien" is a cleaning job, not an interview
    r"|vorstellungsgespräch|bewerbungsgespräch|kennenlerngespräch|einladung zu(m|r)? (\w+ ){0,2}"
    r"(gespräch|telefonat|kennenlernen|interview)|"
    r"(?<!agent )(?<!agents )(?<!technicien )(?<!produits )(?<!service )\b(un|votre|ton|notre|cet|premier|second|"
    r"deuxième|prochain|d['’]|l['’]) ?entretien\b|^entretien\b|entretiens? (d['’]embauche|t[ée]l[ée]phonique|vid[ée]o|"
    r"visio|individuel|technique|rh|de recrutement|en ligne)|"
    r"entrevista|sollicitatiegesprek|kennismakingsgesprek|uitnodiging (voor|tot) (een |het )?\w*gesprek|"
    r"colloquio|convocazione")
TEST_INTL = (
    r"|online-?test|einstufungstest|eignungstest|programmieraufgabe|coding-?aufgabe|fallstudie|probeaufgabe|"
    r"test (technique|en ligne|de logique|d['’]anglais|de personnalit[ée]|de comp[ée]tences)|exercice technique|"
    r"cas pratique|"
    r"prueba (t[eé]cnica|en l[ií]nea|online|de nivel|de ingl[eé]s|de conocimientos|de c[oó]digo)|"
    r"test (t[eé]cnico|en l[ií]nea|de ingl[eé]s|de conocimientos|psicot[eé]cnico)|caso pr[aá]ctico|reto t[eé]cnico|"
    r"desaf[ií]o t[eé]cnico|"
    r"(online|technische|capaciteiten|programmeer)[ -]?toets|technische (test|opdracht)|thuisopdracht|"
    r"programmeeropdracht|casusopdracht|"
    r"test (tecnico|online|di inglese|attitudinale|di logica)|prova tecnica|esercizio tecnico|caso pratico|"
    r"teste (t[eé]cnico|online|de ingl[eê]s|de l[oó]gica|de conhecimentos)|prova (t[eé]cnica|online)|case t[eé]cnico")
# Not in KINDS: weak_rejection() applies them only outside conditional sentences ("Sollten Sie nichts von uns hören,
# können wir Ihre Bewerbung leider nicht berücksichtigen" is a confirmation).
REJECTION_INTL = (
    r"leider[^.!?\n]{0,120}?(\babsage\b|nicht (\w+ ){0,3}(berücksichtig|weiter (berücksichtig|verfolg)|"
    r"weiterverfolg|einladen|einzuladen|ausgewählt|in die engere|erfolgreich|anforderung)|kein\w* (\w+ ){0,2}"
    r"(zusage|stelle|position|positive|einladung|vertragsangebot|angebot)|für (eine[nrs]? )?andere[nrs]? (bewerber|kandidat|person)|"
    r"anderweitig|(stelle|position) (\w+ ){0,3}(besetzt|vergeben))|"
    # "Absage" (rejection), but not "Sie erhalten eine Zu- oder Absage" (a confirmation) nor a cancelled appointment
    r"(?<!oder )(?<!oder eine )(?<!bzw\. )(?<!/)\babsage\b(?! (des|unseres|unserer|ihres|deines|eines|für den) "
    r"(termin|gespräch|interview))|"
    r"(malheureusement|hélas)[^.!?\n]{0,120}?((pas|plus|aucune?) (\w+ ){0,5}(retenu|suite|poursuivre|continuer|"
    r"sélectionn|correspond|inviter)|d['’]autres (candidat|profil)|un autre (candidat|profil)|pourvu)|"
    r"(lamentablemente|desafortunadamente|por desgracia|lastimosamente|infortunadamente)[^.!?\n]{0,120}?"
    r"(no (\w+ ){0,4}(continuar|seguir|avanzar|seleccionad|encaja|ajusta|cumple|corresponde|invitar|ofrecer|contar)|"
    r"otr[oa]s? (candidat|perfil)|cubiert[oa])|"
    r"(helaas|jammer genoeg|spijtig genoeg|tot onze spijt)[^.!?\n]{0,120}?((niet|geen) (\w+ ){0,4}(verder|door|"
    r"uitnodigen|uit te nodigen|aansluit|passend|geselecteerd|aanbieden|positie|plek|positief)|(past|sluit) (\w+ ){0,3}"
    r"niet|andere kandida|vervuld|ingevuld|afgewezen|teleurstellen|afwijzing)|"
    r"(purtroppo|sfortunatamente)[^.!?\n]{0,120}?(non (\w+ ){0,4}(proseguire|procedere|selezionat|in linea|idone|"
    r"invitarti|offrirti|dare seguito)|altri (candidati|profili)|altre candidature|un altro (candidato|profilo)|coperta)|"
    r"(infelizmente|lamentavelmente)[^.!?\n]{0,120}?(n[aã]o (\w+ ){0,4}(seguir|prosseguir|avan[cç]ar|continuar|"
    r"selecionad|aprovad|adequ|enquadr|atende|convid|oferecer|dar continuidade)|outr[oa]s (candidat|perfis)|"
    r"outro (candidato|perfil)|preenchida|encerrad)")
STATUS_INTL = (
    r"|status (ihrer|deiner) bewerbung|statusänderung|bewerbungsstatus|(ihre|deine) bewerbung wird (\w+ )?"
    r"(geprüft|bearbeitet)|rückmeldung zu (ihrer|deiner) bewerbung|zwischenstand|"
    r"statut de (votre|ta) candidature|mise à jour (de|sur) (votre|ta) candidature|(votre|ta) candidature est "
    r"(en cours d['’]examen|à l['’]étude|en cours de traitement)|retour sur (votre|ta) candidature|"
    r"estado de (tu|su) (candidatura|solicitud|postulaci[oó]n)|actualizaci[oó]n (de|sobre) (tu|su) (candidatura|"
    r"solicitud|postulaci[oó]n)|(tu|su) (candidatura|solicitud) (est[aá]|se encuentra) en (revisi[oó]n|proceso)|"
    r"status van (je|jouw|uw) sollicitatie|update over (je|jouw|uw) sollicitatie|(je|jouw|uw) sollicitatie wordt "
    r"(\w+ )?(beoordeeld|bekeken)|terugkoppeling (op|over) (je|jouw|uw) sollicitatie|"
    r"stato della (tua|sua) candidatura|aggiornamento (sulla|della) (tua|sua) candidatura|(la tua|la sua) candidatura "
    r"è in (fase di )?(valutazione|revisione)|esito (della )?(tua |sua )?candidatura|"
    r"(status|estado) da (sua|tua) candidatura|atualiza[cç][aã]o (sobre|da) (sua|tua) candidatura|(sua|tua) "
    r"candidatura (est[aá]|esta) em (an[aá]lise|avalia[cç][aã]o)|retorno (sobre|da) (sua|tua) candidatura")
APPLIED_INTL = (
    r"|(vielen )?dank(e)? für (ihre|deine|die) bewerbung|danke? für (ihr|dein) interesse|vielen dank für (ihr|dein) "
    r"interesse|wir haben (ihre|deine) bewerbung (\w+ )?(erhalten|bekommen)|(ihre|deine) bewerbung (ist )?(\w+ ){0,3}"
    r"(eingegangen|angekommen)|eingangsbestätigung|bewerbungseingang|bestätigung (ihrer|deiner) bewerbung|"
    r"(ihre|deine) bewerbung (als|für|bei|auf|um|zum|zur|an)\b|^(ihre |deine )?bewerbung\b|bewerbung (erfolgreich )?"
    r"(übermittelt|versendet|eingereicht|abgeschickt)|"
    r"merci (pour|de) (votre|ta) candidature|(nous vous remercions|merci) (pour|de) (votre|ton|l['’]) ?intérêt|"
    r"nous vous remercions (pour|de) (votre|ta) candidature|nous avons (bien )?reçu (votre|ta) candidature|"
    r"(votre|ta) candidature (a )?(bien )?(été )?(reçue|envoyée|transmise|enregistrée|prise en compte)|"
    r"accusé de réception|confirmation de (votre |ta )?candidature|(votre|ta) candidature (au|à|a|pour|chez|en tant "
    r"que|sur)\b|^candidature\b|"
    r"gracias por (tu|su) (candidatura|solicitud|postulaci[oó]n|inter[eé]s)|gracias por (postularte|postular|aplicar|"
    r"inscribirte|presentar tu candidatura)|hemos recibido (tu|su) (candidatura|solicitud|postulaci[oó]n|cv|"
    r"curr[ií]culum)|(tu|su) (candidatura|solicitud|postulaci[oó]n) (ha sido|se ha|fue) (\w+ )?(recibida|enviada|"
    r"registrada|completada|presentada)|(tu|su) (candidatura|solicitud|postulaci[oó]n) (a|al|para|en|como)\b|"
    r"confirmaci[oó]n de (tu |su )?(candidatura|solicitud|postulaci[oó]n)|te has (postulado|inscrito)|"
    r"^(tu |su )?(candidatura|postulaci[oó]n)\b|"
    r"bedankt voor (je|jouw|uw) (sollicitatie|interesse|kandidatuur)|dank (je|u) (wel )?voor (je|jouw|uw) "
    r"(sollicitatie|interesse|kandidatuur)|(we|wij) hebben (je|jouw|uw) (sollicitatie|kandidatuur) (\w+ ){0,3}"
    r"ontvangen|(je|jouw|uw) (sollicitatie|kandidatuur) (is )?(\w+ ){0,3}(ontvangen|verzonden|verstuurd)|"
    r"ontvangstbevestiging|bevestiging (van )?(je|jouw|uw) (sollicitatie|kandidatuur)|(je|jouw|uw) (sollicitatie|"
    r"kandidatuur) (voor|als|bij|op)\b|^sollicitatie\b|"
    r"grazie per (la tua|la sua|aver inviato la tua|aver inviato la sua) candidatura|grazie per (esserti|essersi) "
    r"candidat[oa]|grazie per (il tuo|il suo|l['’]) ?interesse|abbiamo ricevuto (la tua|la sua) candidatura|"
    r"(la tua|la sua) candidatura (è|e) stata (\w+ )?(ricevuta|inviata|registrata|inoltrata)|conferma (di )?"
    r"(ricezione|candidatura)|(la tua|la sua) candidatura (per|a|al|alla|come|presso)\b|"
    r"obrigad[oa] (pela|por) (sua|tua|a sua|a tua) candidatura|obrigad[oa] (por|pelo) (se candidatar|candidatar-se|"
    r"seu interesse|o seu interesse|teu interesse|o teu interesse)|(agradecemos|recebemos) (a sua|sua|a tua|tua) "
    r"candidatura|(sua|tua) candidatura (foi|est[aá]) (\w+ )?(recebida|enviada|registrada|registada|confirmada|"
    r"submetida)|(sua|tua) candidatura (para|à|a|na|no|como)\b|confirma[cç][aã]o de candidatura|"
    r"voc[eê] se candidatou|candidatura (recebida|enviada|confirmada)")
ACCOUNT_INTL = (
    r"|(ihre|deine) e-?mail(-adresse)? (zu )?bestätigen|bestätigen sie ihre e-?mail|konto bestätigen|bewerberkonto|"
    r"kandidatenkonto|(ihre|deine) registrierung|"
    r"confirme[zr]? (votre|ton) (adresse|e-?mail|compte)|v[ée]rifiez votre (adresse|e-?mail|compte)|activer votre "
    r"compte|compte candidat|votre inscription|"
    r"confirma (tu|su) (correo|cuenta|email|direcci[oó]n)|verifica (tu|su) (correo|cuenta|email)|activa (tu|su) "
    r"cuenta|cuenta de candidato|(tu|su) registro\b|"
    r"bevestig (je|jouw|uw) (e-?mail|emailadres|account)|activeer (je|uw) account|kandida(at|ten)account|"
    r"(je|uw) registratie|"
    r"conferma (il tuo|la tua|il suo|la sua) (indirizzo|e-?mail|account)|verifica (il tuo|la tua) (indirizzo|"
    r"e-?mail|account)|attiva (il tuo|il suo) account|account candidato|(la tua|la sua) registrazione|"
    r"confirme (o seu|seu|o teu|teu) (e-?mail|endere[cç]o)|confirme (a sua|sua) conta|verifique (o seu|seu) e-?mail|"
    r"ative (a sua|sua) conta|conta de candidato|(seu|o seu) (cadastro|registo)")

KINDS = [  # first match wins, most significant first
    # Job-board alerts ("78% match: Data Analyst job for you") are not applications at all, nor are LinkedIn alert
    # digests ('“internship”: Acme - Security engineer intern posted on 10/4/26'), which would otherwise look like a
    # same-role application.
    ("alert", r"(\d+% match|jobs? for you|new jobs? (matching|for)|job alert|recommended jobs|oferty dla ciebie|"
              r"nowe oferty|proponowane oferty|you may be interested|^[“\"][^”\"]{1,60}[”\"]:\s|"
              r"^[„«]\s?[^“»]{1,60}\s?[“»]\s?:\s|"  # the German / French digest quotes: „python“: … / « python » : …
              r"posted on \d{1,2}/\d{1,2}/\d{2}" + wordstart(ALERT_INTL) + ")"),
    ("offer", r"(offer letter|pleased to offer you|(would|'d) like to (make you an |extend an )?offer|"
              r"congratulations[^.]{0,60}offer|oferta zatrudnienia|propozycja zatrudnienia" + wordstart(OFFER_INTL) + ")"),
    ("interview", r"(interview|rozmow[aęy] (rekrutacyjn|kwalifikacyjn)|invite you to (a |an )?(call|meeting|chat)|"
                  r"schedule (a|an) (call|chat|meeting)|zaproszenie na rozmow" + wordstart(INTERVIEW_INTL) + ")"),
    ("test", r"(placement test|technical test|skills test|english test|assessment|codility|hackerrank|testgorilla|"
             r"coding (task|challenge)|zadanie rekrutacyjne|online test|test link" + wordstart(TEST_INTL) + ")"),
    ("rejection", r"(unfortunately|niestety|not (be )?moving forward|other candidates|not been successful|"
                  r"regret to inform|decided to (proceed|move forward) with|unsuccessful|won'?t be progressing|"
                  r"not selected|position has been filled|(isn.t|is not|not) progressing|won.t be moving|not to (move|proceed) forward|"
                  r"(isn.t|is not|not) (the |a )?right (match|fit)|"
                  # A rejection subject "<Company> | Feedback on your application - <role>": read mails come without
                  # a body, and the snippet's "Thank you for your interest" would make it "applied". Not "feedback on
                  # your application process" (a survey).
                  r"feedback on your application\s*[-–—:]|"
                  r"nie został|nie przeszedł|odrzucon)"),
    ("status", r"(changed its status|status (update|change)|application status|update on your (job )?application|"
               r"is being (assessed|reviewed)|informacja o twojej aplikacji|pracodawca udziela" + wordstart(STATUS_INTL) + ")"),
    ("applied", r"(thank(s| you) (for|in) (applying|your application|your interest|applying at)|received your application|"
                r"application (has been |was )?(received|sent|submitted)|you('ve| have)? (just )?applied|"
                r"your application (to|for|as|at|with)\b|^application (for|to)\b|applied for|"
                r"dziękujemy za (aplikacj|przesłanie|zainteresowanie)|twoja aplikacja|otrzymano zgłoszenie|"
                r"aplikacja na stanowisko|zgłoszenie (na|do) |złożenie aplikacji|otrzymania aplikacji|otrzymaliśmy twoje (cv|aplikacj)|"
                r"application is now with us" + wordstart(APPLIED_INTL) + ")"),
    # Registrations: an account exists on that company's ATS (useful when applying again).
    ("account", r"(verify your (candidate )?account|candidate account|account confirmation|confirm your (email|account)|"
                r"one-time password|potwierdź (konto|adres)|verify your email|signing up" + wordstart(ACCOUNT_INTL) + ")"),
]

GENERIC_SENDERS = re.compile(
    r"^(no-?reply|noreply|notification|notifications|jobs|careers|talent|recruit\w*|hr|team|info|hello|support|"
    # German, French, Spanish, Dutch, Italian, Portuguese HR / careers senders (folded: no accents)
    r"karriere|bewerbermanagement|personal|personalabteilung|recrutement|rh|rrhh|ressources humaines|reclutamiento|"
    r"recursos humanos|seleccion|empleo|werving|vacatures|reclutamento|risorse umane|selezione|carriere|recrutamento|"
    r"carreiras|talento|talentos)$", re.I)
PLATFORM_DOMAINS = ("smartrecruiters", "myworkday", "greenhouse", "lever", "ashby", "darwinbox", "teamtailor",
                    "pracuj", "justjoin", "nofluffjobs", "linkedin", "erecruiter", "workable", "recruitee",
                    "peopleforce", "testedrecruits", "icims", "jobvite", "traffit", "successfactors", "gmail", "google",
                    "outlook", "bulldogjob", "indeed", "elementapp", "personio", "softgarden", "stepstone",
                    "taleo", "jobylon", "join.com", "talentsoft", "welcomekit", "factorial", "rexx-systems")
LEGAL_NOISE = {"sp", "z", "o", "oo", "s", "a", "sa", "spolka", "ograniczona", "odpowiedzialnoscia", "ltd", "llc",
               "inc", "gmbh", "group", "grupa", "the", "co", "company", "corporation",
               "technologies", "technology", "solutions", "services", "systems", "international", "global",
               "kapitalowa", "spk", "k", "sk", "of",
               # other European legal forms: "Globex GmbH & Co. KG", "Initech B.V.", "Acme S.p.A.", "Hooli S.L."
               "ag", "kg", "se", "ug", "mbh", "plc", "bv", "b", "v", "nv", "n", "sl", "slu", "l", "spa", "p", "srl",
               "r", "sas", "sarl", "ltda", "lda", "oy", "oyj", "ab", "aps"}


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").replace("​", "").lower())
    # Letters NFKD does not split: without these "Außendienst" folds to "au endienst".
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("ł", "l").replace("ß", "ss")
    text = text.replace("œ", "oe").replace("æ", "ae").replace("ø", "o")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# Words that say nothing about which company it is. A core made only of these ("Management Solutions" -> "management")
# would match every email mentioning "Talent Management" or "Capacity Management": keep the full name then.
GENERIC_CORE = {"management", "consulting", "consultants", "digital", "data", "software", "it", "tech", "business",
                "capital", "partners", "energy", "health", "media", "design", "engineering", "finance", "financial",
                "bank", "network", "networks", "cloud", "labs", "lab", "studio", "talent", "talents", "people", "career",
                "careers", "recruitment", "hr", "analytics", "ai", "development", "security", "logistics", "trading",
                "innovation", "innovations", "insurance", "holding", "industries", "products", "research", "travel"}
LEGAL_FORMS = {"sp", "z", "o", "oo", "s", "a", "sa", "spolka", "ograniczona", "odpowiedzialnoscia", "ltd", "llc", "inc",
               "gmbh", "spk", "k", "sk", "kapitalowa", "co", "plc", "ag", "bv", "nv", "srl", "sas", "kg", "se", "ug",
               "mbh", "b", "v", "n", "sl", "slu", "l", "spa", "p", "r", "sarl", "ltda", "lda", "oy", "oyj", "ab", "aps"}


@lru_cache(maxsize=4)
def _places_for(s: settings.Settings) -> tuple[frozenset, frozenset, tuple]:
    """(city words, one-word country names, other-city prefixes) of the search settings, folded."""
    cities = frozenset(w for n in (s.city, *s.city_names) for w in fold(n).split())
    countries = frozenset(fold(n) for n in (s.country, *s.country_names) if n and " " not in fold(n))
    return cities, countries, tuple(fold(c) for c in s.other_cities if fold(c))


def _places() -> tuple[frozenset, frozenset, tuple]:
    return _places_for(settings.get())


def company_core(company: str) -> str:
    """'Grupa Globex Sp. z o.o.' -> 'globex'; 'UMBRELLA POLAND sp. z o.o.' -> 'umbrella' (the search country is no
    part of a name); 'Management Solutions' -> 'management solutions' (a generic word alone is not a name)."""
    noise = LEGAL_NOISE | _places()[1]
    words = [w for w in fold(company).split() if w not in noise]
    if words and all(w in GENERIC_CORE for w in words[:3]):
        words = [w for w in fold(company).split() if w not in LEGAL_FORMS]
    return " ".join(words[:3])


# First words that are too common to stand for a company on their own ("Bank X" -> not "bank"; "Johnson X" vs
# "Johnson & Johnson"; "Operator Medyczny ..."). The search city counts too (see brand_word).
BRAND_STOP = GENERIC_CORE | {"and", "johnson", "operator", "general", "first", "new", "united", "american", "european",
                             "euro", "smart", "open", "best", "top", "star", "blue", "green", "red", "white", "black",
                             "polish", "national", "central", "north", "south", "east", "west", "city", "home", "one",
                             "my", "your", "next", "future", "global", "world", "line", "art", "service", "solution",
                             "center", "centre", "institute", "university", "academy", "foundation", "agency", "office",
                             "hub", "hubs", "team", "medical", "pharma", "bio", "auto", "motor", "power", "air", "sea",
                             # ordinary English/Polish words seen as first words of company names
                             "advanced", "advisor", "agencja", "all", "analytic", "assessment", "association", "banking",
                             "bankowy", "base", "benefit", "big", "biuro", "british", "care", "cargo", "centrala",
                             "centralny", "centrum", "change", "check", "client", "code", "cold", "commit", "computer",
                             "confidential", "core", "credit", "customs", "cyber", "danish", "delivery", "diverse",
                             "drivers", "eco", "edge", "emerging", "enter", "enterprise", "exact", "exclusive",
                             "expansion", "extreme", "fast", "few", "fitness", "fleet", "free", "french", "from", "fun",
                             "fundacja", "get", "happy", "hard", "hardware", "healthy", "hotel", "human", "hunter",
                             "idea", "indian", "insider", "instant", "instytut", "inter", "job", "jobs", "just",
                             "justice", "kancelaria", "kids", "klient", "law", "leap", "legacy", "level", "light", "link",
                             "loop", "loyalty", "macro", "man", "match", "method", "metro", "museum", "native",
                             "natural", "nest", "online", "page", "partner", "play", "plus", "polski", "polskie", "port",
                             "portal", "premium", "prime", "pro", "progress", "rekrutacja", "rekruter", "respect",
                             "right", "royal", "sales", "school", "schools", "scope", "search", "sharp", "silent",
                             "simple", "social", "solid", "solve", "square", "standard", "state", "strategic", "strong",
                             "super", "sure", "swiss", "the", "too", "total", "transition", "understanding",
                             "universal", "urban", "vision", "welcome", "whatever", "work", "worldwide",
                             "ministerstwo", "narodowy", "urzad", "zaklad", "zarzad", "spoldzielnia", "stowarzyszenie",
                             "osrodek", "samodzielny", "przychodnia", "laboratorium", "laboratoire", "medyczna",
                             "apteka", "dom", "nowa", "audio", "cosmetic"}


def brand_word(company: str) -> str:
    """The distinctive first word of a multi-word company name, e.g. 'QXR - Global Transport and Logistics' -> 'qxr'
    (its core 'qxr transport and' never appears in the company's mails, so an application there would be missed).
    '' when the first word is generic, the search city, or too short."""
    words = company_core(company).split()
    if len(words) < 2 or len(words[0]) < 3 or words[0] in BRAND_STOP or words[0] in _places()[0]:
        return ""
    return words[0]


# System word lists (macOS, most Linux). Proper nouns there are capitalised, so made-up brand names are absent.
DICT_PATHS = ("/usr/share/dict/words", "/usr/dict/words", "/usr/share/dict/web2", "/usr/share/dict/american-english",
              "/usr/share/dict/british-english")
_UNLOADED = object()
_WORDS: object = _UNLOADED


def _dictionary() -> set[str] | None:
    """Lower-case English words from the first system word list found; None when there is none (Windows, minimal
    Linux): then every brand word is treated as an ordinary word and needs support (see _brand_hit_ok)."""
    global _WORDS
    if _WORDS is _UNLOADED:
        _WORDS = None
        for path in DICT_PATHS:
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    _WORDS = {w.strip() for w in f if w[:1].islower()}
                break
            except OSError:
                continue
    return _WORDS


def _brand_hit_ok(row: sqlite3.Row, company: str, brand: str, text: str) -> bool:
    """A match on the brand word alone, when that word is an ordinary English word, needs more support: the full
    name ('falcon ridge'), or a sender whose name/domain starts with it. Otherwise 'Falcon Ridge' matches a mail from
    'Acme Falcon', 'Ruby Widgets' a 'Ruby on Rails' job and 'Seeking Beta' a mail saying 'we are seeking'. Without a
    word list every brand word needs that support."""
    words_list = _dictionary()
    if words_list is not None and brand not in words_list:
        return True
    words = company_core(company).split()
    if len(words) >= 2 and f" {words[0]} {words[1]} " in text:
        return True
    sender = fold(row["sender_name"] or "")
    domain = (row["sender_email"] or "").split("@")[-1]
    return sender.split(" ")[:1] == [brand] or (not any(p in domain for p in PLATFORM_DOMAINS)
                                                 and fold(domain).startswith(brand))


def _sender_domain(row: sqlite3.Row) -> str:
    """The sender's own domain ('initechce.com' for careers@initechce.com); '' for ATS/mail platforms."""
    domain = (row["sender_email"] or "").split("@")[-1]
    return "" if any(p in domain for p in PLATFORM_DOMAINS) else domain


def _other_company_sender(row: sqlite3.Row, core: str) -> bool:
    """True when the mail comes from some other company's own domain: a consultancy's confirmation for its
    'Contoso Lab' programme names Contoso but is no Contoso application. The whole domain counts, TLD included:
    companies with a brand TLD (careers.hr.globexbank) would otherwise look foreign in their own mails."""
    domain = fold(_sender_domain(row))
    if not domain:
        return False
    flat = domain.replace(" ", "")
    words = [w for w in core.split() if len(w) >= 3]
    return not (core.replace(" ", "") in flat or any(w in flat for w in words))


# Unambiguous rejection sentences in German, French, Spanish, Dutch, Italian and Portuguese, including the negated
# invite ("dass wir Sie nicht zu einem Vorstellungsgespräch einladen können"), which the invite rules would otherwise
# read as an invitation. Word gaps are "( \w+){0,n}" so they never cross a comma or a sentence end. Checked by
# strong_rejection() with a whole-sentence conditional guard (their conditions often open a long sentence).
STRONG_REJECTION_INTL = (
    r"\b(sie|dich|ihre bewerbung|deine bewerbung|ihre kandidatur|deine kandidatur)\b( \w+){0,3} nicht( \w+){0,5} "
    r"(berücksichtigen|berücksichtigt|einladen|einzuladen|weiterverfolgen|weiter verfolgen|in betracht ziehen)\b|"
    r"\b(für|zugunsten) (eine[nrs]? )?andere[nrs]? (bewerber|kandidat|person|profil)\w*|"
    r"gegen (ihre|deine) (bewerbung|kandidatur)|keine (positive|erfreuliche)n? (rückmeldung|nachricht|antwort|mitteilung)|"
    r"(ihnen|dir) (\w+ ){0,2}kein\w* (\w+ )?(stelle|position|zusage|anstellung|vertragsangebot)\b|"
    r"(stelle|position|vakanz) (ist|wurde) (\w+ ){0,2}(besetzt|vergeben)|absage (erteilen|geben|mitteilen|aussprechen)|"
    r"(ihnen|dir) (\w+ ){0,2}absagen|bedauern,? (\w+ ){0,2}(ihnen|dir) mit(zu)?teilen|zu unserem (großen )?bedauern|"
    r"(ihr|dein) profil (passt|entspricht)( \w+){0,3} nicht|"
    # fr ("N'hésitez pas … la suite du processus" is no rejection: the negation must govern "donner suite")
    r"(pas|plus) (\w+ ){0,3}(donner|réserver) (une |de )?suite (favorable |positive )?(à|a)\b|"
    r"ne donner\w* pas (de |une )?suite|\bne (pouvons|pourrons|pouvions|sommes|serons|souhaitons|allons)"
    r"( malheureusement| hélas| donc)? (pas )?(en mesure )?(de |d['’])?(vous (inviter|convier|convoquer)|(y )?(donner|"
    r"réserver) (une |de )?suite|retenir (votre|ta) candidature|(poursuivre|continuer) (le processus |l['’]aventure )?"
    r"avec (vous|toi|votre candidature))|"
    r"n['’](a|ont|avons) (malheureusement |hélas )?pas (pu )?été (retenue?s?|sélectionnée?s?)|"
    r"n['’]avons (malheureusement |hélas )?pas (pu )?retenu|"
    r"(décidé|choisi) de (poursuivre|continuer|retenir|privilégier|avancer)( le processus| notre processus)? avec "
    r"(d['’]autres|un autre|une autre)|(retenu|privilégié|sélectionné|choisi) (un autre|une autre|d['’]autres) "
    r"(candidat|profil|candidature)|(regret|regrettons) de vous (informer|annoncer|faire part)|sommes au regret|"
    r"poste (a été |est )?(déjà )?pourvu|(votre|ton) profil ne correspond pas|"
    # es
    r"(decidido|optado por) (continuar|seguir|avanzar|proseguir)( adelante)?( el proceso| en el proceso)? con "
    r"otr[oa]s?\b|(decidido|optado por) no (continuar|seguir|avanzar|proseguir)|"
    r"no (podemos |vamos a |podremos |ser[aá] posible )?(continuar|seguir|avanzar|proseguir)( adelante)? con (tu|su) "
    r"(candidatura|solicitud|postulaci[oó]n|proceso)|no (podemos|podremos|vamos a) (\w+ ){0,2}(invitarte|invitarle|"
    r"ofrecerte|ofrecerle|contar contigo|contar con usted)|(tu|su) (candidatura|solicitud|perfil|postulaci[oó]n) no "
    r"(ha sido|fue|ha resultado) (seleccionad|elegid)|no (has|ha) sido seleccionad|(lamentamos|sentimos|lamento|siento) "
    r"(tener que )?(informarte|informarle|comunicarte|comunicarle|decirte)|(vacante|puesto|posici[oó]n|plaza) (ya )?"
    r"(ha sido|fue|est[aá]) (cubiert|ocupad)|(seleccionado|elegido|optado por) (a )?otr[oa]s? (candidat|perfil)|"
    r"(tu|su) perfil no (encaja|se ajusta|coincide)|"
    # nl
    r"(besloten|gekozen|beslist) (om )?(niet|met (een )?andere|voor (een )?andere)|verder (te )?gaan met (een )?andere|"
    r"niet verder (te gaan )?met (je|jouw|uw) (sollicitatie|kandidatuur)|niet (verder |langer )?(in behandeling (te )?"
    r"nemen|meenemen|mee te nemen)|niet door naar de (volgende|tweede|derde)|(moeten|moet) (we |wij |ik )?(je|jou|u) "
    r"(helaas )?teleurstellen|(sollicitatie|kandidatuur) (\w+ ){0,3}(afgewezen|af te wijzen)|vacature is (inmiddels |al "
    r"|reeds )*(vervuld|ingevuld)|spijt (ons|me|mij) (je|jou|u) (te )?(moeten )?(laten weten|mee te delen|meedelen)|"
    r"(gekozen|geselecteerd) (voor|met) (een )?andere? kandida|\b(je|jou|u)( \w+){0,2} niet( \w+){0,3} (uitnodigen|"
    r"uit te nodigen)|"
    # it
    r"(deciso|scelto) di (proseguire|procedere|continuare|andare avanti) con (altri|un altro|un['’]altra|altre)|"
    r"(deciso|scelto) di non (proseguire|procedere|dare seguito)|non (possiamo |potremo |poter |potendo |è possibile )?"
    r"(proseguire|procedere|dare seguito)( ulteriormente)? (con|alla) (la tua|la sua|tua|sua) candidatura|"
    r"non (possiamo|potremo|siamo in grado di) (\w+ ){0,2}(invitarti|invitarla|offrirti|offrirle)|"
    r"non (è|e|sei) stat[oa] selezionat|(la tua|la sua) candidatura non (è|e) stata (selezionata|accolta|ritenuta)|"
    r"(ci dispiace|siamo spiacenti|ci rincresce)[^.]{0,20}(informarti|comunicarti|informarla|comunicarle|informare|"
    r"comunicare)|con (rammarico|dispiacere)|posizione (è stata )?(già )?coperta|individuato (altri|un altro|altre) "
    r"(profil|candidat)|"
    # pt
    r"(decidimos|optamos|decidiu|optou) (por )?(seguir|prosseguir|avan[cç]ar|continuar)( o processo)? com "
    r"outr[oa]s?\b|(decidimos|optamos) por n[aã]o (seguir|prosseguir|avan[cç]ar|continuar|dar (continuidade|seguimento))|"
    r"n[aã]o (vamos |iremos |poderemos |podemos |ser[aá] poss[ií]vel )?(seguir|prosseguir|avan[cç]ar|continuar|dar "
    r"(continuidade|seguimento|sequ[eê]ncia))( com| à| a| ao)? (a sua|sua|o seu|seu|a tua|tua|o teu|teu) (candidatura|"
    r"perfil|processo|aplica[cç][aã]o)|n[aã]o (podemos|poderemos|iremos|vamos|conseguiremos|conseguimos) ([\w-]+ ){0,2}"
    r"(convid\w*|oferecer (a vaga|a posi[cç][aã]o|o cargo|uma proposta))|(sua|tua) candidatura n[aã]o (foi|ser[aá]) "
    r"(selecionada|aprovada|aceite|aceita)|n[aã]o foi (selecionad|aprovad)|(lamentamos|lamento|sentimos muito) "
    r"(em |por )?(informar|comunicar|dizer)|vaga (j[aá] )?(foi )?(preenchida|ocupada|encerrada)|(seu|teu) perfil "
    r"n[aã]o (se )?(enquadra|adequa|corresponde|atende)|selecionamos outr[oa]s? (candidat|perfi)")
# Invitation wording (not the bare noun, which confirmations use to describe the process).
INTERVIEW_INVITE_INTL = (
    r"|einladung (zu|zum|zur|für) (\w+ ){0,3}(\w*gespräch|\w*interview|kennenlernen|telefonat|termin)|"
    r"\blade[n]? (wir |ich )?(sie|dich|euch) (\w+ ){0,4}(zu|zum|zur) (\w+ ){0,3}(\w*gespräch|\w*interview|kennenlernen|"
    r"telefonat)\w* ein\b|\b(sie|dich|euch) (\w+ ){0,3}(zu|zum|zur) (\w+ ){0,3}(\w*gespräch|\w*interview|kennenlernen|"
    r"telefonat)\w* (\w+ )?ein(zu)?laden|(möchten|würden|wollen) (wir |ich )?(sie|dich) (\w+ ){0,3}(kennenlernen|"
    r"kennen lernen|sprechen)|(wählen|buchen) (sie|du) (\w+ ){0,3}(einen|ihren|deinen) (termin|zeitslot|slot)|"
    r"\b(einen|ein) (termin|\w*gespräch|\w*interview) (\w+ ){0,4}(zu )?vereinbaren|(gespräch|interview|termin) (ist|wurde) "
    r"(\w+ )?(bestätigt|vereinbart|angesetzt)|"
    r"(vous|t['’]) ?inviter (à|a|pour) (\w+ |l['’])?(\w+ )?(entretien|échange|appel|rencontre|entrevue)|vous invitons "
    r"(à|a) (\w+ |l['’])?(\w+ )?(entretien|échange|appel|rencontre|entrevue)|(invitation|convocation) (à|a|pour) "
    r"(\w+ |l['’])?(\w+ )?(entretien|échange|entrevue)|(souhait\w+|aimerions|voudrions) (vous |te )?(rencontrer|"
    r"échanger avec|parler)|(planifier|programmer|organiser|fixer|convenir d['’]) ?(un |une |votre |l['’])?(\w+ )?"
    r"(entretien|échange|appel|rendez-vous)|(choisissez|sélectionnez|réservez|choisir|sélectionner|réserver) (un|une|"
    r"votre) (créneau|horaire|date)|entretien (est |a été )?(confirmé|planifié|programmé)|"
    r"(invitarte|invitarle|invitarlo|invitarla|invitaros) (a|para) (\w+ ){0,3}(entrevista|llamada|reuni[oó]n|charla|"
    r"videollamada)|\b(te|le|los|las|os) invitamos (a|para) (\w+ ){0,3}(entrevista|llamada|reuni[oó]n|charla|"
    r"videollamada)|(invitaci[oó]n|convocatoria) (a|para|de) (\w+ ){0,2}(entrevista|llamada|reuni[oó]n)|"
    r"(nos gustar[ií]a|queremos|quisi[eé]ramos|nos encantar[ií]a) ([\w-]+ ){0,2}(conocerte|conocerle|hablar contigo|"
    r"hablar con usted|invitarte|invitarle|agendar|programar|concertar)|(agendar|programar|concertar|coordinar) (una |la "
    r"|tu |su )(\w+ )?(entrevista|llamada|reuni[oó]n)|(elige|escoge|selecciona|reserva|elija|escoja|seleccione|reserve) "
    r"(\w+ ){0,3}(horario|franja|hueco|fecha|cita)|entrevista (est[aá] |ha sido |queda )?(confirmada|programada|agendada)|"
    r"\bnodig(en)? (we |wij |ik )?(je|jou|u) (\w+ ){0,3}uit (voor|tot) (\w+ ){0,2}(\w*gesprek|interview|kennismaking)|"
    r"\b(je|jou|u) (\w+ ){0,3}(uitnodigen|uit te nodigen) (voor|tot) (\w+ ){0,2}(\w*gesprek|interview|kennismaking)|"
    r"uitnodiging (voor|tot) (\w+ ){0,2}(\w*gesprek|interview|kennismaking)|(willen|zouden) (we |wij |ik )?(je|jou|u) "
    r"(graag )?(\w+ )?(leren kennen|spreken|ontmoeten)|(een |het |je |jouw |uw )(\w*gesprek|interview|afspraak) "
    r"(\w+ )?(in te plannen|inplannen|te plannen)|(kies|kiest|selecteer|boek|reserveer) (\w+ ){0,3}(tijdslot|tijdstip|"
    r"moment|datum|slot)|gesprek (is )?(bevestigd|ingepland)|"
    r"(invitarti|invitarla|invitarvi) (a|al|ad|per) (\w+ ){0,3}(colloquio|incontro|call|chiamata|videochiamata)|"
    r"\b(ti|la|vi) invitiamo (a|al|ad|per) (\w+ ){0,3}(colloquio|incontro|call|chiamata|videochiamata)|"
    r"(invito|convocazione) (a|al|ad|per) (\w+ ){0,2}(colloquio|incontro)|(vorremmo|ci farebbe piacere|ci piacerebbe|"
    r"desideriamo|saremmo lieti di) ([\w'’]+ ){0,2}(conoscerti|conoscerla|incontrarti|incontrarla|sentirti|parlarti|"
    r"fissare|programmare|organizzare|invitarti|invitarla)|(fissare|programmare|organizzare|pianificare) (un |il |una )"
    r"(\w+ )?(colloquio|incontro|call)|(scegli|seleziona|prenota|scelga|selezioni|prenoti|scegliere|selezionare|"
    r"prenotare) (\w+ ){0,3}(slot|orario|fascia|data)|colloquio (è |e )?(confermato|fissato|programmato)|"
    r"(convid[aá]-l[oa]s?|convidar (voc[eê]|o senhor|a senhora)|convidar-te) (a |para |à )?(\w+ ){0,2}(entrevista|"
    r"conversa|reuni[aã]o|chamada|bate-papo)|\b(te |o |a |os |as )?convidamos (voc[eê] )?(a|para|à) (\w+ ){0,2}"
    r"(entrevista|conversa|reuni[aã]o|chamada)|convite (para|à|a) (\w+ ){0,2}(entrevista|conversa|reuni[aã]o)|"
    r"(gostar[ií]amos|queremos|adorar[ií]amos) (de )?([\w-]+ ){0,2}(conhec[eê]-l[oa]|conhecer voc[eê]|te conhecer|"
    r"conversar|falar com voc[eê]|agendar|marcar)|(agendar|marcar) (uma |a |sua |tua )(\w+ )?(entrevista|conversa|"
    r"reuni[aã]o)|(escolha|selecione|reserve|agende) (\w+ ){0,3}(hor[aá]rio|dia|data)|entrevista (est[aá] |foi )?"
    r"(confirmada|agendada|marcada)")


# Unambiguous rejection sentences. In a full body they beat interview/test words: a rejection may ask for feedback on
# "your interview experience" and would otherwise count as an interview invite.
STRONG_REJECTION = re.compile(
    r"(decided to (proceed|move forward|continue|go ahead) with (an)?other|chosen to (proceed|move forward|continue) with (an)?other|"
    r"not to (move|proceed|take your application) (forward|further)|regret to inform|(have|has) not been successful|"
    r"(isn.t|is not) the right (match|fit)|won.?t be (progressing|moving forward)|will not be (progressing|moving forward)|"
    r"(isn.t|is not|not) progressing further|position has (now )?been filled|not (been )?selected (for|to)|"
    r"not to take your application further|will not be considering you|"
    r"(unable|not able|won.?t be able) to invite you to (the )?next (stage|phase)|"
    r"decided to move forward with candidates whose|nie możemy zaproponować|zdecydowaliśmy się (kontynuować|zaprosić)|"
    r"wybraliśmy (innych|inną|innego))", re.I)
# "If this particular role isn't the right fit, keep an eye on our careers page" (a confirmation) is not one, nor
# "Sans réponse sous 4 semaines, considérez que votre candidature n'a pas été retenue". The Romance "si"/"se" count
# only as "if" ("si votre profil", "se il tuo profilo"): alone they are also reflexive pronouns.
CONDITIONAL = re.compile(
    r"\b(if|in case|should|unless|whether|jeśli|jeżeli|w przypadku|"
    r"wenn|falls|sofern|sollten?|ob|ohne (rückmeldung|antwort|nachricht)|betrachten|"
    r"si (vous|votre|vos|tu|ton|ta|tus|su|sus|usted|nous|no|el|la|le|ce|cette|aucune?|dans|en|pasad\w*)|s['’]ils?|"
    r"au cas où|à défaut|sans (réponse|retour|nouvelles)|considérez|considérer|"
    r"en caso|a menos que|sin (respuesta|noticias)|considera|considere|"
    r"als (je|jij|u|uw|jouw|we|wij|er|het)|indien|mocht(en)?|tenzij|zonder (reactie|bericht|antwoord)|beschouw\w*|"
    r"(?<!-)se (il|la|lo|tu|tuo|tua|sarai|sei|non|o|a|voc[eê]|seu|sua|for|tiver|n[aã]o)|qualora|nel caso|in caso|"
    r"a meno che|senza (riscontro|risposta|notizie)|consideri|da ritenersi|"
    r"caso|sem (retorno|resposta|contato))\b", re.I)
# With a full body the bare word "interview" is too weak: confirmations describe the process ("a recruiter will
# contact you to schedule an interview"). An invitation needs invitation wording.
INTERVIEW_INVITE = re.compile(
    r"(invite you (to|for) (an? |the |our )?(\w+ )?(interview|call|meeting|chat|conversation)|invitation (to|for) (an? )?"
    r"(\w+ )?interview|would (like|love) to (invite|schedule|meet|talk|speak|have a (call|chat))|"
    r"schedule (a|an|your) (\w+ )?(interview|call|chat|meeting)|book (a|your) (slot|time)|calendly|"
    r"interview (is |has been )?(scheduled|confirmed)|zaproszenie na rozmow|zapraszamy (cię |pana )?na rozmow|"
    r"choose (a|your) (slot|time))", re.I)
INTERVIEW_INVITE_OTHER = re.compile(wordstart(INTERVIEW_INVITE_INTL)[1:], re.I)


# "If you qualify, a recruiter will contact you to schedule an interview" describes the process, it is no invite.
# Romance languages mark the future on the verb ("nous vous contacterons pour organiser un entretien"): the common
# future forms of "contact / get back / be able to" are listed instead.
FUTURE_OR_IF = re.compile(
    r"\b(if|in case|should|once|when|will|may|might|could|"
    r"wenn|falls|sofern|sobald|sollten?|werden|wird|eventuell|gegebenenfalls|ggf|möglicherweise|melden|kontakt\w*|"
    r"si (vous|votre|vos|tu|ton|ta|tus|su|sus|usted|nous|no|el|la|le|ce|cette|aucune?|dans|en|pasad\w*)|s['’]ils?|"
    r"lorsqu\w*|quand|dès que|une fois|au cas où|pourr\w+|serons|seront|contacterons|recontacterons|reviendr\w+|allons|"
    r"cuando|en caso|una vez|podr\w+|será|serán|seremos|pondr\w+|contactaremos|comunicaremos|vamos a|llamaremos|"
    r"als (je|jij|u|uw|jouw|we|wij|er|het)|indien|zodra|wanneer|mocht(en)?|zullen|zal|nemen|neemt|"
    r"(?<!-)se (il|la|lo|tu|tuo|tua|sarai|sei|verrai|non|o|a|voc[eê]|seu|sua|for|tiver|n[aã]o)|qualora|nel caso|"
    r"in caso|quando|una volta|potr\w+|sar(à|ai|emo|anno)|contatteremo|ricontatteremo|verr\w+|"
    r"caso|assim que|poder\w+|serão|iremos|irá|irão|entraremos|contataremos|retornaremos)\b", re.I)


def interview_invite(text: str) -> bool:
    for pattern in (INTERVIEW_INVITE, INTERVIEW_INVITE_OTHER):
        for m in pattern.finditer(text):
            if not FUTURE_OR_IF.search(text[max(0, m.start() - 70):m.start()]):
                return True
    return False


STRONG_REJECTION_OTHER = re.compile(wordstart(STRONG_REJECTION_INTL)[1:], re.I)
WEAK_REJECTION = re.compile(wordstart(REJECTION_INTL)[1:], re.I)


def _outside_condition(pattern: re.Pattern, text: str) -> bool:
    """A match whose sentence (up to 150 characters back) has no conditional: "Sollten Sie nichts von uns hören,
    können wir Ihre Bewerbung leider nicht berücksichtigen" / "Als je niets hoort, is je sollicitatie helaas niet
    geselecteerd" describe the process in a confirmation."""
    for m in pattern.finditer(text):
        start = max(text.rfind(c, 0, m.start()) for c in ".!?\n") + 1
        if not CONDITIONAL.search(text[max(start, m.start() - 150):m.start()]):
            return True
    return False


def strong_rejection(text: str) -> bool:
    for m in STRONG_REJECTION.finditer(text):
        if not CONDITIONAL.search(text[max(0, m.start() - 45):m.start()]):
            return True
    return _outside_condition(STRONG_REJECTION_OTHER, text)


def weak_rejection(text: str) -> bool:
    """A "leider / malheureusement / helaas …" rejection (REJECTION_INTL) outside a conditional sentence."""
    return _outside_condition(WEAK_REJECTION, text)


def classify(subject: str, snippet: str, body: str = "") -> str:
    """Kind of a job email. Without a body: first KINDS match on subject + snippet. With the newest message's body
    (mail-check rows): alerts and generic rejection words only count near the top (footers say "new offers",
    confirmations say "in case other candidates are a better match"), strong rejection sentences win, and an
    interview needs invitation wording or "interview" in the subject."""
    if not body:
        text = f"{subject} {snippet}".replace("​", "")
        for kind, pattern in KINDS:
            # The conditional-guarded rejection sentences count here too, at the rejection step: "nous avons le regret
            # de vous informer que votre candidature n'a pas été retenue" names no English/Polish rejection word.
            if re.search(pattern, text, re.I) or (kind == "rejection" and (strong_rejection(text) or
                                                                            weak_rejection(text))):
                return kind
        return "other"
    subject = subject.replace("​", "")
    body = body.replace("​", "")
    full, head = f"{subject} {body}", f"{subject} {body[:300]}"
    patterns = dict(KINDS)
    # Strong rejections beat the alert check: a rejection may invite you to "our email job alert service" in its
    # second sentence and must not be marked as noise.
    if strong_rejection(full) and not re.search(patterns["offer"], full, re.I):
        return "rejection"
    if re.search(patterns["alert"], head, re.I):
        return "alert"
    if re.search(patterns["offer"], full, re.I):
        return "offer"
    if interview_invite(full) or re.search(patterns["interview"], subject, re.I):
        return "interview"
    if re.search(patterns["test"], f"{subject} {body[:600]}", re.I):
        return "test"
    if re.search(patterns["rejection"], head, re.I) or weak_rejection(head):
        return "rejection"
    for kind in ("status", "applied", "account"):
        if re.search(patterns[kind], full, re.I):
            return kind
    return "other"


def parse_gmail_date(value: str) -> str:
    value = (value or "").replace(" ", " ").replace("\xa0", " ").strip()
    for fmt in ("%a, %b %d, %Y, %I:%M %p", "%a, %d %b %Y, %H:%M", "%b %d, %Y, %I:%M %p"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def connect_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if "body" not in {r[1] for r in conn.execute("PRAGMA table_info(app_history)")}:
        conn.execute("ALTER TABLE app_history ADD COLUMN body TEXT")


def reclassify(conn: sqlite3.Connection) -> int:
    """Re-run classify() on every stored row (after the rules change)."""
    connect_schema(conn)
    n = 0
    for row in conn.execute("SELECT id, subject, snippet, body, kind FROM app_history").fetchall():
        kind = classify(row["subject"], row["snippet"], row["body"] or "")
        if kind != row["kind"]:
            conn.execute("UPDATE app_history SET kind = ? WHERE id = ?", (kind, row["id"]))
            n += 1
    conn.commit()
    return n


def import_rows(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    connect_schema(conn)
    added = 0
    for r in rows:
        subject = (r.get("subject") or "").replace("​", "").strip()
        snippet = (r.get("snippet") or "").replace("​", "").strip()
        body = (r.get("body") or "").strip()
        key = (r.get("thread_id") or "", parse_gmail_date(r.get("date", "")), subject)
        cur = conn.execute(
            "INSERT OR IGNORE INTO app_history (thread_id, msg_date, sender_name, sender_email, subject, snippet, "
            "kind, body, imported_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (*key[:2], r.get("sender_name", ""), (r.get("sender_email") or "").lower(), subject, snippet,
             classify(subject, snippet, body), body or None, now()),
        )
        if not cur.rowcount and body:  # seen before without its body (list-only import): add it now
            conn.execute("UPDATE app_history SET body = ? WHERE thread_id = ? AND msg_date = ? AND subject = ? "
                         "AND body IS NULL", (body, *key))
        added += cur.rowcount
    conn.commit()
    reclassify(conn)
    return {"rows": len(rows), "added": added}


def _haystack(row: sqlite3.Row) -> str:
    domain = (row["sender_email"] or "").split("@")[-1]
    domain_part = "" if any(p in domain for p in PLATFORM_DOMAINS) else domain.split(".")[0]
    name = row["sender_name"] or ""
    name = "" if GENERIC_SENDERS.match(fold(name)) or "@" in name else name
    return f" {fold(name)} {fold(row['subject'])} {fold(row['snippet'])} {fold(domain_part)} "


ROLE_NOISE_WORDS = (r"m|k|f|x|d|w|n|mfd|fmx|mfx|she|he|they|her|him|entry level|fqa|remote|hybrid|full time|"
                    r"part time|b2b|uop|"
                    # gender tags and work modes in German, French, Spanish, Dutch, Italian, Portuguese (folded)
                    r"mwd|wmd|hf|fh|vollzeit|teilzeit|homeoffice|temps plein|temps partiel|teletravail|"
                    r"tiempo completo|media jornada|jornada completa|teletrabajo|remoto|voltijd|deeltijd|fulltime|"
                    r"parttime|tempo pieno|tempo parziale|tempo integral|meio periodo|hibrido|ibrido|presencial")


@lru_cache(maxsize=4)
def _role_noise_for(s: settings.Settings) -> re.Pattern:
    cities, countries, prefixes = _places_for(s)
    alts = [re.escape(w) for w in sorted(cities | countries, key=len, reverse=True)]
    alts += [rf"{re.escape(p)}\w*" for p in prefixes]
    return re.compile(rf"\b({ROLE_NOISE_WORDS}{''.join('|' + a for a in alts)})\b")


def _role_noise() -> re.Pattern:
    """Gender tags, work modes and the search's places ('Tester (f/m), <city> / hybrid') are no part of a role."""
    return _role_noise_for(settings.get())
ROLE_STOP = {"and", "or", "the", "a", "an", "of", "for", "in", "with", "to", "at", "on", "team", "role", "position",
             "job", "pl", "eng"}


# The email talks about a specific position ("for the position X", "the X role", "applying to X at Y").
ROLE_MENTION = re.compile(r"(for the (position|role) (of )?\w|\b(position|role|stanowisko)\s*[:\-]|"
                          r"apply(ing)? (for|to) (the )?[A-Z]\w+|\b\w+ (role|position) (at|with)\b|"
                          # German, French, Spanish, Dutch, Italian, Portuguese: "Stelle: X", "Bewerbung als X",
                          # "pour le poste de X", "para el puesto de X", "voor de functie X", "per la posizione di X"
                          r"(?i:\b(stelle|poste|puesto|vacante|functie|vacature|posizione|ruolo|cargo|vaga)\s*[:\-])|"
                          r"(?i:(bewerbung|candidature|candidatura|sollicitatie|kandidatuur|stelle|position) (als|en "
                          r"tant que|como|come)) [A-Z]|(?i:(für|auf) die (stelle|position) (als |des |der )?)\w|"
                          r"(?i:(pour le|au) poste (de |d['’])?)\w|(?i:(para el|al|del) puesto (de )?)\w|"
                          r"(?i:para (la|a) (vacante|posici[oó]n|posi[cç][aã]o|vaga|fun[cç][aã]o) (de )?)\w|"
                          r"(?i:voor de (functie|vacature|positie|rol) (van |als )?)\w|"
                          r"(?i:per (la posizione|il ruolo) (di )?)\w)")
SENIORITY = {"junior", "senior", "mid", "regular", "lead", "principal", "staff", "intern", "trainee", "graduate",
             "associate", "head", "chief", "expert"}


def _role_tokens(text: str) -> list[str]:
    t = _role_noise().sub(" ", fold(text))
    return [w for w in t.split() if w not in ROLE_STOP and len(w) > 1]


def _same_role(job_title: str, text: str, fuzzy: bool = True) -> bool:
    """The email names this job title. Both sides are normalized the same way (a subject repeating the exact title
    'Associate Technical Consultant - Entry-Level Consulting' must match even though "entry level" is noise).
    A match = the title's words appear as one run, or >= 80% of them (min. 2) appear."""
    title = _role_tokens(job_title)
    if not title:
        return False
    hay = _role_tokens(text)
    run, joined = " ".join(title), " ".join(hay)
    if run in joined:
        # "Junior Operations Engineer" is another job than "Operations Engineer".
        before = joined[:joined.index(run)].split()[-1:]
        return not (before and before[0] in SENIORITY and before[0] not in title)
    if not fuzzy:
        return False
    have = set(hay)
    hits = sum(1 for w in title if w in have)
    return len(title) >= 2 and hits / len(title) >= 0.8


def title_core(title: str) -> str:
    """The role name without brackets and trailing qualifiers: 'AI Engineer (LLM automation, agentic coding),
    part-time, remote' -> 'AI Engineer'. Rejections often name only that."""
    t = re.sub(r"\([^)]*\)", " ", title)
    return re.split(r"\s*[,|]\s*|\s+[-–—]\s+", t.strip())[0].strip()


def core_role(job_title: str, text: str) -> bool:
    """The email names the title's core (title_core) as whole words, and the core has 2+ words: "AI Engineer" must not
    match inside "GenAI Engineer", nor "Junior AI Engineer"."""
    core = _role_tokens(title_core(job_title))
    if len(core) < 2 or core == _role_tokens(job_title):
        return False
    run, joined = f" {' '.join(core)} ", f" {' '.join(_role_tokens(text))} "
    if run not in joined:
        return False
    before = joined[:joined.index(run)].split()[-1:]
    return not (before and before[0] in SENIORITY and before[0] not in core)


def for_company(conn: sqlite3.Connection, company: str, title: str = "") -> list[dict]:
    connect_schema(conn)
    core = company_core(company)
    if len(core) < 3:
        return []
    brand = brand_word(company)
    names = [re.escape(core)] + ([re.escape(brand)] if brand else [])
    pattern = re.compile(rf"\b({'|'.join(names)})\b")
    core_pattern = re.compile(rf"\b{re.escape(core)}\b")
    # Companies named like an ATS (Workday, Greenhouse, Lever...) appear in every email sent through that ATS;
    # for them only the sender's own domain (e.g. @workday.com) counts.
    platform_named = any(core.replace(" ", "") in p for p in PLATFORM_DOMAINS + ("myworkdayjobs", "workday"))
    hits = []
    for row in conn.execute("SELECT * FROM app_history WHERE kind != 'alert' ORDER BY msg_date DESC"):
        if platform_named:
            domain = (row["sender_email"] or "").split("@")[-1]
            if not re.fullmatch(rf"([a-z0-9-]+\.)*{re.escape(core.replace(' ', ''))}\.[a-z.]+", domain) or "myworkday" in domain:
                continue
            if not pattern.search(f" {fold(row['subject'])} "):
                continue  # e.g. "Verify your candidate account" sent by workday.com for some other employer
            text = _haystack(row)
        else:
            text = _haystack(row)
            if pattern.search(text) and _other_company_sender(row, core):
                continue
            if brand and not core_pattern.search(text) and pattern.search(text) and not _brand_hit_ok(row, company, brand, text):
                continue
        if platform_named or pattern.search(text):
            role_text = f"{row['subject']} {row['snippet']} {(row['body'] or '')[:600]}"
            same = bool(title) and _same_role(title, role_text)
            hits.append({"date": row["msg_date"], "kind": row["kind"], "subject": row["subject"], "same_role": same,
                         "names_other_role": not same and bool(ROLE_MENTION.search(role_text)),
                         "core_role": not same and bool(title) and core_role(title, role_text),
                         "role_text": role_text})
    return hits


def summarize(hits: list[dict]) -> tuple[str, bool]:
    """Short note + whether it should block/warn strongly (same role applied, or test/interview pending)."""
    if not hits:
        return "", False
    parts, strong = [], False
    for h in hits[:4]:
        role = " (same role)" if h["same_role"] else ""
        parts.append(f"{h['kind']} {h['date']}: {h['subject'][:60]}{role}")
        if h["same_role"] or h["kind"] in ("test", "interview", "offer"):
            strong = True
    more = f" (+{len(hits) - 4} more)" if len(hits) > 4 else ""
    return " | ".join(parts) + more, strong


# An application confirmation this many days older than the posting belongs to an earlier opening of the same title.
REPOST_DAYS = 60


def _days_before(day: str, days: int) -> str:
    return (datetime.fromisoformat(day) - timedelta(days=days)).date().isoformat()


def sync_applied(conn: sqlite3.Connection, statuses: tuple[str, ...] = ("new", "scored", "shortlisted")) -> dict:
    """A job still listed as new/scored/shortlisted whose Gmail history names the same role as an application
    confirmation was applied to (e.g. applied by hand, outside /apply). The job becomes applied, with an application
    row so later rejections sync. Confirmations that name no role are only listed in `review`: with several open jobs
    at one company we cannot tell which one was applied to."""
    connect_schema(conn)
    rows = conn.execute(f"SELECT id, company, title, posted_at, scraped_at FROM jobs "
                        f"WHERE status IN ({','.join('?' * len(statuses))})", statuses).fetchall()
    synced, review = [], []
    for r in rows:
        hits = [h for h in for_company(conn, r["company"], r["title"]) if h["kind"] == "applied"]
        # Exact title only: a fuzzy match would count a "Mid Backend Engineer" posting as the "Junior Backend
        # Engineer" application at the same company.
        same = [h for h in hits if h["same_role"] and _same_role(r["title"], h["role_text"], fuzzy=False)]
        # A confirmation from long before the posting appeared was for an earlier opening of the same title; marking
        # the new posting "applied" would hide it from scoring. Those go to review.
        seen_at = (r["posted_at"] or r["scraped_at"] or "")[:10]
        if same and seen_at and max(h["date"] for h in same) < _days_before(seen_at, REPOST_DAYS):
            review.append({"id": r["id"], "company": r["company"], "title": r["title"],
                           "mails": [f"{h['date']}: {h['subject'][:80]} (older application, re-posting?)"
                                     for h in same[:3]]})
            continue
        if same:
            first = min(same, key=lambda h: h["date"])
            conn.execute("UPDATE jobs SET status = 'applied' WHERE id = ?", (r["id"],))
            conn.execute("INSERT INTO applications (job_id, ats, applied_at, answers, cover_letter, notes) "
                         "VALUES (?, 'gmail-history', ?, '{}', '', ?)",
                         (r["id"], first["date"], f"synced from Gmail history: {first['subject'][:120]}"))
            synced.append({"id": r["id"], "company": r["company"], "title": r["title"], "date": first["date"]})
        elif hits:
            review.append({"id": r["id"], "company": r["company"], "title": r["title"],
                           "mails": [f"{h['date']}: {h['subject'][:80]}" for h in hits[:3]]})
    conn.commit()
    return {"synced": synced, "review": review}


def annotate(conn: sqlite3.Connection, statuses: tuple[str, ...] = ("new", "scored", "shortlisted")) -> dict:
    connect_schema(conn)
    applied = sync_applied(conn, statuses)
    rows = conn.execute(f"SELECT id, company, title FROM jobs WHERE status IN ({','.join('?' * len(statuses))})",
                        statuses).fetchall()
    flagged = []
    for r in rows:
        note, strong = summarize(for_company(conn, r["company"], r["title"]))
        conn.execute("UPDATE jobs SET history_note = ?, history_warn = ? WHERE id = ?", (note or None, int(strong), r["id"]))
        if note:
            flagged.append({"id": r["id"], "company": r["company"], "title": r["title"], "warn": strong, "note": note})
    conn.commit()
    return {"checked": len(rows), "with_history": len(flagged), "flagged": flagged,
            "applied_synced": applied["synced"], "applied_review": applied["review"]}


def counts(conn: sqlite3.Connection) -> dict:
    connect_schema(conn)
    return {r[0]: r[1] for r in conn.execute("SELECT kind, COUNT(*) FROM app_history GROUP BY kind ORDER BY 2 DESC")}


def load_rows(path) -> list[dict]:
    data = json.loads(open(path).read())
    return data if isinstance(data, list) else data.get("rows", [])
