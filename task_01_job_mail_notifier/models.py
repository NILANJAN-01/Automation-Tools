from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum


class Category(str, Enum):
    INTERVIEW = "interview"
    CALL = "call"
    APPLICATION = "application"
    JOB_ALERT = "job_alert"


LABELS = {
    Category.INTERVIEW: "Interview",
    Category.CALL: "Call",
    Category.APPLICATION: "Application update",
    Category.JOB_ALERT: "Job alert / vacancy",
}

# Highlighted (tinted card) in the popup and on the summary page.
HIGHLIGHTED = frozenset({Category.INTERVIEW, Category.CALL})


@dataclass
class JobMail:
    message_id: str  # Gmail message id (hex of IMAP X-GM-MSGID, same as the Gmail API id)
    category: Category
    sender: str
    subject: str
    received_ms: int  # Gmail INTERNALDATE, epoch milliseconds
    summary: str
    view_url: str
    company: str = ""
    role: str = ""
    when: str = ""  # interview/call date & time as written in the mail, "" if not found
    links: list[dict] = field(default_factory=list)  # [{"label": ..., "url": ...}] portal / meeting links

    @property
    def received(self) -> datetime:
        return datetime.fromtimestamp(self.received_ms / 1000, tz=timezone.utc).astimezone()

    @property
    def who(self) -> str:
        name = self.company or self.sender
        if "@" in name:  # bare address like info@naukri.com -> "Naukri"
            domain = name.rpartition("@")[2].split(".")
            name = (domain[-2] if len(domain) >= 2 else domain[0]).capitalize()
        return name

    def to_dict(self) -> dict:
        return {**asdict(self), "category": self.category.value}

    @classmethod
    def from_dict(cls, data: dict) -> "JobMail":
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        values["category"] = Category(values["category"])
        values["links"] = [
            {"label": str(link["label"]), "url": str(link["url"])}
            for link in values.get("links") or [] if isinstance(link, dict) and link.get("url")
        ]
        return cls(**values)


def sort_mails(mails: list[JobMail]) -> list[JobMail]:
    """Newest first (the popup groups them by day)."""
    return sorted(mails, key=lambda m: -m.received_ms)
