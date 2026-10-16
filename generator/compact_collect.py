"""Collect native waste tiles without translating a merged strip along its long axis."""
import contour
import geom
import rigid as R
from scene import Drop


def accepts(pieces, plan):
    return (plan.get('packed', False) and pieces
            and all(d.rect[2] in (3, 4) and d.rect[3] == 4 for _, d in pieces)
            and all(sum(len(row[0]) for row in spec['rows']) == 16
                    for spec in plan['specs'].values()))


def _join(S, ds, name):
    cells = frozenset().union(*(d.target_cells() for d in ds))
    rc = contour.bounds(cells)
    assert len(cells) == sum(len(d.target_cells()) for d in ds) == rc[2]*rc[3]
    vol = round(sum(d.vol for d in ds), 10)
    for d in ds:
        S.remove(d.name)
    d = S.add(Drop(name, rc, vol))
    d.electrode_area = len(cells)
    d.margin_budget = len(cells)-geom.cells(vol)
    d.native_fractional_volume = any(getattr(q, 'native_fractional_volume', False) for q in ds)
    d.waste_grid_packet = d.waste_pipeline_clearance = True
    d.set_cells(cells)
    return d


def _add(S, d, path):
    S.waste_pool.items.append((d, path, None))


def _call_move(S, ds, dx, dy):
    def move():
        for d in ds:
            x,y,w,h = d.rect
            d.rect = (x+dx,y+dy,w,h)
    def safe():
        return all(R.rect_ok(S, d.name, (d.rect[0]+dx,d.rect[1]+dy,*d.rect[2:]),
                            frozenset(getattr(d,'allow',()))) for d in ds)
    return ('call',move,geom.STEP_MS,[d.name for d in ds],safe)


def _assemble(S, job, ds, index):
    """All four tiles are ready: close the vertical gap, then the horizontal gap."""
    import waste_grid as W
    top = [d for d in ds if d.rect[1] == W.TRANSPORT_Y]
    assert len(top) == 2
    for d in ds:
        d.allow = tuple(q.name for q in ds if q is not d)
    for _ in range(4):
        yield _call_move(S,top,0,1)
    def columns():
        bars=[]
        for col in (0,1):
            part=[d for d in ds if d.rect[0] == job['left']+col*(job['w']+4)]
            bars.append(_join(S,part,f"WBAR{job['port']}_{index}_{col}"))
        bars[0].allow=(bars[1].name,)
        bars[1].allow=(bars[0].name,)
        _add(S,bars[1],_close_columns(S,job,bars,index))
    yield ('call',columns,geom.HOLD_MS,[])


def _close_columns(S, job, bars, index):
    for _ in range(4):
        yield _call_move(S,[bars[0]],1,0)
    def block():
        d=_join(S,bars,f"WBLOCK{job['port']}_{index}")
        _add(S,d,_block_path(S,job,d,index))
    yield ('call',block,geom.HOLD_MS,[])


def _release_stage(S,job,index):
    if job['group'] != index:return
    job.update(group=index+1,entry=0)
    if index==3:
        assert S.compact_collect_jobs[0] is job
        S.compact_collect_jobs.pop(0)


def _block_path(S, job, d, index):
    import waste_grid as W
    w=job['w'];park_x=job['final_x']-2*w-geom.MIN_GAP
    target_x=park_x if index%2==0 else park_x-2*w-geom.MIN_GAP
    for action in R.go(d,[('x',target_x)]):
        yield action
        if d.rect[0]>=job['left']+2*w+4+geom.MIN_GAP:
            _release_stage(S,job,index)
    if index%2 == 0:
        yield from R.go(d,[('y',180)])
        # Park below the incoming Y172 corridor, not across it.
        job['parked'][index//2]=d
        yield ('call',lambda:_release_stage(S,job,index),geom.HOLD_MS,[])
        return
    while index//2 not in job['parked']:yield 'wait'
    parked=job['parked'][index//2]
    for _ in range(4):
        yield _call_move(S,[parked],0,-1)
    d.allow=(parked.name,)
    yield from R.go(d,[('x',park_x-2*w)])
    def half():
        block=_join(S,[d,parked],f"WCOL{job['port']}_{index//2}")
        assert block.rect[2:] == (4*w,8)
        target=(park_x,W.TRANSPORT_Y,2*w,16)
        W._settled_shape(S,block,target,contour.fill(target,32*w))
        _release_stage(S,job,index)
        _add(S,block,_half_path(S,job,block,index//2))
    yield ('call',half,geom.LONG_MS,[])


def _half_path(S, job, d, pair):
    import waste_grid as W
    fk=job['port'];second=pair==1
    d.allow=(f'BLOB{fk}',) if second else ()
    target=job['final_x']+(0 if second else 2*job['w'])
    yield from R.go(d,[('x',target)])
    def dock():
        host=job['blobs'].get(fk)
        assert bool(host) == second
        host=_join(S,[host,d] if host else [d],f'BLOB{fk}')
        host.tile_count=16 if second else 8
        host.tile_shape=(job['w'],4);host.tile_area=job['w']*4
        job['blobs'][fk]=host
        for t in range(8 if second else 0,16 if second else 8):
            S.waste_grid_events.append(dict(port=fk,tile_area=job['w']*4,slot=t,
                row=t//4,column=t%4,step=S.nsteps,merged_without_reshape=True,
                tile_shape=[job['w'],4],transport_y=W.TRANSPORT_Y,row_ready=True,
                row_piece_count=4,actual_area=len(host.target_cells()),rect=list(host.rect),
                compact_block_collection=True,whole_port_pipeline=True))
        job['event']['row_merges'].append(S.nsteps)
        if second:
            assert host.rect == (job['final_x'],172,4*job['w'],16)
            job['event']['end_frame']=S.nsteps
    yield ('call',dock,geom.HOLD_MS,[])


def _tile_path(S, job, d, ticket, gx):
    import waste_grid as W
    yield from R.go(d,[('x',gx),('y',164)])
    while (S.compact_collect_jobs[0] is not job or job['group']!=ticket//4
           or job['entry']!=ticket%4):
        yield 'wait'
    i=ticket%4
    # Fill the farther column first; the next tile never crosses an occupied top slot.
    far=0 if job['left']<gx else 1
    col=far if i%2==0 else 1-far
    x=job['left']+col*(job['w']+4)
    yield from R.go(d,[('y',W.TRANSPORT_Y)])
    released=False
    for action in R.go(d,[('x',x)]):
        yield action
        if not released and (d.rect[0]>=gx+geom.CH_W+geom.MIN_GAP
                             or d.rect[0]+d.rect[2]<=gx-geom.MIN_GAP):
            job['entry']+=1;released=True
    if not released:
        job['entry']+=1
    yield from R.go(d,[('y',180 if i<2 else 172)])
    job['arrived'].append(d)
    job['event']['entries'].append(dict(ticket=ticket,frame=S.nsteps))
    if i==3:
        while len(job['arrived'])<(ticket//4+1)*4:
            yield 'wait'
        yield from _assemble(S,job,job['arrived'][-4:],ticket//4)
    else:
        while S.drops.get(d.name) is d:
            yield 'wait'


def send(S, pieces, gx, blobs, plan, jobs):
    import waste_grid as W
    pool=W._pool(S)
    if not hasattr(S,'compact_collect_jobs'):
        S.compact_collect_jobs=[]
    pending=sorted(pieces,key=lambda t:-t[0].r)
    assert len(pending)==len(plan['assign'])
    S.waste_grid_events=getattr(S,'waste_grid_events',[])
    S.waste_batch_events=getattr(S,'waste_batch_events',[])
    S.waste_parallel_events=getattr(S,'waste_parallel_events',[])
    S.waste_batch_events.append(dict(tile_shape=list(pending[0][1].rect[2:]),count=len(pending),
        ports=plan['ports'],collection_group=plan['stop'],flush_after=plan['flush'],
        whole_port_pipeline=True,packed=True,minimum_ports=plan['minimum_ports']))
    tiles=[]
    for (ln,d),(key,r,pos) in zip(pending,plan['assign']):
        if key not in jobs:
            fk=plan['specs'][key]['port'];w=d.rect[2]
            assert not blobs.get(fk)
            fx=geom.HOLE_X[fk]-2*w
            left=(fx-(4*w+12+geom.MIN_GAP) if fx<gx+geom.CH_W+geom.PREFERRED_GAP
                  else gx+geom.CH_W+geom.MIN_GAP)
            job=jobs[key]=dict(port=fk,w=w,left=left,parked={},
                final_x=fx,blobs=blobs,group=0,entry=0,count=0,arrived=[],
                event=dict(port=fk,scheduled_drops=16,start_frame=S.nsteps,entries=[],row_merges=[]))
            S.compact_collect_jobs.append(job);S.waste_parallel_events.append(job['event'])
        job=jobs[key];ticket=job['count'];job['count']+=1
        assert ticket<16 and d.rect[2:]==(job['w'],4)
        d.S=S;d.allow=();d.waste_grid_packet=d.waste_pipeline_clearance=True
        d.tile_area=len(d.target_cells());d.waste_fk=job['port']
        pool.items.append((d,_tile_path(S,job,d,ticket,gx),R._door_rule(gx)))
        tiles.append(d)
    S.step(geom.HOLD_MS,changed=[])
    return tiles
