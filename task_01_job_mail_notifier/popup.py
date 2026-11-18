"""Always-on desktop window listing job-search mails (interviews, calls, application updates, job
alerts) grouped by day, newest first.

main.py starts it at logon and on every scheduled check. It is single-instance: the bind on
127.0.0.1:LOCK_PORT is the lock, and a second launch exits at once. It re-reads history.json,
pending.json and status.json every few seconds.

- ✕ (or Esc) hides it; it comes back after REOPEN_MINUTES (default 2), or at once when new mail arrives.
- Minimise keeps it in the taskbar; it is restored when new mail arrives.
- If the process is killed, the next scheduled check (every 2 min) starts it again.

    pythonw popup.py              # open (or bring the running one to the front)
    pythonw popup.py --no-raise   # used by main.py: start if not running, never steal focus
    pythonw popup.py --sample     # sample data, nothing saved
"""
from __future__ import annotations

import ctypes
import logging
import socket
import subprocess
import sys
import time
import tkinter as tk
import webbrowser
import winsound
from datetime import date, datetime, timedelta
from tkinter import ttk

import config
import ui_kit as ui
from models import HIGHLIGHTED, Category, JobMail, sort_mails
from store import load_mails, read_json, save_mails, write_json

log = logging.getLogger("popup")

APP_ID = "JobMailNotifier.Popup"
LOCK_PORT = 47831
POLL_MS = 3000
WIDTH, HEIGHT = 470, 720  # logical px (scaled for DPI)
GMAIL_HOME = "https://mail.google.com/mail/"
MAX_CARDS = 250
STALE_CHECK_S = 600  # a "checking" status older than this is a crashed check

FILTERS = [
    ("all", "All"),
    ("new", "New"),
    (Category.INTERVIEW, "Interviews"),
    (Category.CALL, "Calls"),
    (Category.APPLICATION, "Updates"),
    (Category.JOB_ALERT, "Job alerts"),
]
TAG = {Category.INTERVIEW: "Interview", Category.CALL: "Call", Category.APPLICATION: "Update",
       Category.JOB_ALERT: "Job alert"}


def acquire_single_instance() -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(4)
        sock.setblocking(False)
        return sock
    except OSError:
        sock.close()
        return None


def ask_running_popup_to_show() -> None:
    try:
        socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=2).close()
    except OSError:
        pass


def day_label(d: date, today: date) -> str:
    if d == today:
        return f"Today · {d:%a %d %b}"
    if d == today - timedelta(days=1):
        return f"Yesterday · {d:%a %d %b}"
    return f"{d:%A, %d %b}"


def clock(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    return f"{dt:%H:%M}" if dt.date() == date.today() else f"{dt:%d %b %H:%M}"


def sample_mails() -> list[JobMail]:
    now = int(time.time() * 1000)
    day = 86_400_000
    gmail = "https://mail.google.com/mail/"
    return [
        JobMail("sample-1", Category.INTERVIEW, "Acme Talent", "[SAMPLE] Interview invitation: SDET, technical round",
                now - 3_600_000, "We'd like to invite you to a technical round for the SDET role. Please confirm the slot.",
                gmail, when="Thu 20 Nov, 3:00 PM IST · Microsoft Teams",
                links=[{"label": "Join Teams meeting", "url": "https://teams.microsoft.com/"},
                       {"label": "Confirm your slot", "url": "https://www.naukri.com/"}]),
        JobMail("sample-2", Category.CALL, "Priya (Recruiter)", "[SAMPLE] Quick call about a QA Lead opening",
                now - 7_200_000, "I tried to reach you about a QA Lead role. When is a good time for a quick call?",
                gmail, when="Date: Tomorrow · Time: 11:30 AM"),
        JobMail("sample-3", Category.APPLICATION, "Naukri", "[SAMPLE] Recruiter viewed your profile",
                now - day, "A recruiter from Globex viewed your profile for Automation Engineer.", gmail,
                links=[{"label": "View recruiter details", "url": "https://www.naukri.com/"}]),
        JobMail("sample-4", Category.JOB_ALERT, "LinkedIn Job Alerts", "[SAMPLE] 12 new jobs for 'Python automation'",
                now - day - 3_600_000, "CGI, Infosys and 10 more companies are hiring for Python automation roles.", gmail,
                links=[{"label": "Senior Python Automation Engineer - CGI", "url": "https://www.linkedin.com/jobs/"},
                       {"label": "SDET II - Infosys", "url": "https://www.linkedin.com/jobs/"}]),
        JobMail("sample-5", Category.JOB_ALERT, "Indeed", "[SAMPLE] Test Automation jobs in Bengaluru",
                now - 3 * day, "New jobs matching your search in Bengaluru.", gmail,
                links=[{"label": "Open on indeed.com", "url": "https://www.indeed.com/"}]),
    ]


class Popup:
    def __init__(self, root: tk.Tk, lock: socket.socket | None, sample: list[JobMail] | None = None):
        self.root = root
        self.lock = lock
        self.settings = config.load_settings()
        self.dark = ui.windows_dark_mode(self.settings.theme)
        self.c = ui.DARK if self.dark else ui.LIGHT
        self.s = root.winfo_fpixels("1i") / 96  # DPI scale
        self.f = ui.Fonts(root)
        self.sample = sample
        self.sample_new = {m.message_id for m in sample or []}
        self.mails: list[JobMail] = []
        self.new_ids: set[str] = set()
        self.hidden: set[str] = set() if sample is not None else set(read_json(config.HIDDEN_FILE, []))
        self.status: dict = {}
        self.filter: str | Category = "all"
        self.stamps: dict = {}
        self.today = date.today()
        self.loaded = False
        self.check_proc: subprocess.Popen | None = None
        self.reopen_id = self.topmost_id = self.resize_id = None
        self.rendered_width = 0
        self.wrap = self.px(WIDTH - 110)
        self.icons = [tk.PhotoImage(file=config.BASE_DIR / "assets" / f"icon-{n}.png") for n in (256, 48, 32, 16)]
        self._build()

    def px(self, n: float) -> int:
        return round(n * self.s)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        root, c, px = self.root, self.c, self.px
        root.title("Job Mail" + (" (sample)" if self.sample is not None else ""))
        root.configure(bg=c["window"])
        root.iconphoto(True, *self.icons)
        root.minsize(px(380), px(400))
        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.bind("<Escape>", lambda e: self.hide())
        root.bind("<F5>", lambda e: self.check_now())
        left, top, right, bottom = ui.work_area()
        w, h = px(WIDTH), min(px(HEIGHT), bottom - top - px(24))
        root.geometry(f"{w}x{h}+{right - w - px(12)}+{bottom - h - px(12)}")
        root.attributes("-alpha", 0.0)
        root.update_idletasks()
        ui.style_title_bar(root, c, self.dark)

        ghost = dict(fonts=self.f, font=self.f.small, fg=c["text"], fill=c["window"], bg=c["window"],
                     hover_fill=c["ghost_hover"], radius=px(6), padx=10, pady=6, scale=self.s)

        # Header: icon, title + status line, Refresh
        self.header = header = tk.Frame(root, bg=c["window"])
        header.pack(fill="x", padx=px(18), pady=(px(14), px(6)))
        tk.Label(header, image=self.icons[2], bg=c["window"]).pack(side="left", padx=(0, px(10)))
        ui.Pill(header, "Refresh", icon=ui.GLYPH["refresh"], command=self.check_now, **ghost).pack(side="right")
        titles = tk.Frame(header, bg=c["window"])
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(titles, text="Job Mail", font=self.f.title, fg=c["text"], bg=c["window"], anchor="w").pack(fill="x")
        self.subtitle = tk.Label(titles, font=self.f.small, fg=c["muted"], bg=c["window"], anchor="w")
        self.subtitle.pack(fill="x")

        # Error banner (packed under the header only while the last check failed)
        self.banner = tk.Frame(root, bg=c["error_tint"])
        self.banner_text = tk.Label(self.banner, font=self.f.small, fg=c["text"], bg=c["error_tint"], anchor="w",
                                    justify="left")
        tk.Label(self.banner, text=ui.GLYPH["warning"], font=self.f.icon, fg=c["error"], bg=c["error_tint"]
                 ).pack(side="left", anchor="n", padx=(px(10), px(8)), pady=px(9))
        self.banner_text.pack(side="left", fill="x", expand=True, pady=px(8))
        log_link = tk.Label(self.banner, text="Open log", font=self.f.link, fg=c["accent"], bg=c["error_tint"],
                            cursor="hand2")
        log_link.pack(side="right", anchor="n", padx=px(10), pady=px(8))
        self._linkify(log_link, lambda: webbrowser.open((config.LOG_DIR / "run.log").resolve().as_uri()))

        # Filter chips
        self.chips_row = tk.Frame(root, bg=c["window"])
        self.chips_row.pack(fill="x", padx=px(18), pady=(px(6), px(8)))

        # Footer (packed before the list so it keeps its space)
        footer = tk.Frame(root, bg=c["window"])
        footer.pack(side="bottom", fill="x")
        tk.Frame(footer, bg=c["divider"], height=1).pack(fill="x")
        buttons = tk.Frame(footer, bg=c["window"])
        buttons.pack(fill="x", padx=px(14), pady=px(12))
        ui.Pill(buttons, "Gmail", icon=ui.GLYPH["mail"], command=lambda: webbrowser.open(GMAIL_HOME), **ghost).pack(side="left")
        ui.Pill(buttons, "Summary", icon=ui.GLYPH["list"], command=self.open_report, **ghost).pack(side="left", padx=px(4))
        ui.Pill(buttons, "Mark all seen", icon=ui.GLYPH["check"], fonts=self.f, font=self.f.small_strong,
                fg=c["on_accent"], fill=c["accent"], hover_fill=c["accent_hover"], bg=c["window"],
                radius=px(6), padx=14, pady=6, scale=self.s, command=self.mark_all_seen).pack(side="right")

        # Scrollable list
        body = tk.Frame(root, bg=c["window"])
        body.pack(fill="both", expand=True, padx=(px(14), px(4)))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.layout("Slim.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure("Slim.Vertical.TScrollbar", troughcolor=c["window"], background=c["border_hover"],
                        bordercolor=c["window"], lightcolor=c["border_hover"], darkcolor=c["border_hover"],
                        gripcount=0, arrowsize=px(6), width=px(6))
        style.map("Slim.Vertical.TScrollbar", background=[("active", c["subtle"])])
        self.canvas = tk.Canvas(body, bg=c["window"], highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(body, orient="vertical", style="Slim.Vertical.TScrollbar", command=self.canvas.yview)
        self.list = tk.Frame(self.canvas, bg=c["window"])
        self.list.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.list_window = self.canvas.create_window((0, 0), window=self.list, anchor="nw")
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y", padx=(px(4), 0))
        root.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120) * 2, "units"))

    def _on_canvas_resize(self, event) -> None:
        self.canvas.itemconfigure(self.list_window, width=event.width)
        if self.loaded and abs(event.width - self.rendered_width) > self.px(24):  # re-wrap text
            if self.resize_id:
                self.root.after_cancel(self.resize_id)
            self.resize_id = self.root.after(250, self.render)

    def _linkify(self, label: tk.Label, action) -> None:
        label.bind("<Enter>", lambda e: label.configure(font=self.f.link_hover))
        label.bind("<Leave>", lambda e: label.configure(font=self.f.link))
        label.bind("<Button-1>", lambda e: action())

    # ------------------------------------------------------------------ data
    def _changed(self, path) -> bool:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            mtime = 0.0
        if self.stamps.get(path) == mtime:
            return False
        self.stamps[path] = mtime
        return True

    def poll(self) -> None:
        try:
            self._accept_show_requests()
            changed = not self.loaded
            if self.sample is None:
                changed |= self._changed(config.HISTORY_FILE) | self._changed(config.PENDING_FILE)
                if self._changed(config.STATUS_FILE):
                    self.status = read_json(config.STATUS_FILE, {})
                    self._render_status()
            if date.today() != self.today:  # "Today" / "Yesterday" headers
                self.today = date.today()
                changed = True
            if self.check_proc is not None and self.check_proc.poll() is not None:
                self.check_proc = None
                self._render_status()
            if changed:
                self.load()
        except Exception:
            log.exception("Popup refresh failed")
        finally:
            self.root.after(POLL_MS, self.poll)

    def _accept_show_requests(self) -> None:
        """Another `popup.py` launch connects to our lock port to ask us to come to the front."""
        if self.lock is None:
            return
        while True:
            try:
                conn, _ = self.lock.accept()
            except (BlockingIOError, OSError):
                return
            conn.close()
            self.show(raise_window=True)

    def load(self) -> None:
        if self.sample is not None:
            mails, new_ids = self.sample, set(self.sample_new)
        else:
            mails = load_mails(config.HISTORY_FILE)
            new_ids = {m.message_id for m in load_mails(config.PENDING_FILE)}
        cutoff_ms = (time.time() - self.settings.history_days * 86400) * 1000
        mails = sort_mails([m for m in mails if m.received_ms >= cutoff_ms])
        new_ids &= {m.message_id for m in mails}
        grew = bool(new_ids - self.new_ids)
        first = not self.loaded
        if first or mails != self.mails or new_ids != self.new_ids:
            self.mails, self.new_ids = mails, new_ids
            self.render(keep_scroll=not first)
        if first:
            self.loaded = True
            self._fade(0.0, 1.0)
        if grew:
            self.attention()

    def visible(self) -> list[JobMail]:
        return [m for m in self.mails if m.message_id not in self.hidden]

    def _matches(self, m: JobMail) -> bool:
        if self.filter == "all":
            return True
        if self.filter == "new":
            return m.message_id in self.new_ids
        return m.category is self.filter

    # ------------------------------------------------------------------ rendering
    def render(self, keep_scroll: bool = True) -> None:
        y = self.canvas.yview()[0] if keep_scroll else 0.0
        self._render_status()
        self._render_chips()
        self._render_list()
        self.root.update_idletasks()
        self.canvas.yview_moveto(y)

    def _render_status(self) -> None:
        visible = self.visible()
        n = len(visible)
        k = sum(m.message_id in self.new_ids for m in visible)
        parts = [f"{n} job mail{'s' if n != 1 else ''} · {self.settings.history_days} days"]
        if k:
            parts.append(f"{k} new")
        st = self.status
        running = self.check_proc is not None and self.check_proc.poll() is None
        checking = running or (st.get("state") == "checking" and time.time() - st.get("at", 0) < STALE_CHECK_S)
        if self.sample is not None:
            parts.append("sample data")
        elif checking:
            parts.append("checking Gmail…")
        elif st.get("last_ok"):
            parts.append(f"checked {clock(st['last_ok'])}")
        self.subtitle.configure(text="  ·  ".join(parts))

        if st.get("state") == "error" and not checking:
            self.banner_text.configure(text=f"Check failed at {clock(st.get('at', time.time()))}: {st.get('message', '')}",
                                       wraplength=self.root.winfo_width() - self.px(150))
            self.banner.pack(fill="x", padx=self.px(18), pady=(0, self.px(6)), after=self.header)
        else:
            self.banner.pack_forget()

    def _render_chips(self) -> None:
        c = self.c
        for child in self.chips_row.winfo_children():
            child.destroy()
        visible = self.visible()
        counts = {"all": len(visible), "new": sum(m.message_id in self.new_ids for m in visible)}
        counts.update({cat: sum(m.category is cat for m in visible) for cat in Category})
        if self.filter != "all" and counts.get(self.filter, 0) == 0:
            self.filter = "all"
        for key, label in FILTERS:
            if key != "all" and counts[key] == 0:
                continue
            selected = key == self.filter
            ui.Pill(
                self.chips_row, f"{label}  {counts[key]}", fonts=self.f,
                font=self.f.small_strong if selected else self.f.small,
                fg=c["chip_selected_text"] if selected else c["muted"],
                fill=c["chip_selected"] if selected else c["chip"],
                border=None if selected else c["border"], bg=c["window"],
                hover_fill=c["chip_selected"] if selected else c["ghost_hover"],
                padx=9, pady=4, scale=self.s, command=lambda key=key: self.set_filter(key),
            ).pack(side="left", padx=(0, self.px(5)))

    def set_filter(self, key) -> None:
        self.filter = key
        self.render(keep_scroll=False)

    def _render_list(self) -> None:
        for child in self.list.winfo_children():
            child.destroy()
        width = self.canvas.winfo_width()
        if width < 100:  # not mapped yet
            width = self.px(WIDTH - 40)
        self.rendered_width = width
        self.wrap = max(width - self.px(70), self.px(200))

        shown = [m for m in self.visible() if self._matches(m)][:MAX_CARDS]
        if not shown:
            text = (f"No job mails in the last {self.settings.history_days} days."
                    if self.filter == "all" else "Nothing here.")
            tk.Label(self.list, text=text, font=self.f.body, fg=self.c["subtle"], bg=self.c["window"]).pack(pady=self.px(40))
            return
        per_day: dict[date, int] = {}
        for m in shown:
            per_day[m.received.date()] = per_day.get(m.received.date(), 0) + 1
        current = None
        for m in shown:
            d = m.received.date()
            if d != current:
                self._day_header(d, per_day[d], first=current is None)
                current = d
            self._card(m)

    def _day_header(self, d: date, count: int, first: bool) -> None:
        c, px = self.c, self.px
        row = tk.Frame(self.list, bg=c["window"])
        row.pack(fill="x", padx=(px(2), px(8)), pady=(0 if first else px(10), px(6)))
        tk.Label(row, text=day_label(d, self.today), font=self.f.section, fg=c["muted"], bg=c["window"]).pack(side="left")
        tk.Label(row, text=str(count), font=self.f.caption, fg=c["subtle"], bg=c["window"]).pack(side="right")

    def _card(self, m: JobMail) -> None:
        c, f, px = self.c, self.f, self.px
        fg, tint = c[m.category]
        highlighted = m.category in HIGHLIGHTED
        is_new = m.message_id in self.new_ids
        colors = dict(c)
        if highlighted:  # interviews and calls stand out with a tinted card
            colors["surface"] = tint
        if is_new:  # new mails get a border in the category colour
            colors["border"] = colors["border_hover"] = fg
        surface = colors["surface"]
        badge_fill = c["surface"] if highlighted else tint
        wrap = self.wrap

        card = ui.Card(self.list, colors=colors, bg=c["window"], radius=px(8), pad=px(12))
        card.pack(fill="x", pady=(0, px(8)), padx=(0, px(6)))
        body = card.body

        top = tk.Frame(body, bg=surface)
        top.pack(fill="x")
        ui.avatar(top, (m.who[:1] or "?").upper(), fg=fg, tint=badge_fill, bg=surface, size=px(34), font=f.avatar
                  ).pack(side="left", anchor="n", padx=(0, px(10)))
        right = tk.Frame(top, bg=surface)
        right.pack(side="right", anchor="n")
        tags = tk.Frame(right, bg=surface)
        tags.pack(anchor="e")
        if is_new:
            ui.Pill(tags, "NEW", fonts=f, font=f.caption_strong, fg=c["on_accent"], fill=c["accent"], bg=surface,
                    padx=6, pady=1, scale=self.s).pack(side="left", padx=(0, px(4)))
        ui.Pill(tags, TAG[m.category], fonts=f, font=f.caption_strong, fg=fg, fill=badge_fill, bg=surface,
                padx=8, pady=1, scale=self.s).pack(side="left")
        tk.Label(right, text=f"{m.received:%H:%M}", font=f.caption, fg=c["subtle"], bg=surface
                 ).pack(anchor="e", pady=(px(3), 0))
        heading = tk.Frame(top, bg=surface)
        heading.pack(side="left", fill="x", expand=True)
        tk.Label(heading, text=m.who, font=f.body_strong, fg=c["text"], bg=surface, anchor="w", justify="left",
                 wraplength=wrap - px(150)).pack(fill="x")
        if m.role:
            tk.Label(heading, text=m.role, font=f.small, fg=c["muted"], bg=surface, anchor="w", justify="left",
                     wraplength=wrap - px(150)).pack(fill="x")

        tk.Label(body, text=m.subject or "(no subject)", font=f.small_strong, fg=c["text"], bg=surface, anchor="w",
                 justify="left", wraplength=wrap).pack(fill="x", pady=(px(8), 0))

        if m.when:
            when = tk.Frame(body, bg=surface)
            when.pack(fill="x", pady=(px(6), 0))
            glyph = ui.GLYPH["phone"] if m.category is Category.CALL else ui.GLYPH["calendar"]
            tk.Label(when, text=glyph, font=f.icon, fg=fg, bg=surface).pack(side="left", anchor="n", pady=(px(1), 0))
            tk.Label(when, text=m.when, font=f.small_strong, fg=fg, bg=surface, anchor="w", justify="left",
                     wraplength=wrap - px(24)).pack(side="left", fill="x", padx=(px(6), 0))

        if m.summary and m.summary != m.subject:
            tk.Label(body, text=m.summary, font=f.small, fg=c["muted"], bg=surface, anchor="w",
                     justify="left", wraplength=wrap).pack(fill="x", pady=(px(4), 0))

        for link in m.links:  # portal / meeting hyperlinks
            row = tk.Frame(body, bg=surface)
            row.pack(fill="x", pady=(px(4), 0))
            tk.Label(row, text=ui.GLYPH["link"], font=f.icon_small, fg=c["accent"], bg=surface
                     ).pack(side="left", anchor="n", pady=(px(2), 0))
            label = tk.Label(row, text=link["label"], font=f.link, fg=c["accent"], bg=surface, cursor="hand2",
                             anchor="w", justify="left", wraplength=wrap - px(24))
            label.pack(side="left", padx=(px(5), 0))
            self._linkify(label, lambda url=link["url"]: self.open_url(m, url))

        actions = tk.Frame(body, bg=surface)
        actions.pack(fill="x", pady=(px(8), 0))
        pill = dict(fonts=f, font=f.small_strong, fill=surface, bg=surface, hover_fill=c["ghost_hover"],
                    radius=px(5), padx=6, pady=3, scale=self.s)
        ui.Pill(actions, "Open in Gmail", icon=ui.GLYPH["open"], fg=c["accent"],
                command=lambda: self.open_url(m, m.view_url), **pill).pack(side="left")
        ui.Pill(actions, "Hide", icon=ui.GLYPH["hide"], fg=c["muted"], command=lambda: self.hide_mail(m),
                **pill).pack(side="right")
        card.fit()

        # Hover highlight for the whole card (Leave also fires when moving onto a child, so re-check).
        def on_enter(_e):
            card.set_hover(True)

        def on_leave(e):
            under = self.root.winfo_containing(e.x_root, e.y_root)
            if under is None or not str(under).startswith(str(card)):
                card.set_hover(False)

        for w in [card, *self._descendants(card)]:
            w.bind("<Enter>", on_enter, add="+")
            w.bind("<Leave>", on_leave, add="+")

    @staticmethod
    def _descendants(widget: tk.Misc) -> list[tk.Misc]:
        out = []
        for child in widget.winfo_children():
            out.append(child)
            out.extend(Popup._descendants(child))
        return out

    # ------------------------------------------------------------------ window behaviour
    def attention(self) -> None:
        """New mail: bring the window back (even from hidden/minimised), keep it on top briefly, chime."""
        self.show(raise_window=True)
        self.root.attributes("-topmost", True)
        if self.topmost_id:
            self.root.after_cancel(self.topmost_id)
        self.topmost_id = self.root.after(6000, lambda: self.root.attributes("-topmost", False))
        winsound.MessageBeep(winsound.MB_ICONASTERISK)

    def show(self, raise_window: bool = False) -> None:
        if self.reopen_id:
            self.root.after_cancel(self.reopen_id)
            self.reopen_id = None
        state = self.root.state()
        if state == "withdrawn" or (state == "iconic" and raise_window):
            self.root.attributes("-alpha", 0.0)
            self.root.deiconify()
            self._fade(0.0, 1.0)
        self.root.lift()
        if raise_window:
            self.root.focus_force()

    def hide(self) -> None:
        """✕ / Esc: hide now, come back after REOPEN_MINUTES (or earlier if new mail arrives)."""
        if self.sample is not None:
            self._fade(1.0, 0.0, then=self.root.destroy)
            return

        def gone() -> None:
            self.root.withdraw()
            self.root.attributes("-alpha", 1.0)

        self._fade(float(self.root.attributes("-alpha")), 0.0, then=gone)
        if self.reopen_id:
            self.root.after_cancel(self.reopen_id)
        self.reopen_id = self.root.after(int(self.settings.reopen_minutes * 60_000), self.show)

    def _fade(self, start: float, end: float, then=None, steps: int = 10) -> None:
        def step(i: int) -> None:
            self.root.attributes("-alpha", start + (end - start) * i / steps)
            if i < steps:
                self.root.after(16, step, i + 1)
            elif then:
                then()
        step(0)

    # ------------------------------------------------------------------ actions
    def check_now(self) -> None:
        """Refresh: run a Gmail check right away (main.py), the list updates when it finishes."""
        if self.sample is not None:
            self.render()
            return
        if self.check_proc is not None and self.check_proc.poll() is None:
            return
        from notifier import pythonw
        self.check_proc = subprocess.Popen(
            [pythonw(), str(config.BASE_DIR / "main.py"), "--trigger", "manual"],
            cwd=config.BASE_DIR, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._render_status()

    def open_url(self, m: JobMail, url: str) -> None:
        webbrowser.open(url)
        self.mark_seen({m.message_id})

    def mark_seen(self, ids: set[str]) -> None:
        ids = set(ids) & self.new_ids
        if not ids:
            return
        if self.sample is not None:
            self.sample_new -= ids
        else:
            save_mails(config.PENDING_FILE, [p for p in load_mails(config.PENDING_FILE) if p.message_id not in ids])
            self._changed(config.PENDING_FILE)  # our own write: no reload
        self.new_ids -= ids
        self.render()

    def mark_all_seen(self) -> None:
        if self.sample is not None:
            self.sample_new.clear()
        else:
            save_mails(config.PENDING_FILE, [])
            self._changed(config.PENDING_FILE)
        self.new_ids.clear()
        self.render()

    def hide_mail(self, m: JobMail) -> None:
        self.hidden.add(m.message_id)
        if self.sample is None:
            current = {x.message_id for x in self.mails}
            write_json(config.HIDDEN_FILE, sorted(self.hidden & current))
        if m.message_id in self.new_ids:
            self.mark_seen({m.message_id})  # re-renders
        else:
            self.render()

    def open_report(self) -> None:
        report = config.REPORT_DIR / "latest.html"
        if report.exists():
            webbrowser.open(report.resolve().as_uri())


def main() -> int:
    sample = "--sample" in sys.argv
    lock = None
    if not sample:
        lock = acquire_single_instance()
        if lock is None:  # already running: bring it to the front unless started by a background check
            if "--no-raise" not in sys.argv:
                ask_running_popup_to_show()
            return 0
    config.LOG_DIR.mkdir(exist_ok=True)
    config.migrate_legacy_files()
    logging.basicConfig(filename=config.LOG_DIR / "popup.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)  # own taskbar icon
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    root.report_callback_exception = lambda *exc: log.error("Tk callback failed", exc_info=exc)
    Popup(root, lock, sample_mails() if sample else None).poll()
    root.mainloop()
    if lock is not None:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
