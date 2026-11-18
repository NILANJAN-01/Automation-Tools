"""Settings from .env / environment, the Gmail app password from Windows Credential Manager, and file paths.

Everything personal lives in data/, logs/ and reports/ (all git-ignored) or in Credential Manager.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
RULES_FILE = BASE_DIR / "rules.toml"  # optional user additions to the sorting rules (see rules.example.toml)
DATA_DIR = Path(os.environ.get("JOBMAIL_DATA_DIR") or BASE_DIR / "data")
STATE_FILE = DATA_DIR / "state.json"      # time window + already-processed message ids (main.py)
HISTORY_FILE = DATA_DIR / "history.json"  # every job mail of the last HISTORY_DAYS: what the popup lists
PENDING_FILE = DATA_DIR / "pending.json"  # mails not yet seen in the popup (NEW badge) until "Mark all seen"
HIDDEN_FILE = DATA_DIR / "hidden.json"    # message ids hidden from the popup (written by popup.py only)
STATUS_FILE = DATA_DIR / "status.json"    # last check: checking / ok / error (written by main.py only)
LOG_DIR = BASE_DIR / "logs"
REPORT_DIR = BASE_DIR / "reports"

KEYRING_SERVICE = "JobMailNotifier"  # Credential Manager entry: service + Gmail address -> app password


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class Settings:
    gmail_address: str = ""
    gmail_app_password: str = ""  # only filled by load_settings(with_secret=True)
    first_run_lookback_days: int = 7
    max_lookback_days: int = 7
    history_days: int = 14
    reopen_minutes: float = 2  # popup comes back this long after it is closed
    theme: str = "auto"  # popup theme: auto (follow Windows) / light / dark


def keyring_password(address: str) -> str:
    if not address:
        return ""
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, address) or ""
    except Exception:  # keyring missing or no backend: fall back to .env
        return ""


def save_keyring_password(address: str, password: str) -> None:
    import keyring
    keyring.set_password(KEYRING_SERVICE, address, password)


def load_settings(with_secret: bool = False) -> Settings:
    file_values = _read_env_file(ENV_FILE)

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key, file_values.get(key, default))

    address = get("GMAIL_ADDRESS").strip()
    password = ""
    if with_secret:
        # Google shows app passwords as "abcd efgh ijkl mnop"; the spaces are not part of it.
        password = (get("GMAIL_APP_PASSWORD") or keyring_password(address)).replace(" ", "")
    return Settings(
        gmail_address=address,
        gmail_app_password=password,
        first_run_lookback_days=int(get("FIRST_RUN_LOOKBACK_DAYS", "7")),
        max_lookback_days=int(get("MAX_LOOKBACK_DAYS", "7")),
        history_days=int(get("HISTORY_DAYS", "14")),
        reopen_minutes=float(get("REOPEN_MINUTES", "2")),
        theme=get("THEME", "auto").strip().lower(),
    )


def set_env_value(key: str, value: str | None) -> None:
    """Set (or with None, remove) one KEY=value line in .env, keeping everything else."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out, done = [], False
    for line in lines:
        if line.strip().partition("=")[0].strip() == key and not line.strip().startswith("#"):
            if value is not None and not done:
                out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if value is not None and not done:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def migrate_legacy_files() -> None:
    """Older versions kept the JSON files next to the code; move them into data/."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path in (STATE_FILE, HISTORY_FILE, PENDING_FILE, HIDDEN_FILE, STATUS_FILE):
        old = BASE_DIR / path.name
        if old.exists() and not path.exists():
            try:
                old.replace(path)
            except OSError:
                pass
