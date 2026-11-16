"""Probe disc: can the unit read data stored past the licensing block?

  python tools/carin_probe_far.py NAV_DB.iso OUT.iso

Factory disc with one change: the detail-road grid (root entry 0, eleven 0x08 units the
unit must read to draw any detail map) is copied to just past the licensing block at the
end of carindb, and the root points at the copy. Every other byte of carindb is factory;
the old grid stays in place, unreferenced. carin_iso writes the grown file with TPD/
straight after it and its directory updated, as on disc 6 (which worked).

If the detail map draws, data past the licensing block is readable and the rebuild has
as much room as a CD. If the disc is rejected, the map must fit before it.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import carin, carin_iso  # noqa: E402


def main(src, out):
    img = open(src, "rb").read()
    _f, db = carin.read_db(img)
    by = {u.off: u for u in carin.units(db)}
    root_u = by[0xC00]
    root = bytearray(carin.decode(db, root_u))
    a0, = struct.unpack_from(">I", root, 0x10)
    off = (a0 >> 8) << 8
    grid = []
    while off in by and by[off].table == 0x08 and len(grid) < 11:
        grid.append(by[off])
        off += by[off].size
    assert len(grid) == 11, "expected the detail grid's 11 units"
    new = bytearray(db)
    pos = len(db)
    first = None
    for u in grid:
        raw = bytearray(db[u.off:u.off + u.size])
        # the unit header carries its own address: offset >> 8 | page count
        struct.pack_into(">I", raw, 0, ((pos >> 8) << 8) | (u.pages & 0xFF))
        new += raw
        first = first if first is not None else ((pos >> 8) << 8) | u.pages
        pos += u.size
    struct.pack_into(">I", root, 0x10, first)
    new[0xC00:0xE00] = carin.encode_in_place(root_u, bytes(root))
    new = bytes(new)
    carin.units(new)                     # chain must still walk end to end
    open(out, "wb").write(carin_iso.replace_db(img, new))
    print(f"grid moved {grid[0].off:#x} -> {len(db):#x} (past the licensing block at "
          f"{0x5AF8000:#x}); carindb {len(db):,} -> {len(new):,} bytes; wrote {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
