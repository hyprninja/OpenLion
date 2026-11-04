"""Render decoded map segments (with shapes) to a PNG, coloured by seginfo rank.
No third-party libraries. Usage:
  python -I render.py <map file> <si decompressed> <out.png> lon0 lat0 lon1 lat1 [width]
"""
import collections
import math
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tiles  # noqa: E402

COLOURS = {0: (150, 150, 150), 1: (90, 110, 150), 2: (40, 90, 200), 3: (230, 140, 0), 4: (200, 0, 0)}


def polylines(m, SI, box):
    x0, y0, x1, y1 = box
    for i in range(m.pcl_cnt):
        p = tiles.Parcel(m.parcel(i))
        P = p.positions()
        if not P:
            continue
        xs = [a for a, _ in P]
        ys = [b for _, b in P]
        if max(xs) < x0 or min(xs) > x1 or max(ys) < y0 or min(ys) > y1:
            continue
        X = p.positions("exc_pos")
        ss = p.segments()
        sh = p.shapes()
        ends = collections.defaultdict(list)
        for ni, n in enumerate(p.nodes()):
            if not n["long"]:
                for s in n["segs"]:
                    ends[s].append(ni)
        for s, sg in enumerate(ss):
            e = ends[s]
            if len(e) == 2:
                A, B = P[e[0]], P[e[1]]
            elif len(e) == 1 and sg["right_node"] is not None and sg["right_node"] < len(X):
                A, B = P[e[0]], X[sg["right_node"]]
            elif len(e) == 1 and sg["left_node"] is not None and sg["left_node"] < len(X):
                A, B = X[sg["left_node"]], P[e[0]]
            else:
                continue
            pts = [A]
            if s < len(sh):
                x, y = A
                for dx, dy in sh[s]:
                    x += dx
                    y += dy
                    pts.append((x, y))
            pts.append(B)
            rank = tiles.seginfo_fields(SI[tiles.seginfo_index(sg)])["rank"]
            yield rank, pts


def render(map_path, si_path, out, box, W=1400):
    m = tiles.MapFile(open(map_path, "rb").read())
    SI = tiles.load_seginfo(open(si_path, "rb").read())
    x0, y0, x1, y1 = box
    k = math.cos(math.radians((y0 + y1) / 2e5))
    H = int(W * (y1 - y0) / ((x1 - x0) * k))
    img = bytearray(b"\xff" * (W * H * 3))
    sx = lambda x: (x - x0) / (x1 - x0) * W
    sy = lambda y: (y1 - y) / (y1 - y0) * H
    lines = sorted(polylines(m, SI, box), key=lambda t: t[0])
    for rank, pts in lines:
        col = COLOURS.get(rank, (0, 0, 0))
        thick = 1 if rank < 3 else 2
        for a, b in zip(pts, pts[1:]):
            ax, ay, bx, by = sx(a[0]), sy(a[1]), sx(b[0]), sy(b[1])
            n = int(max(abs(bx - ax), abs(by - ay))) + 1
            for t in range(n + 1):
                px = int(ax + (bx - ax) * t / n)
                py = int(ay + (by - ay) * t / n)
                for ox in range(thick):
                    for oy in range(thick):
                        qx, qy = px + ox, py + oy
                        if 0 <= qx < W and 0 <= qy < H:
                            j = 3 * (qy * W + qx)
                            img[j:j + 3] = bytes(col)
    raw = b"".join(b"\x00" + bytes(img[3 * W * y:3 * W * (y + 1)]) for y in range(H))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    with open(out, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    return len(lines), W, H


if __name__ == "__main__":
    a = sys.argv
    box = tuple(int(round(float(v) * 1e5)) for v in a[4:8])
    print(render(a[1], a[2], a[3], box, int(a[8]) if len(a) > 8 else 1400))
