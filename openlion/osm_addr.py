"""OSM addresses -> house-number ranges on street segments (report 24).

read():   every node / building with addr:housenumber and addr:street, as compact
          arrays (street name normalised like the road names).
assign(): each address goes to the nearest segment of a street with the same name
          (within MAX_DIST), on the side of the road it lies on. Per segment side the
          numbers become one range: base at the segment's left end (seg_a), base +
          delta at its right end, with the side's parity (even / odd / mixed).

The factory card stores the same thing: one short_addr (or long_addr) link per
numbered side instead of the plain abs_addr link, and the street's min / max number
on its road record.
"""
import array
import collections
import math
import re

from . import mapwrite

MAX_DIST = 250          # deg * 1e5 north-south (~280 m); x is scaled by cos(lat)
CELL = 1024             # grid cell for the segment index (~1 km)
MAX_NUMBER = 65535       # the road record's u16 min / max
PARITY_SHARE = 0.8      # this share of one parity -> an even / odd side; else mixed


class Addresses:
    """Address points: x, y (deg * 1e5), street id into names, number."""

    def __init__(self):
        self.names, self._ids = [], {}
        self.x, self.y = array.array("i"), array.array("i")
        self.street, self.num = array.array("I"), array.array("I")

    def add(self, street, x, y, numbers):
        sid = self._ids.get(street)
        if sid is None:
            sid = self._ids[street] = len(self.names)
            self.names.append(street)
        for n in numbers:
            self.x.append(x)
            self.y.append(y)
            self.street.append(sid)
            self.num.append(n)

    def __len__(self):
        return len(self.num)

    def __getstate__(self):
        return (self.names, self.x, self.y, self.street, self.num)

    def __setstate__(self, s):
        self.names, self.x, self.y, self.street, self.num = s
        self._ids = {n: i for i, n in enumerate(self.names)}

    def extend(self, other):
        remap = []
        for n in other.names:
            sid = self._ids.get(n)
            if sid is None:
                sid = self._ids[n] = len(self.names)
                self.names.append(n)
            remap.append(sid)
        self.x.extend(other.x)
        self.y.extend(other.y)
        self.street.extend(remap[s] for s in other.street)
        self.num.extend(other.num)


_RANGE = re.compile(r"^(\d+)\s*[A-Za-z]?\s*[-–]\s*(\d+)\s*[A-Za-z]?$")
_LEAD = re.compile(r"^(\d+)")


def numbers(text):
    """House numbers in an addr:housenumber value: '12' '12A' '3/45' (unit 3 of 45)
    '1-5' '12;14' -> [12] [12] [45] [1, 5] [12, 14]. Others give []."""
    out = []
    for part in re.split(r"[;,]", text or ""):
        p = part.strip()
        if "/" in p:
            p = p.rsplit("/", 1)[1].strip()
        m = _RANGE.match(p)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out += [a, b] if a < b and b - a <= 200 else [a]
            continue
        m = _LEAD.match(p)
        if m:
            out.append(int(m.group(1)))
    return [n for n in out if 0 < n <= MAX_NUMBER]


def read(pbf_path, bbox=None, node_index="flex_mem"):
    """Addresses with a street: nodes, and buildings / areas at their centre."""
    import osmium
    import osmium.filter
    from .osm_names import normalise
    out = Addresses()
    key = "addr:housenumber"
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION | osmium.osm.AREA)
          .with_locations(node_index)
          .with_areas(osmium.filter.KeyFilter(key))
          .with_filter(osmium.filter.EntityFilter(osmium.osm.NODE | osmium.osm.AREA))
          .with_filter(osmium.filter.KeyFilter(key)))
    for o in fp:
        street = normalise(o.tags.get("addr:street"))
        nums = numbers(o.tags.get(key))
        if not street or not nums:
            continue
        if o.is_node():
            if not o.location.valid():
                continue
            x, y = round(o.location.lon * 1e5), round(o.location.lat * 1e5)
        else:
            pts = [(n.lon, n.lat) for ring in o.outer_rings() for n in ring]
            if not pts:
                continue
            x = round(sum(p[0] for p in pts) / len(pts) * 1e5)
            y = round(sum(p[1] for p in pts) / len(pts) * 1e5)
        if bbox and not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
            continue
        out.add(street, x, y, nums)
    return out


def merge(parts):
    out = Addresses()
    for a in parts:
        out.extend(a)
    return out


def _polyline(g, s):
    pts = [g.node_xy[g.seg_a[s]]]
    if g.seg_shape[s]:
        pts += list(g.seg_shape[s])
    pts.append(g.node_xy[g.seg_b[s]])
    return pts


def _project(pts, x, y, kx):
    """(distance^2, t along the line 0..1, side) of point (x, y); x scaled by kx."""
    best = None
    lens, total = [], 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        L = math.hypot((x2 - x1) * kx, y2 - y1)
        lens.append(L)
        total += L
    run = 0.0
    for i, ((x1, y1), (x2, y2)) in enumerate(zip(pts, pts[1:])):
        dx, dy = (x2 - x1) * kx, y2 - y1
        px, py = (x - x1) * kx, y - y1
        L2 = dx * dx + dy * dy
        u = 0.0 if L2 == 0 else max(0.0, min(1.0, (px * dx + py * dy) / L2))
        ex, ey = px - u * dx, py - u * dy
        d2 = ex * ex + ey * ey
        if best is None or d2 < best[0]:
            cross = dx * py - dy * px
            best = (d2, (run + u * lens[i]) / total if total else 0.5, "right" if cross < 0 else "left")
        run += lens[i]
    return best


def _trim(obs, max_span):
    """The largest group of numbers spanning at most max_span (a stray 132647 among 3-41)."""
    obs = sorted(obs, key=lambda o: o[1])
    best, j = (0, 0), 0
    for i in range(len(obs)):
        while obs[i][1] - obs[j][1] > max_span:
            j += 1
        if i + 1 - j > best[1] - best[0]:
            best = (j, i + 1)
    return obs[best[0]:best[1]]


def _fit(obs, max_span=None):
    """[(t, n)] on one side -> (parity, base at t=0, number at t=1).
    max_span: the widest range the segment can carry; numbers beyond it are dropped."""
    if max_span is not None and max(n for _, n in obs) - min(n for _, n in obs) > max_span:
        obs = _trim(obs, max_span)
    odd = sum(n & 1 for _, n in obs)
    if odd >= PARITY_SHARE * len(obs):
        parity, obs = "odd", [(t, n) for t, n in obs if n & 1]
    elif len(obs) - odd >= PARITY_SHARE * len(obs):
        parity, obs = "even", [(t, n) for t, n in obs if not n & 1]
    else:
        parity = "mixed"
    lo, hi = min(n for _, n in obs), max(n for _, n in obs)
    a, b = lo, hi
    if len(obs) >= 2:
        tm = sum(t for t, _ in obs) / len(obs)
        nm = sum(n for _, n in obs) / len(obs)
        stt = sum((t - tm) ** 2 for t, _ in obs)
        stn = sum((t - tm) * (n - nm) for t, n in obs)
        if stt > 1e-9:
            slope = stn / stt
            # reach the segment's ends, but never more than the span seen on it
            span = hi - lo
            f0, f1 = nm - slope * tm, nm + slope * (1 - tm)
            if slope < 0:
                a, b = hi, lo
                a = max(a, min(round(f0), hi + span))
                b = min(b, max(round(f1), lo - span))
            else:
                a = min(a, max(round(f0), lo - span))
                b = max(b, min(round(f1), hi + span))
    floor = 2 if parity == "even" else 1
    a, b = max(a, floor), max(b, floor)
    if parity != "mixed":                      # keep the side's parity, rounding outwards
        want = 1 if parity == "odd" else 0
        if a & 1 != want:
            a += 1 if a > b else -1
        if b & 1 != want:
            b += 1 if b > a else -1
        a, b = max(a, floor), max(b, floor)
    top = MAX_NUMBER - (parity == "even")       # 65535 is odd
    return parity, min(a, top), min(b, top)


def _stray(addr, idx, k=6, radius=150, cell=150):
    """Indices in idx (one street's addresses) whose number is far off its k nearest
    neighbours within radius: more than 3 x their spread + 6 from their median. Stray
    tags like a lone '7' among 23-30 would otherwise stretch a segment's range."""
    X, Y, N = addr.x, addr.y, addr.num
    grid = collections.defaultdict(list)
    for i in idx:
        grid[(X[i] // cell, Y[i] // cell)].append(i)
    r2 = radius * radius
    bad = set()
    for i in idx:
        x, y = X[i], Y[i]
        cx, cy = x // cell, y // cell
        near = []
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for j in grid.get((gx, gy), ()):
                    if j != i:
                        d2 = (X[j] - x) ** 2 + (Y[j] - y) ** 2
                        if d2 <= r2:
                            near.append((d2, N[j]))
        if len(near) < 3:
            continue
        near.sort()
        ns = sorted(n for _, n in near[:k])
        m = ns[len(ns) // 2]
        spread = sorted(abs(n - m) for n in ns)[len(ns) // 2]
        if abs(N[i] - m) > 3 * spread + 6:
            bad.add(i)
    return bad


def _by_street(addr):
    """{street id: [address indices]}"""
    out = collections.defaultdict(list)
    for i, s in enumerate(addr.street):
        out[s].append(i)
    return out


def outliers(addr):
    """bytearray: 1 for each stray number (see _stray)."""
    bad = bytearray(len(addr))
    for idx in _by_street(addr).values():
        for i in _stray(addr, idx):
            bad[i] = 1
    return bad


def assign(g, groups, addr, stats=None):
    """groups: {(city, name): [segments]} (osm_names). ->
    ranges {seg: [(code, base, delta)]}, numbers {key: (min, max)}.
    Works one street name at a time, so only that street's grids are in memory."""
    stats = stats if stats is not None else collections.Counter()
    if not addr or not len(addr):
        return {}, {}
    segs_of_name = collections.defaultdict(list)     # street id -> [(key, segments)]
    for key, segs in groups.items():
        n = addr._ids.get(key[1]) if key[1] else None
        if n is not None:
            segs_of_name[n].append((key, segs))
    ranges, numbers_of = {}, {}
    lim = MAX_DIST * MAX_DIST
    X, Y, N = addr.x, addr.y, addr.num
    for n, idx in sorted(_by_street(addr).items()):
        keyed = segs_of_name.get(n)
        if not keyed:
            stats["addresses: no street of that name nearby"] += len(idx)
            continue
        stray = _stray(addr, idx)
        stats["addresses: off from their neighbours"] += len(stray)
        # this street's segments in a grid, each with its polyline
        grid, lines, key_of_seg = collections.defaultdict(list), {}, {}
        for key, segs in keyed:
            for s in segs:
                key_of_seg[s] = key
                pts = _polyline(g, s)
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                lines[s] = (pts, min(xs), min(ys), max(xs), max(ys))
                for cx in range((min(xs) - MAX_DIST * 2) // CELL, (max(xs) + MAX_DIST * 2) // CELL + 1):
                    for cy in range((min(ys) - MAX_DIST) // CELL, (max(ys) + MAX_DIST) // CELL + 1):
                        grid[(cx, cy)].append(s)
        obs = collections.defaultdict(list)
        for i in idx:
            if i in stray:
                continue
            if N[i] > MAX_NUMBER:       # older caches kept numbers the road record can't hold
                stats["addresses: number too big"] += 1
                continue
            x, y = X[i], Y[i]
            cand = grid.get((x // CELL, y // CELL))
            if not cand:
                stats["addresses: no street of that name nearby"] += 1
                continue
            kx = math.cos(math.radians(y / 1e5))
            best, best_d2 = None, lim
            for s in cand:
                pts, x0, y0, x1, y1 = lines[s]
                bx = (x0 - x if x < x0 else x - x1 if x > x1 else 0) * kx
                by = y0 - y if y < y0 else y - y1 if y > y1 else 0
                if bx * bx + by * by > best_d2:      # its box is already farther than the best
                    continue
                p = _project(pts, x, y, kx)
                if p[0] < best_d2 or (best is None and p[0] == best_d2):   # first of equals wins
                    best, best_d2 = (p, s), p[0]
            if best is None:
                stats["addresses: street too far away"] += 1
                continue
            (_, t, side), s = best
            obs[(s, side)].append((t, N[i]))
            stats["addresses: matched"] += 1
        for (s, side), o in sorted(obs.items()):
            span = max(60, round(g.seg_len[s] * 0.3048 * 2))     # ~2 numbers per metre, at least 60
            if max(n for _, n in o) - min(n for _, n in o) > span:
                stats["segment sides: trimmed to the segment's length"] += 1
            parity, a, b = _fit(o, span)
            ranges.setdefault(s, []).append((mapwrite.HN_CODE[(side, parity)], a, b - a))
            stats[f"segment sides: {parity}"] += 1
            k = key_of_seg[s]
            lo, hi = numbers_of.get(k, (a, a))
            numbers_of[k] = (min(lo, a, b), max(hi, a, b))
    stats["numbered segments"] = len(ranges)
    stats["numbered streets"] = len(numbers_of)
    return ranges, numbers_of


def links(ranges, road_id):
    """Link tuples for one segment's ranges on road road_id."""
    out = []
    for code, base, delta in ranges:
        try:
            kind, rng = mapwrite.encode_range(code, base, delta)
        except ValueError:          # can't be stored: leave this side without numbers
            continue
        out.append((kind, 0, road_id, rng))
    return tuple(out)
