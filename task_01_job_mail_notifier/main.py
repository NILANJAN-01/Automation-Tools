"""Job Mail Notifier: read job / interview / call mails from Gmail (IMAP) and list them in an always-on popup.

Usage:
    python main.py --setup             # enter Gmail address + app password (saved in Windows Credential Manager)
    python main.py                     # check now (also (re)opens the popup if it isn't running)
    python main.py --dry-run           # print results only; no popup, state unchanged
    python main.py --days 14 --all     # re-scan 14 days, including mails already looked at
    python main.py --trigger interval  # used by the scheduled task (every 2 min + on unlock)
    python main.py --test-popup        # show the popup with sample data
"""
from __future__ import annotations

import argparse
import imaplib
import logging
import msvcrt
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler

import config
import rules
from gmail_imap import IMAP_HOST, IMAP_PORT, GmailAuthError, SetupError, fetch_job_mails, wait_for_network
from models import LABELS, sort_mails
from store import load_mails, merge, read_json, save_mails, write_json

log = logging.getLogger("job_mail")
LOG_FILE = config.LOG_DIR / "run.log"
LOCK_FILE = config.LOG_DIR / ".run.lock"
KEEP_SEEN_DAYS = 30


def setup_logging(verbose: bool) -> None:
    config.LOG_DIR.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [RotatingFileHandler(LOG_FILE, maxBytes=500_000, backupCount=2, encoding="utf-8")]
    if sys.stdout is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


@contextmanager
def single_run():
    """Logon, unlock, periodic and Refresh-button checks can fire together; only one may run at a time."""
    config.LOG_DIR.mkdir(exist_ok=True)
    with open(LOCK_FILE, "a+") as fh:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


def set_status(state: str, message: str = "", found: int | None = None) -> None:
    """status.json tells the popup what the checker is doing (checking / ok / error)."""
    status = read_json(config.STATUS_FILE, {})
    now = int(time.time())
    status.update({"state": state, "at": now, "message": message})
    if state == "ok":
        status["last_ok"] = now
        status["found"] = found or 0
    write_json(config.STATUS_FILE, status)


def compute_after(state: dict, settings: config.Settings, days_override: int | None) -> datetime:
    now = time.time()
    if days_override is not None:
        return datetime.fromtimestamp(now - days_override * 86400)
    last = state.get("last_success_epoch")
    if not last:
        return datetime.fromtimestamp(now - settings.first_run_lookback_days * 86400)
    # 1h overlap so late-delivered mails are not missed; the seen-list prevents repeats.
    return datetime.fromtimestamp(max(last - 3600, now - settings.max_lookback_days * 86400))


def setup() -> int:
    """Interactive: ask for the Gmail address and app password, test the login, save them."""
    import getpass

    current = config.load_settings(with_secret=True)
    print("Job Mail Notifier setup\n")
    address = input(f"Gmail address [{current.gmail_address or 'you@gmail.com'}]: ").strip() or current.gmail_address
    if "@" not in address:
        print("A Gmail address is needed.")
        return 1
    print("\nCreate an app password at https://myaccount.google.com/apppasswords")
    print("(needs 2-Step Verification). Google shows 16 letters; spaces don't matter.")
    keep = current.gmail_app_password if address == current.gmail_address else ""
    prompt = "App password (hidden" + ("; Enter keeps the saved one" if keep else "") + "): "
    password = getpass.getpass(prompt).replace(" ", "") or keep
    if not password:
        print("No password entered; nothing saved.")
        return 1

    print("Testing the Gmail login...")
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
        imap.login(address, password)
        imap.logout()
    except imaplib.IMAP4.error as exc:
        print(f"Gmail refused the login: {exc}\nCheck the address, and use an app password (not your normal password).")
        return 1
    except OSError as exc:
        print(f"Could not reach Gmail: {exc}")
        return 1

    config.set_env_value("GMAIL_ADDRESS", address)
    try:
        config.save_keyring_password(address, password)
        config.set_env_value("GMAIL_APP_PASSWORD", None)  # kept in Credential Manager instead
        print("Login OK. Saved the app password in Windows Credential Manager.")
    except Exception as exc:  # no keyring backend: keep it in .env
        config.set_env_value("GMAIL_APP_PASSWORD", password)
        print(f"Login OK. Credential Manager unavailable ({exc}); saved the app password in .env instead.")
    return 0


def forget() -> int:
    address = config.load_settings().gmail_address
    try:
        import keyring
        keyring.delete_password(config.KEYRING_SERVICE, address)
        print(f"Removed the saved app password for {address} from Credential Manager.")
    except Exception as exc:
        print(f"No saved app password removed ({exc}).")
    return 0


def is_set_up() -> bool:
    s = config.load_settings(with_secret=True)
    return bool(s.gmail_address and s.gmail_app_password)


def run(args: argparse.Namespace) -> int:
    settings = config.load_settings(with_secret=True)
    rules.apply(rules.load(config.RULES_FILE))
    if not args.dry_run:
        from notifier import launch_popup
        launch_popup()  # always on screen; exits at once if it is already running
        set_status("checking")
    if not settings.gmail_address or not settings.gmail_app_password:
        raise SetupError("Gmail is not set up: run  .\\.venv\\Scripts\\python.exe main.py --setup  (see README).")

    state = read_json(config.STATE_FILE, {})
    after = compute_after(state, settings, args.days)
    started = int(time.time())
    seen: dict[str, int] = state.get("seen", {})  # {gmail message id: received epoch ms}
    after_ms = int(after.timestamp() * 1000)
    skip = set() if args.all else {mid for mid, ms in seen.items() if ms >= after_ms}
    log.info("Checking for job mails since %s (trigger=%s, %d already looked at)", after, args.trigger, len(skip))

    wait_for_network(attempts=10 if args.trigger == "logon" else 3)
    fetch = lambda: fetch_job_mails(settings.gmail_address, settings.gmail_app_password, after, skip)  # noqa: E731
    try:
        items, processed = fetch()
    except (OSError, imaplib.IMAP4.abort) as exc:  # dropped connection / timeout: one retry
        log.warning("First attempt failed, retrying in 20s: %s", exc)
        time.sleep(20)
        items, processed = fetch()
    items = sort_mails(items)
    log.info("Found %d job mail(s)", len(items))

    from report import write_report

    if args.dry_run:
        report_path = write_report(items, after, config.REPORT_DIR)
        for m in items:
            when = f"  [when: {m.when}]" if m.when else ""
            print(f"{m.received:%d %b %H:%M}  {LABELS[m.category]:<20} {m.who[:26]:<26} {m.subject[:70]}{when}")
            for link in m.links:
                print(f"{'':>40}-> {link['label']}: {link['url'][:90]}")
        print(f"\n{len(items)} job mail(s). Summary page: {report_path}")
        return 0

    # history.json = every job mail of the last HISTORY_DAYS (what the popup lists).
    history_start_ms = (started - settings.history_days * 86400) * 1000
    # Mails re-read in this run (e.g. --all after a rule change) that are no longer job mails drop out.
    dropped = set(processed) - {m.message_id for m in items}
    old_history = load_mails(config.HISTORY_FILE)
    known = {m.message_id for m in old_history}
    history = sort_mails([m for m in merge(old_history, items, history_start_ms) if m.message_id not in dropped])
    if history != sort_mails(old_history):
        save_mails(config.HISTORY_FILE, history)
    new = [m for m in items if m.message_id not in known]
    old_pending = load_mails(config.PENDING_FILE)
    pending = [m for m in merge(old_pending, new, history_start_ms) if m.message_id not in dropped]
    if pending != old_pending:  # NEW badge in the popup until "Mark all seen"
        save_mails(config.PENDING_FILE, pending)
    if new:
        log.info("%d new job mail(s) for the popup", len(new))
    write_report(history, datetime.fromtimestamp(history_start_ms / 1000), config.REPORT_DIR,
                 frozenset(m.message_id for m in pending))

    seen.update(processed)
    cutoff_ms = (started - KEEP_SEEN_DAYS * 86400) * 1000
    state["seen"] = {mid: ms for mid, ms in seen.items() if ms >= cutoff_ms}
    state["last_success_epoch"] = started
    state.pop("failing_since", None)
    write_json(config.STATE_FILE, state)
    set_status("ok", found=len(new))
    return 0


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, (GmailAuthError, SetupError, ConnectionError)):
        return str(exc)
    if isinstance(exc, (OSError, imaplib.IMAP4.abort)):
        return f"Could not reach Gmail ({exc}). Will retry automatically."
    return f"{type(exc).__name__}: {exc}"


def report_failure(exc: Exception) -> None:
    """Show the error in the popup; toast only the first failure of a streak, not every 2-minute retry."""
    message = friendly_error(exc)
    set_status("error", message)
    state = read_json(config.STATE_FILE, {})
    if state.get("failing_since"):
        return
    state["failing_since"] = int(time.time())
    write_json(config.STATE_FILE, state)
    from notifier import notify_error
    notify_error(message, LOG_FILE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print results; no popup, state unchanged")
    parser.add_argument("--days", type=int, help="look back this many days instead of 'since last run'")
    parser.add_argument("--all", action="store_true", help="re-read mails already looked at before")
    parser.add_argument("--trigger", choices=["logon", "interval", "unlock", "manual"], default="manual")
    parser.add_argument("--test-popup", action="store_true", help="show the popup with sample data and exit")
    parser.add_argument("--setup", action="store_true", help="enter Gmail address + app password and test them")
    parser.add_argument("--is-set-up", action="store_true", help="exit 0 if Gmail credentials are configured")
    parser.add_argument("--forget", action="store_true", help="delete the saved app password from Credential Manager")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    config.migrate_legacy_files()
    if args.setup:
        return setup()
    if args.is_set_up:
        return 0 if is_set_up() else 1
    if args.forget:
        return forget()
    setup_logging(args.verbose)

    if args.test_popup:
        from notifier import launch_popup
        launch_popup(sample=True)
        return 0

    with single_run() as acquired:
        if not acquired:
            log.info("Another check is already running; skipping (trigger=%s)", args.trigger)
            if not args.dry_run:
                from notifier import launch_popup
                launch_popup()
            return 0
        try:
            return run(args)
        except Exception as exc:
            if isinstance(exc, (GmailAuthError, SetupError, ConnectionError)):
                log.error("Check failed: %s", exc)
            else:
                log.exception("Check failed")
            if not args.dry_run:
                report_failure(exc)
            return 1


if __name__ == "__main__":
    sys.exit(main())
