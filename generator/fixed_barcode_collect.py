"""B07: overlap arrival, pooling and short-edge-first transport to F1."""
import contour
import geom
import rigid as R
from scene import Drop

BOX = (48, 172, 12, 16)


def join(S, drops, name):
    cells = frozenset().union(*(d.target_cells() for d in drops))
    box = contour.bounds(cells)
    assert len(cells) == sum(len(d.target_cells()) for d in drops) == box[2]*box[3]
    volume = round(sum(d.vol for d in drops), 10)
    for d in drops:
        S.remove(d.name)
    d = S.add(Drop(name, box, volume))
    d.electrode_area = len(cells)
    d.waste_grid_packet = d.waste_pipeline_clearance = True
    d.set_cells(cells)
    return d


def join_pairs(S, drops, prefix):
    """Only the right-hand rectangle moves, along its horizontal long axis."""
    pairs = list(zip(drops[::2], drops[1::2]))
    for left, right in pairs:
        assert left.rect[1:] == right.rect[1:]
        assert right.rect[2] >= right.rect[3] or right.rect[2:] == (3,4)
        right.allow = (left.name,)
    R.run(S, [(right, R.go(right, [('x', left.rect[0]+left.rect[2])]))
              for left, right in pairs])
    combined = [join(S, pair, f'{prefix}_{i}') for i, pair in enumerate(pairs)]
    S.step(geom.HOLD_MS)
    return combined


def accepts(pieces, plan):
    return (plan.get('packed') and plan['ports'] == [1] and len(pieces) == 16
            and all(d.name.startswith('BKWA') and d.rect[2:] == (3, 4)
                    and abs(d.vol - .6) < 1e-8 for _, d in pieces)
            and all(s.get('strict_staging') for s in plan['specs'].values()))


def send(S, pieces, gx, blobs, plan):
    import waste_grid as W
    assert gx == 46 and not blobs.get(1)
    pending = sorted(pieces, key=lambda item: -item[0].r)
    state = {'entry': 0, 'ready': set()}
    staged = []

    def stage(d, ticket, x, y):
        yield from R.go(d, [('x', gx), ('y', 164)])
        while state['entry'] != ticket:
            yield 'wait'
        yield from R.go(d, [('y', 172)])
        released = False
        for action in R.go(d, [('x', x)]):
            yield action
            if not released and d.rect[0] >= gx + geom.CH_W + geom.MIN_GAP:
                state['entry'] += 1
                released = True
        assert released
        yield from R.go(d, [('y', y)])
        state['ready'].add(ticket)

    jobs = []
    for ticket, (_, d) in enumerate(pending):
        slot=(7-ticket) if ticket<8 else (23-ticket)
        x, y = 60 + (9 if getattr(S,'strict_transport',False) else 7) * slot, 180
        d.barcode_stage_x=x
        d.S = S
        d.native_packet = d.waste_grid_packet = d.waste_pipeline_clearance = True
        d.allow = ()
        staged.append(d)
        from transport_spacing import queue_exit
        jobs.append((d, queue_exit(S,d,gx,stage(d,ticket,x,y))))
    pool=W._pool(S)
    pool.items.extend((d,g,R._door_rule(gx)) for d,g in jobs)
    W._drain(S,lambda:set(range(4))<=state['ready'])

    S.waste_grid_events = getattr(S, 'waste_grid_events', [])
    S.barcode_batch_events = []
    staged.sort(key=lambda d: d.barcode_stage_x)
    host = None
    def quarter(ds,prefix):
        pairs=join_pairs(S,ds,prefix+'_PAIR')
        q=join_pairs(S,pairs,prefix+'_QUARTER')[0]
        assert q.rect[2:]==(12,4)
        R.reshape_many(S,[(q,(*q.rect[:2],8,6))],dur=4000)
        return q

    S.barcode_overlap_events=[]
    S.barcode_pipeline_events=[]
    for batch in range(2):
        first=set(range(batch*8,batch*8+4))
        W._drain(S,lambda:first<=state['ready'])
        S.barcode_pipeline_events.append(dict(kind='prepare_quarter',batch=batch+1,
            frame=S.nsteps,staged_tickets=sorted(state['ready'])))
        far=quarter(staged[batch*8+4:batch*8+8],f'B07_FAR{batch}')
        target=staged[batch*8].barcode_stage_x+8
        pool.items.append((far,R.go(far,[('x',target)]),None))
        all_eight=set(range(batch*8,batch*8+8))
        W._drain(S,lambda:all_eight<=state['ready'])
        before=far.rect[0]
        near=quarter(staged[batch*8:batch*8+4],f'B07_NEAR{batch}')
        S.barcode_overlap_events.append(dict(batch=batch+1,frame=S.nsteps,
            far_quarter_cells_moved_during_near_preparation=before-far.rect[0]))
        far.allow=(near.name,)
        W._drain(S,lambda:far.rect[0]==target)
        half=join(S,[near,far],f'B07_HALF{batch}')
        S.step(geom.HOLD_MS)
        assert half.rect[2:] == (16,6) and half.vol == 4.8
        R.reshape(S, half, (*half.rect[:2],12,8), dur=4000)
        dock_x = 48 + 6*batch
        if batch==0:
            R.run(S,[(half,R.go(half,[('x',60)]))])
            W._drain(S,lambda:all(d.rect[0]>=60 for d in staged[8:]))
            S.barcode_pipeline_events.append(dict(kind='mouth_cleared',frame=S.nsteps,
                remaining_drop_min_x=min(d.rect[0] for d in staged[8:])))
        half.allow = ('BLOB1',) if host else ()
        R.run(S, [(half,R.go(half,[('x',dock_x)]))])
        # Already at the final location: reshape here, never move the 6x16 bar.
        touch = [(half.name,host.name)] if host else ()
        R.reshape(S, half, (dock_x,172,6,16), dur=4000, touching=touch)
        if host:
            host = join(S,[host,half],'BLOB1')
        else:
            S.remove(half.name)
            half.name='BLOB1'
            host=S.add(half)
            host.allow=()
        host.tile_count=8*(batch+1)
        host.tile_shape,host.tile_area=(3,4),12
        S.step(geom.HOLD_MS)
        S.barcode_batch_events.append(dict(batch=batch+1,frame=S.nsteps,
            actual_volume_ul=4.8,transport_shape=[12,8],dock_column=[dock_x,172,6,16],
            collected_rect=list(host.rect),collected_volume_ul=host.vol))
    S.waste_grid_events.extend(dict(port=1,tile_shape=[3,4],slot=i,
        batch=i//8+1,long_axis_only_transport=True) for i in range(16))
    assert host.rect == BOX and host.target_cells() == contour.fill(BOX, 192)
    blobs[1] = host
    W._drain(S,lambda:not S.waste_pool.items)
    return [d for _, d in pending]
