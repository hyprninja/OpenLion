"""Compare mg1-mg4 derived from the mp0 graph with the factory layers.
Usage: python tests/derive_layers.py <path to navigation/db_aunz/db>
"""
import sys,struct,collections,time
import os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import graph, layers, flat
DB=(sys.argv[1] if len(sys.argv)>1 else '../navigation/db_aunz/db').rstrip('/')+'/'
si=flat.unpack(open(DB+'auz.si','rb').read()); SI=struct.unpack_from(f'<{struct.unpack_from("<I",si,0x56)[0]}I',si,0x5e)
t=time.time(); g0=graph.extract(open(DB+'auz.mp0','rb').read()); print('mp0 graph',g0.n_nodes,g0.n_segs,f'{time.time()-t:.0f}s',flush=True)
for k in (4,3,2,1):
    gf=graph.extract(open(DB+f'auz.mg{k}','rb').read()); gd=layers.derive(g0,k,SI)
    a,b=graph.summary(gf),graph.summary(gd)
    out={}
    for key in a:
        x,y=a[key],b[key]; out[key]=(sum((x-y).values()),sum((y-x).values()),sum(x.values()))
    print(f'mg{k}: (only factory, only derived, factory total) {out}',flush=True)
