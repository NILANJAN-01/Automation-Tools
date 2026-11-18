"""Small JSON files shared between main.py (writer) and popup.py (reader / clearer)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from models import JobMail


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    """Atomic write so the popup never reads a half-written file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_mails(path: Path) -> list[JobMail]:
    mails = []
    for raw in read_json(path, []):
        try:
            mails.append(JobMail.from_dict(raw))
        except (KeyError, ValueError, TypeError):
            continue
    return mails


def save_mails(path: Path, mails: list[JobMail]) -> None:
    write_json(path, [m.to_dict() for m in mails])


def merge(existing: list[JobMail], new: list[JobMail], min_received_ms: int = 0) -> list[JobMail]:
    """Union by message id (newer copy wins), dropping mails received before `min_received_ms`."""
    by_id = {m.message_id: m for m in existing}
    by_id.update({m.message_id: m for m in new})
    return [m for m in by_id.values() if m.received_ms >= min_received_ms]
