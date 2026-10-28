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
             receiver=None,mouth=0,issued=0,received=0,actual_ul=0.)
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


def path(S,job,d,ticket,gx,at_head=False,dispatch=None):
    import waste_grid as W
    if not at_head:yield from R.go(d,[('x',gx),('y',164)])
    while job['mouth']!=ticket:yield 'wait'
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
    # receivers are entered from the left along the unchanged Y172 corridor.
    under=left<gx+geom.CH_W+geom.PREFERRED_GAP
    released=False
    if under:
        d.waste_receiver=host.name;d.allow=(host.name,)
        yield from R.go(d,[('y',top),('x',left)])
    else:
        yield from R.go(d,[('y',W.TRANSPORT_Y)])
        for action in R.go(d,[('x',left-d.rect[2]-6)]):
            yield action
            if not released and d.rect[0]>=gx+geom.CH_W+geom.MIN_GAP:
                job['mouth']+=1;released=True
        d.waste_receiver=host.name;d.allow=(host.name,)
        yield from R.go(d,[('x',left)])
    absorb(S,job,d)
    if not released:job['mouth']+=1


def continuous_path(S,dispatch,d,gx):
    """Assign equal-volume packets in their actual arrival order at Y164."""
    yield from R.go(d,[('x',gx),('y',164)])
    job=next(j for j in dispatch if j['issued']<j['expected'])
    ticket=job['issued'];job['issued']+=1;d.waste_fk=job['port']
    S.waste_arrival_events=getattr(S,'waste_arrival_events',[])
    S.waste_arrival_events.append(dict(packet=d.name,port=job['port'],ticket=ticket,
        batch=d.batch_round,frame=S.nsteps,actual_ul=d.vol))
    yield from path(S,job,d,ticket,gx,at_head=True,dispatch=dispatch)


def send(S,pieces,gx,blobs,plan,jobs):
    import waste_grid as W
    pool=W._pool(S);pending=sorted(pieces,key=lambda item:-item[0].r)
    assert len(pending)==len(plan['assign'])
    S.waste_grid_events=getattr(S,'waste_grid_events',[])
    S.waste_batch_events=getattr(S,'waste_batch_events',[])
    S.batch_members=getattr(S,'batch_members',{});batch=len(S.batch_members)
    members=S.batch_members[batch]=[];S.batch_channel_x=gx
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
        from transport_spacing import queue_exit
        pool.items.append((d,queue_exit(S,d,gx,continuous_path(S,dispatch,d,gx)),R._door_rule(gx)))
    S.step(geom.HOLD_MS,changed=[])
    return members
