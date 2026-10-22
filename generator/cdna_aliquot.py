"""0.40µL sample aliquots: 1.60→0.80→0.40, no unequal peeling or padding."""
import geom
import ops
import rigid as R
import waste_grid as W
from native_packets import partition


def _stream_exit(S,d,gx,previous,first,assignments,jobs):
    """Reuse vacated channel space; allocate outlet slots in arrival order."""
    while ((previous is not None and previous.rect[0]<gx)
           or (first and any(q.rect[0]<gx for q in first))):
        yield 'wait'
    # Wait outside the channel's six-cell clearance, not halfway through
    # its door where a descending packet and an entering packet could jam.
    yield from R.go(d,[('y',d.rect[1]+1),('x',gx-6-d.rect[2])])
    travel=gx-d.rect[0]
    def opening():
        target=(gx,d.rect[1],*d.rect[2:])
        if not R.rect_ok(S,d.name,target):return False
        for other in S.drops.values():
            if other is d:continue
            x,y,w,h=other.rect
            if x+w>gx-6 and x<gx+geom.CH_W and y+h<=d.rect[1]:
                if d.rect[1]-y-h<6+travel:return False
        return True
    while not opening():yield 'wait'
    yield from R.go(d,[('x',gx)])
    S.a02_exit_events=getattr(S,'a02_exit_events',[])
    S.a02_exit_events.append(dict(name=d.name,batch=d.batch_round,frame=S.nsteps,
        shape=list(d.rect[2:]),actual_ul=d.vol,
        first_batch_still_in_channel=any(S.drops.get(q.name) is q and q.rect[1]<172 for q in first)))
    yield from R.go(d,[('y',164)])
    ticket=S.a02_outlet_ticket;S.a02_outlet_ticket+=1
    key,row,pos=assignments[ticket];job=jobs[key]
    assert d.rect[2:]==(job['rows'][row]['widths'][pos],4)
    d.waste_fk=job['port'];d.slot=job['rows'][row]['n']-1-pos
    job['event'].setdefault('arrival_order',[]).append(d.name)
    yield from W._tile_path(S,d,job,ticket,row,pos,gx)


def _send_stream(S,pieces,gx,blobs,specs,assignments,jobs,previous,first):
    pool=W._pool(S)
    S.waste_grid_events=getattr(S,'waste_grid_events',[])
    for key,spec in specs.items():
        if key not in jobs:
            job=jobs[key]=W._job(S,spec,blobs,gx)
            # This local mixed-size row uses cumulative widths, not the
            # equal-width shorthand used by the other released routes.
            for row in job['rows']:
                slots=list(reversed(row['widths']))
                sw=sum(slots)+(len(slots)-1)*geom.CUT_GAP
                left=(job['final_x']-6-sw if spec.get('stage_before_hole') else
                      job['final_x']+spec['W']+geom.PREFERRED_GAP)
                row['stage_x']=[left+sum(slots[:i])+i*geom.CUT_GAP for i in range(len(slots))]
                base=left+sw-sum(slots) if spec.get('stage_before_hole') else left
                row['contract_x']=[base+sum(slots[:i]) for i in range(len(slots))]
    if not hasattr(S,'a02_outlet_ticket'):S.a02_outlet_ticket=0
    S.batch_channel_x=gx
    S.batch_members=getattr(S,'batch_members',{})
    batch=len(S.batch_members);members=S.batch_members[batch]=[]
    S.waste_batch_events=getattr(S,'waste_batch_events',[])
    shape=list(pieces[0][1].rect[2:])
    S.waste_batch_events.append(dict(count=len(pieces),ports=[3,1],tile_shape=shape,
                                    streaming_channel=True))
    S.batch_events=getattr(S,'batch_events',[])
    S.batch_events.append(dict(round=batch,start_frame=S.nsteps,packets=len(pieces)))
    for ln,d in pieces:
        assert list(d.rect[2:])==shape and d.vol in (.4,.8)
        d.S=S;d.allow=();d.waste_grid_packet=d.waste_pipeline_clearance=True
        d.batch_round=batch;d.tile_area=d.rect[2]*d.rect[3]
        leader=previous.get(ln.id);previous[ln.id]=d
        path=_stream_exit(S,d,gx,leader,first,assignments,jobs)
        pool.items.append((d,path,R._door_rule(gx)));members.append(d)
    S.step(geom.HOLD_MS,changed=[])
    return members


def sample_aliquots(S, lanes, blobs):
    lanes=list(lanes)
    assert len(lanes)==16 and all(S.get(ln.name).vol==1.6 for ln in lanes)
    ops.settle_loaded(S)
    halves=partition(S,[S.get(ln.name) for ln in lanes],2,'x',4)
    # Dispatch the right-hand half before cutting the remaining half.
    rows={3:[([4]*3,4)]*4,
          1:[([4]*3,4),([4]+[2]*4,4),([2]*6,4),([2]*6,4)]}
    specs={(1,k):dict(port=k,W=12,rows=rows[k],native=True,
                        strict_staging=True,stage_before_hole=k==3) for k in (3,1)}
    assignments=[((1,k),r,pos) for k in (3,1) for r,(widths,h) in enumerate(rows[k])
                 for pos in range(len(widths))]
    gx=geom.gap_x(geom.waste_gap(lanes[0].c));jobs={};previous={}
    first=_send_stream(S,[(ln,halves[i][1]) for i,ln in reversed(list(enumerate(lanes)))],
                       gx,blobs,specs,assignments,jobs,previous,[])
    W._drain(S,lambda:len(getattr(S,'a02_exit_events',[]))==16)
    S.a02_right_exit_before_second_split=S.nsteps
    quarters=partition(S,[ds[0] for ds in halves],2,'x',4)
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
    by_lane={ln.id:[*quarters[i],halves[i][1]] for i,ln in enumerate(lanes)}
    for ln in lanes:
        keep=by_lane[ln.id][0]
        S.remove(keep.name);keep.name=ln.name;S.add(keep)
    # Smaller drops follow the intact half when channel space opens.
    rest=[(ln,by_lane[ln.id][1]) for ln in reversed(lanes)]
    _send_stream(S,rest,gx,blobs,specs,assignments,jobs,previous,first)
    # Keep the published downstream input mask, after (not during) splitting.
    ops.move_rects(S,{ln.name:ln.X0+1 for ln in lanes})
    W._drain(S,lambda:not S.waste_pool.items)
    assert S.a02_outlet_ticket==32
    W.flush(S,blobs)
    assert all(abs(S.get(ln.name).vol-.4)<1e-8 for ln in lanes)


def load_and_aliquot(p):
    p.load_samples('A','A01 样本母液1.60µL（对半取样投入0.40µL）','S',1.6,'L','待测核酸母液')
    sample_aliquots(p.S,p.ALL,p.blob)
    p.flow('A','A02 样本逐级对半：1.60→0.80→0.40µL；每道弃1.20µL',
           [p.cmd('两级等分，保留0.40µL；0.80µL（4×4）与0.40µL（2×4）原形接续排废')])
