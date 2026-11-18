"""Writes a local HTML page listing recent job mails day by day (same look as the popup), with Gmail and portal links."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from models import HIGHLIGHTED, Category, JobMail, sort_mails

TAG = {Category.INTERVIEW: "Interview", Category.CALL: "Call", Category.APPLICATION: "Update",
       Category.JOB_ALERT: "Job alert"}
FILTER_LABEL = {Category.INTERVIEW: "Interviews", Category.CALL: "Calls", Category.APPLICATION: "Updates",
                Category.JOB_ALERT: "Job alerts"}

_CSS = """
:root { color-scheme: light dark;
  --bg:#f3f3f3; --surface:#fff; --border:#e5e5e5; --border-hover:#c7c7c7; --text:#1b1b1f; --muted:#5f6368;
  --subtle:#8a8f98; --accent:#0067c0; --chip-sel:#dbe9f7; --chip-sel-text:#003e73;
  --interview:#b5470b; --interview-tint:#fdeee3; --call:#7a3fb5; --call-tint:#f3ebfb;
  --application:#0f5fa8; --application-tint:#e6f0fa; --job_alert:#0e7a3d; --job_alert-tint:#e5f3ea; }
@media (prefers-color-scheme: dark) { :root {
  --bg:#202020; --surface:#2b2b2b; --border:#3a3a3a; --border-hover:#575757; --text:#fff; --muted:#c8c8c8;
  --subtle:#9d9d9d; --accent:#4cc2ff; --chip-sel:#173a52; --chip-sel-text:#9fdcff;
  --interview:#ffb07a; --interview-tint:#3d2a1d; --call:#d4b0ff; --call-tint:#33264a;
  --application:#82c8ff; --application-tint:#1c2f40; --job_alert:#7fd6a0; --job_alert-tint:#1d3326; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:14.5px/1.5 "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif; }
main { max-width:760px; margin:0 auto; padding:32px 16px 56px; }
header { display:flex; align-items:center; gap:14px; }
header img { width:44px; height:44px; }
h1 { font:700 24px/1.2 "Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif; margin:0; }
h2 { font-size:13px; font-weight:600; color:var(--muted); margin:22px 2px 8px; display:flex; justify-content:space-between; }
h2 span { color:var(--subtle); font-weight:400; }
.meta { color:var(--muted); font-size:13px; margin-top:2px; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin:22px 0 6px; }
.chip { font:inherit; font-size:13px; color:var(--muted); background:var(--surface); border:1px solid var(--border);
        border-radius:999px; padding:4px 12px; cursor:pointer; }
.chip:hover { border-color:var(--border-hover); }
.chip[aria-pressed="true"] { background:var(--chip-sel); color:var(--chip-sel-text); border-color:transparent; font-weight:600; }
.card { display:grid; grid-template-columns:auto 1fr auto; gap:4px 12px; background:var(--surface);
        border:1px solid var(--border); border-radius:8px; padding:14px; margin-bottom:8px; }
.card:hover { border-color:var(--border-hover); }
.card.hl { background:var(--tint); }
.card.new { border-color:var(--fg); }
.avatar { grid-row:span 2; width:36px; height:36px; border-radius:50%; display:grid; place-items:center;
          font-weight:600; font-size:15px; background:var(--badge); color:var(--fg); }
.who { font-weight:600; }
.subject { font-size:13.5px; font-weight:600; }
.side { grid-row:span 2; text-align:right; }
.tag { display:inline-block; font-size:11px; font-weight:600; border-radius:999px; padding:1px 8px;
       background:var(--badge); color:var(--fg); }
.time { display:block; color:var(--subtle); font-size:12px; margin-top:4px; }
.when, .summary, .links, .actions { grid-column:2 / -1; }
.when { font-weight:600; font-size:13px; margin-top:4px; color:var(--fg); }
.summary { color:var(--muted); font-size:13.5px; }
.links a { display:block; font-size:13px; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.actions a { font-weight:600; font-size:13px; }
.badge-new { font-size:11px; font-weight:700; letter-spacing:.04em; color:var(--accent); margin-left:6px; }
.empty { color:var(--subtle); text-align:center; padding:48px 0; }
@media (max-width:520px) { .card { grid-template-columns:auto 1fr; } .side { grid-column:2; grid-row:auto; text-align:left; } }
"""

_JS = """
document.querySelectorAll('.chip').forEach(chip => chip.addEventListener('click', () => {
  document.querySelectorAll('.chip').forEach(c => c.setAttribute('aria-pressed', c === chip));
  const f = chip.dataset.filter;
  document.querySelectorAll('.card').forEach(card => { card.hidden = f !== 'all' && card.dataset.cat !== f; });
  document.querySelectorAll('section.day').forEach(s => { s.hidden = !s.querySelector('.card:not([hidden])'); });
}));
"""


def _day_label(d: date) -> str:
    today = date.today()
    if d == today:
        return f"Today · {d:%a %d %b}"
    if d == today - timedelta(days=1):
        return f"Yesterday · {d:%a %d %b}"
    return f"{d:%A, %d %b %Y}"


def write_report(items: list[JobMail], since: datetime, out_dir: Path, new_ids: frozenset[str] = frozenset()) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    items = sort_mails(items)
    counts = Counter(m.category for m in items)
    chips = [f'<button class="chip" data-filter="all" aria-pressed="true">All&nbsp; {len(items)}</button>']
    chips += [
        f'<button class="chip" data-filter="{cat.value}" aria-pressed="false">{FILTER_LABEL[cat]}&nbsp; {counts[cat]}</button>'
        for cat in Category if counts[cat]
    ]
    days: dict[date, list[JobMail]] = {}
    for m in items:
        days.setdefault(m.received.date(), []).append(m)
    sections = "\n".join(
        f'<section class="day"><h2>{_day_label(d)} <span>{len(ms)}</span></h2>\n'
        + "\n".join(_card(m, m.message_id in new_ids) for m in ms) + "</section>"
        for d, ms in days.items()
    )
    body = sections or '<p class="empty">No job-related emails in this period.</p>'
    icon = (Path(__file__).resolve().parent / "assets" / "icon-64.png").as_uri()
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Mail Summary</title><link rel="icon" href="{icon}"><style>{_CSS}</style></head>
<body><main>
<header><img src="{icon}" alt=""><div><h1>Job Mail</h1>
<div class="meta">{len(items)} job mail{'s' if len(items) != 1 else ''} since {since:%d %b %Y} · updated {datetime.now():%d %b, %H:%M}</div></div></header>
<nav class="chips">{''.join(chips) if items else ''}</nav>
{body}
</main><script>{_JS}</script></body></html>"""
    path = out_dir / "latest.html"
    path.write_text(page, encoding="utf-8")
    return path


def _card(m: JobMail, is_new: bool = False) -> str:
    cat = m.category.value
    hl = m.category in HIGHLIGHTED
    classes = "card" + (" hl" if hl else "") + (" new" if is_new else "")
    style = f"--fg:var(--{cat});--tint:var(--{cat}-tint);--badge:var(--{'surface' if hl else cat + '-tint'})"
    role = f'<div class="role">{escape(m.role)}</div>' if m.role else '<div></div>'
    when = f'<div class="when">{"📞" if m.category is Category.CALL else "📅"} {escape(m.when)}</div>' if m.when else ""
    summary = f'<div class="summary">{escape(m.summary)}</div>' if m.summary and m.summary != m.subject else ""
    links = "".join(
        f'<a href="{escape(link["url"])}" target="_blank" rel="noopener">🔗 {escape(link["label"])}</a>' for link in m.links
    )
    new = '<span class="badge-new">NEW</span>' if is_new else ""
    return f"""<article class="{classes}" data-cat="{cat}" style="{style}">
  <div class="avatar">{escape((m.who[:1] or "?").upper())}</div>
  <div class="who">{escape(m.who)}{new}</div>
  <div class="side"><span class="tag">{TAG[m.category]}</span><span class="time">{m.received:%H:%M}</span></div>
  {role}
  <div class="subject" style="grid-column:2 / -1">{escape(m.subject)}</div>
  {when}
  {summary}
  <div class="links">{links}</div>
  <div class="actions"><a href="{escape(m.view_url)}" target="_blank" rel="noopener">Open in Gmail ↗</a></div>
</article>"""
