# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Layout and conventions

A collection of independent daily-automation tools for Windows. Each task is a self-contained folder named `task_NN_<name>/` with:
- its own `.venv` (Python 3.11+; developed on 3.15, CI tests 3.11–3.13)
- `requirements.txt`
- optional `.env` (git-ignored; `.env.example` documents the keys)
- `main.py` entry point
- `README.md`
- `tests/` (stdlib `unittest`, no pytest)

Tasks share no code. When adding a task, add a row to the root `README.md` table and a job to `.github/workflows/tests.yml`. Scheduled runs use Windows Task Scheduler, registered by a per-task `install_task.ps1`, not cron or a service.

Shell is PowerShell on Windows. Run Python through the task's venv, e.g. `.\.venv\Scripts\python.exe`. Scheduled tasks use `pythonw.exe`, so there's no console: anything a user must see goes to a window, a toast notification (winotify) or a log file.

The repo is public. Secrets go in Windows Credential Manager (`keyring`) or `.env`. Personal data goes in the task's `data/`, `logs/` and `reports/`. All of these are git-ignored; never commit them, and keep personal names and addresses out of tests.

For **task_01** the user chose plain IMAP with a Gmail app password (no Claude), so it keeps working when the Claude usage limit is hit; don't move it back to a connector. For other tasks, ask. If a claude.ai connector is used, a plain Python process cannot call it. The pattern is: Python launches `claude -p` headless with
- `--allowedTools` limited to the needed MCP tools,
- `--permission-mode dontAsk`,
- `--json-schema` for structured output, read from `structured_output` in the `--output-format json` envelope.

Do **not** pass `--bare`: it disables claude.ai OAuth and therefore the connectors.

## Commands (run inside a task folder)

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests                   # all tests
.\.venv\Scripts\python.exe -m unittest tests.test_mail_rules.ClassifyTests  # one class
.\.venv\Scripts\python.exe main.py --dry-run --days 3                       # task_01: real Gmail, no popup, no state change
.\.venv\Scripts\python.exe main.py --test-popup                             # task_01: separate popup with sample data
.\.venv\Scripts\python.exe main.py --setup                                  # task_01: Gmail address + app password -> Credential Manager
powershell -ExecutionPolicy Bypass -File .\install_task.ps1                 # (re)register scheduled tasks
```

## task_01_job_mail_notifier

Data flow: `main.py`
1. `notifier.launch_popup` starts `popup.py --no-raise` detached (no-op if running), and `data/status.json` is set to `checking`. Every check doubles as the popup watchdog.
2. The password comes from `.env` `GMAIL_APP_PASSWORD`, else from keyring (`JobMailNotifier`, address). `rules.apply` merges `rules.toml` into the built-in lists.
3. Computes a lookback window from `data/state.json`: since the last successful run minus 1 h, capped at `MAX_LOOKBACK_DAYS`.
4. `gmail_imap.fetch_job_mails` opens INBOX read-only. It runs `UID SEARCH X-GM-RAW <gmail query>`, fetches `X-GM-MSGID INTERNALDATE` for all hits, and then `BODY.PEEK[]<0.400000>` only for ids not in `seen`.
5. `mailparse.parse_message` turns MIME into text, `<a>` anchors and calendar-invite start times. `classify.classify` sorts by regex phrase lists (user phrases first, then interview > call > application > job_alert, else dropped). `pick_links` keeps portal/meeting links.
6. New items are merged into `history.json` (the popup's list). Ids re-read this run that no longer classify are dropped. Items not already in history go into `pending.json` (NEW badges). `report.write_report` renders `reports/latest.html`.
7. Every id looked at is saved in `state.json` `seen`, kept 30 days. On failure, the status becomes `error` plus a message, and a toast is shown only for the first failure of a streak (`failing_since`).

`popup.py` is an always-on Tkinter window styled by `ui_kit.py`:
- Fluent light/dark palettes follow the Windows theme, or `THEME` in `.env`.
- Tk's canvas doesn't anti-alias, so rounded shapes (cards, pills, buttons, avatars) are rendered pixel-by-pixel into cached `PhotoImage`s.
- `ui.Card` must have `.fit()` called after its body is filled. Its embedded frame never reports a real size while the card is too short.
- Sizes are logical px scaled by DPI (`Popup.px`). Icons use the Segoe Fluent Icons glyphs in `ui.GLYPH`.
- It is single-instance, via a listening socket on `127.0.0.1:47831`. Without `--no-raise`, a second launch connects to it and the running popup comes to the front.
- Every 3 s it polls the mtimes of the data files and re-renders only when the data changed. When the NEW set grows, it restores itself, stays topmost for 6 s and chimes.
- ✕ / Esc withdraw the window, and `after(REOPEN_MINUTES)` brings it back. Refresh / F5 spawns `main.py --trigger manual`. `--sample` uses in-memory data, no lock and no writes.
- JSON files shared between processes are written atomically (`store.write_json`).

Scheduling (`install_task.ps1`): `(logon)` runs `--trigger logon` (15 s delay). `(periodic)` runs `--trigger interval` every 2 min (`-IntervalMinutes`) plus on session unlock. If not set up yet, the installer runs `main.py --setup`. `main.single_run` (an msvcrt lock in `logs/.run.lock`) stops overlapping checks.

Non-obvious points:
- Gmail `after:` is date-granular (midnight), so the query over-fetches. Exact filtering uses INTERNALDATE plus `seen`.
- The message id is `format(X-GM-MSGID, "x")`, the same id the Gmail web app uses. The link is `https://mail.google.com/mail/?authuser=<addr>#all/<hex id>`.
- Naukri routes every link through `tr.naukri.com`, so links are filtered by anchor text (`SKIP_TEXT` / `GOOD_TEXT`), not by domain. Naukri calendar invites carry a placeholder DTSTART (year 0001), which is ignored.
- The venv `pythonw.exe` is a launcher that starts a child interpreter, so the Tk window belongs to the child PID. To screenshot it, use `PrintWindow`.
- After changing rules, re-sort with `main.py --days 14 --all`.
