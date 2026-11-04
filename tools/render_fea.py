"""Render auz.fea features (filled polygons + lines), optionally with mp0 roads.
Usage: python -I render_fea.py <auz.fea> <out.png> lon0 lat0 lon1 lat1 [width] [types]
types: comma list like 2:55,2:75,1:200 to draw only those (cat:type).
"""
import math
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import features  # noqa: E402

PALETTE = {
    (2, 80): (170, 205, 240), (2, 75): (140, 190, 235), (2, 90): (140, 190, 235),
    (2, 10): (160, 210, 150), (2, 12): (175, 220, 165), (2, 14): (190, 225, 180),
    (2, 55): (185, 230, 170), (2, 204): (150, 215, 130), (2, 110): (235, 225, 190),
    (2, 150): (215, 210, 230), (2, 35): (245, 240, 230), (2, 125): (240, 200, 200),
    (1, 90): (60, 130, 220), (1, 95): (80, 80, 80), (1, 155): (200, 60, 60),
    (1, 200): (230, 120, 0), (1, 202): (160, 0, 160),
}
OTHER = [(230, 150, 150), (200, 170, 120), (150, 150, 220), (220, 200, 120), (170, 220, 220),
         (210, 160, 210), (190, 190, 190), (240, 180, 120)]


def colour(key):
    if key in PALETTE:
        return PALETTE[key]
    return OTHER[hash(key) % len(OTHER)]


def render(fea_path, out, box, W=1400, only=None, level=0):
    f = features.FeatureFile(open(fea_path, "rb").read())
    lv = f.levels[level]
    x0, y0, x1, y1 = box
    k = math.cos(math.radians((y0 + y1) / 2e5))
    H = int(W * (y1 - y0) / ((x1 - x0) * k))
    img = bytearray(b"\xff" * (W * H * 3))
    sx = lambda x: (x - x0) / (x1 - x0) * W
    sy = lambda y: (y1 - y) / (y1 - y0) * H
    bx0, by0 = lv["bbox"][0], lv["bbox"][1]
    ixs = range(int((x0 - bx0) // lv["parcel_w"]), int((x1 - bx0) // lv["parcel_w"]) + 1)
    iys = range(int((y0 - by0) // lv["parcel_h"]), int((y1 - by0) // lv["parcel_h"]) + 1)
    polys, lines = [], []
    for ix in ixs:
        for iy in iys:
            for ft in f.features(level, ix, iy):
                key = (ft["category"], ft["type"])
                if only and key not in only:
                    continue
                for part in ft["parts"]:
                    pts = [(sx(x), sy(y)) for x, y in part]
                    if ft["category"] == 2 and len(pts) >= 3:
                        polys.append((key, pts))
                    elif ft["category"] == 1:
                        lines.append((key, pts))
    order = lambda kp: {80: 0, 35: 1}.get(kp[0][1], 5)
    for key, pts in sorted(polys, key=order):
        col = bytes(colour(key))
        ys = [p[1] for p in pts]
        for yy in range(max(0, int(min(ys))), min(H, int(max(ys)) + 1)):
            xs = []
            for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
                if (ay <= yy < by) or (by <= yy < ay):
                    xs.append(ax + (yy - ay) * (bx - ax) / (by - ay))
            xs.sort()
            for a, b in zip(xs[::2], xs[1::2]):
                for xx in range(max(0, int(a)), min(W, int(b) + 1)):
                    j = 3 * (yy * W + xx)
                    img[j:j + 3] = col
    for key, pts in lines:
        col = bytes(colour(key))
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            n = int(max(abs(bx - ax), abs(by - ay))) + 1
            for t in range(n + 1):
                px, py = int(ax + (bx - ax) * t / n), int(ay + (by - ay) * t / n)
                if 0 <= px < W and 0 <= py < H:
                    j = 3 * (py * W + px)
                    img[j:j + 3] = col
    raw = b"".join(b"\x00" + bytes(img[3 * W * y:3 * W * (y + 1)]) for y in range(H))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    with open(out, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                 + ch(b"IDAT", zlib.compress(raw, 9)) + ch(b"IEND", b""))
    return len(polys), len(lines)


if __name__ == "__main__":
    a = sys.argv
    box = tuple(int(round(float(v) * 1e5)) for v in a[3:7])
    only = None
    if len(a) > 8 and a[8]:
        only = {tuple(int(x) for x in t.split(":")) for t in a[8].split(",")}
    print(render(a[1], a[2], box, int(a[7]) if len(a) > 7 else 1400, only))
