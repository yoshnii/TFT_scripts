"""0.40µL sample aliquots: 1.60→0.80→0.40, no unequal peeling or padding."""
import geom
import ops
import rigid as R
import waste_grid as W
from native_packets import partition


def sample_aliquots(S, lanes, blobs):
    lanes=list(lanes)
    assert len(lanes)==16 and all(S.get(ln.name).vol==1.6 for ln in lanes)
    ops.settle_loaded(S)
    halves=partition(S,[S.get(ln.name) for ln in lanes],2,'x',4)
    # Reserve the four-cell opening for the next pair of cuts before cutting.
    ops.move_rects(S,{ds[0].name:ln.X0+1 for ln,ds in zip(lanes,halves)})
    quarters=partition(S,[d for ds in halves for d in ds],2,'x',4)
    by_lane={ln.id:[*quarters[2*i],*quarters[2*i+1]] for i,ln in enumerate(lanes)}
    for ln in lanes:
        keep=by_lane[ln.id][0]
        S.remove(keep.name);keep.name=ln.name;S.add(keep)
    # Forty-eight native 2×4 waste packets; two balanced 12×16 outlet pools.
    # This keeps the already approved F-hole aspiration tail, without reshaping packets.
    specs={(1,k):dict(port=k,W=12,rows=[([2]*6,4)]*4,native=True,
                        strict_staging=True) for k in (3,1)}
    assignments=[((1,k),i//6,i%6) for k in (3,1) for i in range(24)]
    gx=geom.gap_x(geom.waste_gap(lanes[0].c));jobs={}
    for batch in range(3):
        pieces=[(ln,by_lane[ln.id][3-batch]) for ln in lanes]
        plan=dict(stop=1,flush=batch==2,ports=[3,1],assign=assignments[16*batch:16*(batch+1)],
                  specs=specs,minimum_ports=2,packed=True)
        tiles=W._send_pipeline(S,pieces,gx,blobs,plan,jobs)
        # Use the existing conservative outlet/lookahead queue at both ports.
        # The last row must be staged before the preceding row docks near Y172;
        # otherwise passing packets would touch the already collected pool.
        for job in jobs.values():job['under_exit']=True
        W._drain(S,lambda:all(S.drops.get(d.name) is not d or d.rect[1]>=W.TRANSPORT_Y for d in tiles))
    W._drain(S,lambda:not S.waste_pool.items)
    W.flush(S,blobs)
    assert all(abs(S.get(ln.name).vol-.4)<1e-8 for ln in lanes)


def load_and_aliquot(p):
    p.load_samples('A','A01 样本母液1.60µL（对半取样投入0.40µL）','S',1.6,'L','待测核酸母液')
    sample_aliquots(p.S,p.ALL,p.blob)
    p.flow('A','A02 样本逐级对半：1.60→0.80→0.40µL；每道弃1.20µL',
           [p.cmd('两级等分，保留0.40µL；三滴0.40µL原形并行排废')])
