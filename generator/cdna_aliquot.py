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
    # Make room on the right, not by pushing the retained branch out of its well.
    # At column A: 15..19 / 23..27 -> 15..19 / 27..31;
    # the next equal cuts end at X=11,17,23,29 (all within X=10..46).
    ops.move_rects(S,{ds[1].name:ln.X0+17 for ln,ds in zip(lanes,halves)})
    quarters=partition(S,[d for ds in halves for d in ds],2,'x',4)
    # Splitting/staging may not borrow either adjacent channel. The later
    # intentional waste exit is a separate route, not a splitting exception.
    for ln in lanes:
        events=[e for e in S.split_events if any(name.startswith(ln.name+'_') for name in e['children'])]
        for event in events:
            for frame in event['positions']:
                for x,y,w,h in frame:
                    assert ln.X0<=x<x+w<=ln.X0+geom.LANE_L,('取样劈裂越出本反应区',ln.id,(x,y,w,h))
                    py=geom.physical_zone(ln.c,ln.r)[1]
                    assert py<=y<y+h<=py+geom.PHYSICAL_LANE_H
    by_lane={ln.id:[*quarters[2*i],*quarters[2*i+1]] for i,ln in enumerate(lanes)}
    for ln in lanes:
        keep=by_lane[ln.id][0]
        S.remove(keep.name);keep.name=ln.name;S.add(keep)
    # Forty-eight native 2×4 waste packets; two balanced 12×16 outlet pools.
    # This keeps the already approved F-hole aspiration tail, without reshaping packets.
    specs={(1,k):dict(port=k,W=12,rows=[([2]*6,4)]*4,native=True,
                        strict_staging=True,stage_before_hole=k==3) for k in (3,1)}
    assignments=[((1,k),i//6,i%6) for k in (3,1) for i in range(24)]
    gx=geom.gap_x(geom.waste_gap(lanes[0].c));jobs={}
    S.batch_generic=True
    for batch in range(3):
        pieces=[(ln,by_lane[ln.id][3-batch]) for ln in lanes]
        plan=dict(stop=1,flush=batch==2,ports=[3,1],assign=assignments[16*batch:16*(batch+1)],
                  specs=specs,minimum_ports=2,packed=True)
        ordered=sorted(pieces,key=lambda item:-item[0].r)
        # Do not interleave opposing bottom-band row transfers for F3 and F1.
        # Both pools are still aspirated together; no extra tip or stop.
        for key in dict.fromkeys(a[0] for a in plan['assign']):
            chosen=[(piece,a) for piece,a in zip(ordered,plan['assign']) if a[0]==key]
            sub=dict(plan,assign=[a for piece,a in chosen])
            tiles=W._send_pipeline(S,[piece for piece,a in chosen],gx,blobs,sub,jobs)
            for job in jobs.values():
                if job['port']!=3:job['under_exit']=True
            W._drain(S,lambda:all(S.drops.get(d.name) is not d or d.rect[1]>=W.TRANSPORT_Y for d in tiles))
            if chosen[-1][1][1:]==(3,5):
                W._drain(S,lambda:not S.waste_pool.items)
    W._drain(S,lambda:not S.waste_pool.items)
    W.flush(S,blobs)
    assert all(abs(S.get(ln.name).vol-.4)<1e-8 for ln in lanes)


def load_and_aliquot(p):
    p.load_samples('A','A01 样本母液1.60µL（对半取样投入0.40µL）','S',1.6,'L','待测核酸母液')
    sample_aliquots(p.S,p.ALL,p.blob)
    p.flow('A','A02 样本逐级对半：1.60→0.80→0.40µL；每道弃1.20µL',
           [p.cmd('两级等分，保留0.40µL；三滴0.40µL原形并行排废')])
