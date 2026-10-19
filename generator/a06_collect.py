"""A06 only: native 4x4 tiles -> moving 8x8 blocks -> fixed F1/F3 quilts.

Quilt tiles may touch during the approved fixed-port merger. No assembled
quilt is translated or reshaped; the existing F-hole withdrawal tail follows.
"""
import geom
import rigid as R
import compact_collect as C


def far_clear(S):
    jobs = S.a06_collect_jobs
    return (3 in jobs and jobs[3]['count'] == 16
            and all(d.rect[0] >= 65 for d in S.drops.values()
                    if getattr(d, 'waste_fk', None) == 3))


def _block_path(S, job, d, index):
    import waste_grid as W
    port = job['port']
    # Lower slots first leave the Y172 four-cell incoming corridor open.
    col, row = ((1, 1), (0, 1), (1, 0), (0, 0))[index]
    target_x = job['final_x'] + 8*col
    target_y = W.TRANSPORT_Y + 8*row
    if port == 1:
        while not far_clear(S):
            yield 'wait'
    d.allow = tuple(q.name for q in job['docked'])
    yield from R.go(d, [('y', target_y)])
    released = False
    for action in R.go(d, [('x', target_x)]):
        yield action
        # Near-port top blocks go opposite incoming tiles: do not admit the
        # next group until the block has crossed the shared channel completely.
        limit = (53 if port == 1 and row == 0
                 else job['left'] + 11 + geom.MIN_GAP)
        if not released and d.rect[0] >= limit:
            job['group'] = index+1; job['entry'] = 0; released = True
    def dock():
        job['docked'].append(d)
        d.a06_fixed_quilt = True
        for q in S.drops.values():
            if getattr(q,'a06_transport_square',False) and q.waste_fk == port:
                q.allow = tuple(b.name for b in job['docked'] if b is not q)
        job['event']['docks'].append(dict(frame=S.nsteps, rectangle=list(d.rect), index=index))
        if not released:
            job['group'] = index+1; job['entry'] = 0
        if index == 3:
            host = C._join(S, job['docked'], f'BLOB{port}')
            host.waste_fk = port
            assert host.rect == (job['final_x'],172,16,16) and host.vol == 12.
            job['blobs'][port] = host
            job['event']['end_frame'] = S.nsteps
    yield ('call', dock, geom.HOLD_MS, [])
    while index < 3 and S.drops.get(d.name) is d:
        yield 'wait'


def _assemble(S, job, ds, index):
    # Local 2x2 closure at the approved three-cell minimum, without the later
    # 16x8 / 8x16 detour. Keep individual masks until the complete 8x8 exists.
    for d in ds:
        d.allow = tuple(q.name for q in ds if q is not d)
    top = [d for d in ds if d.rect[1] == 172]
    for _ in range(3):
        yield C._call_move(S, top, 0, 1)
    left = [d for d in ds if d.rect[0] == job['left']]
    for _ in range(3):
        yield C._call_move(S, left, 1, 0)
    def block():
        d = C._join(S, ds, f'A06_BLOCK{job["port"]}_{index}')
        assert d.rect[2:] == (8,8) and d.vol == 3.
        d.S = S; d.waste_fk = job['port']; d.a06_transport_square = True
        C._add(S, d, _block_path(S, job, d, index))
        job['event']['blocks'].append(dict(frame=S.nsteps,rectangle=list(d.rect),volume_ul=d.vol))
    yield ('call', block, geom.HOLD_MS, [])


def _tile_path(S, job, d, ticket, gx):
    import waste_grid as W
    yield from R.go(d, [('x',gx),('y',164)])
    while job['group'] != ticket//4 or job['entry'] != ticket%4:
        yield 'wait'
    i = ticket%4
    far = 0 if job['left'] < gx else 1
    col = far if i%2 == 0 else 1-far
    x = job['left'] + col*7
    yield from R.go(d, [('y',W.TRANSPORT_Y)])
    released = False
    for action in R.go(d, [('x',x)]):
        yield action
        if not released and (d.rect[0] >= gx+geom.CH_W+geom.MIN_GAP
                             or d.rect[0]+4 <= gx-geom.MIN_GAP):
            job['entry'] += 1; released = True
    if not released:
        job['entry'] += 1
    yield from R.go(d, [('y',179 if i < 2 else 172)])
    job['arrived'].append(d)
    if i == 3:
        while len(job['arrived']) < (ticket//4+1)*4:
            yield 'wait'
        yield from _assemble(S,job,job['arrived'][-4:],ticket//4)
    else:
        while S.drops.get(d.name) is d:
            yield 'wait'


def send(S,pieces,gx,blobs,plan,jobs):
    import waste_grid as W
    assert gx == 46 and set(p['port'] for p in plan['specs'].values()) == {1,3}
    assert all(d.rect[2:] == (4,4) and d.vol == .75 for ln,d in pieces)
    pool = W._pool(S)
    if not hasattr(S,'a06_collect_jobs'):
        S.a06_collect_jobs = {}
    pending = sorted(pieces,key=lambda t:-t[0].r)
    for (ln,d),(key,r,pos) in zip(pending,plan['assign']):
        if key not in jobs:
            port = plan['specs'][key]['port']
            # Near staging ends at X42, four cells from the X46 channel.
            # It shortens the old left-side detour without occupying the exit.
            job = jobs[key] = dict(port=port,left=53 if port==3 else 31,
                final_x=geom.HOLE_X[port]-8,blobs=blobs,group=0,entry=0,count=0,
                arrived=[],docked=[],event=dict(port=port,blocks=[],docks=[]))
            S.a06_collect_jobs[port] = job
        job = jobs[key];ticket=job['count'];job['count']+=1
        assert ticket < 16
        d.S=S;d.allow=();d.waste_fk=job['port']
        d.waste_grid_packet=d.waste_pipeline_clearance=True
        pool.items.append((d,_tile_path(S,job,d,ticket,gx),R._door_rule(gx)))
    S.step(geom.HOLD_MS,changed=[])
    return [d for ln,d in pending]
