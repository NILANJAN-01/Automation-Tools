"""Optional user additions to the built-in sorting rules, read from rules.toml (see rules.example.toml).

Phrases are plain text (case-insensitive, whole words), not regular expressions.
"""
from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

import classify
import gmail_imap
import mailparse
from models import Category

log = logging.getLogger(__name__)

CATEGORY_KEYS = {"interview": Category.INTERVIEW, "call": Category.CALL,
                 "application": Category.APPLICATION, "job_alert": Category.JOB_ALERT}


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        log.error("Ignoring %s: %s", path, exc)
        return {}


def _strings(value) -> list[str]:
    return [str(v).strip() for v in value or [] if str(v).strip()] if isinstance(value, list) else []


def phrase_regex(phrases: list[str]) -> re.Pattern | None:
    if not phrases:
        return None
    return re.compile("|".join(rf"(?<!\w){re.escape(p)}(?!\w)" for p in phrases), re.I)


def apply(user: dict) -> None:
    """Merge the user's rules into the built-in ones (call once per process, before checking mail)."""
    boards = [b.lower().removeprefix("www.") for b in _strings(user.get("job_boards"))]
    mailparse.JOB_BOARDS.extend(b for b in boards if b not in mailparse.JOB_BOARDS)
    mailparse.PORTALS.update(boards)
    gmail_imap.KEYWORDS.extend(k for k in _strings(user.get("keywords")) if k not in gmail_imap.KEYWORDS)

    noise = user.get("noise") if isinstance(user.get("noise"), dict) else {}
    classify.NOISE_SENDERS = classify.NOISE_SENDERS + tuple(s.lower() for s in _strings(noise.get("senders")))
    classify.USER_NOISE = phrase_regex(_strings(noise.get("subjects")))

    phrases = user.get("phrases") if isinstance(user.get("phrases"), dict) else {}
    for key, cat in CATEGORY_KEYS.items():
        classify.USER_PHRASES[cat] = phrase_regex(_strings(phrases.get(key)))
    unknown = set(phrases) - set(CATEGORY_KEYS)
    if unknown:
        log.warning("rules.toml: unknown [phrases] keys %s (use %s)", sorted(unknown), ", ".join(CATEGORY_KEYS))
