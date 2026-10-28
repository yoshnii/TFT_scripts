"""R05 only: maximize symmetric opening inside each 36-cell reaction well.

The single approved temporary carrier exception is 32→30→32 cells for
one waiting 1.25µL half. Real liquid remains 1.25µL throughout.
"""
import geom,ops,rigid as R
from scene import _gap,_overlap
from native_packets import partition


def station(S,ln,d):
    X,Y=geom.physical_zone(ln.c,ln.r);w,h=d.rect[2:]
    others=[q for q in S.drops.values() if q is not d and q.vol>0
            and X<=q.rect[0] and q.rect[0]+q.rect[2]<=X+36
            and Y<=q.rect[1] and q.rect[1]+q.rect[3]<=Y+6]
    for gap in range((36-w)//2*2,1,-2):
        delta=gap//2;options=[]
        for x in range(X+delta,X+36-w-delta+1):
            if x+w//2==ln.mag:continue
            candidates=[(x,Y+1,w,h)]
            for k in range(delta+1):
                candidates.extend(((x-k,Y+1,w//2,h),(x+w//2+k,Y+1,w//2,h)))
            if not all(geom.reaction_holes_ok(rc,1) and geom.keep_out_ok(rc)
                       and all(not _overlap(rc,q.rect) and _gap(rc,q.rect)>=2 for q in others)
                       for rc in candidates):continue
            options.append(x)
        if options:return min(options,key=lambda x:abs(x-d.rect[0])),gap
    raise AssertionError(('R05无完整矩形对半空间',ln.id,d.rect,[q.rect for q in others]))


def cut(S,items,first=False):
    plans=[station(S,ln,d) for ln,d in items]
    gaps={g for x,g in plans};assert len(gaps)==1
    gap=gaps.pop();ops.move_rects(S,{d.name:x for (ln,d),(x,g) in zip(items,plans)})
    # Preserve three seconds at the 2/4-cell openings and five seconds at
    # the completed opening; extra single-cell opening moves take 1s.
    times=([1000]*(gap//2+1) if first else
           [1000]+[5000 if g==gap else 3000 if g in (2,4) else 1000 for g in range(2,gap+1,2)])
    pairs=partition(S,[d for ln,d in items],2,'x',gap,frame_ms=times)
    S.r05_wide_events=getattr(S,'r05_wide_events',[])
    S.r05_wide_events.append(dict(gap=gap,parents=[d.name for ln,d in items],
        stations=[x for x,g in plans],frame_ms=times,actual_parent_ul=items[0][1].vol,
        mother_shape=list(items[0][1].rect[2:]),first=first))
    return pairs


def first_half(S,items,blobs,plans,jobs,gx):
    import fixed_waste_collect as F
    import waste_grid as W
    roots=[d for ln,d in items]
    assert all(d.vol==5. and d.rect[2:]==(20,5) for d in roots)
    halves=cut(S,items,first=True)
    S.aqueous_wait_gap=2
    held=[p[0] for p in halves];active=[p[1] for p in halves]
    R.run(S,[(d,R.go(d,[('x',ln.X0+16)])) for (ln,_),d in zip(items,active)])
    for (ln,_),d in zip(items,active):
        d.electrode_area=64;d.margin_budget=64-geom.cells(d.vol)
        d.native_authorized_padding=True;d.native_fractional_volume=True
        d.rect=(ln.X0+16,ln.Y0,16,4)
    S.step(4000,changed=[d.name for d in active])
    quarters=cut(S,[(ln,d) for (ln,_),d in zip(items,active)])
    pending=[p[0] for p in quarters];right=[p[1] for p in quarters]
    for (ln,_),d in zip(items,pending):
        assert d.vol==1.25 and d.rect[2:]==(8,4)
        d.electrode_area=30;d.margin_budget=30-geom.cells(d.vol)
        d.rect=(d.rect[0],ln.Y0,6,5)
    S.step(4000,changed=[d.name for d in pending])
    leaves=cut(S,[(ln,d) for (ln,_),d in zip(items,right)])
    sent=[]
    def send(leaves,index):
        pieces=[]
        for (ln,_),d in zip(items,leaves):
            assert d.vol==.625 and d.rect[2:]==(4,4)
            d.native_packet=True;d.native_leaf_shape=(4,4);pieces.append((ln,d))
        tiles=F.send(S,pieces,gx,blobs,plans[index],jobs);sent.extend(tiles)
        W._drain(S,lambda:all(S.drops.get(d.name) is not d or d.rect[0]>=gx for d in tiles))
    send([p[1] for p in leaves],0);send([p[0] for p in leaves],1)
    # The next maximally opened cut reaches the right well edge. Let the
    # current train clear the reaction rows before reserving that full span.
    W._drain(S,lambda:all(S.drops.get(d.name) is not d or d.rect[1]>=172 for d in sent))
    for d in pending:
        assert d.vol==1.25 and d.rect[2:]==(6,5)
        d.electrode_area=32;d.margin_budget=32-geom.cells(d.vol)
        d.rect=(*d.rect[:2],8,4)
    S.step(4000,changed=[d.name for d in pending])
    leaves=cut(S,[(ln,d) for (ln,_),d in zip(items,pending)])
    send([p[1] for p in leaves],2);send([p[0] for p in leaves],3)
    W._drain(S,lambda:not S.waste_pool.items);W.flush(S,blobs)
    assert all(d.rect==(ln.X0,ln.Y0,10,5) and d.vol==2.5 for (ln,_),d in zip(items,held))
    return held
