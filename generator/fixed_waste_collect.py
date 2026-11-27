"""Fixed, pre-energized final waste receivers; actual liquid is accounted separately."""
import geom,contour
import rigid as R
from scene import Drop,_gap,_overlap


def accepts(pieces,plan):
    return bool(plan.get('packed') and pieces and all(
        spec.get('native') and spec['W'] in (12,16)
        and sum(row[2] if len(row)>2 else row[1] for row in spec['rows'])==16
        for spec in plan['specs'].values()))


def handoff(scene,a,b):
    receiver,incoming=(a,b) if getattr(a,'fixed_waste_receiver',False) else (b,a)
    return (getattr(receiver,'fixed_waste_receiver',False)
        and getattr(incoming,'waste_receiver',None)==receiver.name
        and receiver.name in getattr(incoming,'allow',())
        and incoming.vol>0 and len(incoming.target_cells())<=32)


def make_job(S,spec,blobs):
    count=sum(len(row[0]) for row in spec['rows'])
    height=sum(row[2] if len(row)>2 else row[1] for row in spec['rows'])
    rect=(geom.HOLE_X[spec['port']]-spec['W']//2,188-height,spec['W'],height)
    job=dict(port=spec['port'],rect=rect,expected=count,blobs=blobs,
             receiver=None,mouth=0,issued=0,received=0,actual_ul=0.,lane_mouth={172:0,182:0},lane_issued={172:0,182:0})
    S.fixed_waste_events=getattr(S,'fixed_waste_events',[])
    S.waste_grid_events=getattr(S,'waste_grid_events',[])
    return job


def can_open(S,job):
    from transport_spacing import required
    ghost=Drop('RESERVE',job['rect'],0.)
    ghost.waste_grid_packet=True;ghost.fixed_waste_receiver=True
    for d in S.drops.values():
        for rc in d.rects():
            if _overlap(ghost.rect,rc) or _gap(ghost.rect,rc)<max(geom.MIN_GAP,required(S,ghost,ghost.rect,d,rc)):
                return False
    return True


def open_receiver(S,job):
    assert job['receiver'] is None and can_open(S,job)
    assert not job['blobs'].get(job['port'])
    name=f"BLOB{job['port']}";d=S.add(Drop(name,job['rect'],0.))
    d.fixed_waste_receiver=True;d.waste_grid_packet=d.waste_pipeline_clearance=True
    d.electrode_area=job['rect'][2]*job['rect'][3];d.margin_budget=d.electrode_area
    d.native_authorized_padding=True;d.tile_count=0
    job['receiver']=job['blobs'][job['port']]=d
    S.fixed_waste_events.append(dict(kind='preopen',port=job['port'],frame=S.nsteps,
        rect=list(d.rect),carrier_cells=d.electrode_area,actual_ul=0.,expected_packets=job['expected']))


def absorb(S,job,d):
    host=job['receiver'];assert host and set(d.target_cells())<=set(host.target_cells())
    assert job['received']<job['expected']
    incoming=d.vol;host.vol=round(host.vol+incoming,10)
    host.margin_budget=host.electrode_area-geom.cells(host.vol);assert host.margin_budget>=0
    host.tile_count+=1;S.remove(d.name)
    job['received']+=1;job['actual_ul']=round(job['actual_ul']+incoming,10)
    S.fixed_waste_events.append(dict(kind='absorb',port=job['port'],frame=S.nsteps,
        packet=d.name,packet_rect=list(d.rect),packet_ul=incoming,total_ul=host.vol,
        rect=list(host.rect),carrier_cells=host.electrode_area,count=job['received']))
    S.waste_grid_events.append(dict(port=job['port'],tile_shape=list(d.rect[2:]),
        tile_area=len(d.target_cells()),slot=job['received']-1,step=S.nsteps,
        direct_fixed_receiver=True,merged_without_reshape=True,actual_area=host.electrode_area,
        actual_ul=host.vol,rect=list(host.rect)))


def approach_during_collection(S, job, d, lane, gx):
    """Approach the dock while farther packets are still crossing its footprint."""
    left, top, width, _ = job['rect']
    from_above = left < gx + geom.CH_W + geom.PREFERRED_GAP

    def open_if_clear():
        if job['receiver'] is not None:
            return True
        # Dispatch assigns all packets farthest port first. Pending far packets
        # can still cross this dock until their complete footprint clears it.
        passing = any(getattr(q, 'waste_fk', 0) > job['port']
                      and getattr(q, 'continuous_waste_exit', False)
                      and q.rect[0] < left + width + 6 for q in S.drops.values())
        if passing or not can_open(S, job):
            return False
        open_receiver(S, job)
        return True

    open_if_clear()
    if not from_above:
        for action in R.go(d, [('y', lane), ('x', left-d.rect[2]-6)]):
            if job['receiver'] is None:
                open_if_clear()
            yield action
    while not open_if_clear():
        yield 'wait'
    host = job['receiver']
    d.waste_receiver = host.name
    d.allow = (host.name,)
    if from_above:
        yield from R.go(d, [('y', top)])
    dock = min(max(d.rect[0],left),left+width-d.rect[2]) if geom.COLS==4 else left
    yield from R.go(d, [('x', dock)])
    absorb(S, job, d)


def path(S,job,d,ticket,gx,at_head=False,dispatch=None):
    if not at_head:yield from R.go(d,[('x',gx),('y',164)])
    lane=getattr(d,'bottom_lane_y',172)
    while job['lane_mouth'][lane]!=ticket:yield 'wait'
    if dispatch and len(dispatch) >= 3:
        job['lane_mouth'][lane] += 1
        yield from approach_during_collection(S, job, d, lane, gx)
        return
    if job['receiver'] is None:
        # Near pools must not open across the route of still-passing drops
        # destined for a farther pool. Keep the original far-to-near order.
        while dispatch and (any(j['port']>job['port'] and j['issued']<j['expected'] for j in dispatch)
                or any(getattr(q,'waste_fk',0)>job['port']
                       and getattr(q,'continuous_waste_exit',False)
                       and q.rect[0]<job['rect'][0]+job['rect'][2]+6
                       for q in S.drops.values())):
            yield 'wait'
        while not can_open(S,job):yield 'wait'
        open_receiver(S,job)
    host=job['receiver'];left,top,width,height=host.rect
    # A receiver below the channel (F1) is entered from above. The other
    # far receivers accept packets from two separated horizontal corridors.
    under=left<gx+geom.CH_W+geom.PREFERRED_GAP
    released=False
    if under:
        d.waste_receiver=host.name;d.allow=(host.name,)
        dock = min(max(gx,left),left+width-d.rect[2]) if geom.COLS==4 else left
        if geom.COLS==4:
            job['lane_mouth'][lane]+=1;released=True
        yield from R.go(d,[('y',top),('x',dock)])
    else:
        yield from R.go(d,[('y',lane)])
        for action in R.go(d,[('x',left-d.rect[2]-6)]):
            yield action
            if not released and d.rect[0]>=gx+geom.CH_W+geom.MIN_GAP:
                job['lane_mouth'][lane]+=1;released=True
        d.waste_receiver=host.name;d.allow=(host.name,)
        yield from R.go(d,[('x',left)])
    absorb(S,job,d)
    if not released:job['lane_mouth'][lane]+=1


def continuous_path(S,dispatch,d,gx):
    """Assign equal-volume packets in their actual arrival order at Y164."""
    rail=getattr(d,"waste_rail_x",gx)
    yield from R.go(d,[('x',rail),('y',164)])
    job=next(j for j in dispatch if j['issued']<j['expected'])
    lanes=(172,182) if job['port']!=1 and job['packet_signature'][1]==(4,4) and (geom.COLS==4 or any(abs(job['packet_signature'][0]-v)<1e-8 for v in (.625,.75))) else (172,)
    lane=lanes[job['issued']%len(lanes)]
    if hasattr(d,'waste_rail_x') and len(lanes)>1 and not hasattr(S,'aliquot_outer_rows'):
        lane=172 if rail>gx else 182
    ticket=job['lane_issued'][lane];job['lane_issued'][lane]+=1
    job['issued']+=1;d.waste_fk=job['port'];d.bottom_lane_y=lane
    S.waste_arrival_events=getattr(S,'waste_arrival_events',[])
    S.waste_arrival_events.append(dict(packet=d.name,port=job['port'],ticket=ticket,
        batch=d.batch_round,frame=S.nsteps,actual_ul=d.vol,lane_y=lane))
    yield from path(S,job,d,ticket,rail,at_head=True,dispatch=dispatch)


def send(S,pieces,gx,blobs,plan,jobs,record=True):
    import waste_grid as W
    pool=W._pool(S);pending=sorted(pieces,key=lambda item:-item[0].r)
    assert len(pending)==len(plan['assign'])
    S.waste_grid_events=getattr(S,'waste_grid_events',[])
    S.waste_batch_events=getattr(S,'waste_batch_events',[])
    S.batch_members=getattr(S,'batch_members',{})
    batch=plan.get('ready_round',len(S.batch_members))
    members=S.batch_members.setdefault(batch,[])
    S.batch_channel_x=gx
    S.waste_batch_events.append(dict(tile_shape=list(pending[0][1].rect[2:]),
        count=len(pending),ports=plan['ports'],collection_group=plan['stop'],
        flush_after=plan['flush'],whole_port_pipeline=True,packed=True,
        minimum_ports=plan['minimum_ports'],fixed_final_receiver=True))
    for key,spec in plan['specs'].items():
        if key not in jobs:jobs[key]=make_job(S,spec,blobs)
    dispatch=sorted((jobs[key] for key in plan['specs']),key=lambda j:-j['port'])
    # Admission order may change across rounds, but every packet in this
    # collection stop has the same real volume and shape. Per-hole counts,
    # real totals and aspiration boundaries therefore remain unchanged.
    packet_signature=(pending[0][1].vol,pending[0][1].rect[2:])
    for j in dispatch:
        assert j.setdefault('packet_signature',packet_signature)==packet_signature
    for (ln,d),(key,row,pos) in zip(pending,plan['assign']):
        d.S=S;d.allow=();d.waste_grid_packet=d.waste_pipeline_clearance=True
        d.continuous_waste_exit=True
        d.pre_advance_exit=True;d.early_exit=plan.get('early_exit',False)
        d.batch_round=batch;d.tile_area=len(d.target_cells())
        members.append(d)
        if getattr(S,'dual_waste_outlet',False):
            assert geom.COLS==4 and d.rect[2:]==(4,4)
            d.waste_rail_x=gx+10 if ln.r<=7 else gx
        if hasattr(S,'cleanup_outer_rows') and S.cleanup_outer_rows[batch]:
            assert geom.COLS==4 and d.vol==.75 and d.rect[2:]==(4,4)
            d.waste_rail_x=gx+10 if ln.r<=S.cleanup_outer_rows[batch] else gx
        from transport_spacing import queue_exit
        pool.items.append((d,queue_exit(S,d,gx,continuous_path(S,dispatch,d,gx)),R._door_rule(gx)))
    if record:S.step(geom.HOLD_MS,changed=[])
    return members

def send_ready_rows(S,pieces,gx,blobs,plan,jobs,index,previous):
    """Offer a packet when its own preceding sibling clears the well."""
    import waste_grid as W
    assert geom.COLS==4 and len(pieces)==len(plan['assign'])==16
    pending=sorted(pieces,key=lambda pair:-pair[0].r)
    def available(row):
        first=previous.get(row)
        return first is None or S.drops.get(first.name) is not first or first.rect[0]>=gx

    while pending:
        W._drain(S,lambda:any(available(ln.r) for ln,_ in pending))
        for ln,d in pending[:]:
            if not available(ln.r):continue
            if S.aliquot_outer_rows[index]:
                d.waste_rail_x=gx+10 if ln.r<=S.aliquot_outer_rows[index] else gx
            local=dict(plan,ready_round=index,assign=[plan['assign'][16-ln.r]])
            send(S,[(ln,d)],gx,blobs,local,jobs,record=False)
            previous[ln.r]=d
            pending.remove((ln,d))
        S.step(geom.HOLD_MS,changed=[])
