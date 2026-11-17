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


class Tiling(unittest.TestCase):
    def test_factory_split_shapes(self):
        # a square splits into two wide halves, a wide half into two squares
        sq = (cn.OX, cn.OY, 0x30000, 0x30000)
        a, b = cn.halves(sq)
        self.assertEqual(a, (cn.OX, cn.OY, 0x30000, 0x18000))
        self.assertEqual(b, (cn.OX, cn.OY + 0x18000, 0x30000, 0x18000))
        self.assertEqual(cn.halves(a), ((cn.OX, cn.OY, 0x18000, 0x18000),
                                        (cn.OX + 0x18000, cn.OY, 0x18000, 0x18000)))
        # the smallest street-level tile is 0xC000 x 0x6000
        self.assertTrue(cn.can_split((cn.OX, cn.OY, 0xC000, 0xC000), 0xC000))
        self.assertFalse(cn.can_split((cn.OX, cn.OY, 0xC000, 0x6000), 0xC000))

    def test_locator_finds_rectangles(self):
        top = (cn.OX, cn.OY, 0x180000, 0x180000)
        lower, upper = cn.halves(top)
        left, right = cn.halves(lower)
        loc = cn.Locator([left, right, upper], 0x180000, 0xC000)
        self.assertEqual(loc.find(cn.OX + 10, cn.OY + 10), left)
        self.assertEqual(loc.find(cn.OX + 0x100000, cn.OY + 10), right)
        self.assertEqual(loc.find(cn.OX + 10, cn.OY + 0x100000), upper)


class Clip(unittest.TestCase):
    def test_cells_on_line(self):
        self.assertEqual(cn.cells_on_line(10, 10, 20, 20, 100), [(0, 0)])
        cells = cn.cells_on_line(10, 10, 250, 10, 100)
        self.assertEqual(cells, [(0, 0), (1, 0), (2, 0)])

    def test_clip_two_tiles(self):
        leaves = [(cn.OX, cn.OY, T, T), (cn.OX + T, cn.OY, T, T)]
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
        # an untagged primary road is group 2: level 3 only (levels 1 and 2 hold groups 0 and 0-1).
        # A coarse tile with no roads never occurs on the factory disc, so levels 1 and 2 get
        # none here and the level-3 tile keeps parent 0; every detail tile has a parent
        self.assertEqual([k for k in tiles if k[0] in (1, 2)], [])
        for key, rec in tiles.items():
            if key[0] == 6:
                self.assertIsNotNone(rec.parent, key)
        lv3 = [k for k in tiles if k[0] == 3]
        self.assertEqual(len(lv3), 1)
        b = tiles[lv3[0]].logical(net, addr, struct.pack(">IHH", 0, 3, 0))
        t = ct.parse(b, False)
        self.assertEqual(t.nroads, 1)                    # the main road, merged into one
        self.assertTrue(t.n8 >= 1)                       # lists its detail children
        # 0x5C: the smallest detail tile under it, not its own size
        smallest = min(k[1][2] for k in tiles if k[0] == 6)
        self.assertEqual(struct.unpack_from(">I", b, 0x5C)[0], smallest)

    def test_class_codes_are_factory_codes(self):
        net = tiny_net()
        for c in net.cls:
            self.assertIn(c & 0xFF, cn.FACTORY_CLASSES[c >> 8])
        self.assertEqual(cn.factory_class(0x910C), 0x8B0C)   # two-way stays two-way
        self.assertEqual(cn.factory_class(0x8B01), 0x8B11)   # slip roads are one-way


class Chains(unittest.TestCase):
    def test_no_road_dropped(self):
        # a ring of 70 same-class roads: the 64-road cap and the ring rule both stop the
        # forward walk early; every road must still be in exactly one merged road
        net = cn.Net()
        pts = [net.add_node(*P(1000 + 50 * i, 1000 + (i % 2) * 3)) for i in range(70)]
        tags = {"highway": "residential", "name": "Ring Road"}
        for i in range(70):
            a, b = pts[i], pts[(i + 1) % 70]
            if net.x[a] > net.x[b]:
                a, b = b, a
            net.add_seg(a, b, [], tags, 0)
        net.finish()
        segs = list(range(net.n_segs))
        for start in (segs, list(reversed(segs)), segs[35:] + segs[:35]):
            ch = cb.merge_chains(net, start)
            got = [m for v in ch.values() for m in v]
            self.assertEqual(sorted(got), segs)
            for v in ch.values():
                self.assertNotEqual(net.a[v[0]], net.b[v[-1]])   # no self-loops


class Grid(unittest.TestCase):
    def test_cell_unit(self):
        cell = 0x180000
        tiles = [(6, (cn.OX, cn.OY, T, T)), (6, (cn.OX + T, cn.OY, T, T))]
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
