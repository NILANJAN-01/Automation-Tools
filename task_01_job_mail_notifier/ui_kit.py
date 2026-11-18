"""Fluent-style building blocks for Tkinter: Windows theme detection, anti-aliased rounded
shapes (rendered into PhotoImages, since Tk's canvas does not anti-alias) and a few widgets."""
from __future__ import annotations

import ctypes
import tkinter as tk
import winreg
from ctypes import wintypes
from functools import lru_cache
from tkinter import font as tkfont

from models import Category

# --------------------------------------------------------------------------- theme

LIGHT = {
    "window": "#f3f3f3", "surface": "#ffffff", "border": "#e5e5e5", "border_hover": "#c7c7c7",
    "text": "#1b1b1f", "muted": "#5f6368", "subtle": "#8a8f98", "divider": "#e5e5e5",
    "accent": "#0067c0", "accent_hover": "#1975c5", "on_accent": "#ffffff",
    "ghost_hover": "#e9e9ec", "chip": "#ffffff", "chip_selected": "#dbe9f7", "chip_selected_text": "#003e73",
    "warn": "#9d5d00", "warn_tint": "#fff4ce", "error": "#c42b1c", "error_tint": "#fde7e9",
    Category.INTERVIEW: ("#b5470b", "#fdeee3"),
    Category.CALL: ("#7a3fb5", "#f3ebfb"),
    Category.APPLICATION: ("#0f5fa8", "#e6f0fa"),
    Category.JOB_ALERT: ("#0e7a3d", "#e5f3ea"),
}
DARK = {
    "window": "#202020", "surface": "#2b2b2b", "border": "#3a3a3a", "border_hover": "#575757",
    "text": "#ffffff", "muted": "#c8c8c8", "subtle": "#9d9d9d", "divider": "#353535",
    "accent": "#4cc2ff", "accent_hover": "#62cbff", "on_accent": "#000000",
    "ghost_hover": "#2f2f2f", "chip": "#2b2b2b", "chip_selected": "#173a52", "chip_selected_text": "#9fdcff",
    "warn": "#fce100", "warn_tint": "#433519", "error": "#ff99a4", "error_tint": "#442726",
    Category.INTERVIEW: ("#ffb07a", "#3d2a1d"),
    Category.CALL: ("#d4b0ff", "#33264a"),
    Category.APPLICATION: ("#82c8ff", "#1c2f40"),
    Category.JOB_ALERT: ("#7fd6a0", "#1d3326"),
}

# Segoe Fluent Icons glyphs
GLYPH = {"calendar": "", "open": "", "check": "", "mail": "", "list": ""}

GLYPH.update({"refresh": "", "link": "", "phone": "", "hide": "", "warning": ""})


def windows_dark_mode(theme: str = "auto") -> bool:
    """theme: 'light', 'dark' or 'auto' (follow the Windows app theme)."""
    if theme in ("light", "dark"):
        return theme == "dark"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def _colorref(hex_color: str) -> int:
    r, g, b = _rgb(hex_color)
    return r | (g << 8) | (b << 16)


def style_title_bar(root: tk.Tk, colors: dict, dark: bool) -> None:
    """Dark/light title bar and a caption colour matching the window (Windows 11)."""
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dwm(hwnd, 20, ctypes.byref(ctypes.c_int(1 if dark else 0)), 4)            # immersive dark mode
        dwm(hwnd, 35, ctypes.byref(ctypes.c_int(_colorref(colors["window"]))), 4)  # caption colour
        dwm(hwnd, 36, ctypes.byref(ctypes.c_int(_colorref(colors["text"]))), 4)    # caption text
    except (AttributeError, OSError):
        pass


def work_area() -> tuple[int, int, int, int]:
    """Screen area excluding the taskbar: (left, top, right, bottom)."""
    rect = wintypes.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
        return rect.left, rect.top, rect.right, rect.bottom
    user32 = ctypes.windll.user32
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1) - 48


class Fonts:
    def __init__(self, root: tk.Misc):
        fams = set(tkfont.families(root))
        display = "Segoe UI Variable Display" if "Segoe UI Variable Display" in fams else "Segoe UI"
        text = "Segoe UI Variable Text" if "Segoe UI Variable Text" in fams else "Segoe UI"
        semibold = "Segoe UI Semibold" if "Segoe UI Semibold" in fams else text
        icons = "Segoe Fluent Icons" if "Segoe Fluent Icons" in fams else "Segoe MDL2 Assets"
        self.title = tkfont.Font(root, family=display, size=14, weight="bold")
        self.body = tkfont.Font(root, family=text, size=10)
        self.body_strong = tkfont.Font(root, family=semibold, size=10)
        self.small = tkfont.Font(root, family=text, size=9)
        self.small_strong = tkfont.Font(root, family=semibold, size=9)
        self.caption = tkfont.Font(root, family=text, size=8)
        self.caption_strong = tkfont.Font(root, family=semibold, size=8)
        self.avatar = tkfont.Font(root, family=semibold, size=12)
        self.icon = tkfont.Font(root, family=icons, size=10)
        self.icon_small = tkfont.Font(root, family=icons, size=9)
        self.link = tkfont.Font(root, family=text, size=9)
        self.link_hover = tkfont.Font(root, family=text, size=9, underline=True)
        self.section = tkfont.Font(root, family=semibold, size=9)


# --------------------------------------------------------------------------- drawing

def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def _coverage(x: int, y: int, w: int, h: int, r: float, inset: float, ss: int = 4) -> float:
    """Fraction of pixel (x, y) inside the rounded rect inset by `inset` px with radius r - inset."""
    x0, y0, x1, y1 = inset, inset, w - inset, h - inset
    rad = max(r - inset, 0.0)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    hits = 0
    for sy in range(ss):
        py = y + (sy + 0.5) / ss
        for sx in range(ss):
            px = x + (sx + 0.5) / ss
            if not (x0 <= px <= x1 and y0 <= py <= y1):
                continue
            cx = min(max(px, x0 + rad), x1 - rad)
            cy = min(max(py, y0 + rad), y1 - rad)
            if (px - cx) ** 2 + (py - cy) ** 2 <= rad * rad:
                hits += 1
    return hits / (ss * ss)


@lru_cache(maxsize=256)
def _rounded_rows(w: int, h: int, r: int, fill: str, border: str | None, bg: str) -> str:
    f, b, g = _rgb(fill), _rgb(border or fill), _rgb(bg)
    bw = 1 if border else 0

    def color(outer: float, inner: float) -> str:
        return _hex(tuple(g[i] * (1 - outer) + b[i] * (outer - inner) + f[i] * inner for i in range(3)))

    plain_fill = _hex(f)
    edge = color(1, 0)
    rows = []
    for y in range(h):
        if r <= y < h - r:  # middle band: only the left/right border pixels differ
            if bw:
                rows.append("{" + " ".join([edge] + [plain_fill] * (w - 2) + [edge]) + "}")
            else:
                rows.append("{" + " ".join([plain_fill] * w) + "}")
            continue
        row = []
        for x in range(w):
            if r <= x < w - r:  # top/bottom straight edge
                inner = 0.0 if bw and (y == 0 or y == h - 1) else 1.0
                row.append(color(1, inner))
            else:
                outer = _coverage(x, y, w, h, r, 0)
                inner = _coverage(x, y, w, h, r, bw) if bw else outer
                row.append(color(outer, inner))
        rows.append("{" + " ".join(row) + "}")
    return " ".join(rows)


_IMAGES: dict[tuple, tk.PhotoImage] = {}


def rounded_image(w: int, h: int, r: int, fill: str, bg: str, border: str | None = None) -> tk.PhotoImage:
    key = (w, h, r, fill, border, bg)
    img = _IMAGES.get(key)
    if img is None:
        img = tk.PhotoImage(width=w, height=h)
        img.put(_rounded_rows(w, h, r, fill, border, bg))
        _IMAGES[key] = img
    return img


# --------------------------------------------------------------------------- widgets

class Pill(tk.Canvas):
    """Rounded label/button: optional icon glyph + text, optional hover colours and command."""

    def __init__(self, parent, text: str, *, fonts: Fonts, font: tkfont.Font, fg: str, fill: str, bg: str,
                 border: str | None = None, icon: str = "", hover_fill: str | None = None,
                 hover_fg: str | None = None, command=None, padx: int = 10, pady: int = 4, radius: int | None = None,
                 scale: float = 1.0):
        self.fonts, self.font = fonts, font
        self.text, self.icon = text, icon
        self.colors = {"fg": fg, "fill": fill, "border": border, "hover_fill": hover_fill or fill,
                       "hover_fg": hover_fg or fg, "bg": bg}
        self.command = command
        self.padx, self.pady = round(padx * scale), round(pady * scale)
        icon_font = fonts.icon_small
        self.icon_w = icon_font.measure(icon) + round(6 * scale) if icon else 0
        self.w = self.padx * 2 + self.icon_w + font.measure(text)
        self.h = self.pady * 2 + font.metrics("linespace")
        self.radius = radius if radius is not None else self.h // 2
        super().__init__(parent, width=self.w, height=self.h, bg=bg, highlightthickness=0, bd=0,
                         cursor="hand2" if command else "")
        self._draw(False)
        if command:
            self.bind("<Enter>", lambda e: self._draw(True))
            self.bind("<Leave>", lambda e: self._draw(False))
            self.bind("<Button-1>", lambda e: self.command())

    def _draw(self, hover: bool) -> None:
        c = self.colors
        self.delete("all")
        img = rounded_image(self.w, self.h, self.radius, c["hover_fill"] if hover else c["fill"], c["bg"], c["border"])
        self.create_image(0, 0, image=img, anchor="nw")
        fg = c["hover_fg"] if hover else c["fg"]
        x = self.padx
        if self.icon:
            self.create_text(x, self.h / 2 + 1, text=self.icon, font=self.fonts.icon_small, fill=fg, anchor="w")
            x += self.icon_w
        self.create_text(x, self.h / 2, text=self.text, font=self.font, fill=fg, anchor="w")

    def set_colors(self, **colors) -> None:
        self.colors.update(colors)
        self._draw(False)


class Card(tk.Canvas):
    """Rounded surface whose height follows its content frame (`self.body`)."""

    def __init__(self, parent, *, colors: dict, bg: str, radius: int, pad: int):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=10)
        self.colors, self.bg_color, self.radius, self.pad = colors, bg, radius, pad
        self.hover = False
        self.body = tk.Frame(self, bg=colors["surface"])
        self.window = self.create_window(pad, pad, window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_resize)

    def fit(self) -> None:
        """Call after filling `body`: size the card to the content's requested height.
        (The body's own <Configure> is useless here: while the card is too short the
        embedded window is unmapped and never reports its real size.)"""
        self.body.update_idletasks()
        h = self.body.winfo_reqheight() + 2 * self.pad
        if int(self.cget("height")) != h:
            self.configure(height=h)
        self._paint()

    def _on_resize(self, event) -> None:
        self.itemconfigure(self.window, width=max(event.width - 2 * self.pad, 10))
        self._paint()

    def set_hover(self, hover: bool) -> None:
        if hover != self.hover:
            self.hover = hover
            self._paint()

    def _paint(self) -> None:
        w, h = self.winfo_width(), int(self.cget("height"))
        if w < 2 * self.radius or h < 2 * self.radius:
            return
        border = self.colors["border_hover"] if self.hover else self.colors["border"]
        img = rounded_image(w, h, self.radius, self.colors["surface"], self.bg_color, border)
        self.delete("bgimg")
        self.create_image(0, 0, image=img, anchor="nw", tags="bgimg")
        self.tag_lower("bgimg")


def avatar(parent, letter: str, *, fg: str, tint: str, bg: str, size: int, font: tkfont.Font) -> tk.Canvas:
    c = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
    c.create_image(0, 0, image=rounded_image(size, size, size // 2, tint, bg), anchor="nw")
    c.create_text(size / 2, size / 2, text=letter, fill=fg, font=font)
    return c
