"""废液原形逐格移到TEC下方；整批选最少孔，整排拼接，不旋转、不重塑。"""
from functools import lru_cache
import collections
import geom,contour
from scene import Drop

PORTS=(4,3,2,1)
PACKET_AREAS=frozenset((4,6,8,10,12,16,18,20,24))
TRANSPORT_Y=172  # CSV内所有TEC的下边界=23+149；禁止提前横穿其他列。

def layout(w,h,count):
    """固定小滴长宽，用完整行列拼成一个孔旁的紧凑矩形；放不下返回None。"""
    assert 0<w<=geom.CH_W and h>0 and count>0
    options=[]
    for columns in range(1,count+1):
        if count%columns:continue
        rows=count//columns;W,H=columns*w,rows*h
        if W<=40 and H<=189-TRANSPORT_Y and max(W,H)<=2*min(W,H):
            options.append((max(W,H)/min(W,H),max(W,H),columns,rows))
    return min(options)[2:] if options else None

def plan_batch(w,h,count):
    """先最少孔，其次均衡各孔数量；不为了均分而额外开孔。"""
    feasible={n:layout(w,h,n) for n in range(1,count+1)}
    @lru_cache(None)
    def solve(n):
        if not n:return ()
        choices=[]
        for size,shape in feasible.items():
            if size>n or shape is None:continue
            rest=solve(n-size)
            if rest is not None:choices.append(tuple(sorted((size,*rest),reverse=True)))
        return min(choices,key=lambda sizes:(len(sizes),max(sizes),sum(x*x for x in sizes),sizes)) if choices else None
    sizes=solve(count)
    assert sizes is not None and len(sizes)<=len(PORTS),('本批无法在有限F孔旁拼出紧凑矩形',w,h,count)
    return [dict(port=i+1,count=n,columns=feasible[n][0],rows=feasible[n][1]) for i,n in enumerate(sizes)]

def area(d):return len(d.target_cells())

def flush(S,blobs):
    import rigid as R
    items=[(k,blobs[k]) for k in PORTS if blobs.get(k)]
    if not items:return
    # 只逐格平移到孔中央，不改变已经自然拼成的矩形。
    R.run(S,[(d,R.go(d,[('x',geom.HOLE_X[k]-d.rect[2]//2)])) for k,d in items])
    for k,d in items:
        W,H=d.rect[2:]
        assert area(d)==W*H and max(W,H)/min(W,H)<=2 and d.rect[1]+H==189
    S.step(geom.HOLD_MS,changed=[])
    info=dict(hole='/'.join(f'F{k}' for k,d in items),volume_ul=round(sum(d.vol for k,d in items),3),
              port_volumes={f'F{k}':d.vol for k,d in items},drop_names=[d.name for k,d in items],
              rects=[list(d.rect) for k,d in items],kind='原形小矩形自然拼接汇液',rounding_gap_cells=.5)
    S.manual_aspirations.append((S.relays[-1],info))
    for k,d in items:S.remove(d.name);blobs[k]=None
    S.step(geom.HOLD_MS,changed=[])

# ── 流水线排废（R04/A05）：底部运输作为后台进程，与反应区内切块在同一帧并行 ───────────
class _Pool:
    def __init__(self):self.items=[];self.moved_last=False

def _pool(S):
    pool=getattr(S,'waste_pool',None)
    if pool is None:
        pool=S.waste_pool=_Pool();S.background=_advance
    return pool

def _advance(S):
    """后台每帧推进一步：每个小滴/整排至多做一个动作；位置不满足间距就原地等。不记录帧（由当前 S.step 记录）。"""
    import rigid as R
    pool=S.waste_pool;moved=[];dur=0
    for item in list(pool.items):
        d,g,rule=item
        if S.drops.get(d.name) is not d:
            pool.items.remove(item);continue
        if getattr(d,'pending',None) is None:
            try:d.pending=next(g)
            except StopIteration:d.pending='done'
        act=d.pending
        if act=='done':
            pool.items.remove(item);d.pending=None;continue
        if act=='wait':
            d.pending=None;continue
        if isinstance(act,tuple) and act[0]=='call':
            act[1]();d.pending=None;dur=max(dur,act[2]);moved.extend(n for n in act[3] if n in S.drops);continue
        dx,dy=R.DIRS[act];cand=(d.rect[0]+dx,d.rect[1]+dy,d.rect[2],d.rect[3])
        if R.rect_ok(S,d.name,cand,frozenset(getattr(d,'allow',()))) and (rule is None or rule(d,cand)):
            d.rect=cand;d.pending=None;moved.append(d.name)
    pool.moved_last=bool(moved)
    touch=[(d.name,a) for d,g,rule in pool.items if d.name in S.drops for a in getattr(d,'allow',())]
    return moved,touch,dur

def _drain(S,until,limit=400):
    """只推进后台，直到条件满足；长时间无移动视为排队死锁。"""
    stall=0
    while not until():
        S.step(geom.STEP_MS,changed=[])
        stall=0 if S.waste_pool.moved_last else stall+1
        if stall>limit:
            info=[(d.name,d.rect,d.pending) for d,g,r in S.waste_pool.items[:6]]
            raise RuntimeError(f'流水线排废死锁 @step{S.nsteps}: {info}')

def _make_row(S,job,r):
    ds=sorted(job['arrived'][r],key=lambda q:q.slot)
    union=frozenset().union(*(q.target_cells() for q in ds));rc=contour.bounds(union)
    assert len(union)==rc[2]*rc[3] and contour.connected(union),'整排未自然组成完整矩形'
    vol=round(sum(q.vol for q in ds),2)
    for q in ds:S.remove(q.name)
    row=S.add(Drop(f"WROW{job['port']}_{r}",rc,vol));row.set_cells(union);row.allow=(f"BLOB{job['port']}",)
    row.tiles=len(ds);row.tile_area=ds[0].tile_area
    S.waste_pool.items.append((row,_row_path(S,row,job,r),None))

def _row_path(S,row,job,r):
    import rigid as R
    yield from R.go(row,[('x',job['final_x'])])
    while job['docked'][0]!=r:yield 'wait'            # 自下而上逐排停靠
    yield from R.go(row,[('y',189-(r+1)*job['h'])])
    yield ('call',lambda:_dock(S,row,job,r),geom.HOLD_MS,[f"BLOB{job['port']}"])

def _dock(S,row,job,r):
    fk=job['port'];blobs=job['blobs'];host=blobs.get(fk);cells=row.target_cells()
    if host:
        union=host.target_cells()|cells
        assert len(union)==area(host)+area(row) and contour.connected(union),'汇液未接触或重叠'
        S.remove(row.name);host.vol=round(host.vol+row.vol,2)
        host.rect=contour.bounds(union);host.set_cells(union);host.tile_count+=row.tiles
    else:
        S.remove(row.name);host=blobs[fk]=S.add(Drop(f'BLOB{fk}',contour.bounds(cells),row.vol))
        host.set_cells(cells);host.tile_area=row.tile_area;host.tile_shape=(job['w'],job['h']);host.tile_count=row.tiles
    assert area(host)==host.rect[2]*host.rect[3],'整排汇液终态缺角'
    for c in range(row.tiles):
        S.waste_grid_events.append(dict(port=fk,tile_area=row.tile_area,slot=r*job['columns']+c,row=r,column=c,
            step=S.nsteps,merged_without_reshape=True,tile_shape=[job['w'],job['h']],transport_y=TRANSPORT_Y,
            row_ready=True,row_piece_count=row.tiles,actual_area=area(host),rect=list(host.rect),
            whole_port_pipeline=True))
    job['event']['row_merges'].append(S.nsteps);job['docked'][0]+=1
    if job['docked'][0]==job['rows']:job['event']['end_frame']=S.nsteps

def _tile_path(S,d,job,t,r,col,entry,gx):
    """小滴：按票号单列进通道 → 下到Y172 → 去本排就绪位（前方有小滴就按间距排队）→ 本排到齐后下一帧同步靠拢 → 合成整排。"""
    import rigid as R
    h,w=job['h'],job['w']
    def release():
        if entry[0]==t:entry[0]+=1
    origin_y=d.rect[1]
    # 各道小块同时横移进废液通道：相邻两道相距9行，小块高4–6行，通道内间距≥3；
    # 整列一起下行，逐个到达底部运输带。出口仍受 _door_rule 保护。
    job['event']['entries'].append(dict(ticket=t,frame=S.nsteps))
    yield from R.go(d,[('x',gx)])
    if job['under_exit']:
        # 汇液框位于通道出口下方（F1）：整排要横移到出口下方再停靠，后一排在通道内带上方等前一排停靠完。
        hold_y=TRANSPORT_Y-h-geom.PREFERRED_GAP
        for a in R.go(d,[('y',max(origin_y,hold_y))]):
            yield a
            if d.rect[1]>=origin_y+h+geom.MIN_GAP:release()
        while job['docked'][0]<r:yield 'wait'
    for a in R.go(d,[('y',TRANSPORT_Y)]):
        yield a
        if d.rect[1]>=origin_y+h+geom.MIN_GAP:release()
    for a in R.go(d,[('x',job['stage_left']+col*(w+geom.CUT_GAP))]):
        yield a
        if d.rect[0]>=gx+geom.CH_W+geom.MIN_GAP:release()
    release()
    job['arrived'][r].append(d)
    if len(job['arrived'][r])==job['columns']:job['ready'][r]=S.nsteps
    while r not in job['ready'] or S.nsteps<=job['ready'][r]:yield 'wait'
    ds=sorted(job['arrived'][r],key=lambda q:q.slot);i=ds.index(d)
    d.allow=(f"BLOB{job['port']}",*(q.name for q in ds if q is not d))
    yield from R.go(d,[('x',job['stage_left']+i*w)])
    job['contracted'][r]+=1
    if job['contracted'][r]==job['columns']:job['merge_ready'][r]=S.nsteps
    while r not in job['merge_ready'] or S.nsteps<=job['merge_ready'][r]:yield 'wait'
    if i==0:
        yield ('call',lambda:_make_row(S,job,r),geom.HOLD_MS,[f"WROW{job['port']}_{r}"])

def _send_pipeline(S,pieces,gx,blobs,ports,collection_group):
    """登记一批小滴到后台流水线后立即返回。
    规则不变：小滴不变形、通道内单列；每排在就绪区到齐后同步靠拢成完整矩形，自下而上逐排停靠。
    改变：后一排不等前一排停靠完即可出发，在就绪区左侧按间距排队，前一排离开后进入就绪区。"""
    import rigid as R
    pool=_pool(S)
    pending=sorted(pieces,key=lambda t:-t[0].r)      # 票号按到达底部的先后：最深的道最先到达
    for ln,d in pending:
        cells=d.target_cells();rect=contour.bounds(cells)
        assert len(cells)==rect[2]*rect[3],('排废小滴必须是完整矩形',d.name,rect,len(cells))
        d.rect=rect;d.set_cells(cells);d.waste_grid_packet=True
    S.step(geom.HOLD_MS,changed=[])
    S.waste_grid_events=getattr(S,'waste_grid_events',[])
    S.waste_batch_events=getattr(S,'waste_batch_events',[])
    S.waste_parallel_events=getattr(S,'waste_parallel_events',[])
    assert len({d.rect[2:] for ln,d in pending})==1,'整步调度的一批必须同形'
    assert all(k in PORTS and not blobs.get(k) for k in ports),'调度给出的收集孔已占用'
    w,h=pending[0][1].rect[2:]
    plans=plan_batch(w,h,len(pending))
    assert len(plans)==len(ports),'整步调度的孔数与本批紧凑块数不一致'
    plans=[dict(p,port=k) for p,k in zip(plans,reversed(ports))]
    S.waste_batch_events.append(dict(tile_shape=[w,h],count=len(pending),minimum_ports=len(plans),plans=plans,
                                    collection_group=collection_group,ports=[p['port'] for p in plans],
                                    flush_after=False,whole_port_pipeline=True))
    entry=[0];ticket=0;tiles=[]
    for plan in reversed(plans):                     # 先远孔：远孔小滴先进通道，后续小滴始终在其后方
        fk=plan['port'];columns=plan['columns']
        port_items=pending[:plan['count']];del pending[:plan['count']]
        W=columns*w;stage_width=W+(columns-1)*geom.CUT_GAP
        job=dict(port=fk,columns=columns,rows=plan['rows'],w=w,h=h,blobs=blobs,
                 final_x=geom.HOLE_X[fk]-W//2,stage_left=max(gx+geom.CH_W+geom.MIN_GAP,geom.HOLE_X[fk]-stage_width//2),
                 arrived={r:[] for r in range(plan['rows'])},ready={},contracted={r:0 for r in range(plan['rows'])},
                 merge_ready={},docked=[0],
                 event=dict(port=fk,scheduled_drops=len(port_items),start_frame=S.nsteps,entries=[],row_merges=[]))
        job['under_exit']=job['final_x']<gx+geom.CH_W+geom.PREFERRED_GAP
        assert job['stage_left']+stage_width<=geom.GRID_W
        S.waste_parallel_events.append(job['event'])
        for i,(ln,d) in enumerate(port_items):
            r=i//columns;col=columns-1-i%columns
            d.S=S;d.waste_fk=fk;d.slot=r*columns+col;d.tile_area=area(d)
            d.waste_pipeline_clearance=True;assert d.tile_area in PACKET_AREAS
            d.tile_columns=columns;d.entry_ticket=ticket;d.allow=(f'BLOB{fk}',)
            pool.items.append((d,_tile_path(S,d,job,ticket,r,col,entry,gx),R._door_rule(gx)))
            tiles.append(d);ticket+=1
    return tiles

def send(S,pieces,gx,blobs,final=False,ports=None,flush_after=True,parallel=False,collection_group=None):
    """ports=None：本批独立收集（先吸走旧废液，用最近的孔，送完即吸）。
    ports=[…]：由 bites 的整步调度给出本批各块的孔号（由远到近），不提前吸液，flush_after 决定送完后是否停靠吸液。"""
    import rigid as R
    pending=list(sorted(pieces,key=lambda t:-t[0].r))
    for ln,d in pending:
        cells=d.target_cells();rect=contour.bounds(cells)
        assert len(cells)==rect[2]*rect[3],('排废小滴必须是完整矩形',d.name,rect,len(cells))
        d.rect=rect;d.set_cells(cells);d.waste_grid_packet=True
    # 118格体系切出的10格子滴可能沿用2×6的描述框，实际上电是2×5。
    # 先记录一帧完全相同的电极，再开始刚体移动，避免旧描述框触发隐式形变。
    S.step(geom.HOLD_MS,changed=[])
    S.waste_grid_events=getattr(S,'waste_grid_events',[])
    S.waste_batch_events=getattr(S,'waste_batch_events',[])
    if ports is None:
        flush(S,blobs)
    else:
        assert all(k in PORTS and not blobs.get(k) for k in ports),'调度给出的收集孔已占用'
        assert len({d.rect[2:] for ln,d in pending})==1,'整步调度的一批必须同形'
    while pending:
        w,h=pending[0][1].rect[2:]
        group=[t for t in pending if t[1].rect[2:]==(w,h)]
        pending=[t for t in pending if t[1].rect[2:]!=(w,h)]
        plans=plan_batch(w,h,len(group))
        if ports is not None:
            assert len(plans)==len(ports),'整步调度的孔数与本批紧凑块数不一致'
            # plans 按 reversed 顺序处理：最后一块最先送，分到最远的孔。
            plans=[dict(p,port=k) for p,k in zip(plans,reversed(ports))]
        S.waste_batch_events.append(dict(tile_shape=[w,h],count=len(group),minimum_ports=len(plans),plans=plans,
                                        collection_group=collection_group,ports=[p['port'] for p in plans],
                                        flush_after=flush_after,whole_port_pipeline=parallel))
        # 先处理远孔，完成后不再穿过其停靠矩形。
        for plan in reversed(plans):
            fk=plan['port'];columns=plan['columns'];rows=plan['rows'];W=columns*w
            port_items=group[:plan['count']];del group[:plan['count']]
            final_x=geom.HOLE_X[fk]-W//2
            stage_width=W+(columns-1)*geom.CUT_GAP
            stage_left=max(gx+geom.CH_W+geom.PREFERRED_GAP,geom.HOLE_X[fk]-stage_width//2)
            assert stage_left+stage_width<=geom.GRID_W,'整排就绪区超出芯片'
            for row_index in range(rows):
                ds=[];batch=[];entry_front=[0]
                for ticket,(ln,d) in enumerate(port_items[row_index*columns:(row_index+1)*columns]):
                    col=columns-1-ticket  # 从右至左就绪，不穿越先到的液滴。
                    d.S=S;d.waste_fk=fk;d.slot=row_index*columns+col;d.tile_area=area(d)
                    assert d.tile_area in PACKET_AREAS
                    d.tile_columns=columns;d.entry_ticket=ticket;d.allow=(f'BLOB{fk}',)
                    stage_x=stage_left+col*(w+geom.CUT_GAP)
                    def path(d=d,stage_x=stage_x):
                        origin_y=d.rect[1]
                        while entry_front[0]!=d.entry_ticket:yield 'wait'
                        def release_entry():
                            if entry_front[0]==d.entry_ticket:entry_front[0]+=1
                        yield from R.go(d,[('x',gx)])
                        for action in R.go(d,[('y',TRANSPORT_Y)]):
                            yield action
                            if d.rect[1]>=origin_y+h+geom.MIN_GAP:release_entry()
                        for action in R.go(d,[('x',stage_x)]):
                            yield action
                            if d.rect[0]>=gx+geom.CH_W+geom.MIN_GAP:release_entry()
                        release_entry()
                    ds.append(d);batch.append((d,path()))
                R.run(S,batch,rule=R._door_rule(gx))
                ds.sort(key=lambda d:d.slot)
                for d in ds:d.allow=(f'BLOB{fk}',*(q.name for q in ds if q is not d))
                # 所有小框到齐后同步逐格靠拢，完整一排先横向对齐，再往下接入。
                R.run(S,[(d,R.go(d,[('x',stage_left+i*w)])) for i,d in enumerate(ds)])
                union=frozenset().union(*(d.target_cells() for d in ds));rc=contour.bounds(union)
                assert len(union)==rc[2]*rc[3] and contour.connected(union),'整排未自然组成完整矩形'
                vol=round(sum(d.vol for d in ds),2)
                for d in ds:S.remove(d.name)
                row=S.add(Drop(f'WROW{fk}',rc,vol));row.set_cells(union);row.allow=(f'BLOB{fk}',)
                S.step(geom.HOLD_MS,changed=[row.name],touching=[(row.name,f'BLOB{fk}')])
                R.run(S,[(row,R.go(row,[('x',final_x),('y',189-(row_index+1)*h)]))])
                host=blobs.get(fk);cells=row.target_cells()
                if host:
                    union=host.target_cells()|cells
                    assert len(union)==area(host)+area(row) and contour.connected(union),'汇液未接触或重叠'
                    S.remove(row.name);host.vol=round(host.vol+row.vol,2)
                    host.rect=contour.bounds(union);host.set_cells(union);host.tile_count+=len(ds)
                else:
                    S.remove(row.name);host=blobs[fk]=S.add(Drop(f'BLOB{fk}',contour.bounds(cells),row.vol))
                    host.set_cells(cells);host.tile_area=ds[0].tile_area;host.tile_shape=(w,h);host.tile_count=len(ds)
                assert area(host)==host.rect[2]*host.rect[3],'整排汇液终态缺角'
                for tile in ds:
                    S.waste_grid_events.append(dict(port=fk,tile_area=tile.tile_area,slot=tile.slot,
                        row=row_index,column=tile.slot%columns,step=S.nsteps,merged_without_reshape=True,
                        tile_shape=[w,h],transport_y=TRANSPORT_Y,row_ready=True,row_piece_count=len(ds),
                        actual_area=area(host),rect=list(host.rect)))
                S.step(geom.HOLD_MS,changed=[host.name])
        assert not group
        if flush_after:flush(S,blobs)

def _take_width(index,n,collect_three):
    return (4,4,3,4,3,3,4)[index] if collect_three else (2 if n==118 else geom.CH_W)

def cut_rounds(items,collect_three=False):
    """按 bites 的切法（不改动）逐批预测排废小滴形状；只用电极格数计算，不产生帧。"""
    state={ln.id:(frozenset(d.target_cells()),d.rect[2],d.rect[3]) for ln,d in items}
    rounds=[];index=0
    while state:
        shapes=collections.Counter()
        for k,(cells,W,H) in list(state.items()):
            n=len(cells);b=contour.bounds(cells)
            if n in PACKET_AREAS and b[2]<=geom.CH_W:
                shapes[b[2:]]+=1;del state[k];continue
            wr=_take_width(index,n,collect_three);take=10 if n==118 else wr*H
            right=contour.fill((0,0,wr,H),take);left=contour.fill((0,0,W-wr,H),n-take)
            shapes[contour.bounds(right)[2:]]+=1;state[k]=(left,W-wr,H)
        rounds.append(shapes);index+=1
    return rounds

def _block_areas(w,h,plan,port,gx,parallel):
    """一块汇液框在孔旁的终态矩形，以及整排就绪/靠拢时在 Y172 占用的横向范围。"""
    columns,rows=plan['columns'],plan['rows'];W,H=columns*w,rows*h
    final=(geom.HOLE_X[port]-W//2,189-H,W,H)
    width=W+(columns-1)*geom.CUT_GAP
    left=max(gx+geom.CH_W+(geom.MIN_GAP if parallel else geom.PREFERRED_GAP),geom.HOLE_X[port]-width//2)
    x0=min(left,final[0]);x1=max(left+width,final[0]+W)
    return final,(x0,TRANSPORT_Y,x1-x0,h)

def _place(groups,gx,parallel):
    """groups：本次停靠内按送出顺序排列的 [(w,h,plan)]。在 F1–F4 中选递减孔号，
    后送的块（更近的孔）就绪区与已停靠的远孔汇液框保持间距；孔号总和最小者优先（离废液空隙最近）。"""
    import itertools
    from scene import _gap
    clearance=geom.MIN_GAP if parallel else geom.PREFERRED_GAP
    for combo in sorted(itertools.combinations(sorted(PORTS,reverse=True),len(groups)),key=lambda c:(sum(c),c)):
        placed=[];ok=True
        for (w,h,plan),port in zip(groups,combo):
            final,stage=_block_areas(w,h,plan,port,gx,parallel)
            if any(_gap(stage,b)<clearance or _gap(final,b)<clearance for b in placed):ok=False;break
            placed.append(final)
        if ok:return list(combo)
    return None

def port_schedule(rounds,final=True,gx=None,parallel=False):
    """每批仍按原紧凑块布局（plan_batch 的最少块数）；各批连续分配到 F1–F4，
    一次停靠放入尽可能多的整批，放不下下一整批时才停靠吸液。
    同一次停靠内先送的块放远孔，后送的块不经过已停靠的汇液框；孔位选离废液空隙最近的可行组合。"""
    gx=geom.gap_x(1) if gx is None else gx
    batches=[]
    for shapes in rounds:
        assert len(shapes)==1,'整步调度要求每批小滴同形'
        (w,h),count=next(iter(shapes.items()))
        plans=plan_batch(w,h,count)
        batches.append([(w,h,plan) for plan in reversed(plans)])   # send 按 reversed(plans) 送出
    stops=[];i=0
    while i<len(batches):
        best=None;j=i
        while j<len(batches):
            groups=[g for b in batches[i:j+1] for g in b]
            ports=_place(groups,gx,parallel) if len(groups)<=len(PORTS) else None
            if ports is None:break
            best=(j,ports);j+=1
        assert best,('单批无法放入 F1–F4',batches[i])
        stops.append((list(range(i,best[0]+1)),best[1]));i=best[0]+1
    schedule=[None]*len(rounds)
    for number,(members,ports) in enumerate(stops,1):
        k=0
        for r in members:
            n=len(batches[r]);schedule[r]=dict(ports=ports[k:k+n],stop=number,
                flush=r==members[-1] and (final or number<len(stops)));k+=n
    return schedule

def bites(S,items,blobs,final=True,pellets=None,collect_three=False,collect_a05=False):
    """主排废块按4格通道宽度切；118格先分出右侧10格余量，余108格完整。"""
    import rigid as R
    items=list(items);lanes={ln.id:ln for ln,d in items};rem={ln.id:d for ln,d in items};index=0
    gx=geom.gap_x(geom.waste_gap(items[0][0].c))
    if collect_three:
        assert len(items)==16 and all(ln.c==1 and area(d)==100 for ln,d in items),'三批合组仅用于单列R04的5µL液相'
        assert not any(blobs.values()),'R04合批开始前必须无未吸出的旧废液'
    if collect_a05:
        assert len(items)==16 and all(ln.c==1 and area(d)==30 for ln,d in items)
        assert not any(blobs.values())
    # 100格按25×4切，每次4×4；30格按6×5切成4×5+2×5。
    # 形变与移液分别记录，先为变宽的母框让出反应区内空间。
    big=[(ln,d) for ln,d in items if area(d)==100]
    if big:
        # 16道同步：先同时逐格平移到 X0+9，再同时等面积整形为 25×4（移动与形变不在同一帧）。
        R.run(S,[(d,R.go(d,[('x',ln.X0+9)])) for ln,d in big])
        R.reshape_many(S,[(d,(ln.X0+9,ln.Y0,25,4)) for ln,d in big])
    # 整步统一调度：先按切法预测每批小滴，再把各批依次分到 F 孔，四孔用尽才停靠吸液。
    assert not any(blobs.get(k) for k in PORTS),'排废调度开始前必须无未吸出的旧废液'
    rounds=cut_rounds(items,collect_three)
    schedule=port_schedule(rounds,final,gx,True)
    S.waste_schedule_events=getattr(S,'waste_schedule_events',[])
    S.waste_schedule_events.append(dict(rounds=[dict(r) for r in rounds],schedule=schedule,step=S.nsteps))
    while rem:
        touch=[(d.name,pellets[k].name) for k,d in rem.items()] if pellets else []
        R.run(S,[(d,R.go(d,[('x',lanes[k].X0+geom.LANE_L-geom.CUT_GAP//2-d.rect[2])])) for k,d in rem.items()])
        specs=[];keys=[];pieces=[];gone=[]
        for k,d in rem.items():
            ln=lanes[k];n=area(d)
            if n in PACKET_AREAS and contour.bounds(d.target_cells())[2]<=geom.CH_W:
                # 仅调整描述包围框，不扩展实际电极格。
                cells=d.target_cells();d.rect=contour.bounds(cells);d.set_cells(cells)
                pieces.append((ln,d));gone.append(k);continue
            H=d.rect[3]
            wr=_take_width(index,n,collect_three)
            take=10 if n==118 else wr*H
            assert n>take and H in (4,5,6)
            assert wr*H>=take
            wl=d.rect[2]-wr;l,r=R.split_x(d,wl,wr)
            assert n-take<=wl*H
            v=take*geom.CELL_UL if d.vol else 0
            specs.append((d,(l,r),(round(d.vol-v,2),round(v,2)),(d.name,f'WG{k}_{index}')));keys.append(k)
        if specs:
            for k,(left,right) in zip(keys,R.split_many(S,specs,touching=touch)):
                rem[k]=left;pieces.append((lanes[k],right))
        for k in gone:del rem[k]
        S.step(geom.HOLD_MS,changed=[d.name for ln,d in pieces],touching=touch)
        shapes=collections.Counter(contour.bounds(d.target_cells())[2:] for ln,d in pieces)
        assert shapes==rounds[index],('排废切块与整步调度预测不一致',index,dict(shapes),dict(rounds[index]))
        plan=schedule[index]
        # 流水线（全部排废步骤）：本批登记到后台；本批全部离开反应区和进液通道（到达底部运输带或已汇入）后，
        # 立即切下一批，与本批在底部的行进、靠拢、停靠并行。本次停靠的最后一批送完后才吸液。
        tiles=_send_pipeline(S,pieces,gx,blobs,plan['ports'],plan['stop'])
        if plan['flush']:
            _drain(S,lambda:not S.waste_pool.items);flush(S,blobs)
        else:
            _drain(S,lambda:all(S.drops.get(d.name) is not d or d.rect[1]>=TRANSPORT_Y for d in tiles))
        index+=1
    if pellets:
        for k,p in pellets.items():
            S.remove(p.name);p.name=lanes[k].name;p.allow=();p.magnet_only=(p.vol==0);S.add(p)
    if final:flush(S,blobs)

def egress_pieces(S,items,blob_state,final=True,collect_a05=False):
    items=list(items)
    assert all(d.vol>0 and not getattr(d,'magnet_only',False) for ln,d in items),'磁珠排废必须先加入真实载液并重悬，禁止0µL磁珠块自行搬运'
    for ln,d in items:d.magnet_only=False
    bites(S,items,blob_state,final,collect_a05=collect_a05)

def supernatant_bites(S,lanes,blob_state,pellet_vol,final=True,collect_three=False):
    assert pellet_vol==0,'当前方案按用户要求仅留磁珠，不保留计划液相'
    lanes=list(lanes);pellets={};items=[]
    for ln in lanes:
        d=S.get(ln.name);S.remove(d.name);d.name=f'LIQ{ln.id}';S.add(d)
        p=S.add(Drop(f'PEL{ln.id}',ln.rect(ln.mag-1,3),0));p.magnet_only=True;pellets[ln.id]=p
        d.allow=(p.name,);p.allow=(d.name,);items.append((ln,d))
    S.step(geom.HOLD_MS,touching=[(d.name,pellets[ln.id].name) for ln,d in items])
    bites(S,items,blob_state,final,pellets,collect_three=collect_three)
