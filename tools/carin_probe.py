"""One-change probe discs: the factory disc with exactly one thing altered.

  python tools/carin_probe.py MODE NAV_DB.iso OUT.iso

  far         the detail-road grid (root entry 0, eleven 0x08 units) copied to just past
              the licensing block, root pointed at the copy (disc 11)
  zgrid       the detail-road grid recompressed with zlib in place (the factory stores
              every 0x08 unit)
  gridinside  the detail-road grid copied inside the original file, over the start of the
              0x04 house-number block, root pointed at the copy; the house numbers of the
              tiles whose 0x04 units are overwritten are lost (listed when built)
  ztiles      every stored street-level tile (502 units, table 0x00) recompressed with zlib
              in place (the factory zlib-compresses 2 of its 6,454 detail tiles)

Every other byte of carindb is factory; `far` grows the file, the rest keep its size.
"""
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import carin, carin_iso  # noqa: E402

PAGE = carin.PAGE


def zlib_in_place(db, u):
    """A stored unit re-encoded as zlib, same offset and page count."""
    raw = db[u.off:u.off + u.size]
    assert u.codec == carin.STORED
    body = zlib.compress(raw[8:], 9)
    addr, table = struct.unpack_from(">IH", raw)
    out = struct.pack(">IHH", addr, table, (carin.ZLIB << 8) | u.pages) + body
    assert len(out) <= u.size, f"unit at {u.off:#x} doesn't shrink"
    return out.ljust(u.size, b"\0")


def detail_grid(db, by, root):
    a0, = struct.unpack_from(">I", root, 0x10)
    off = (a0 >> 8) << 8
    grid = []
    while off in by and by[off].table == 0x08 and len(grid) < 11:
        grid.append(by[off])
        off += by[off].size
    assert len(grid) == 11, "expected the detail grid's 11 units"
    return grid


def copy_units(db, grid, pos):
    """The grid units re-addressed for a new position; returns (bytes, first address)."""
    out = bytearray()
    first = None
    for u in grid:
        raw = bytearray(db[u.off:u.off + u.size])
        a = ((pos + len(out)) >> 8) << 8 | (u.pages & 0xFF)
        struct.pack_into(">I", raw, 0, a)
        first = first if first is not None else a
        out += raw
    return bytes(out), first


def main(mode, src, out):
    img = open(src, "rb").read()
    _f, db = carin.read_db(img)
    units = carin.units(db)
    by = {u.off: u for u in units}
    root_u = by[0xC00]
    root = bytearray(carin.decode(db, root_u))
    new = bytearray(db)
    note = ""
    if mode == "far":
        grid = detail_grid(db, by, root)
        blob, first = copy_units(db, grid, len(db))
        new += blob
        struct.pack_into(">I", root, 0x10, first)
        note = f"grid {grid[0].off:#x} -> {len(db):#x} (past the licensing block)"
    elif mode == "zgrid":
        for u in detail_grid(db, by, root):
            new[u.off:u.off + u.size] = zlib_in_place(db, u)
        note = "detail grid recompressed in place"
    elif mode == "gridinside":
        grid = detail_grid(db, by, root)
        hn = sorted((u for u in units if u.table == 0x04), key=lambda u: u.off)
        start = hn[0].off
        blob, first = copy_units(db, grid, start)
        end = start + len(blob)
        # resume the chain at the first 0x04 unit boundary after the copy, filling the gap
        nxt = next(u.off for u in hn if u.off >= end)
        lost = [u for u in hn if u.off < nxt]
        new[start:end] = blob
        prev, pos = start + len(blob) - grid[-1].size, end
        while pos < nxt:
            k = min((nxt - pos) // PAGE, 0xFF)
            new[pos:pos + k * PAGE] = struct.pack(">IHH", ((prev >> 8) << 8) | k, 0x05, 0).ljust(k * PAGE, b"\0")
            prev, pos = pos, pos + k * PAGE
        struct.pack_into(">I", root, 0x10, first)
        tiles = []
        for u in lost:
            ta = struct.unpack_from(">I", db, u.off + 12)[0]
            tu = by.get((ta >> 8) << 8)
            if tu:
                x0, y0, x1, y1 = struct.unpack_from(">iiii", db, tu.off + 0x44)
                tiles.append(carin.to_lonlat((x0 + x1) // 2, (y0 + y1) // 2))
        lo = min(t[0] for t in tiles), max(t[0] for t in tiles)
        la = min(t[1] for t in tiles), max(t[1] for t in tiles)
        note = (f"grid {grid[0].off:#x} -> {start:#x} (inside the file); house numbers lost for "
                f"{len(lost)} tiles between {lo[0]:.1f}-{lo[1]:.1f}E, {la[0]:.1f}-{la[1]:.1f}S")
    elif mode == "ztiles":
        n = 0
        for u in units:
            if u.table == 0x00 and u.codec == carin.STORED:
                new[u.off:u.off + u.size] = zlib_in_place(db, u)
                n += 1
        note = f"{n} stored detail tiles recompressed in place"
    else:
        raise SystemExit(__doc__)
    new[0xC00:0xE00] = carin.encode_in_place(root_u, bytes(root))
    new = bytes(new)
    carin.units(new)                     # the chain must still walk end to end
    open(out, "wb").write(carin_iso.replace_db(img, new))
    print(f"{mode}: {note}; carindb {len(db):,} -> {len(new):,} bytes; wrote {out}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(*sys.argv[1:4])
