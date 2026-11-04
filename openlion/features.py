"""Parser for the Siemens/Continental feature file (auz.fea). BIG-ENDIAN.
Layout from au.mod table "feature" (schema offsets 0x558C-0x5E7A).

  FeatureFileHeader (100 B): 5 x char[16] stamps, u32 dbID, u32 fileID,
                             u32 fileCnt, 8 B Zip_Statistic
  MapHeader x fileCnt (84 B): i32 min_long, min_lat, max_long, max_lat,
        i32 parcel_width, parcel_height, parcel_cnt_x, parcel_cnt_y,
        parcel_norm_x, parcel_norm_y, feaType, u32 scaleCnt,
        u32 scales[8] (first scaleCnt valid), u32 offset (absolute)
  SubFile at offset: parcel_cnt_x * parcel_cnt_y x ParcelHeader (12 B):
        u32 offset (absolute), u32 byteCnt, u32 byteCntZip
        byteCntZip == byteCnt -> stored raw, else zlib
"""
import struct
import zlib

MAPHDR = struct.Struct(">4i6iiI8II")


def wrap16(v):
    return (v + 0x8000) % 0x10000 - 0x8000


class FeatureFile:
    def __init__(self, data: bytes):
        self.d = data
        self.dbID, self.fileID, self.fileCnt = struct.unpack_from(">III", data, 0x50)
        self.levels = []
        for k in range(self.fileCnt):
            v = MAPHDR.unpack_from(data, 0x64 + MAPHDR.size * k)
            lv = {"bbox": v[0:4], "parcel_w": v[4], "parcel_h": v[5], "cnt_x": v[6],
                  "cnt_y": v[7], "norm_x": v[8], "norm_y": v[9], "feaType": v[10],
                  "scales": list(v[12:12 + v[11]]), "offset": v[20]}
            n = lv["cnt_x"] * lv["cnt_y"]
            lv["parcels"] = [struct.unpack_from(">III", data, lv["offset"] + 12 * j) for j in range(n)]
            self.levels.append(lv)

    def parcel_raw(self, level, ix, iy):
        """Grid is row-major from the south-west: index = iy * cnt_x + ix,
        ix = (lon - min_long) // parcel_w, iy = (lat - min_lat) // parcel_h."""
        lv = self.levels[level]
        off, n, nz = lv["parcels"][iy * lv["cnt_x"] + ix]
        if n == 0:
            return b""
        blob = self.d[off:off + nz]
        out = blob if nz == n else zlib.decompress(blob)
        if len(out) != n:
            raise ValueError("size mismatch")
        return out

    def parcel_origin(self, level, ix, iy):
        """Parcel centre, degrees * 1e5."""
        lv = self.levels[level]
        x0, y0 = lv["bbox"][0], lv["bbox"][1]
        return (x0 + ix * lv["parcel_w"] + lv["parcel_w"] // 2,
                y0 + iy * lv["parcel_h"] + lv["parcel_h"] // 2)

    def features(self, level, ix, iy, absolute=True):
        """Parcel: u32 feaCnt, then per feature
          u8 category (1 line, 2 poly, 4 point, 8 road, 16 label), u8 type,
          u8 flags, u8 scaleFlag (bit per scale), u8 lenAtt, u8 nameCnt, u8 ?,
          u8 lenNames, u32 feaPointCnt,
          attribute[lenAtt], names[lenNames] ({u8 trans, u8 len, text} each),
          feaPointCnt x (i16, i16): first relative to parcel centre, rest cumulative.
        Coordinate unit = parcel_w / norm_x (= 1e-5 deg on level 0)."""
        r = self.parcel_raw(level, ix, iy)
        if not r:
            return []
        lv = self.levels[level]
        cx, cy = self.parcel_origin(level, ix, iy)
        sx, sy = lv["parcel_w"] / lv["norm_x"], lv["parcel_h"] / lv["norm_y"]
        n = struct.unpack_from(">I", r, 0)[0]
        off, out = 4, []
        for _ in range(n):
            cat, typ, fl, sf, la, b5, b6, ln = r[off:off + 8]
            cnt = struct.unpack_from(">I", r, off + 8)[0]
            att = r[off + 12:off + 12 + la]
            nb = r[off + 12 + la:off + 12 + la + ln]
            names, q = [], 0
            while q + 2 <= len(nb):
                names.append(nb[q + 2:q + 2 + nb[q + 1]].decode("latin-1"))
                q += 2 + nb[q + 1]
            po = off + 12 + la + ln
            pts, parts, x, y = [], [[]], 0, 0
            sep = False
            for i in range(cnt):
                dx, dy = struct.unpack_from(">hh", r, po + 4 * i)
                if i and dx == 0 and dy == 0:
                    sep = True          # (0,0) delta = start a new part; next delta is a move
                    continue
                # cumulative with 16-bit wrap-around (steps > 32767 wrap)
                x, y = (dx, dy) if i == 0 else (wrap16(x + dx), wrap16(y + dy))
                p = (cx + x * sx, cy + y * sy) if absolute else (x, y)
                if sep:
                    parts.append([])
                    sep = False
                pts.append(p)
                parts[-1].append(p)
            out.append({"category": cat, "type": typ, "flags": fl, "scaleFlag": sf,
                        "b5": b5, "b6": b6, "attr": att, "names": names,
                        "points": pts, "parts": parts})
            off = po + 4 * cnt
        if off != len(r):
            raise ValueError(f"framing: ended {off} of {len(r)}")
        return out
