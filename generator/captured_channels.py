"""R06/C05/C06: use idle adjacent channels for captured-water splitting.

Carrier areas, real volumes and the existing F-receiver paths stay separate.
Return prepared leaves to the well doorway before the unchanged transport
queue takes over; borrowing a channel never relaxes transport clearance.
"""
import geom,ops,rigid as R
from scene import _gap,_overlap
from native_packets import partition

STAGES=('R06','C05','C06')


def station(S,ln,d):
    assert getattr(S,'captured_channel_stage',None) in STAGES
    X,Y=geom.physical_zone(ln.c,ln.r);w,h=d.rect[2:]
    lo,hi=X-geom.CH_W,X+36+geom.CH_W
    others=[q for q in S.drops.values() if q is not d and q.vol>0]
    def clearance(rc,q):
        if not getattr(q,'native_packet',False):return 2
        if min(rc[1],q.rect[1])>=162 and max(rc[1],q.rect[1])>=172:
            return getattr(S,'outlet_staging_gap',None) or 6
        return 6
    for gap in range((hi-lo-w)//2*2,1,-2):
        delta=gap//2;choices=[]
        for x in range(lo+delta,hi-w-delta+1):
            if x+w//2==ln.mag:continue
            rects=[(x,ln.Y0,w,h)]
            for k in range(delta+1):
                rects.extend(((x-k,ln.Y0,w//2,h),(x+w//2+k,ln.Y0,w//2,h)))
            if all(geom.reaction_holes_ok(rc,1) and geom.keep_out_ok(rc)
                   and all(not _overlap(rc,q.rect) and _gap(rc,q.rect)>=clearance(rc,q)
                           for q in others) for rc in rects):choices.append(x)
        if choices:
            # C05 still holds a 3.08µL carrier. Pack the waiting side left
            # so rounding an even opening does not waste the next cut's slot.
            return (min(choices) if S.captured_channel_stage=='C05'
                    else min(choices,key=lambda x:abs(x-d.rect[0]))),gap
    raise AssertionError(('磁吸水相无安全双侧切分空间',S.captured_channel_stage,ln.id,d.rect))


def cut(S,items,first=False):
    plans=[station(S,ln,d) for ln,d in items]
    gaps={g for x,g in plans};assert len(gaps)==1
    gap=gaps.pop()
    ops.move_rects(S,{d.name:x for (ln,d),(x,g) in zip(items,plans)})
    times=([1000]*(gap//2+1) if first else
           [1000]+[5000 if g==gap else 3000 if g in (2,4) else 1000
                   for g in range(2,gap+1,2)])
    pairs=partition(S,[d for ln,d in items],2,'x',gap,frame_ms=times)
    for e in S.split_events[-len(items):]:e['captured_channel_stage']=S.captured_channel_stage
    S.captured_channel_events=getattr(S,'captured_channel_events',[])
    S.captured_channel_events.append(dict(stage=S.captured_channel_stage,gap=gap,
        parents=[d.name for ln,d in items],stations=[x for x,g in plans],
        frame_ms=times,actual_parent_ul=items[0][1].vol,
        mother_shape=list(items[0][1].rect[2:]),first=first))
    return pairs


def shape(S,items,w,h,area=None,physical_y=False):
    changed=[]
    for ln,d in items:
        target=(d.rect[0],ln.Y0-int(physical_y),w,h)
        if d.rect==target and len(d.target_cells())==(area or w*h):continue
        d.electrode_area=area or w*h
        d.margin_budget=d.electrode_area-geom.cells(d.vol)
        assert d.margin_budget>=0
        d.native_authorized_padding=True;d.native_fractional_volume=True
        d.rect=target;changed.append(d.name)
    if changed:S.step(4000,changed=changed)


def enter(S,items,stage):
    assert stage in STAGES and not getattr(S,'captured_channel_stage',None)
    S.captured_channel_stage=stage
    for ln,d in items:d.captured_prep_row=(ln.c,ln.r)


def leave(S):
    S.captured_channel_stage=None
    for d in S.drops.values():d.__dict__.pop('captured_prep_row',None)


def quarter(S,items,branches,plans,start_round,gx,blobs,jobs,stage,expanded=False,entered=False):
    """One retained half, split twice, producing the same four 4×4 leaves."""
    import fixed_waste_collect as F
    import waste_grid as W
    active=list(zip((ln for ln,_ in items),branches))
    if not entered:enter(S,active,stage)
    sent=[]
    try:
        # A 6-high C06 waiting carrier cannot slide across the S/B holes.
        # Restore its already-approved 16×4 cutting carrier before moving.
        shape(S,active,16,4)
        halves=cut(S,active)
        pending=[p[0] for p in halves];right=[p[1] for p in halves]
        pending_items=list(zip((ln for ln,_ in items),pending))
        if expanded:shape(S,pending_items,7,5,35)
        leaves=cut(S,list(zip((ln for ln,_ in items),right)))
        def send(leaves,index):
            pieces=list(zip((ln for ln,_ in items),leaves))
            # The cut endpoint may borrow either channel. Give it back
            # before native_packet/queue flags apply the transport rules.
            ops.move_rects(S,{d.name:max(ln.X0,min(d.rect[0],ln.X0+32))
                              for ln,d in pieces})
            for ln,d in pieces:
                assert d.rect[2:]==(4,4)
                d.native_packet=True;d.native_leaf_shape=(4,4)
            tiles=F.send(S,pieces,gx,blobs,plans[start_round+index],jobs)
            sent.extend(tiles)
            W._drain(S,lambda:all(S.drops.get(d.name) is not d or d.rect[0]>=gx for d in tiles))
        send([p[1] for p in leaves],0);send([p[0] for p in leaves],1)
        # Reserve the entire borrowed width only after those trains pass.
        W._drain(S,lambda:all(S.drops.get(d.name) is not d or d.rect[1]>=172 for d in sent))
        if expanded:shape(S,pending_items,8,4,32)
        leaves=cut(S,pending_items)
        send([p[1] for p in leaves],2);send([p[0] for p in leaves],3)
        W._drain(S,lambda:not S.waste_pool.items)
        # During C05 the retained 3.08µL carrier returns to the exact old
        # C06 input before the original F-hole suction mask is emitted.
        if stage=='C05':
            held=[(ln,d) for ln,_ in items for d in S.drops.values()
                  if getattr(d,'captured_prep_row',None)==(ln.c,ln.r)
                  and d.vol==3.08 and not getattr(d,'native_packet',False)]
            assert len(held)==len(items)
            ops.move_rects(S,{d.name:ln.X0 for ln,d in held})
        # Arrival at X10 already records 1s in this exact full mask. Its
        # additional 3s coalesce to the published C05 F-tail's 4s total.
        W.flush(S,blobs,full_mask_ms=3000 if stage=='C05' else None)
    finally:
        if not entered:leave(S)


def c05_c06(S,items,blobs,plans,jobs,gx):
    enter(S,items,'C05');S.aqueous_wait_gap=2
    try:
        assert all(d.vol==6.16 and d.rect[2:]==(32,4) for ln,d in items)
        halves=cut(S,items,first=True)
        held=[p[0] for p in halves]
        held_items=list(zip((ln for ln,_ in items),held))
        # Preserve the existing 66-cell staging exception, never add liquid.
        shape(S,held_items,11,6,66,physical_y=True)
        quarter(S,items,[p[1] for p in halves],plans,0,gx,blobs,jobs,'C05',True,True)
        assert all(d.rect==(ln.X0,ln.Y0-1,11,6) for ln,d in held_items)
    finally:leave(S)
    quarter(S,items,held,plans,4,gx,blobs,jobs,'C06',True)
