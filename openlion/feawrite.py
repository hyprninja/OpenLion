"""Writer for the feature file (auz.fea). Big-endian. See features.py for the layout.

Subfile per level: grid of (cnt_x * cnt_y + 1) x {u32 offset, u32 byteCnt, u32 byteCntZip}
(the extra last entry is {end of this subfile, 0, 0}), then the parcels in grid order,
contiguous. A parcel is stored raw when zlib does not make it smaller.
"""
import collections
import struct
import zlib

from .features import MAPHDR, FeatureFile, wrap16


def decode_parcel(r: bytes):
    """-> list of feature dicts with raw deltas kept (exact round trip)."""
    n = struct.unpack_from(">I", r, 0)[0]
    off, out = 4, []
    for _ in range(n):
        hdr = r[off:off + 8]
        cnt = struct.unpack_from(">I", r, off + 8)[0]
        la, ln = hdr[4], hdr[7]
        att = r[off + 12:off + 12 + la]
        names = r[off + 12 + la:off + 12 + la + ln]
        po = off + 12 + la + ln
        deltas = [struct.unpack_from(">hh", r, po + 4 * i) for i in range(cnt)]
        out.append({"category": hdr[0], "type": hdr[1], "flags": hdr[2], "scaleFlag": hdr[3],
                    "b5": hdr[5], "b6": hdr[6], "attr": att, "names_raw": names, "deltas": deltas})
        off = po + 4 * cnt
    if off != len(r):
        raise ValueError("feature parcel framing")
    return out


def encode_names(names, trans=4) -> bytes:
    return b"".join(bytes([trans, len(t)]) + t for t in (x.encode("latin-1") for x in names))


def deltas_from_parts(parts):
    """parts: lists of (x, y) in parcel units relative to the centre.
    First point absolute; following points as deltas (16-bit wrap); between parts a
    (0,0) separator, then the move delta to the next part's first point."""
    d, last = [], None
    for pi, part in enumerate(parts):
        for i, (x, y) in enumerate(part):
            if last is None:
                d.append((x, y))
            else:
                if i == 0:
                    d.append((0, 0))
                d.append((wrap16(x - last[0]), wrap16(y - last[1])))
            last = (x, y)
    return d


def encode_feature(f) -> bytes:
    names = f.get("names_raw")
    if names is None:
        names = encode_names(f.get("names", []))
    deltas = f.get("deltas")
    if deltas is None:
        deltas = deltas_from_parts(f["parts"])
    b5 = f.get("b5", 1 if names else 0)
    hdr = bytes([f["category"], f["type"], f["flags"], f["scaleFlag"], len(f["attr"]), b5,
                 f.get("b6", 0), len(names)]) + struct.pack(">I", len(deltas))
    return hdr + f["attr"] + names + b"".join(struct.pack(">hh", a, b) for a, b in deltas)


def encode_parcel(features) -> bytes:
    return struct.pack(">I", len(features)) + b"".join(encode_feature(f) for f in features)


def store_parcel(raw: bytes):
    """-> (stored bytes, byteCnt, byteCntZip)."""
    if not raw:
        return b"", 0, 0
    z = zlib.compress(raw, 9)
    if len(z) >= len(raw):
        return raw, len(raw), len(raw)
    return z, len(raw), len(z)


def encode_fea(template_header: bytes, levels, zip_statistic=None) -> bytes:
    """levels: [{"bbox", "parcel_w", "parcel_h", "cnt_x", "cnt_y", "norm_x", "norm_y",
                 "feaType", "scales", "scale_pad", "raw_parcels": [bytes per cell]}]."""
    pos = 0x64 + MAPHDR.size * len(levels)
    subfiles, offsets = [], []
    for lv in levels:
        offsets.append(pos)
        cells = lv["raw_parcels"]
        grid_size = 12 * (len(cells) + 1)
        data_pos = pos + grid_size
        grid, data = bytearray(), bytearray()
        for raw in cells:
            stored, n, nz = store_parcel(raw)
            grid += struct.pack(">III", data_pos + len(data), n, nz)
            data += stored
        end = data_pos + len(data)
        grid += struct.pack(">III", end, 0, 0)
        subfiles.append(bytes(grid) + bytes(data))
        pos = end
    heads = bytearray()
    for lv, off in zip(levels, offsets):
        sc = list(lv["scales"]) + list(lv.get("scale_pad", []))
        sc = (sc + [0] * 8)[:8]
        heads += MAPHDR.pack(*lv["bbox"], lv["parcel_w"], lv["parcel_h"], lv["cnt_x"], lv["cnt_y"],
                             lv["norm_x"], lv["norm_y"], lv["feaType"], len(lv["scales"]), *sc, off)
    if zip_statistic is None:
        zip_statistic = template_header[0x5C:0x64]
    hdr = template_header[:0x58] + struct.pack(">I", len(levels)) + zip_statistic
    return hdr + bytes(heads) + b"".join(subfiles)


def roundtrip(d: bytes):
    """Decode every parcel to features and re-encode the whole file."""
    f = FeatureFile(d)
    st = collections.Counter()
    levels = []
    for k, lv in enumerate(f.levels):
        cells = []
        for j, (o, n, nz) in enumerate(lv["parcels"]):
            raw = f.parcel_raw(k, j % lv["cnt_x"], j // lv["cnt_x"]) if n else b""
            if raw:
                feats = decode_parcel(raw)
                re = encode_parcel(feats)
                st["parcel ok" if re == raw else "parcel DIFF"] += 1
                # independent check of the parts encoder on the same features
                for ft in feats:
                    parts = parts_from_deltas(ft["deltas"])
                    st["parts encoder ok" if deltas_from_parts(parts) == ft["deltas"] else "parts encoder DIFF"] += 1
                cells.append(re)
            else:
                cells.append(b"")
        o = 0x64 + MAPHDR.size * k
        v = MAPHDR.unpack_from(d, o)
        levels.append({"bbox": v[0:4], "parcel_w": v[4], "parcel_h": v[5], "cnt_x": v[6], "cnt_y": v[7],
                       "norm_x": v[8], "norm_y": v[9], "feaType": v[10], "scales": v[12:12 + v[11]],
                       "scale_pad": v[12 + v[11]:20], "raw_parcels": cells})
    return encode_fea(d[:0x64], levels), st


def parts_from_deltas(deltas):
    parts, x, y, sep = [[]], 0, 0, False
    for i, (dx, dy) in enumerate(deltas):
        if i and dx == 0 and dy == 0:
            sep = True
            continue
        x, y = (dx, dy) if i == 0 else (wrap16(x + dx), wrap16(y + dy))
        if sep:
            parts.append([])
            sep = False
        parts[-1].append((x, y))
    return parts
