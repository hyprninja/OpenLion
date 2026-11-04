"""Parser for the decompressed Siemens/Continental road table (auz.rd) and
string table (auz.str). Layouts come from the datamodel in au.mod.

Common header (0x5E bytes):
  0x00 char[16] x5  stamp, copyright, db_release, db_version, comp_version
  0x50 u32 dbID   0x54 u16 fileID   0x56 u32 rec_cnt   0x5A u32 byte_cnt

Road record (29 bytes + nameNum * 5):
  u8 countryID, u32 cityID, u8 roadInfo,
  i32 min_long, i32 min_lat, i32 delta_long, i32 delta_lat  (deg * 1e5)
  u16 maxNumber, u16 minNumber, u8 nameNum, u16 intersectionCount,
  nameNum x { u32 roadNameOffset (into .str), u8 mainSeqNr }

Usage: python -I roads.py <auz.rd> <auz.str> [out.tsv]
"""
import struct
import sys

HDR = 0x5E
REC = struct.Struct("<BIBiiiiHHBH")  # 29 bytes
NAME = struct.Struct("<IB")


def header(d):
    dbid, fid, cnt, size = struct.unpack_from("<IHII", d, 0x50)
    return {"dbID": dbid, "fileID": fid, "rec_cnt": cnt, "byte_cnt": size}


def iter_roads(d):
    h = header(d)
    off = HDR
    for i in range(h["rec_cnt"]):
        start = off
        (cty_ctry, city, info, lon, lat, dlon, dlat,
         maxn, minn, nn, ic) = REC.unpack_from(d, off)
        off += REC.size
        names = []
        for _ in range(nn):
            no, seq = NAME.unpack_from(d, off)
            names.append((no, seq))
            off += NAME.size
        yield {
            "idx": i, "offset": start, "countryID": cty_ctry, "cityID": city,
            "roadInfo": info, "min_long": lon, "min_lat": lat,
            "delta_long": dlon, "delta_lat": dlat, "maxNumber": maxn,
            "minNumber": minn, "intersectionCount": ic, "names": names,
        }
    if off != h["byte_cnt"]:
        raise ValueError(f"ended at {off:#x}, header says {h['byte_cnt']:#x}")


def string_at(s, off):
    """roadNameOffset is relative to the end of the .str header (0x5E)."""
    off += HDR
    end = s.index(b"\0", off)
    return s[off:end]


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
                      f"{r['minNumber']}\t{r['maxNumber']}\t{r['intersectionCount']}\t{nm}\n")
    print(header(rd), "parsed", n, "records; framing consistent with byte_cnt")
