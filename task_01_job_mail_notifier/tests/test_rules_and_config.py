import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import classify  # noqa: E402
import config  # noqa: E402
import gmail_imap  # noqa: E402
import mailparse  # noqa: E402
import rules  # noqa: E402
from classify import classify as cat  # noqa: E402
from models import Category  # noqa: E402

RULES = """
job_boards = ["www.stepstone.de"]
keywords = ["praktikum"]
[phrases]
interview = ["panel discussion"]
job_alert = ["stellenangebot"]
bogus = ["x"]
[noise]
senders = ["spam.example.com"]
subjects = ["weekly digest"]
"""


class RulesTests(unittest.TestCase):
    def setUp(self):
        saved = (list(mailparse.JOB_BOARDS), set(mailparse.PORTALS), list(gmail_imap.KEYWORDS),
                 classify.NOISE_SENDERS, classify.USER_NOISE, dict(classify.USER_PHRASES))

        def restore():
            mailparse.JOB_BOARDS[:] = saved[0]
            mailparse.PORTALS.clear()
            mailparse.PORTALS.update(saved[1])
            gmail_imap.KEYWORDS[:] = saved[2]
            classify.NOISE_SENDERS, classify.USER_NOISE = saved[3], saved[4]
            classify.USER_PHRASES.clear()
            classify.USER_PHRASES.update(saved[5])
        self.addCleanup(restore)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rules.toml"
            path.write_text(RULES, encoding="utf-8")
            with self.assertLogs("rules", "WARNING"):  # unknown [phrases] key is reported
                rules.apply(rules.load(path))

    def test_user_rules_extend_the_built_in_ones(self):
        self.assertIn("stepstone.de", mailparse.JOB_BOARDS)
        self.assertIn("from:stepstone.de", gmail_imap.build_query(__import__("datetime").datetime(2026, 1, 1)))
        self.assertIn("praktikum", gmail_imap.KEYWORDS)
        self.assertEqual(cat("Team update", "", "Let's have a panel discussion on Friday"), Category.INTERVIEW)
        self.assertEqual(cat("Neues Stellenangebot für Sie", "x@jobs.example.de", ""), Category.JOB_ALERT)
        self.assertEqual(cat("Your weekly profile", "x@stepstone.de", "new jobs for you"), Category.JOB_ALERT)
        self.assertIsNone(cat("Interview invitation", "hr@spam.example.com", ""))
        self.assertIsNone(cat("Weekly digest: hiring news", "a@b.com", ""))
        # whole words only
        self.assertIsNone(cat("Stellenangebote", "a@b.com", ""))

    def test_missing_or_broken_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(rules.load(Path(d) / "none.toml"), {})
            bad = Path(d) / "bad.toml"
            bad.write_text("job_boards = [", encoding="utf-8")
            with self.assertLogs("rules", "ERROR"):
                self.assertEqual(rules.load(bad), {})

    def test_phrase_regex_is_literal_and_whole_word(self):
        rx = rules.phrase_regex(["c++ role", "hr"])
        self.assertTrue(rx.search("A C++ Role for you"))
        self.assertTrue(rx.search("call from HR."))
        self.assertFalse(rx.search("three"))
        self.assertIsNone(rules.phrase_regex([]))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name, value in (("ENV_FILE", self.dir / ".env"), ("BASE_DIR", self.dir),
                            ("DATA_DIR", self.dir / "data"), ("STATE_FILE", self.dir / "data" / "state.json"),
                            ("HISTORY_FILE", self.dir / "data" / "history.json")):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)

    def test_set_env_value_updates_adds_and_removes(self):
        config.ENV_FILE.write_text("# note\nGMAIL_ADDRESS=old@gmail.com\nGMAIL_APP_PASSWORD=secret\nTHEME=dark\n",
                                   encoding="utf-8")
        config.set_env_value("GMAIL_ADDRESS", "new@gmail.com")
        config.set_env_value("GMAIL_APP_PASSWORD", None)
        config.set_env_value("HISTORY_DAYS", "30")
        self.assertEqual(config.ENV_FILE.read_text(encoding="utf-8"),
                         "# note\nGMAIL_ADDRESS=new@gmail.com\nTHEME=dark\nHISTORY_DAYS=30\n")

    def test_env_password_wins_and_spaces_are_removed(self):
        config.ENV_FILE.write_text("GMAIL_ADDRESS=a@gmail.com\nGMAIL_APP_PASSWORD=abcd efgh ijkl mnop\n", encoding="utf-8")
        self.assertEqual(config.load_settings().gmail_app_password, "")  # not read unless asked
        self.assertEqual(config.load_settings(with_secret=True).gmail_app_password, "abcdefghijklmnop")

    def test_legacy_files_move_into_data(self):
        (self.dir / "state.json").write_text("{}", encoding="utf-8")
        (self.dir / "history.json").write_text("[]", encoding="utf-8")
        config.migrate_legacy_files()
        self.assertTrue(config.STATE_FILE.exists())
        self.assertTrue(config.HISTORY_FILE.exists())
        self.assertFalse((self.dir / "state.json").exists())


if __name__ == "__main__":
    unittest.main()
