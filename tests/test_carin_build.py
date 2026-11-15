"""VY/VZ disc rebuild: clipping, tile building, grids, search trie and ISO relocation.
Synthetic data only.

  python -m unittest discover -s tests -p "test_*.py"
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openlion import carin, carin_build as cb, carin_iso, carin_net as cn  # noqa: E402
from openlion import carin_search as cs, carin_tile as ct  # noqa: E402
import test_carin  # noqa: E402

T = 0xC000          # one minimum detail tile


def P(i, j):
    """A snapped point i, j steps of 64 into the world, off tile edges."""
    return cn.OX + 64 * i, cn.OY + 64 * j


class FakeFactory:
    def grid_cached(self, i):
        return ({0: 0x180000, 1: 0x300000, 2: 0xC00000, 3: 0x600000, 4: 0x300000}[i], [], [])

    def tile_at(self, layer, x, y):
        return None


def tiny_net():
    """A cross of four roads round one junction, with a long road through two tiles."""
    net = cn.Net()
    c = net.add_node(*P(100, 100))
    ends = [net.add_node(*P(100, 300)), net.add_node(*P(300, 100)),
            net.add_node(*P(100, 10)), net.add_node(*P(10, 100))]
    far = net.add_node(*P(30000, 101))      # past the first 0x180000 cell (24,576 steps)
    tags = {"highway": "residential", "name": "Test Street"}
    for e in ends:
        net.add_seg(c, e, [], tags, 0)
    net.add_seg(ends[1], far, [P(20000, 105)], {"highway": "primary", "name": "Main Road", "ref": "A1"}, 1)
    net.finish()
    return net


class Clip(unittest.TestCase):
    def test_cells_on_line(self):
        self.assertEqual(cn.cells_on_line(10, 10, 20, 20, 100), [(0, 0)])
        cells = cn.cells_on_line(10, 10, 250, 10, 100)
        self.assertEqual(cells, [(0, 0), (1, 0), (2, 0)])

    def test_clip_two_tiles(self):
        leaves = [(cn.OX, cn.OY, T), (cn.OX + T, cn.OY, T)]
        loc = cn.Locator(leaves, 0x180000, T)
        pts = [P(100, 100), P(1000, 120)]
        pcs = cn.clip(pts, loc)
        self.assertEqual(len(pcs), 2)
        (r0, a), (r1, b) = pcs
        self.assertEqual(a[-1], b[0])                    # pieces meet at the crossing
        self.assertEqual(a[-1][0], cn.OX + T)            # on the shared edge
        self.assertEqual((a[0], b[-1]), (pts[0], pts[-1]))


class Tiles(unittest.TestCase):
    def build(self):
        net = tiny_net()
        labels = lambda s: ("testville", "test state")
        levels, tiles, node_levels = cb.build_levels(net, labels, FakeFactory())
        for rec in tiles.values():
            rec.prepare(net, labels, node_levels)
        boff = {(k[0], k[1], b): rec.offs[6] + 16 * j for k, rec in tiles.items() for j, b in enumerate(rec.bnd)}
        for key, rec in tiles.items():
            rec.partner_off = (lambda lv: lambda pk, pr: boff[(lv, pr, pk)])(key[0])
        ids = {k: i + 1 for i, k in enumerate(sorted(tiles, key=str))}
        addr = lambda k: (ids.get(k, 0x7F) << 8) | 1
        return net, tiles, addr

    def test_detail_tiles_parse_and_link(self):
        net, tiles, addr = self.build()
        detail = {k: r for k, r in tiles.items() if k[0] == 6}
        self.assertEqual(len(detail), 2)
        out = {}
        for k, rec in detail.items():
            b = rec.logical(net, addr, struct.pack(">IHH", 0, 0, 0))
            t = ct.parse(b, True)
            self.assertEqual(ct.write(t, b[:8]), b)
            out[k] = (b, t)
        roads = sum(t.nroads for _b, t in out.values())
        self.assertEqual(roads, 6)                       # 4 + the long road in two pieces
        for k, (b, t) in out.items():
            self.assertEqual(t.n6, 1)                    # one crossing each side
            x, y, fr, f, a, po, z = struct.unpack_from(">HHHHIHH", t.s6)
            lim = (t.bbox[2] - t.bbox[0]) // 64
            self.assertIn(x, (0, lim))
            other = [ok for ok in out if ok != k][0]
            self.assertEqual(a, addr(other))
            names = [b[p:b.index(b"\0", p)] for p, _f in t.names]
            self.assertIn(b"australia", names)
            self.assertIn(b"testville", names)

    def test_overview_levels(self):
        net, tiles, addr = self.build()
        # an untagged primary road is group 2: level 3 only (levels 1 and 2 hold groups 0 and 0-1)
        self.assertEqual([k for k in tiles if k[0] in (1, 2)], [])
        lv3 = [k for k in tiles if k[0] == 3]
        self.assertEqual(len(lv3), 1)
        b = tiles[lv3[0]].logical(net, addr, struct.pack(">IHH", 0, 3, 0))
        t = ct.parse(b, False)
        self.assertEqual(t.nroads, 1)                    # the main road, merged into one
        self.assertTrue(t.n8 >= 1)                       # lists its level-3 children


class Grid(unittest.TestCase):
    def test_cell_unit(self):
        cell = 0x180000
        tiles = [(6, (cn.OX, cn.OY, T)), (6, (cn.OX + T, cn.OY, T))]
        grids, cells = cb.make_grid(6, cell, tiles)
        self.assertEqual(sum(len(g.keys) for g in grids), 256 * 256)
        cu = [c for c in cells if c.key[2] == (0, 0)][0]
        self.assertEqual(cu.n, 32)
        addr = lambda k: 0x100 * (1 + [t for t in tiles].index(k)) | 1 if k in tiles else 0x5001
        b = cu.logical(addr, struct.pack(">IHH", 0, 9, 0))
        (o0, m), (o1, k), sub = struct.unpack_from(">HH", b, 8), struct.unpack_from(">HH", b, 12), \
            struct.unpack_from(">I", b, 16)[0]
        self.assertEqual((m, k, sub), (1024, 2, T))
        first = struct.unpack_from(">H", b, o0)[0]               # sub-cell (0, 0)
        second = struct.unpack_from(">H", b, o0 + 2 * 32)[0]     # sub-cell (1, 0), x-major
        self.assertEqual(struct.unpack_from(">I", b, first)[0], addr(tiles[0]))
        self.assertEqual(struct.unpack_from(">I", b, second)[0], addr(tiles[1]))


class Search(unittest.TestCase):
    def test_trie_leaves_cover_all(self):
        names = sorted(["a%03d" % i for i in range(600)] + ["b1", "b2", "zz"])
        tr = cs.make_trie(names, 0, len(names))
        spans = list(cs.leaves(tr))
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], len(names))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(spans, spans[1:])))
        self.assertTrue(all(hi - lo <= cs.MAX_LEAF for lo, hi in spans))
        self.assertEqual(tr[0][1], 0)                    # 'a' has 600 names: a sub-trie


class Iso(unittest.TestCase):
    def test_relocate(self):
        db = test_carin.sample_db()
        img = test_carin.iso_with([(b"carindb", db), (b"DAYTON", b"\x00\x2a" + bytes(40))])
        bigger = db + bytes(5 * 2048)
        out = carin_iso.replace_db(img, bigger)
        files = {f.path: out[f.lba * 2048:f.lba * 2048 + f.size] for f in carin.iso_files(out)}
        self.assertEqual(files["/carindb"], bigger)
        self.assertEqual(files["/DAYTON"], b"\x00\x2a" + bytes(40))


if __name__ == "__main__":
    unittest.main()
