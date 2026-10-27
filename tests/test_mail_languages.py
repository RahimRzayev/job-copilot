"""Job mail in German, French, Spanish, Dutch, Italian and Portuguese: /mail-check buckets, history kinds (with and
without the body), status sync, and the precision guards (an everyday "leider" / "malheureusement" must not turn an
invite or a confirmation into a rejection)."""
from datetime import date

import pytest

from jobhunt import history, mailcheck
from jobhunt.db import connect
from jobhunt.mailcheck import classify

TODAY = date(2026, 10, 3)


def row(subject, body="", snippet=None, sender="careers@globex.example", name="Globex Recruiting"):
    return {"subject": subject, "snippet": body[:150] if snippet is None else snippet, "body": body,
            "sender_email": sender, "sender_name": name, "date": "Sat, Oct 3, 2026, 10:00 AM", "thread_id": subject[:10]}


def check(subject, body, kind, bucket):
    """The history kind and the /mail-check bucket, with the body and (snippet only) without it."""
    assert history.classify(subject, body[:150], body) == kind, f"{subject} (body)"
    assert classify(row(subject, body), TODAY)[0] == bucket, f"{subject} (body)"


# (language, subject, body) per category: realistic mails, each with process wording that must not change the kind.
RECEIVED = [
    ("de", "Eingangsbestätigung Ihrer Bewerbung",
     "Sehr geehrter Herr Example,\nvielen Dank für Ihre Bewerbung und Ihr Interesse an der Globex GmbH. Wir haben Ihre "
     "Unterlagen erhalten und prüfen diese sorgfältig. Sollten Sie in die engere Auswahl kommen, laden wir Sie zu "
     "einem persönlichen Gespräch ein. Bitte haben Sie Verständnis, dass dies leider einige Wochen dauern kann. Sie "
     "erhalten in jedem Fall eine Zu- oder Absage von uns. Bitte antworten Sie nicht auf diese E-Mail."),
    ("fr", "Confirmation de votre candidature – Stagiaire Développeur",
     "Bonjour Alex,\nNous vous remercions pour votre candidature au poste de Stagiaire Développeur. Nous avons bien reçu "
     "votre dossier et l'étudierons avec attention. Si votre profil correspond à nos besoins, nous vous contacterons "
     "pour organiser un entretien. Sans réponse de notre part sous 4 semaines, considérez que votre candidature n'a pas "
     "été retenue. Merci de ne pas répondre à cet e-mail."),
    ("es", "Hemos recibido tu candidatura",
     "Hola Alex:\nGracias por tu candidatura para el puesto de Becario de Desarrollo. Hemos recibido tu CV y lo "
     "revisaremos en los próximos días. Si tu perfil encaja con lo que buscamos, nos pondremos en contacto contigo para "
     "concertar una entrevista. Por favor, no respondas a este correo."),
    ("nl", "Bedankt voor je sollicitatie",
     "Beste Alex,\nBedankt voor je sollicitatie als Junior Developer bij Globex. We hebben je sollicitatie in goede orde "
     "ontvangen. Als je profiel aansluit bij de functie, nemen we contact met je op om een gesprek in te plannen."),
    ("it", "Conferma ricezione candidatura",
     "Ciao Alex,\ngrazie per la tua candidatura per la posizione di Stagista Sviluppatore. Abbiamo ricevuto il tuo CV "
     "e lo valuteremo con attenzione. Se il tuo profilo sarà in linea con la posizione, verrai contattato per fissare "
     "un colloquio. Ti preghiamo di non rispondere a questa email."),
    ("pt", "Recebemos sua candidatura",
     "Olá Alex,\nobrigado pela sua candidatura à vaga de Estagiário de Desenvolvimento. Recebemos o seu currículo e "
     "vamos analisá-lo com atenção. Caso seu perfil seja selecionado, entraremos em contato para agendar uma "
     "entrevista. Por favor, não responda a este e-mail."),
]
REJECTION = [
    ("de", "Ihre Bewerbung bei Globex",
     "Sehr geehrter Herr Example,\nvielen Dank für Ihr Interesse an der Position Werkstudent Softwareentwicklung. Nach "
     "sorgfältiger Prüfung müssen wir Ihnen leider mitteilen, dass wir uns für andere Bewerber entschieden haben."),
    ("fr", "Votre candidature chez Globex",
     "Bonjour Alex,\nNous vous remercions de l'intérêt que vous portez à Globex. Malheureusement, nous ne pouvons pas "
     "donner suite à votre candidature pour le poste de Stagiaire Développeur. Nous vous souhaitons bonne continuation."),
    ("es", "Actualización sobre tu candidatura",
     "Hola Alex:\nGracias por tu interés en Globex y por el tiempo dedicado al proceso. Lamentablemente, hemos decidido "
     "continuar con otros candidatos cuyo perfil se ajusta más a los requisitos del puesto."),
    ("nl", "Je sollicitatie bij Globex",
     "Beste Alex,\nBedankt voor je interesse in de functie Junior Developer. Helaas moeten we je teleurstellen: we "
     "hebben besloten om niet verder te gaan met je sollicitatie."),
    ("it", "Aggiornamento sulla tua candidatura",
     "Ciao Alex,\ngrazie per l'interesse dimostrato verso Globex. Purtroppo abbiamo deciso di proseguire con altri "
     "candidati il cui profilo è più in linea con la posizione."),
    ("pt", "Atualização sobre a sua candidatura",
     "Olá Alex,\nagradecemos o seu interesse na Globex. Infelizmente, decidimos seguir com outros candidatos cujo "
     "perfil está mais alinhado à vaga."),
]
INVITE = [
    ("de", "Einladung zum Vorstellungsgespräch – Werkstudent Softwareentwicklung",
     "Hallo Alex,\nvielen Dank für Ihre Bewerbung. Wir möchten Sie gerne zu einem Vorstellungsgespräch einladen. "
     "Bitte wählen Sie über den folgenden Link einen Termin aus."),
    ("fr", "Invitation à un entretien – Stagiaire Développeur",
     "Bonjour Alex,\nSuite à votre candidature, nous avons le plaisir de vous inviter à un entretien. Merci de choisir "
     "un créneau via le lien ci-dessous. N'hésitez pas à nous contacter si vous avez des questions sur la suite du "
     "processus."),
    ("es", "Invitación a entrevista – Becario de Desarrollo",
     "Hola Alex:\nNos gustaría invitarte a una entrevista por videollamada para el puesto de Becario de Desarrollo. "
     "Por favor, elige un horario en el siguiente enlace."),
    ("nl", "Uitnodiging sollicitatiegesprek",
     "Beste Alex,\nGraag nodigen we je uit voor een sollicitatiegesprek bij Globex. Kies via de link een tijdslot dat "
     "je schikt."),
    ("it", "Invito a colloquio – Stagista Sviluppatore",
     "Ciao Alex,\nsiamo lieti di invitarti a un colloquio conoscitivo per la posizione di Stagista Sviluppatore. Ti "
     "preghiamo di scegliere uno slot dal calendario."),
    ("pt", "Convite para entrevista – Estágio em Desenvolvimento",
     "Olá Alex,\ngostaríamos de convidá-lo para uma entrevista online. Por favor, escolha um horário no link abaixo."),
]
# Invites whose subject names no interview: only the invitation wording in the body tells.
INVITE_BODY_ONLY = [
    ("de", "Ihre Bewerbung bei Globex", "Guten Tag Herr Example, vielen Dank für Ihre Bewerbung. Gerne laden wir Sie zu "
     "einem persönlichen Kennenlernen ein."),
    ("fr", "Votre candidature – Stagiaire", "Bonjour Alex, merci pour votre candidature. Votre profil a retenu notre "
     "attention et nous souhaiterions vous rencontrer."),
    ("es", "Tu candidatura en Globex", "Hola Alex: El equipo de selección se ha reunido y nos gustaría conocerte. Te "
     "invitamos a una entrevista el martes."),
    ("nl", "Je sollicitatie bij Globex", "Beste Alex, bedankt voor je sollicitatie als Junior Developer. We willen je "
     "graag uitnodigen voor een kennismakingsgesprek."),
    ("it", "La tua candidatura in Globex", "Ciao Alex, il processo di selezione si è concluso positivamente e vorremmo "
     "invitarti a un colloquio."),
    ("pt", "Sua candidatura na Globex", "Olá Alex, obrigado por se candidatar. Gostaríamos de convidá-lo para uma "
     "entrevista com o time na próxima semana."),
]


@pytest.mark.parametrize("lang,subject,body", RECEIVED, ids=[c[0] for c in RECEIVED])
def test_application_received(lang, subject, body):
    check(subject, body, "applied", "job_info")
    assert history.classify(subject, body[:150]) == "applied"  # read mail: list row without body


@pytest.mark.parametrize("lang,subject,body", REJECTION, ids=[c[0] for c in REJECTION])
def test_rejection(lang, subject, body):
    check(subject, body, "rejection", "job_info")
    assert classify(row(subject, body), TODAY)[1] == "rejection"


@pytest.mark.parametrize("lang,subject,body", INVITE, ids=[c[0] for c in INVITE])
def test_interview_invite(lang, subject, body):
    check(subject, body, "interview", "keep")
    assert history.classify(subject, body[:150]) == "interview"


@pytest.mark.parametrize("lang,subject,body", INVITE_BODY_ONLY, ids=[c[0] for c in INVITE_BODY_ONLY])
def test_interview_invite_in_body_only(lang, subject, body):
    check(subject, body, "interview", "keep")


@pytest.mark.parametrize("subject,snippet", [
    ("Absage: Ihre Bewerbung als Werkstudent", "Sehr geehrter Herr Example, leider können wir Ihre Bewerbung nicht "
                                               "berücksichtigen."),
    ("Rückmeldung zu Ihrer Bewerbung", "Hallo Alex, leider müssen wir dir mitteilen, dass die Stelle bereits besetzt "
                                       "wurde."),
    ("Réponse à votre candidature", "Bonjour Alex, après étude de votre dossier, nous avons le regret de vous informer "
                                    "que votre candidature n'a pas été retenue."),
    ("Votre candidature", "Bonjour Alex, malheureusement nous ne sommes pas en mesure de donner une suite favorable à "
                          "votre candidature."),
    ("Tu candidatura en Globex", "Hola Alex, lamentablemente no vamos a continuar con tu candidatura para el puesto."),
    ("Terugkoppeling sollicitatie", "Beste Alex, helaas hebben we gekozen voor een andere kandidaat."),
    ("Esito candidatura", "Gentile Alex, purtroppo la tua candidatura non è stata selezionata per il ruolo di Stagista."),
    ("Retorno do processo seletivo", "Olá Alex, infelizmente não vamos seguir com a sua candidatura neste momento."),
], ids=["de-absage", "de-besetzt", "fr-regret", "fr-suite", "es", "nl", "it", "pt"])
def test_rejection_without_body(subject, snippet):
    """Read mails come from the list view: subject and snippet only."""
    assert history.classify(subject, snippet) == "rejection"
    assert classify(row(subject, snippet=snippet), TODAY) == ("job_info", "rejection")


def test_tests_offers_alerts_codes():
    assert history.classify("Online-Test für Ihre Bewerbung bei Globex", "Bitte absolvieren Sie den Test.") == "test"
    assert history.classify("Test technique – Stagiaire Développeur", "Voici le lien vers le test en ligne.") == "test"
    assert history.classify("Prueba técnica – Becario", "Te enviamos la prueba técnica.") == "test"
    assert history.classify("Ihre Zusage", "", "Hallo Alex, wir freuen uns, Ihnen die Stelle als Werkstudent anbieten "
                                               "zu können. Anbei finden Sie unser Vertragsangebot.") == "offer"
    assert history.classify("Proposta", "Olá Alex, temos o prazer de lhe oferecer a vaga de Estagiário.") == "offer"
    assert classify(row("Neue Jobs für Sie: Werkstudent Python", snippet="5 neue Stellenangebote"), TODAY)[0] == "noise"
    assert classify(row("Nouvelles offres d'emploi pour vous", snippet="Développeur Python"), TODAY)[0] == "noise"
    assert classify(row("Jobs, die Sie interessieren könnten", snippet="Werkstudent Python"), TODAY)[0] == "noise"
    assert classify(row("„python“: Globex – Python Developer und mehr", snippet=""), TODAY)[0] == "noise"
    assert classify(row("Ihr Bestätigungscode", snippet="Ihr Bestätigungscode lautet 123456."), TODAY) == \
        ("keep", "fresh verification code")
    assert classify(row("Votre code de vérification", snippet="482913"), date(2026, 10, 9)) == \
        ("job_info", "old verification code")
    assert classify(row("Sicherheitswarnung", snippet="Neue Anmeldung bei Ihrem Konto"), TODAY) == \
        ("keep", "account security notice")


def test_recruiter_requests_stay_unread():
    for subject, body in [
        ("Ihre Bewerbung bei Globex", "Hallo Alex, vielen Dank für Ihre Bewerbung. Könnten Sie uns bitte Ihre "
                                      "Gehaltsvorstellung mitteilen?"),
        ("Votre candidature", "Bonjour Alex, merci pour votre candidature. Pourriez-vous nous indiquer vos "
                              "disponibilités ?"),
        ("Tu candidatura", "Hola Alex, gracias por tu candidatura. Por favor, envíanos tu certificado de estudios."),
        ("Je sollicitatie", "Beste Alex, bedankt voor je sollicitatie. Wil je de vragenlijst in de bijlage invullen?"),
        ("La tua candidatura", "Ciao Alex, grazie per la tua candidatura. Ti preghiamo di inviarci il tuo portfolio."),
        ("Sua candidatura", "Olá Alex, obrigado pela sua candidatura. Por favor, preencha o formulário em anexo."),
    ]:
        assert classify(row(subject, body), TODAY) == ("keep", "asks for something / next steps"), subject
    assert classify(row("Ihre Bewerbung – nächste Schritte", snippet="Vielen Dank für Ihre Bewerbung."), TODAY)[0] == "keep"


def test_everyday_rejection_words_do_not_reject_invites():
    """"leider" / "malheureusement" / "helaas" in another sentence of an invite, or a failed call, is no rejection."""
    for subject, body in [
        ("Einladung zum Vorstellungsgespräch", "Hallo Alex, wir möchten Sie gerne zu einem Vorstellungsgespräch "
                                               "einladen. Leider ist unser Büro derzeit im Umbau, daher findet das "
                                               "Gespräch per Video statt."),
        ("Ihre Bewerbung bei Globex", "Guten Tag Herr Example, gerne laden wir Sie zu einem persönlichen Kennenlernen "
                                      "ein. Leider konnten wir Sie telefonisch nicht erreichen."),
        ("Ihre Bewerbung", "Hallo Alex, leider konnten wir Sie nicht erreichen und möchten Sie daher zu einem "
                           "Vorstellungsgespräch einladen."),
        ("Votre candidature", "Bonjour Alex, nous souhaiterions vous rencontrer. Malheureusement, notre bureau est "
                              "fermé cette semaine, l'entretien aura donc lieu en visio."),
        ("Uitnodiging sollicitatiegesprek", "Beste Alex, graag nodigen we je uit voor een gesprek. Helaas is ons kantoor "
                                            "die week gesloten, dus het gesprek is online."),
    ]:
        assert history.classify(subject, body[:150], body) == "interview", body
        assert classify(row(subject, body), TODAY) == ("keep", "interview"), body
    assert history.classify("Einladung zum Vorstellungsgespräch", "Hallo Alex, leider ist der ursprüngliche Termin "
                                                                  "nicht mehr frei.") == "interview"


def test_conditional_rejection_sentences_in_confirmations():
    """Confirmations say what happens without an answer ("Sollten Sie nichts hören, … leider nicht berücksichtigen",
    "Als je niets hoort, … helaas niet geselecteerd", "Zu- oder Absage"): no rejection."""
    for subject, body in [
        ("Ihre Bewerbung bei Globex", "Sehr geehrter Herr Example, vielen Dank für Ihre Bewerbung. Leider ist es uns "
         "nicht möglich, jede Bewerbung individuell zu beantworten. Bitte beachten Sie, dass unvollständige Bewerbungen "
         "nicht berücksichtigt werden können. Sollten Sie innerhalb von 4 Wochen nichts von uns hören, können wir Ihre "
         "Bewerbung leider nicht berücksichtigen. Wir haben Ihre Bewerbung für weitere Bewerbungsverfahren gespeichert."),
        ("Ihre Bewerbung bei Globex", "Hallo Alex, vielen Dank für deine Bewerbung als Werkstudent. Wir melden uns bei "
         "dir, um ein Vorstellungsgespräch zu vereinbaren, sobald wir alle Bewerbungen gesichtet haben. Sollten Sie "
         "innerhalb von vier Wochen nichts von uns hören, betrachten Sie Ihre Bewerbung bitte als nicht berücksichtigt."),
        ("Votre candidature chez Globex", "Bonjour Alex, merci pour votre candidature. Compte tenu du nombre de "
         "candidatures, nous ne pouvons proposer un entretien à tous les candidats. Si votre candidature n'est pas "
         "retenue, nous conserverons votre CV."),
        ("Tu candidatura en Globex", "Hola Alex, gracias por tu candidatura. Si no recibes respuesta en 15 días, "
         "considera que tu candidatura no ha sido seleccionada."),
        ("Bedankt voor je sollicitatie", "Beste Alex, bedankt voor je sollicitatie. Als je binnen twee weken niets hebt "
         "gehoord, is je sollicitatie helaas niet geselecteerd."),
        ("Grazie per la tua candidatura", "Ciao Alex, grazie per la tua candidatura. Qualora la tua candidatura non venga "
         "selezionata, ti informeremo via email."),
        ("Recebemos sua candidatura", "Olá Alex, obrigado pela sua candidatura. Caso sua candidatura não seja "
         "selecionada, manteremos seu currículo em nosso banco de talentos."),
    ]:
        assert history.classify(subject, body[:150], body) == "applied", body
        assert classify(row(subject, body), TODAY) == ("job_info", "applied"), body
    assert history.classify("Bedankt voor je sollicitatie", "Beste Alex, mocht je binnen twee weken niets horen, dan is "
                                                            "je sollicitatie helaas niet geselecteerd.") == "applied"


def test_rejection_beats_interview_wording():
    """A rejection after the interview, or one that refuses the interview, is a rejection; a negated offer is no offer."""
    for subject, body in [
        ("Ihr Vorstellungsgespräch bei Globex", "Hallo Alex,\nvielen Dank für das angenehme Vorstellungsgespräch letzte "
                                                "Woche. Leider haben wir uns für einen anderen Kandidaten entschieden."),
        ("Ihre Bewerbung", "Sehr geehrter Herr Example, nach Prüfung Ihrer Unterlagen müssen wir Ihnen mitteilen, dass "
                           "wir Sie leider nicht zu einem Vorstellungsgespräch einladen können."),
        ("Je sollicitatie", "Beste Alex, na zorgvuldige overweging kunnen we je helaas niet uitnodigen voor een gesprek."),
        ("Candidatura", "Ciao Alex, dopo il colloquio non possiamo invitarti alla fase successiva."),
        ("Ihre Bewerbung", "Hallo Alex, leider können wir Ihnen kein Vertragsangebot machen."),
    ]:
        assert history.classify(subject, body[:150], body) == "rejection", body
        assert classify(row(subject, body), TODAY) == ("job_info", "rejection"), body


def test_other_precision_guards():
    # "agent d'entretien" is a cleaning job; "Entretien – X" alone is an interview
    assert history.classify("Votre candidature au poste d'agent d'entretien", "Merci pour votre candidature.") == "applied"
    assert history.classify("Entretien – Globex", "Bonjour Alex, êtes-vous disponible mardi à 10h ?") == "interview"
    # a recruiter cancelling an appointment ("Termin absagen") is no rejection
    assert history.classify("Ihr Termin am Montag", "", "Hallo Alex, leider muss ich unseren Termin am Montag absagen. "
                                                        "Wann passt es Ihnen nächste Woche?") != "rejection"
    # the reflexive "se" / "-se" (es, pt) is no "if" that would block the invite
    assert history.classify("Tu candidatura", "", "Hola Alex, tu perfil se ajusta a lo que buscamos y nos gustaría "
                                                  "invitarte a una entrevista.") == "interview"
    assert history.classify("Sua candidatura", "", "Olá Alex, você candidatou-se a uma vaga e gostaríamos de convidá-lo "
                                                   "para uma entrevista.") == "interview"


def test_gmail_query_covers_the_languages_and_stays_short():
    q = history.GMAIL_QUERY
    assert len(q) < 2000 and q.endswith(" newer_than:2y")  # cli.py replaces the period
    for word in ("Bewerbung", "Absage", "Vorstellungsgespräch", "candidature", "entretien", "candidatura", "entrevista",
                 "sollicitatie", "colloquio", "aplikacja", "interview"):
        assert f" {word} " in q.replace("(", " ").replace(")", " ")


def test_fold_keeps_letters_nfkd_cannot_split():
    assert history.fold("Außendienst Œuvre") == "aussendienst oeuvre"


def test_german_rejection_syncs_and_names_other_role(tmp_path):
    """A German rejection flips the single open application; one naming another role ("Bewerbung als …") does not."""
    conn = connect(tmp_path / "t.db")
    for i, company, title in ((1, "Globex GmbH", "Werkstudent Softwareentwicklung (m/w/d)"),
                              (2, "Initech AG", "Werkstudent Data Engineering (m/w/d)")):
        conn.execute("INSERT INTO jobs (id, dedupe_key, source, title, company, url, scraped_at, status) "
                     "VALUES (?,?,?,?,?,?,?,?)", (i, f"k{i}", "x", title, company, "u", "2026-10-01", "applied"))
        conn.execute("INSERT INTO applications (job_id, applied_at) VALUES (?, ?)", (i, "2026-10-01T10:00:00"))
    history.import_rows(conn, [
        {"thread_id": "g", "date": "Tue, Oct 6, 2026, 9:00 AM", "sender_name": "Globex Karriere",
         "sender_email": "karriere@globex.de", "subject": "Ihre Bewerbung bei Globex", "snippet": "",
         "body": "Sehr geehrter Herr Example, vielen Dank für Ihr Interesse. Nach sorgfältiger Prüfung müssen wir Ihnen "
                 "leider mitteilen, dass wir uns für andere Bewerber entschieden haben."},
        {"thread_id": "i", "date": "Tue, Oct 6, 2026, 10:00 AM", "sender_name": "Initech Karriere",
         "sender_email": "karriere@initech.de", "subject": "Ihre Bewerbung bei Initech", "snippet": "",
         "body": "Vielen Dank für Ihre Bewerbung als Werkstudent Marketing. Leider haben wir uns für einen anderen "
                 "Kandidaten entschieden."},
    ])
    assert {c["id"]: c["to"] for c in mailcheck.sync_job_statuses(conn)} == {1: "rejected"}


def test_french_confirmation_syncs_applied(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO jobs (dedupe_key, source, title, company, url, scraped_at, status) "
                 "VALUES ('k', 'x', 'Stagiaire Développeur', 'Globex', 'u', '2026-10-01', 'scored')")
    history.import_rows(conn, [{"thread_id": "f", "date": "Tue, Oct 6, 2026, 9:00 AM", "sender_name": "Globex",
                                "sender_email": "recrutement@globex.fr",
                                "subject": "Votre candidature au poste de Stagiaire Développeur",
                                "snippet": "Nous avons bien reçu votre candidature."}])
    assert [r["title"] for r in history.annotate(conn)["applied_synced"]] == ["Stagiaire Développeur"]


def test_european_legal_forms_are_not_part_of_the_name():
    from jobhunt.history import company_core
    assert company_core("Globex GmbH & Co. KG") == "globex"
    assert company_core("Initech B.V.") == "initech"
    assert company_core("Acme S.p.A.") == "acme"
    assert company_core("Hooli S.L.") == "hooli"
