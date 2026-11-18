"""Read job mails straight from Gmail over IMAP (app password), independent of Claude.

Gmail's IMAP extensions do the heavy lifting: X-GM-RAW runs a normal Gmail search, X-GM-MSGID gives
the same message id the Gmail web app / API uses (for "Open in Gmail" links). The mailbox is opened
read-only and bodies are fetched with BODY.PEEK, so nothing is marked as read or changed.
"""
from __future__ import annotations

import imaplib
import logging
import re
import socket
import time
from datetime import datetime
from urllib.parse import quote

from classify import classify, extract_when, snippet
from mailparse import JOB_BOARDS, parse_message, pick_links
from models import Category, JobMail

log = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
MAX_BODY_BYTES = 400_000  # enough for the text; skips most of a big attachment
MAX_MESSAGES = 400        # per check (the first run looks back several days)

# Gmail-side pre-filter so only plausible mails are downloaded; classify.py makes the real decision.
KEYWORDS = [
    "interview", "vacancy", "vacancies", "hiring", "job", "jobs", "recruiter", "recruitment",
    "application", "applied", "shortlisted", "opening", "openings", "position", "career",
    "careers", "candidate", "candidature", "offer", "assessment", "round", "walkin",
    "call", "phone", "telephonic", "callback",
]


class GmailAuthError(RuntimeError):
    """Login refused: wrong address / app password, or IMAP access is off."""


class SetupError(RuntimeError):
    """Something the user has to configure first."""


def build_query(after: datetime) -> str:
    # `after:` is date-granular (midnight); exact filtering happens on INTERNALDATE.
    words = " ".join(KEYWORDS)
    boards = " ".join(f"from:{d}" for d in JOB_BOARDS)
    return f"in:inbox -from:me after:{after:%Y/%m/%d} {{{words} {boards}}}"


def gmail_link(account: str, message_id: str) -> str:
    return f"https://mail.google.com/mail/?authuser={quote(account, safe='@')}#all/{message_id}"


def _quote(arg: str) -> str:
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_meta(line: bytes) -> tuple[int, str, int] | None:
    """b'7 (X-GM-MSGID 1881... INTERNALDATE "14-Nov-2026 10:04:27 +0000" UID 52)' -> (uid, hex id, epoch ms)."""
    uid = re.search(rb"\bUID (\d+)", line)
    gmid = re.search(rb"X-GM-MSGID (\d+)", line)
    stamp = imaplib.Internaldate2tuple(line)
    if not (uid and gmid and stamp):
        return None
    return int(uid.group(1)), format(int(gmid.group(1)), "x"), int(time.mktime(stamp)) * 1000


def wait_for_network(host: str = IMAP_HOST, port: int = IMAP_PORT, attempts: int = 10, wait_s: int = 15) -> None:
    """Right after logon Wi-Fi is often not connected yet."""
    for attempt in range(1, attempts + 1):
        try:
            socket.create_connection((host, port), timeout=5).close()
            return
        except OSError as exc:
            log.info("Network not ready (%d/%d): %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(wait_s)
    raise ConnectionError("No internet connection (could not reach Gmail)")


def build_mail(raw: bytes, message_id: str, received_ms: int, account: str) -> JobMail | None:
    p = parse_message(raw)
    if account and p.sender_addr == account.lower():
        return None
    category = classify(p.subject, p.sender_addr, p.text, p.has_invite)
    if category is None:
        return None
    when = extract_when(p.text, p.invite_when) if category in (Category.INTERVIEW, Category.CALL) else ""
    return JobMail(
        message_id=message_id,
        category=category,
        sender=p.sender_name or p.sender_addr,
        subject=p.subject,
        received_ms=received_ms,
        summary=snippet(p.text, p.subject),
        view_url=gmail_link(account, message_id),
        when=when,
        links=pick_links(p.anchors, allow_untitled=p.plain_urls, extra_urls=p.invite_urls),
    )


def fetch_job_mails(account: str, app_password: str, after: datetime, seen: set[str]
                    ) -> tuple[list[JobMail], dict[str, int]]:
    """Returns (job mails, {message id: received ms} of every mail looked at, job-related or not)."""
    if not account or not app_password:
        raise SetupError("Gmail is not set up: run  .\\.venv\\Scripts\\python.exe main.py --setup  (see README).")
    after_ms = int(after.timestamp() * 1000)
    log.info("Searching Gmail (IMAP) for job mails after %s", after)
    started = time.monotonic()
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=60)
    try:
        try:
            imap.login(account, app_password)
        except imaplib.IMAP4.error as exc:
            raise GmailAuthError(
                "Gmail login failed: the app password may be wrong or revoked. Run  main.py --setup  again "
                f"(it must be an app password, not your normal password). Server said: {exc}"
            ) from exc
        imap.select("INBOX", readonly=True)
        typ, data = imap.uid("SEARCH", "X-GM-RAW", _quote(build_query(after)))
        if typ != "OK":
            raise RuntimeError(f"Gmail search failed: {data!r}")
        uids = (data[0] or b"").split()[-MAX_MESSAGES:]

        candidates: list[tuple[int, str, int]] = []
        for i in range(0, len(uids), 200):
            typ, data = imap.uid("FETCH", b",".join(uids[i:i + 200]).decode(), "(X-GM-MSGID INTERNALDATE)")
            for line in data or []:
                meta = parse_meta(line[0] if isinstance(line, tuple) else line) if line else None
                if meta:
                    candidates.append(meta)

        todo = [c for c in candidates if c[2] >= after_ms and c[1] not in seen]
        log.info("%d candidate mail(s), %d new to read", len(candidates), len(todo))
        mails: list[JobMail] = []
        processed: dict[str, int] = {}
        for uid, message_id, received_ms in todo:
            typ, data = imap.uid("FETCH", str(uid), f"(BODY.PEEK[]<0.{MAX_BODY_BYTES}>)")
            raw = next((part[1] for part in data or [] if isinstance(part, tuple)), None)
            if typ != "OK" or raw is None:
                log.warning("Could not read message %s", message_id)
                continue
            try:
                mail = build_mail(raw, message_id, received_ms, account)
            except Exception:
                log.exception("Could not parse message %s", message_id)
                mail = None
            processed[message_id] = received_ms
            if mail:
                mails.append(mail)
        log.info("Gmail check took %.0fs: %d job mail(s)", time.monotonic() - started, len(mails))
        return mails, processed
    finally:
        try:
            imap.logout()
        except Exception:
            pass
