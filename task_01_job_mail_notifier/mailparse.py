"""Turn a raw RFC 822 mail into readable text, its links and (for calendar invites) the start time."""
from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
from html.parser import HTMLParser
from urllib.parse import urlsplit

URL_RX = re.compile(r"https?://[^\s<>\"'()\[\]{}|\\^`]+", re.I)
_INVISIBLE = re.compile(r"[​-‏͏­⁠﻿]")

# Job boards, applicant-tracking systems and assessment sites: links to them are worth showing.
JOB_BOARDS = [
    "naukri.com", "linkedin.com", "indeed.com", "glassdoor.com", "foundit.in", "monster.com",
    "instahyre.com", "iimjobs.com", "hirist.tech", "shine.com", "cutshort.io", "wellfound.com",
    "timesjobs.com", "apna.co", "internshala.com", "unstop.com",
]
PORTALS = set(JOB_BOARDS) | {
    "lnkd.in", "myworkdayjobs.com", "workday.com", "greenhouse.io", "lever.co", "smartrecruiters.com",
    "icims.com", "taleo.net", "successfactors.com", "successfactors.eu", "ashbyhq.com", "jobvite.com",
    "recruitee.com", "breezy.hr", "zohorecruit.com", "zohorecruit.in", "darwinbox.in", "keka.com",
    "freshteam.com", "hackerrank.com", "codility.com", "hackerearth.com", "mettl.com", "codesignal.com",
    "testgorilla.com", "calendly.com", "turing.com", "uplers.com", "hirect.in",
}
MEETINGS = {"teams.microsoft.com": "Teams", "teams.live.com": "Teams", "meet.google.com": "Google Meet",
            "zoom.us": "Zoom", "webex.com": "Webex"}
SKIP_SITES = {"facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "t.me", "whatsapp.com",
              "play.google.com", "apps.apple.com", "itunes.apple.com", "google.com"}
SKIP_URL = re.compile(r"unsubscribe|opt-?_?out|/preferences|emailpref|/settings|notification|privacy|/terms|"
                      r"/help|support\.|feedback|mailto:|tel:", re.I)
SKIP_TEXT = re.compile(r"unsubscribe|privacy|terms|help|faq|manage|setting|preference|feedback|report|block|"
                       r"security|fake|contact us|about us|download|get (the )?app|google play|app store|"
                       r"view (it |this )?(e-?mail|mail|message|online)|in (your )?browser|opt out|follow us|"
                       r"(update|edit|complete|improve) (your )?profile|add missing|"
                       r"^(facebook|twitter|x|instagram|youtube|linkedin|whatsapp|telegram|threads)$|"
                       r"^[\w.-]+\.(com|in|co|io|net|org|ai)$", re.I)  # last: a bare logo link like "naukri.com"
GOOD_TEXT = re.compile(r"apply|job|role|position|opening|vacanc|interview|schedule|slot|book|join|meeting|"
                       r"assessment|test|respond|reply|accept|confirm|portal|track|status|profile|recruiter|"
                       r"career|view (details|more|all|application)|see (all|more)|details", re.I)
GENERIC_TEXT = re.compile(r"^(click( here)?|here|link|open|view|this link|more|go)$", re.I)
MAX_LINKS = 5


@dataclass
class ParsedMail:
    subject: str = ""
    sender_name: str = ""
    sender_addr: str = ""
    text: str = ""
    anchors: list[tuple[str, str]] = field(default_factory=list)  # (link text, url) in document order
    plain_urls: bool = False  # anchors come from bare URLs in plain text (no link text)
    invite_when: str = ""
    invite_urls: list[str] = field(default_factory=list)
    has_invite: bool = False


class _HtmlScan(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "ul", "ol", "table", "section", "article",
             "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer", "blockquote"}
    SKIP = {"style", "script", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.anchors: list[tuple[str, str]] = []
        self._skip = 0
        self._href: str | None = None
        self._atext: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "a":
            self._href, self._atext = (a.get("href") or "").strip(), []
        elif tag == "img" and self._href is not None and a.get("alt"):
            self._atext.append(" " + a["alt"] + " ")
        if tag in self.BLOCK:
            self.parts.append("\n")
        elif tag == "td":
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            self.anchors.append((" ".join("".join(self._atext).split()), self._href))
            self._href = None
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._atext.append(data)


def clean_text(text: str) -> str:
    """Collapse whitespace inside lines and drop empty / repeated lines."""
    out: list[str] = []
    for line in _INVISIBLE.sub("", text).replace("\xa0", " ").splitlines():
        line = " ".join(line.split())
        if line and (not out or out[-1] != line):
            out.append(line)
    return "\n".join(out)


def html_scan(html: str) -> tuple[str, list[tuple[str, str]]]:
    scan = _HtmlScan()
    try:
        scan.feed(html)
        scan.close()
    except Exception:  # malformed markup: keep whatever was parsed
        pass
    return clean_text("".join(scan.parts)), scan.anchors


def _part_text(part) -> str:
    try:
        return part.get_content()
    except (LookupError, ValueError, AssertionError):  # unknown charset / broken encoding
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", "replace")


def _header(msg, name: str) -> str:
    try:
        return " ".join(str(msg[name] or "").split())
    except Exception:
        return ""


def parse_ics(ics: str) -> tuple[str, list[str]]:
    """Start time (+ location) and URLs of a text/calendar invite."""
    unfolded = re.sub(r"\r?\n[ \t]", "", ics)
    when = ""
    m = re.search(r"^DTSTART[^:\r\n]*:(\d{8})(?:T(\d{6})(Z)?)?", unfolded, re.M)
    if m:
        day, clock, utc = m.groups()
        try:
            if int(day[:4]) < 2000:  # placeholder dates (Naukri walk-ins use 0001-01-01)
                raise ValueError(day)
            if clock:
                dt = datetime.strptime(day + clock, "%Y%m%d%H%M%S")
                if utc:
                    dt = dt.replace(tzinfo=timezone.utc).astimezone()
                when = f"{dt:%a %d %b}, {dt.hour % 12 or 12}:{dt:%M} {dt:%p}"
            else:
                when = f"{datetime.strptime(day, '%Y%m%d'):%a %d %b}"
        except ValueError:
            when = ""
    loc = re.search(r"^LOCATION[^:\r\n]*:(.+)$", unfolded, re.M)
    if loc and when:
        place = loc.group(1).replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").strip()
        if place and len(place) <= 80 and not URL_RX.search(place):
            when += f" · {place}"
    return when, URL_RX.findall(unfolded.replace("\\n", " ").replace("\\,", ","))


def parse_message(raw: bytes) -> ParsedMail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = parseaddr(_header(msg, "from"))
    p = ParsedMail(subject=_header(msg, "subject"), sender_name=name.strip().strip('"'), sender_addr=addr.lower())
    plain, html, ics = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype in ("text/calendar", "application/ics"):
            ics.append(_part_text(part))
        elif part.get_content_disposition() == "attachment":
            continue
        elif ctype == "text/plain":
            plain.append(_part_text(part))
        elif ctype == "text/html":
            html.append(_part_text(part))

    html_text, anchors = html_scan("\n".join(html)) if html else ("", [])
    plain_text = clean_text("\n".join(plain))
    p.text = plain_text if len(plain_text) >= 40 or not html_text else html_text
    if anchors:
        p.anchors = anchors
    else:
        p.anchors, p.plain_urls = [("", u) for u in URL_RX.findall(plain_text)], True
    if ics:
        p.has_invite = True
        p.invite_when, p.invite_urls = parse_ics("\n".join(ics))
    elif re.match(r"(updated )?invitation:", p.subject, re.I):
        p.has_invite = True
    return p


def site(url: str) -> str:
    """Registrable-ish domain: 'https://www.jobs.naukri.com/x' -> 'naukri.com', 'a.co.in' kept as 3 labels."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac", "gov"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _meeting(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return next((name for d, name in MEETINGS.items() if host == d or host.endswith("." + d)), "")


def pick_links(anchors: list[tuple[str, str]], allow_untitled: bool = False, extra_urls: list[str] = ()) -> list[dict]:
    """Portal / meeting links worth a click: apply, view job, join interview... (no unsubscribe, social, etc.)."""
    scored: list[tuple[int, int, str, str]] = []
    for order, (text, url) in enumerate([*anchors, *(("", u) for u in extra_urls)]):
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")) or SKIP_URL.search(url):
            continue
        text = " ".join(_INVISIBLE.sub("", text).split())
        meeting = _meeting(url)
        dom = site(url)
        if meeting:
            scored.append((0, order, f"Join {meeting} meeting", url))
            continue
        if dom in SKIP_SITES or (text and SKIP_TEXT.search(text)):
            continue
        portal = dom in PORTALS or "career" in url.lower() or "/jobs" in url.lower()
        if text and GOOD_TEXT.search(text):
            prio = 1
        elif portal and (text or allow_untitled or order >= len(anchors)):
            prio = 2
        else:
            continue
        label = text if len(text) >= 3 and not GENERIC_TEXT.match(text) else f"Open on {dom}"
        if len(label) > 60:
            label = label[:59].rstrip() + "…"
        scored.append((prio, order, label, url))

    links, seen_urls, seen_labels = [], set(), set()
    for _prio, _order, label, url in sorted(scored):
        if url in seen_urls or label.lower() in seen_labels:
            continue
        seen_urls.add(url)
        seen_labels.add(label.lower())
        links.append({"label": label, "url": url})
        if len(links) == MAX_LINKS:
            break
    return links
