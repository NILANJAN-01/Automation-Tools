"""User-facing signals: the always-on popup window (popup.py), and a Windows toast when checks start failing."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import config

log = logging.getLogger(__name__)

APP_ID = "Job Mail Notifier"
ICON = config.BASE_DIR / "assets" / "icon-256.png"


def pythonw() -> str:
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else exe)


def launch_popup(sample: bool = False) -> None:
    """Start popup.py detached. If one is already running it exits at once (single instance) without
    stealing focus, so a minimised or ✕-hidden window stays that way until new mail arrives."""
    subprocess.Popen(
        [pythonw(), str(config.BASE_DIR / "popup.py"), "--sample" if sample else "--no-raise"],
        cwd=config.BASE_DIR,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def show_toast(title: str, msg: str, launch: str) -> None:
    from winotify import Notification, audio

    icon = str(ICON) if ICON.exists() else ""
    toast = Notification(app_id=APP_ID, title=title, msg=msg, launch=launch, icon=icon)
    toast.set_audio(audio.Default, loop=False)
    toast.show()


def notify_error(message: str, log_path: Path, title: str = "Job Mail Notifier: check failed") -> None:
    try:
        msg = message if len(message) <= 200 else message[:199] + "…"
        show_toast(title, msg, log_path.resolve().as_uri())
    except Exception:  # never let a broken toast hide the original error
        log.exception("Could not show error toast")
