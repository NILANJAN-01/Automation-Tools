"""Rule-based sorting of job-search mails (no AI): category, interview/call time and a short snippet.

Order matters: interview > call > application update > job alert. A mail that matches none of them,
or looks like noise (CI "job failed", webinars, interview-tips articles...), is left out.
Tune the phrase lists below if something is missed or wrongly included.
"""
from __future__ import annotations

import re

from mailparse import JOB_BOARDS
from models import Category


def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


NOISE_SENDERS = (
    "github.com", "gitlab.com", "atlassian.net", "atlassian.com", "bitbucket.org", "circleci.com",
    "travis-ci.com", "dev.azure.com", "azuredevops", "jenkins", "vercel.com", "netlify.com", "sentry.io",
    "medium.com", "substack.com", "coursera.org", "udemy.com", "edx.org", "youtube.com", "codecademy.com",
    "ambitionbox.com", "accounts.google.com",
)
NOISE_SUBJECT = _rx(
    r"^what'?s new\b", r"\bprivacy\b", r"\bverification code\b", r"\binterview (insights|reviews?)\b",
    r"\bsalary (&|and) interview\b",
    r"\b(build|pipeline|workflow|deploy(ment)?|job|run)\b.{0,40}\b(failed|failing|succeeded|passed|fixed|completed|cancell?ed)\b",
    r"\bwebinar\b", r"\bnewsletter\b", r"\b(course|masterclass|bootcamp|certification|workshop)\b",
    r"\binterview (tips|questions|prep(aration)?|guide|hacks|experience)\b", r"\bhow to (crack|ace|prepare|answer)\b",
    r"\bsalary (report|guide|trends)\b",
)

INTERVIEW_SUBJECT = _rx(
    r"\binterview", r"\bassessment\b",
    r"\b(coding|online|aptitude|technical|written|take[- ]home) (test|challenge|assessment|exercise|assignment)\b",
    r"\b(technical|tech|hr|managerial|final|first|second|third|1st|2nd|3rd|client|f2f|face[- ]to[- ]face|virtual) round\b",
    r"\bwalk[- ]?in\b", r"\b(hackerrank|codility|hackerearth|mettl|codesignal|testgorilla)\b",
)
INTERVIEW_BODY = _rx(
    r"\binterview (is |has been )?(scheduled|confirmed|rescheduled|invit)",
    r"\b(schedul|arrang|book|confirm)\w* (an |your |the |a )?(interview|technical round|hr round)",
    r"\binvite you (for|to) (an |a |the )?(interview|assessment)",
    r"\b(shortlisted|selected) for (the )?(interview|next round|assessment)",
    r"\binterview (slot|invitation|invite|details|schedule|link)\b",
)
CALL = _rx(
    r"\b(phone|telephonic|telephone) (screen|screening|call|discussion|conversation|round)\b",
    r"\b(quick|brief|short|introductory|intro|exploratory) (call|chat)\b",
    r"\b(tried|trying|was trying) to (call|reach) you\b", r"\bmissed (a |our |my |your )?call\b",
    r"\b(schedule|set up|setup|arrange|have|hop on|jump on|get on|book) a (quick |short |brief )?call\b",
    r"\b(connect|speak|talk)( with you)? (over|on) (a |the )?(call|phone)\b",
    r"\bcall (you|me) (back|at|on|tomorrow|today)\b", r"\bcall ?back\b",
    r"\b(share|send)( me)? your (phone|contact|mobile) (number|no|details)\b",
    r"\bavailable (for|to take) a call\b", r"\bgive (you|me) a call\b", r"\bbest (time|number) to (call|reach)\b",
)
JOB_CONTEXT = _rx(
    r"\b(job|role|position|opening|vacanc\w*|resume|cv|candidate|candidature|opportunit\w*|recruit\w*|hiring|"
    r"interview|ctc|notice period)\b",
)
# Company recruiting senders: talent@ibm.com, careers@..., noreply@careers.harman.com, recruitment@...
RECRUITING_SENDER = re.compile(r"(^|[.@_-])(talent|careers?|recruit\w*|hiring|jobs?|hr)([.@_-]|$)", re.I)
APPLICATION = _rx(
    r"\byour application\b", r"\bapplication (received|submitted|status|update|for|was|has been)\b",
    r"\b(thanks|thank you) for (applying|your interest|your application)\b", r"\byou applied\b",
    r"\bapplied (for|to)\b", r"\bshortlisted\b", r"\b(not been|not|weren't|were not) (selected|shortlisted)\b",
    r"\bregret to inform\b", r"\b(move|moving|proceed|proceeding) forward with other\b",
    r"\bdecided not to (proceed|move forward)\b", r"\boffer (letter|of employment)\b", r"\bjob offer\b",
    r"\brecruiters?(\(s\))? (viewed|is interested|are interested|expressed interest|showed interest|wants to|reached out|interested)\b",
    r"\bexpressed interest in your\b", r"\bjob applies\b",
    r"\bviewed your (profile|application|cv|resume)\b", r"\binterested in your (profile|cv|resume|application)\b",
    r"\byour (profile|cv|resume) (has been|was|got) (viewed|shortlisted|downloaded|selected)\b", r"\bcandidature\b",
)
# Unmistakable even without a job word nearby (APPLICATION in the body otherwise needs JOB_CONTEXT).
APPLICATION_STRONG = _rx(
    r"\bregret to inform\b", r"\b(thanks|thank you) for applying\b", r"\b(not been|weren't|were not) (selected|shortlisted)\b",
    r"\bapplication was not (selected|shortlisted|successful)\b", r"\bother candidates\b", r"\boffer letter\b",
)
JOB_ALERT = _rx(
    r"\b(is|are|now) hiring\b", r"\bhiring (for|now|alert)\b", r"\bjob alerts?\b",
    r"\bjobs? (for you|matching|recommended|you may|based on)\b", r"\brecommended jobs?\b", r"\bnew jobs?\b",
    r"\bjob (openings?|opportunit\w+|vacanc\w+)\b", r"\bvacanc(y|ies)\b", r"\bopenings? (for|in|at)\b",
    r"\bapply now\b", r"\bearly (access|alert|applicant)\b", r"\bnvite\b", r"\bcareer opportunit\w*\b",
    r"\bwhere would you fit\b",
)

_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DAY = r"(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(day|nesday|rsday|urday)?"
DATE_RX = re.compile(
    rf"\b(\d{{1,2}}(st|nd|rd|th)?[\s\-/.,]*{_MONTH}([\s,\-/]*\d{{2,4}})?|{_MONTH}\s+\d{{1,2}}(st|nd|rd|th)?(,?\s*\d{{4}})?|"
    rf"\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}|{_DAY}|tomorrow|today)\b", re.I)
TIME_RX = re.compile(r"\b\d{1,2}([:.]\d{2})?\s*(am|pm|a\.m\.|p\.m\.)|\b\d{1,2}:\d{2}\s*(hrs|hours|ist)?\b", re.I)
LABELLED = re.compile(r"^\s*(date|time|day|when|venue|location|mode|timing|timings|slot|schedule|interview (date|time|mode|venue))"
                      r"\s*(&\s*time)?\s*[:\-–]\s*\S", re.I)
GREETING = re.compile(r"^(hi|hello|hey|dear|greetings|good (morning|afternoon|evening))\b.{0,50}$", re.I)
BOILERPLATE = re.compile(r"view (this |it )?(e-?mail|mail|message) in|unsubscribe|privacy|if you (cannot|can't|are unable)|"
                         r"click here|download (the )?app|all rights reserved|©|this (e-?mail|message) was sent|"
                         r"do not reply|^https?://", re.I)


def _domain(addr: str) -> str:
    return addr.rpartition("@")[2].lower()


def is_job_board(addr: str) -> bool:
    d = _domain(addr)
    return any(d == b or d.endswith("." + b) for b in JOB_BOARDS)


# Filled from rules.toml by rules.apply(): user phrases win over the built-in ones.
USER_PHRASES: dict[Category, re.Pattern | None] = {cat: None for cat in Category}
USER_NOISE: re.Pattern | None = None


def classify(subject: str, sender_addr: str, text: str, has_invite: bool = False) -> Category | None:
    domain = _domain(sender_addr)
    if any(n in domain for n in NOISE_SENDERS) or NOISE_SUBJECT.search(subject):
        return None
    if USER_NOISE and USER_NOISE.search(subject):
        return None
    head = text[:5000]
    for cat in (Category.INTERVIEW, Category.CALL, Category.APPLICATION, Category.JOB_ALERT):
        pattern = USER_PHRASES.get(cat)
        if pattern and (pattern.search(subject) or pattern.search(head)):
            return cat
    board = is_job_board(sender_addr)
    context = bool(JOB_CONTEXT.search(subject) or JOB_CONTEXT.search(head) or board)

    if INTERVIEW_SUBJECT.search(subject) or INTERVIEW_BODY.search(head) or (has_invite and context):
        return Category.INTERVIEW
    if context and (CALL.search(subject) or CALL.search(head)):
        return Category.CALL
    # Subject first: "Urgently hiring for..." is an alert even if the body says "you applied" somewhere.
    if APPLICATION.search(subject):
        return Category.APPLICATION
    if JOB_ALERT.search(subject):
        return Category.JOB_ALERT
    if (context and APPLICATION.search(head)) or APPLICATION_STRONG.search(head):
        return Category.APPLICATION
    # Job boards also send social / marketing mail: need a job word or a jobs sender (jobalerts-noreply@...).
    if board and (JOB_CONTEXT.search(subject) or JOB_ALERT.search(head) or "job" in sender_addr.partition("@")[0].lower()):
        return Category.JOB_ALERT
    if RECRUITING_SENDER.search(sender_addr) and (JOB_CONTEXT.search(subject) or JOB_ALERT.search(head)):
        return Category.JOB_ALERT
    return None


def _trim(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip(" ,;:-") + "…"


def extract_when(text: str, invite_when: str = "") -> str:
    """Interview/call date and time as written in the mail (best effort), e.g. 'Date: 20 Nov · Time: 3 PM IST'."""
    if invite_when:
        return invite_when
    lines = text.splitlines()[:120]
    labelled = [_trim(line.strip(), 70) for line in lines if LABELLED.match(line) and len(line) <= 160]
    if labelled:
        return " · ".join(labelled[:3])
    for line in lines:
        if len(line) <= 220 and DATE_RX.search(line) and TIME_RX.search(line):
            return _trim(line.strip(), 120)
    return ""


def snippet(text: str, subject: str = "", limit: int = 170) -> str:
    """First meaningful sentence(s) of the body, skipping greetings and boilerplate."""
    picked: list[str] = []
    size = 0
    for line in text.splitlines()[:80]:
        line = line.strip()
        if len(line) < 12 or GREETING.match(line) or BOILERPLATE.search(line) or line == subject:
            continue
        picked.append(line)
        size += len(line) + 1
        if size >= limit:
            break
    return _trim(" ".join(picked), limit)
