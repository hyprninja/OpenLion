"""Re-prove doc 33's edge-record finding: every codec 0x0E street tile holds exactly n6
records, and each partner points back.   python check_edge_records.py FACTORY.iso"""
import sys
from common import Disc, carin

d = Disc(sys.argv[1])
codec = [u.off for u in d.units if u.table == 0 and u.codec == carin.CODEC_E]
H = {o: d.edge_records(o) for o in codec}
exact = sum(1 for o in codec if len(H[o]) == d.sections(o)[6][1])
pairs = back = 0
for t, hits in H.items():
    o6 = d.sections(t)[6][0]
    for k, (_i, a, po) in enumerate(hits):
        n = (a >> 8) << 8
        if n in H:
            j = (po - d.sections(n)[6][0]) // 16
            pairs += 1
            b = H[n][j] if j < len(H[n]) else None
            back += bool(b) and (b[1] >> 8) << 8 == t and b[2] == o6 + 16 * k
print(f"{exact}/{len(codec)} tiles with exactly n6 records; {back}/{pairs} partner records point back")
