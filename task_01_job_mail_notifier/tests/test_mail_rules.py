import sys
import tempfile
import time
import unittest
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import main  # noqa: E402
from classify import classify, extract_when, snippet  # noqa: E402
from gmail_imap import GmailAuthError, build_mail, build_query, gmail_link, parse_meta  # noqa: E402
from mailparse import parse_ics, parse_message, pick_links, site  # noqa: E402
from models import Category  # noqa: E402
from report import write_report  # noqa: E402

ME = "me@gmail.com"


def raw_mail(subject, body="", html=None, sender="Acme HR <hr@acme.com>", ics=None) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ME, subject
    msg.set_content(body or "(see html)")
    if html:
        msg.add_alternative(html, subtype="html")
    if ics:
        msg.add_attachment(ics.encode(), maintype="text", subtype="calendar", filename="invite.ics")
    return msg.as_bytes()


class ClassifyTests(unittest.TestCase):
    def cat(self, subject, body="", sender="hr@acme.com", invite=False):
        return classify(subject, sender, body, invite)

    def test_interviews(self):
        self.assertEqual(self.cat("Interview invitation - SDET"), Category.INTERVIEW)
        self.assertEqual(self.cat("Job | Python Engineer- F2F interview on 14th Nov | Pro Nvite",
                                  sender="x@naukri.com"), Category.INTERVIEW)
        self.assertEqual(self.cat("Next steps", "We would like to schedule an interview with you."), Category.INTERVIEW)
        self.assertEqual(self.cat("Online assessment for QA role"), Category.INTERVIEW)
        self.assertEqual(self.cat("Invitation: Discussion", "Role: SDET", invite=True), Category.INTERVIEW)

    def test_calls(self):
        self.assertEqual(self.cat("Quick call?", "I tried to reach you about a QA Lead position."), Category.CALL)
        self.assertEqual(self.cat("Telephonic discussion for the Automation role"), Category.CALL)
        self.assertEqual(self.cat("Hello", "Can we set up a call to discuss the opening? Please share your phone number."),
                         Category.CALL)
        # a sales "call" without any job context is not a job mail
        self.assertIsNone(self.cat("Book a call with our team", "Grow your business 3x with our CRM."))

    def test_application_updates(self):
        self.assertEqual(self.cat("You applied for 2 jobs on 17 Nov", sender="info@naukri.com"), Category.APPLICATION)
        self.assertEqual(self.cat("Recruiter viewed your profile", sender="info@naukri.com"), Category.APPLICATION)
        self.assertEqual(self.cat("Update", "We regret to inform you that your application was not selected."),
                         Category.APPLICATION)

    def test_job_alerts(self):
        self.assertEqual(self.cat("CGI is hiring: SDET"), Category.JOB_ALERT)
        self.assertEqual(self.cat("12 new jobs for 'python automation'", sender="jobalerts-noreply@linkedin.com"),
                         Category.JOB_ALERT)
        self.assertEqual(self.cat('"sdet": Acme - Senior SDET and more', sender="jobalerts-noreply@linkedin.com"),
                         Category.JOB_ALERT)

    def test_cases_seen_in_the_real_inbox(self):
        self.assertEqual(self.cat("1 recruiter(s) expressed interest in your Naukri profile", sender="info@naukri.com"),
                         Category.APPLICATION)
        self.assertEqual(self.cat("Feedback on your Naukri job applies done since last week", sender="info@naukri.com"),
                         Category.APPLICATION)
        # subject says alert, body mentions "applied": the subject wins
        self.assertEqual(self.cat("Alex, Urgently hiring for Automation Engineer", "Jobs you applied for...",
                                  sender="info@naukri.com"), Category.JOB_ALERT)
        self.assertEqual(self.cat("Job | Hiring for Embedded Python Automation Engineer | Pro Nvite",
                                  "Thank you for applying", sender="x@naukri.com"), Category.JOB_ALERT)
        self.assertEqual(self.cat("New Nokia career opportunities at Nokia", sender="donotreply.nokiaME@people.nokia.com"),
                         Category.JOB_ALERT)
        self.assertEqual(self.cat("Where would you fit at IBM?", "Explore open roles.", sender="talent@ibm.com"),
                         Category.JOB_ALERT)
        self.assertIsNone(self.cat("Salary & interview insights for Sdet Engineer roles", sender="noreply@ambitionbox.com"))
        self.assertIsNone(self.cat("What's new at logcat.ai: the Claude connector", "Your application logs, role-based access"))
        self.assertIsNone(self.cat("HARMAN: Data Privacy Policy - No Response", sender="noreply@careers.harman.com"))
        self.assertIsNone(self.cat("Feedback for your Applied Jobs interview preparation is here!", sender="info@naukri.com"))

    def test_noise(self):
        self.assertIsNone(self.cat("Run failed: CI job build-and-test", sender="notifications@github.com"))
        self.assertIsNone(self.cat("[repo] Build job failed on main"))
        self.assertIsNone(self.cat("Top 50 interview questions for testers", sender="news@naukri.com"))
        self.assertIsNone(self.cat("Webinar: hiring trends 2027"))
        self.assertIsNone(self.cat("Ravi commented on your post", sender="notifications-noreply@linkedin.com"))
        self.assertIsNone(self.cat("Your order has shipped", "Track your package."))


class WhenAndSnippetTests(unittest.TestCase):
    def test_labelled_lines(self):
        text = "Dear Candidate,\nDate: 20th Nov 2026\nTime: 3:00 PM IST\nVenue: Koramangala office\nRegards"
        self.assertEqual(extract_when(text), "Date: 20th Nov 2026 · Time: 3:00 PM IST · Venue: Koramangala office")

    def test_sentence_with_date_and_time(self):
        self.assertEqual(extract_when("Hi,\nYour interview is on Friday 21 Nov at 11:30 AM on Teams.\nThanks"),
                         "Your interview is on Friday 21 Nov at 11:30 AM on Teams.")
        self.assertEqual(extract_when("No dates in here."), "")
        self.assertEqual(extract_when("whatever", invite_when="Thu 20 Nov, 3:00 PM"), "Thu 20 Nov, 3:00 PM")

    def test_snippet_skips_greeting_and_boilerplate(self):
        text = "View this email in your browser\nHi Alex,\nGreat news! Acme has shortlisted your profile for SDET.\nUnsubscribe"
        self.assertEqual(snippet(text), "Great news! Acme has shortlisted your profile for SDET.")
        self.assertTrue(snippet("word " * 100).endswith("…"))


class LinkTests(unittest.TestCase):
    def test_picks_portal_and_meeting_links_and_skips_junk(self):
        anchors = [
            ("Unsubscribe", "https://www.naukri.com/unsubscribe?x=1"),
            ("", "https://www.facebook.com/naukri"),
            ("Senior SDET - Acme", "https://www.naukri.com/job-listings-123"),
            ("Apply now", "https://acme.wd3.myworkdayjobs.com/job/456"),
            ("Privacy policy", "https://www.naukri.com/privacy"),
            ("click here", "https://teams.microsoft.com/l/meetup-join/abc"),
            ("Read our blog", "https://blog.example.com/post"),
            ("Apply now", "https://www.naukri.com/apply/789"),
        ]
        links = pick_links(anchors)
        self.assertEqual(links[0], {"label": "Join Teams meeting", "url": "https://teams.microsoft.com/l/meetup-join/abc"})
        labels = [link["label"] for link in links]
        self.assertIn("Apply now", labels)
        self.assertIn("Senior SDET - Acme", labels)
        self.assertEqual(labels.count("Apply now"), 1)  # same label kept once
        self.assertFalse(any("unsubscribe" in link["url"] or "facebook" in link["url"] or "blog" in link["url"]
                             for link in links))

    def test_naukri_tracking_links_keep_only_useful_ones(self):
        track = "https://tr.naukri.com?data=%7B{}%7D"
        anchors = [(text, track.format(i)) for i, text in enumerate(
            ["Apply now", "Facebook", "naukri.com", "Get App", "Twitter", "Update profile", "Add missing details",
             "block this recruiter from searching your resume.", "Security Advice", "View details"])]
        self.assertEqual([link["label"] for link in pick_links(anchors)], ["Apply now", "View details"])

    def test_placeholder_invite_date_is_ignored(self):
        self.assertEqual(parse_ics("BEGIN:VEVENT\nDTSTART:00010101T000000\nEND:VEVENT")[0], "")

    def test_plain_text_urls_get_site_labels(self):
        links = pick_links([("", "https://www.naukri.com/job/1"), ("", "https://example.com/x")], allow_untitled=True)
        self.assertEqual(links, [{"label": "Open on naukri.com", "url": "https://www.naukri.com/job/1"}])

    def test_site(self):
        self.assertEqual(site("https://www.jobs.naukri.com/a"), "naukri.com")
        self.assertEqual(site("https://careers.acme.co.in/x"), "acme.co.in")


class ParseMessageTests(unittest.TestCase):
    def test_html_mail_text_and_anchors(self):
        html = ("<html><head><style>p{color:red}</style></head><body><p>Hi there,</p>"
                "<p>Acme&nbsp;is hiring an <b>SDET</b>.</p><a href='https://www.naukri.com/j/1?a=1&amp;b=2'>"
                "<img alt='Apply'></a></body></html>")
        p = parse_message(raw_mail("Acme is hiring", html=html, sender='"Naukri" <info@naukri.com>'))
        self.assertEqual((p.sender_name, p.sender_addr), ("Naukri", "info@naukri.com"))
        self.assertIn("Acme is hiring an SDET.", p.text)
        self.assertNotIn("color:red", p.text)
        self.assertEqual(p.anchors, [("Apply", "https://www.naukri.com/j/1?a=1&b=2")])

    def test_calendar_invite(self):
        ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;TZID=India Standard Time:20261120T150000\r\n"
               "LOCATION:Microsoft Teams Meeting\r\nDESCRIPTION:Join: https://teams.microsoft.com/l/meetup-join/x\r\n"
               "END:VEVENT\r\nEND:VCALENDAR\r\n")
        when, urls = parse_ics(ics)
        self.assertEqual(when, "Fri 20 Nov, 3:00 PM · Microsoft Teams Meeting")
        self.assertEqual(urls, ["https://teams.microsoft.com/l/meetup-join/x"])
        p = parse_message(raw_mail("Invitation: Technical round", "Role: SDET", ics=ics))
        self.assertTrue(p.has_invite)
        self.assertEqual(p.invite_when, when)


class BuildMailTests(unittest.TestCase):
    def test_interview_mail_end_to_end(self):
        html = ("<p>Dear Alex,</p><p>We are pleased to invite you to an interview for the SDET role.</p>"
                "<p>Date: 21 Nov 2026</p><p>Time: 11:00 AM IST</p>"
                "<a href='https://teams.microsoft.com/l/meetup-join/1'>Join the interview</a>"
                "<a href='https://acme.com/unsubscribe'>Unsubscribe</a>")
        m = build_mail(raw_mail("Interview for SDET at Acme", html=html), "1a2b", 1_795_000_000_000, ME)
        self.assertEqual(m.category, Category.INTERVIEW)
        self.assertEqual(m.sender, "Acme HR")
        self.assertEqual(m.when, "Date: 21 Nov 2026 · Time: 11:00 AM IST")
        self.assertEqual(m.view_url, "https://mail.google.com/mail/?authuser=me@gmail.com#all/1a2b")
        self.assertEqual(m.links, [{"label": "Join Teams meeting", "url": "https://teams.microsoft.com/l/meetup-join/1"}])
        self.assertIn("invite you to an interview", m.summary)

    def test_non_job_and_own_mail_are_skipped(self):
        self.assertIsNone(build_mail(raw_mail("Your order has shipped", "Track it."), "1", 0, ME))
        self.assertIsNone(build_mail(raw_mail("Interview notes", "x", sender=f"Me <{ME}>"), "2", 0, ME))


class ImapHelperTests(unittest.TestCase):
    def test_query(self):
        q = build_query(datetime(2026, 11, 5, 14, 30))
        self.assertTrue(q.startswith("in:inbox -from:me after:2026/11/05 {"))
        self.assertIn("from:naukri.com", q)
        self.assertIn(" call ", q)
        self.assertTrue(q.endswith("}"))

    def test_parse_fetch_meta(self):
        line = b'7 (X-GM-MSGID 1881713115952046520 INTERNALDATE "14-Nov-2026 10:04:27 +0000" UID 52)'
        uid, mid, ms = parse_meta(line)
        self.assertEqual((uid, mid), (52, "1a1d301bf583e1b8"))
        self.assertEqual(ms, int(datetime.fromisoformat("2026-11-14T10:04:27+00:00").timestamp()) * 1000)
        self.assertIsNone(parse_meta(b"7 (UID 52)"))

    def test_gmail_link(self):
        self.assertEqual(gmail_link("a.b@gmail.com", "ff"), "https://mail.google.com/mail/?authuser=a.b@gmail.com#all/ff")


class FailureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name in ("STATE_FILE", "STATUS_FILE"):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, Path(tmp.name) / f"{name}.json")
        import notifier
        self.toasts = []
        self.addCleanup(setattr, notifier, "notify_error", notifier.notify_error)
        notifier.notify_error = lambda msg, path, title="": self.toasts.append(msg)

    def test_error_goes_to_status_and_toasts_once_per_streak(self):
        exc = GmailAuthError("Gmail login failed: check GMAIL_APP_PASSWORD")
        main.report_failure(exc)
        main.report_failure(exc)
        self.assertEqual(self.toasts, [str(exc)])
        status = main.read_json(config.STATUS_FILE, {})
        self.assertEqual((status["state"], status["message"]), ("error", str(exc)))


class WindowTests(unittest.TestCase):
    def test_first_run_uses_lookback(self):
        s = config.Settings(first_run_lookback_days=2)
        after = main.compute_after({}, s, None)
        self.assertAlmostEqual(time.time() - after.timestamp(), 2 * 86400, delta=5)

    def test_since_last_run_with_overlap_and_cap(self):
        s = config.Settings(max_lookback_days=7)
        now = time.time()
        self.assertAlmostEqual(main.compute_after({"last_success_epoch": now - 600}, s, None).timestamp(), now - 4200, delta=5)
        self.assertAlmostEqual(main.compute_after({"last_success_epoch": now - 30 * 86400}, s, None).timestamp(), now - 7 * 86400, delta=5)


class ReportTests(unittest.TestCase):
    def test_report_escapes_groups_and_links(self):
        m = build_mail(raw_mail("<b>Interview</b> for SDET", html="<p>Time: 10 AM</p>"
                                "<a href='https://www.naukri.com/apply'>Apply</a>"), "1a", int(time.time() * 1000), ME)
        with tempfile.TemporaryDirectory() as d:
            html = write_report([m], datetime.now(), Path(d), frozenset({"1a"})).read_text(encoding="utf-8")
        self.assertIn("&lt;b&gt;Interview&lt;/b&gt;", html)
        self.assertIn("Today ·", html)
        self.assertIn('class="card hl new"', html)
        self.assertIn('href="https://www.naukri.com/apply"', html)
        self.assertIn('href="https://mail.google.com/', html)


if __name__ == "__main__":
    unittest.main()
