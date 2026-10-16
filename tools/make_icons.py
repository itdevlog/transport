"""Generate PNG app icons from the same shapes as the SVG icons.

Pure stdlib (zlib + struct) so no image library is required. Run:

    python3 tools/make_icons.py
"""

import os
import struct
import zlib

CANVAS = 192  # design coordinate space, matches static/icon-192.svg
BRAND = (0xC8, 0x10, 0x2E)  # #c8102e
WHITE = (255, 255, 255)

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _in_rounded_rect(x, y, x0, y0, x1, y1, r):
    dx = max(x0 - x, 0.0, x - x1)
    dy = max(y0 - y, 0.0, y - y1)
    if dx == 0.0 and dy == 0.0:
        return True
    return dx * dx + dy * dy <= r * r


def _in_circle(x, y, cx, cy, r):
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _is_white(x, y):
    # windows (cut-outs showing brand colour) are handled first
    if _in_rounded_rect(x, y, 58, 60, 90, 96, 6) or _in_rounded_rect(x, y, 102, 60, 134, 96, 6):
        return False
    if _in_rounded_rect(x, y, 44, 44, 148, 132, 14):  # bus body
        return True
    if _in_circle(x, y, 68, 140, 13) or _in_circle(x, y, 124, 140, 13):  # wheels
        return True
    return False


def _pixel(out_x, out_y, scale, samples=3):
    white = 0
    for i in range(samples):
        for j in range(samples):
            x = (out_x + (i + 0.5) / samples) / scale
            y = (out_y + (j + 0.5) / samples) / scale
            if _is_white(x, y):
                white += 1
    if white == 0:
        return BRAND
    if white == samples * samples:
        return WHITE
    cov = white / (samples * samples)
    return tuple(round(BRAND[k] + (WHITE[k] - BRAND[k]) * cov) for k in range(3))


def _chunk(tag, data):
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(path, size):
    scale = size / CANVAS
    rows = []
    for y in range(size):
        row = bytearray([0])  # filter type 0 (None)
        for x in range(size):
            row += bytes(_pixel(x, y, scale))
        rows.append(bytes(row))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + _chunk(b"IEND", b"")
    )
    with open(path, "wb") as fh:
        fh.write(png)


def main():
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        write_png(os.path.join(STATIC, name), size)
        print("wrote", name, size)


if __name__ == "__main__":
    main()
