"""Parser for the decompressed Siemens/Continental road table (auz.rd) and
string table (auz.str). Layouts come from the datamodel in au.mod.

Common header (0x5E bytes):
  0x00 char[16] x5  stamp, copyright, db_release, db_version, comp_version
  0x50 u32 dbID   0x54 u16 fileID   0x56 u32 rec_cnt   0x5A u32 byte_cnt

Road record (29 bytes + nameNum * 5; datamodel 5.3 / MY17: 26 bytes + nameNum * 5):
  u8 countryID, u32 cityID, u8 roadInfo,
  i32 min_long, i32 min_lat, i32 delta_long, i32 delta_lat  (deg * 1e5)
  u16 maxNumber, u16 minNumber      (5.2.1)   |   u8 hasNumbers (5.3)
  u8 nameNum, u16 intersectionCount,
  nameNum x { u32 roadNameOffset (into .str), u8 mainSeqNr }

Usage: python -m openlion.roads <auz.rd> <auz.str> [out.tsv]
"""
import struct
import sys

from . import schema

HDR = 0x5E
REC = schema.V521.road_rec  # 29 bytes (5.2.1); the active schema decides
NAME = struct.Struct("<IB")


def header(d):
    dbid, fid, cnt, size = struct.unpack_from("<IHII", d, 0x50)
    return {"dbID": dbid, "fileID": fid, "rec_cnt": cnt, "byte_cnt": size}


def iter_roads(d, sch=None):
    sch = sch or schema.active()
    rec = sch.road_rec
    h = header(d)
    off = HDR
    for i in range(h["rec_cnt"]):
        start = off
        v = rec.unpack_from(d, off)
        if sch.road_numbers == "u16pair":
            (cty_ctry, city, info, lon, lat, dlon, dlat, maxn, minn, nn, ic) = v
            nums = {"maxNumber": maxn, "minNumber": minn}
        else:
            (cty_ctry, city, info, lon, lat, dlon, dlat, has, nn, ic) = v
            nums = {"hasNumbers": has}
        off += rec.size
        names = []
        for _ in range(nn):
            no, seq = NAME.unpack_from(d, off)
            names.append((no, seq))
            off += NAME.size
        yield {
            "idx": i, "offset": start, "countryID": cty_ctry, "cityID": city,
            "roadInfo": info, "min_long": lon, "min_lat": lat,
            "delta_long": dlon, "delta_lat": dlat, **nums,
            "intersectionCount": ic, "names": names,
        }
    if off != h["byte_cnt"]:
        raise ValueError(f"ended at {off:#x}, header says {h['byte_cnt']:#x}")


def string_at(s, off):
    """roadNameOffset is relative to the end of the .str header (0x5E)."""
    off += HDR
    end = s.index(b"\0", off)
    return s[off:end]


# ---- writers ---------------------------------------------------------------

def encode_road(r, sch=None) -> bytes:
    """Missing number fields are filled in from the other version's fields, so a
    record dict from either datamodel encodes in the active one."""
    sch = sch or schema.active()
    if sch.road_numbers == "u16pair":
        nums = (r.get("maxNumber", 0), r.get("minNumber", 0))
    else:
        has = r.get("hasNumbers")
        if has is None:
            has = 1 if (r.get("maxNumber") or r.get("minNumber")) else 0
        nums = (has,)
    out = sch.road_rec.pack(r["countryID"], r["cityID"], r["roadInfo"], r["min_long"], r["min_lat"],
                            r["delta_long"], r["delta_lat"], *nums,
                            len(r["names"]), r["intersectionCount"])
    return out + b"".join(NAME.pack(o, q) for o, q in r["names"])


def encode_roads(template: bytes, records) -> bytes:
    """Road table: common header (rec_cnt = #roads, byte_cnt = total size) + records.
    A record's byte offset minus 0x5E is the roadID that map parcels use."""
    from .common import build_header
    body = b"".join(encode_road(r) for r in records)
    return build_header(template, len(records), HDR + len(body)) + body


def parse_strings(s: bytes):
    """String table body: entries {u8 trans, text, NUL}. trans 0x04 = name,
    0x02 = '[n]' placeholder. Returns [(rel_offset, trans, text_bytes)]."""
    cnt = struct.unpack_from("<I", s, 0x56)[0]
    out, off = [], HDR
    for _ in range(cnt):
        end = s.index(b"\0", off)
        out.append((off - HDR, s[off], s[off + 1:end]))
        off = end + 1
    if off != len(s):
        raise ValueError("string table framing")
    return out


def encode_strings(template: bytes, entries):
    """entries: [(trans, text_bytes)]. Returns (table bytes, [rel_offset per entry])."""
    from .common import build_header
    parts, offs, pos = [], [], 0
    for trans, text in entries:
        rec = bytes([trans]) + text + b"\0"
        offs.append(pos)
        parts.append(rec)
        pos += len(rec)
    body = b"".join(parts)
    return build_header(template, len(entries), HDR + len(body)) + body, offs


if __name__ == "__main__":
    rd = open(sys.argv[1], "rb").read()
    st = open(sys.argv[2], "rb").read()
    out = open(sys.argv[3], "w", encoding="utf-8") if len(sys.argv) > 3 else None
    n = 0
    for r in iter_roads(rd):
        n += 1
        if out:
            nm = " | ".join(f"{string_at(st, o)[1:].decode('latin-1')}#{q}" for o, q in r["names"])
            out.write(f"{r['idx']}\t{r['countryID']}\t{r['cityID']}\t{r['roadInfo']:#04x}\t"
                      f"{r['min_long']/1e5:.5f}\t{r['min_lat']/1e5:.5f}\t"
                      f"{(r['min_long']+r['delta_long'])/1e5:.5f}\t{(r['min_lat']+r['delta_lat'])/1e5:.5f}\t"
                      f"{r.get('minNumber', '')}\t{r.get('maxNumber', r.get('hasNumbers'))}\t"
                      f"{r['intersectionCount']}\t{nm}\n")
    print(header(rd), "parsed", n, "records; framing consistent with byte_cnt")
