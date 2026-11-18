import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from models import Category, JobMail, sort_mails  # noqa: E402
from store import load_mails, merge, save_mails  # noqa: E402


def mail(mid, ms=1000, cat=Category.JOB_ALERT, summary="s"):
    return JobMail(mid, cat, "Sender", "Subj", ms, summary, "https://mail.google.com/mail/")


class StoreTests(unittest.TestCase):
    def test_roundtrip(self):
        m = mail("a", cat=Category.INTERVIEW)
        m.when = "Mon 10 AM"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            save_mails(p, [m])
            self.assertEqual(load_mails(p), [m])

    def test_missing_or_corrupt_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            self.assertEqual(load_mails(p), [])
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_mails(p), [])
            p.write_text('[{"message_id": "x", "category": "bogus"}]', encoding="utf-8")
            self.assertEqual(load_mails(p), [])

    def test_merge_dedups_newer_wins_and_prunes(self):
        merged = merge([mail("a", summary="old"), mail("old", ms=10)], [mail("a", summary="new"), mail("b")], 100)
        self.assertEqual(sorted(m.message_id for m in merged), ["a", "b"])
        self.assertEqual(next(m for m in merged if m.message_id == "a").summary, "new")

    def test_sort_newest_first(self):
        got = sort_mails([mail("alert", 900), mail("int", 100, Category.INTERVIEW), mail("alert2", 950)])
        self.assertEqual([m.message_id for m in got], ["alert2", "alert", "int"])

    def test_links_roundtrip_and_old_records_without_links(self):
        m = mail("a", cat=Category.CALL)
        m.links = [{"label": "Apply", "url": "https://www.naukri.com/x"}]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            save_mails(p, [m])
            self.assertEqual(load_mails(p), [m])
            p.write_text('[{"message_id": "x", "category": "interview", "sender": "s", "subject": "t", '
                         '"received_ms": 1, "summary": "", "view_url": "u"}]', encoding="utf-8")
            self.assertEqual(load_mails(p)[0].links, [])


class WhoTests(unittest.TestCase):
    def test_who_prefers_company_and_prettifies_addresses(self):
        m = mail("a")
        m.company = "Acme"
        self.assertEqual(m.who, "Acme")
        m.company, m.sender = "", "info@naukri.com"
        self.assertEqual(m.who, "Naukri")
        m.sender = "jobs-noreply@mail.linkedin.com"
        self.assertEqual(m.who, "Linkedin")
        m.sender = "Naukri Alerts"
        self.assertEqual(m.who, "Naukri Alerts")


class LockTests(unittest.TestCase):
    def test_second_run_is_refused_while_first_holds_lock(self):
        with main.single_run() as first:
            self.assertTrue(first)
            code = "import main; cm = main.single_run(); print(cm.__enter__())"
            out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True).stdout.strip()
            self.assertEqual(out, "False")
        with main.single_run() as again:
            self.assertTrue(again)


if __name__ == "__main__":
    unittest.main()
