"""Generate assets/icon-*.png and assets/icon.ico (no third-party imaging library needed).

Design: blue rounded square, white envelope, orange notification dot.
    python tools/make_icon.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "assets"
SIZES = [16, 24, 32, 48, 64, 256]
SS = 4  # supersampling factor for anti-aliasing

TOP, BOTTOM = (59, 130, 246), (29, 78, 216)  # gradient: blue-500 -> blue-700
WHITE, DOT = (255, 255, 255), (249, 115, 22)  # orange-500


def _rounded_rect(x, y, x0, y0, x1, y1, r):
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _dist_to_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def sample(u: float, v: float):
    """Colour (r, g, b, a) at normalised coordinates u, v in [0, 1]."""
    # Notification dot (drawn over everything, with a ring in the background colour)
    dx, dy = u - 0.80, v - 0.22
    d2 = dx * dx + dy * dy
    if d2 <= 0.115 ** 2:
        return (*DOT, 255)
    if not _rounded_rect(u, v, 0.0, 0.0, 1.0, 1.0, 0.22):
        return (0, 0, 0, 0)
    bg = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * v) for i in range(3))
    if d2 <= 0.155 ** 2:  # gap ring around the dot
        return (*bg, 255)
    # Envelope body
    x0, y0, x1, y1 = 0.20, 0.32, 0.80, 0.74
    if _rounded_rect(u, v, x0, y0, x1, y1, 0.05):
        # Flap: a "V" stroke in the background colour
        stroke = 0.035
        if min(_dist_to_segment(u, v, x0 + 0.03, y0 + 0.04, 0.5, 0.56),
               _dist_to_segment(u, v, x1 - 0.03, y0 + 0.04, 0.5, 0.56)) <= stroke:
            return (*bg, 255)
        return (*WHITE, 255)
    return (*bg, 255)


def render(size: int) -> bytes:
    rows = []
    n = size * SS
    for py in range(size):
        row = bytearray([0])  # PNG filter type 0
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    r, g, b, a = sample((px * SS + sx + 0.5) / n, (py * SS + sy + 0.5) / n)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            alpha = acc[3] / (SS * SS)
            if acc[3]:
                row += bytes((round(acc[0] / acc[3]), round(acc[1] / acc[3]), round(acc[2] / acc[3]), round(alpha)))
            else:
                row += bytes(4)
        rows.append(bytes(row))
    return _png(size, size, b"".join(rows))


def _png(w: int, h: int, raw: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _ico(pngs: dict[int, bytes]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, data in pngs.items():
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return header + entries + blobs


def main() -> None:
    OUT.mkdir(exist_ok=True)
    pngs = {s: render(s) for s in SIZES}
    for s, data in pngs.items():
        (OUT / f"icon-{s}.png").write_bytes(data)
    (OUT / "icon.ico").write_bytes(_ico(pngs))
    print(f"Wrote {len(pngs)} PNGs and icon.ico to {OUT}")


if __name__ == "__main__":
    main()
