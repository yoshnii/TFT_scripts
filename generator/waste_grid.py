"""长边近半分废液，精确轮廓逐格转运，底部整排汇集到有限F孔。"""
from functools import lru_cache
import collections
import geom,contour
from scene import Drop

PORTS=(4,3,2,1)
PACKET_AREAS=frozenset((4,6,8,10,12,16,18,20,24))
TRANSPORT_Y=172  # CSV内所有TEC的下边界=23+149；禁止提前横穿其他列。
DOCK_BOTTOM=188

def _compact_cells(n,rect,mirror=False):
    """列高最多相差一格的缺角轮廓；不以包围框面积代替液量。"""
    x,y,w,h=rect;q,r=divmod(n,w)
    assert 0<q<=h and q+(r>0)<=h
    return frozenset((x+(w-1-i if mirror else i),y+j)
                     for i in range(w) for j in range(q+(i<r)))

def _settled_shape(S,d,rect,cells):
    """用户允许的局部等面积形变；调用方单独记录4秒稳定帧。"""
    cells=frozenset(cells);before=d.target_cells();old=contour.bounds(before)
    assert len(cells)==len(before) and contour.connected(cells)
    assert before&cells,'形变不得替代跨空白区转移'
    assert rect[2:]==tuple(reversed(old[2:])) or max(rect[2]/old[2],old[2]/rect[2],rect[3]/old[3],old[3]/rect[3])<=2.1
    d.rect=rect;d.set_cells(cells)
    if cells==before:return
    # Scene的通用逐格形变器不适用于已获准的孔旁短时转形。
    # 仅更新这一滴的形变起点；Scene._record仍检查全部实际电极。
    if id(d) in S.contour_previous:
        S.contour_previous[id(d)]=dict(object=d,rect=d.rect,vol=d.vol,cells=cells)
    S.waste_morph_events=getattr(S,'waste_morph_events',[])
    S.waste_morph_events.append(dict(name=d.name,frame=S.nsteps,area=len(cells),
        before=list(old),after=list(rect),settle_ms=geom.LONG_MS,
        source_cells=sorted(before),target_cells=sorted(cells)))

def _split_widths(n,height=3):
    hs=[h for h in range(1,height+1) if n%h==0 and 2<=n//h<=32 and min((n//h)//2,(n//h+1)//2)>=.4*(n//h)]
    assert hs,('无法以完整矩形近半切开',n,height)
    H=max(hs);w=n//H;wa=w//2;wb=w-wa
    return wa*H,wb*H,wa,wb,H

def _cut_x(ln,n,left_edges,height=3):
    a,b,wa,wb,H=_split_widths(n,height)
    x=max(ln.X0+2,max(left_edges,default=ln.X0-geom.PREFERRED_GAP)+geom.PREFERRED_GAP+2)
    return x,wa+wb

def _leaf_areas(ln,n,left_edges=(),height=3):
    """深度优先，先清空右分支；极窄余量不足时在通道口转形。"""
    if n<=16:return [n]
    x,w=_cut_x(ln,n,left_edges,height)
    if x+w+2>ln.X0+geom.LANE_L+geom.CH_W:
        assert n<=32,('安全带放不下平衡切分',n,left_edges)
        return [n]
    a,b,wa,wb,H=_split_widths(n,height)
    return _leaf_areas(ln,b,(*left_edges,x-2+wa),height)+_leaf_areas(ln,a,left_edges,height)

def _packet_shape(n):
    # 底部以不超过3格高的完整小框原形拼接，通道方向另行保持≤4格宽。
    full=[(n//h,h) for h in range(1,4) if n%h==0]
    if full:return min(full,key=lambda p:max(p)/min(p))
    return (4,(n+3)//4)

def layout(w,h,count):
    """固定小滴长宽，用完整行列拼成一个孔旁的紧凑矩形；放不下返回None。"""
    assert 0<w<=geom.CH_W and h>0 and count>0
    options=[]
    for columns in range(1,count+1):
        if count%columns:continue
        rows=count//columns;W,H=columns*w,rows*h
        if W<=40 and H<=DOCK_BOTTOM-TRANSPORT_Y and max(W,H)<=2*min(W,H):
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
        assert area(d)==W*H and max(W,H)/min(W,H)<=2.5 and d.rect[1]+H==DOCK_BOTTOM
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
            if len(act)>4 and not act[4]():continue
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
    row_spec=job['rows'][r]
    ds=sorted(job['arrived'][r],key=lambda q:q.slot)
    union=frozenset().union(*(q.target_cells() for q in ds));rc=contour.bounds(union)
    assert len(union)==sum(area(q) for q in ds) and contour.connected(union),'整排轮廓重叠或不连通'
    target=(rc[0],rc[1],job['W'],row_spec.get('row_h',row_spec['h']))
    assert len(union)==target[2]*target[3],'整排终态面积不守恒'
    vol=round(sum(q.vol for q in ds),10)
    margin=len(union)-geom.cells(vol)
    assert margin>=0
    for q in ds:S.remove(q.name)
    row=S.add(Drop(f"WROW{job['port']}_{r}",rc,vol));row.electrode_area=len(union);row.set_cells(union);row.allow=(f"BLOB{job['port']}",)
    row.margin_budget=margin
    row.native_fractional_volume=any(getattr(q,'native_fractional_volume',False) for q in ds)
    if union!=contour.fill(target,len(union)):
        _settled_shape(S,row,target,contour.fill(target,len(union)))
    row.tiles=len(ds);row.tile_area=ds[0].tile_area;row.tile_shapes=[q.rect[2:] for q in ds]
    row.tile_areas=[q.tile_area for q in ds]
    S.waste_pool.items.append((row,_row_path(S,row,job,r),None))

def _row_path(S,row,job,r):
    import rigid as R
    spec=job['rows'][r]
    compact_exit=job.get('a06_compact',False)
    strict=job.get('strict_staging',False)
    if (job['under_exit'] and (compact_exit or strict or spec.get('row_h',spec['h'])<spec['h'])
            and spec['dock_y']<TRANSPORT_Y+spec['h']+(geom.MIN_GAP if compact_exit or strict else 0)
            and r+1<len(job['rows'])):
        # F1末两排：先把已齐的整排停在出口左侧，再放后排通过；
        # 不让5格高子滴穿过已升至Y176的4格高汇液排。
        yield from R.go(row,[('x',geom.gap_x(1)-geom.PREFERRED_GAP-row.rect[2])])
        job['lookahead'].add(r+1)
        while len(job['arrived'][r+1])<job['rows'][r+1]['n']:yield 'wait'
    while job['docked'][0]!=r:yield 'wait'            # 自下而上逐排停靠
    yield from R.go(row,[('x',job['final_x'])])
    yield from R.go(row,[('y',job['rows'][r]['dock_y'])])
    yield ('call',lambda:_dock(S,row,job,r),geom.HOLD_MS,[f"BLOB{job['port']}"])

def _dock(S,row,job,r):
    fk=job['port'];blobs=job['blobs'];host=blobs.get(fk);cells=row.target_cells()
    if host:
        union=host.target_cells()|cells
        assert len(union)==area(host)+area(row) and contour.connected(union),'汇液未接触或重叠'
        S.remove(row.name);host.vol=round(host.vol+row.vol,10);host.margin_budget=len(union)-geom.cells(host.vol);host.electrode_area=len(union)
        host.rect=contour.bounds(union);host.set_cells(union);host.tile_count+=row.tiles
    else:
        S.remove(row.name);host=blobs[fk]=S.add(Drop(f'BLOB{fk}',contour.bounds(cells),row.vol))
        host.electrode_area=len(cells);host.margin_budget=row.margin_budget
        host.set_cells(cells);host.tile_area=row.tile_area;host.tile_shape=tuple(row.tile_shapes[0]);host.tile_count=row.tiles
    assert area(host)==host.rect[2]*host.rect[3],'整排汇液终态缺角'
    for c,(shape,tile_area) in enumerate(zip(row.tile_shapes,row.tile_areas)):
        S.waste_grid_events.append(dict(port=fk,tile_area=tile_area,slot=job['slot_base'][r]+c,row=r,column=c,
            step=S.nsteps,merged_without_reshape=True,tile_shape=list(shape),transport_y=TRANSPORT_Y,
            row_ready=True,row_piece_count=row.tiles,actual_area=area(host),rect=list(host.rect),
            whole_port_pipeline=True))
    job['event']['row_merges'].append(S.nsteps);job['docked'][0]+=1
    if job['docked'][0]==len(job['rows']):job['event']['end_frame']=S.nsteps

def _tile_path(S,d,job,t,r,pos,gx):
    """小块：同时进通道 → 下到Y172 → 去本排就绪位（前方有小块就按间距排队）→ 本排到齐后下一帧同步靠拢 → 合成整排。"""
    import rigid as R
    spec=job['rows'][r];h=spec['h'];slot=spec['n']-1-pos      # 先到的小块占右侧槽位
    origin_y=d.rect[1]
    # 各道小块同时横移进废液通道：相邻两道相距9行，小块高4–6行，通道内间距≥3；
    # 整列一起下行，逐个到达底部运输带。出口仍受 _door_rule 保护。
    job['event']['entries'].append(dict(ticket=t,frame=S.nsteps))
    packet_w=spec['widths'][pos]
    n=area(d)
    cw,ch=min(((w,n//w) for w in range(1,5) if n%w==0),key=lambda p:max(p)/min(p))
    if job.get('balanced',False) and d.rect[2:]!=(cw,ch):
        # 在本行和四格通道的并集中横移；只在完全落入通道后加高。
        yield from R.go(d,[('x',gx+geom.CH_W-d.rect[2])])
        target=(gx,origin_y,cw,ch)
        yield ('call',lambda:_settled_shape(S,d,target,_compact_cells(area(d),target)),geom.LONG_MS,[d.name],
               lambda:R.rect_ok(S,d.name,target,frozenset(getattr(d,'allow',()))))
    yield from R.go(d,[('x',gx)])
    if job['under_exit']:
        # 汇液框位于通道出口下方（F1）：整排要横移到出口下方再停靠，后一排在通道内带上方等前一排停靠完。
        hold_y=TRANSPORT_Y-ch-geom.PREFERRED_GAP
        yield from R.go(d,[('y',max(origin_y,hold_y))])
        while job['docked'][0]<r and r not in job['lookahead']:yield 'wait'
    if (cw,ch)!=(packet_w,h):
        yield from R.go(d,[('y',TRANSPORT_Y+h-ch)])
        target=(gx,TRANSPORT_Y,packet_w,h)
        yield ('call',lambda:_settled_shape(S,d,target,contour.fill(target,n)),geom.LONG_MS,[d.name],
               lambda:R.rect_ok(S,d.name,target,frozenset(getattr(d,'allow',()))))
    else:yield from R.go(d,[('y',TRANSPORT_Y)])
    yield from R.go(d,[('x',spec['stage_x'][slot])])
    job['arrived'][r].append(d)
    if len(job['arrived'][r])==spec['n']:job['ready'][r]=S.nsteps
    while r not in job['ready'] or S.nsteps<=job['ready'][r]:yield 'wait'
    ds=job['arrived'][r]
    d.allow=(() if job.get('strict_staging',False) else (f"BLOB{job['port']}",))+tuple(q.name for q in ds if q is not d)
    yield from R.go(d,[('x',spec['contract_x'][slot])])
    job['contracted'][r]+=1
    if job['contracted'][r]==spec['n']:job['merge_ready'][r]=S.nsteps
    while r not in job['merge_ready'] or S.nsteps<=job['merge_ready'][r]:yield 'wait'
    if slot==0:
        yield ('call',lambda:_make_row(S,job,r),geom.LONG_MS if spec.get('row_h',h)!=h else geom.HOLD_MS,[f"WROW{job['port']}_{r}"])

def _row_spec(W,widths,h,port,gx,native=False,row_h=None):
    """一排：widths 为到达顺序的小块宽度；先到者占右侧槽位。就绪位间距 CUT_GAP，靠拢后宽度恰为 W。"""
    n=len(widths);slots=list(reversed(widths))
    sw=sum(widths)+(n-1)*geom.CUT_GAP
    left=max(gx+geom.CH_W+geom.MIN_GAP,geom.HOLE_X[port]-sw//2)
    if native and row_h is not None and row_h<h:
        fx=geom.HOLE_X[port]-W//2
        left=(max(gx+geom.CH_W+geom.MIN_GAP,fx+W+geom.PREFERRED_GAP)
              if fx<gx+geom.CH_W+geom.PREFERRED_GAP else fx-sw-geom.MIN_GAP)
    assert left+sw<=geom.GRID_W
    return dict(h=h,n=n,widths=list(widths),
                stage_x=[left+sum(slots[:s])+s*geom.CUT_GAP for s in range(n)],
                contract_x=[left+sum(slots[:s]) for s in range(n)])

def _job(S,spec,blobs,gx):
    """由调度规格生成一个孔的汇液任务（首次有小块送往该孔时创建）。"""
    rows=[];y=DOCK_BOTTOM;base=[];k=0
    for row in spec['rows']:
        widths,h,*row_h=row;row_h=row_h[0] if row_h else h
        r=_row_spec(spec['W'],widths,h,spec['port'],gx,spec.get('native',False),row_h)
        if spec.get('a06_compact',False):
            sw=sum(widths)+(len(widths)-1)*geom.CUT_GAP
            fx=geom.HOLE_X[spec['port']]-spec['W']//2
            left=(fx+spec['W']+geom.PREFERRED_GAP if fx<gx+geom.CH_W+geom.PREFERRED_GAP
                  else fx-sw-geom.MIN_GAP)
            r['stage_x']=[left+s*(widths[0]+geom.CUT_GAP) for s in range(len(widths))]
            r['contract_x']=[left+s*widths[0] for s in range(len(widths))]
        if spec.get('strict_staging',False):
            left=geom.HOLE_X[spec['port']]+spec['W']//2+geom.PREFERRED_GAP
            r['stage_x']=[left+s*(widths[0]+geom.CUT_GAP) for s in range(len(widths))]
            r['contract_x']=[left+s*widths[0] for s in range(len(widths))]
        y-=row_h;r['row_h']=row_h;r['dock_y']=y;rows.append(r);base.append(k);k+=len(widths)
    job=dict(port=spec['port'],W=spec['W'],rows=rows,blobs=blobs,slot_base=base,
             final_x=geom.HOLE_X[spec['port']]-spec['W']//2,
             arrived={r:[] for r in range(len(rows))},ready={},contracted={r:0 for r in range(len(rows))},
             merge_ready={},docked=[0],lookahead=set(),
             balanced=spec.get('balanced',False),a06_compact=spec.get('a06_compact',False),
             strict_staging=spec.get('strict_staging',False),
             event=dict(port=spec['port'],scheduled_drops=k,start_frame=S.nsteps,entries=[],row_merges=[]))
    job['under_exit']=job['final_x']<gx+geom.CH_W+geom.PREFERRED_GAP
    S.waste_parallel_events=getattr(S,'waste_parallel_events',[]);S.waste_parallel_events.append(job['event'])
    return job

def _send_pipeline(S,pieces,gx,blobs,plan,jobs):
    """登记一批小块到后台流水线后立即返回。plan['assign'] 按到达顺序给出每块的 (孔任务, 排, 排内到达序号)。
    规则不变：小块不变形、通道内间距≥3；每排在就绪区到齐后同步靠拢成完整矩形，自下而上逐排停靠。"""
    import rigid as R
    pool=_pool(S)
    pending=sorted(pieces,key=lambda t:-t[0].r)      # 到达顺序：最深的道最先到达底部
    for ln,d in pending:
        cells=d.target_cells();rect=contour.bounds(cells)
        assert contour.connected(cells) and len(cells)==geom.cells(d.vol)+d.margin_budget,('排废小块轮廓/面积无效',d.name,rect,len(cells))
        d.rect=rect;d.set_cells(cells);d.waste_grid_packet=True
    S.step(geom.HOLD_MS,changed=[])
    S.waste_grid_events=getattr(S,'waste_grid_events',[])
    S.waste_batch_events=getattr(S,'waste_batch_events',[])
    assert len(plan['assign'])==len(pending),'调度块数与本批小块数不一致'
    S.waste_batch_events.append(dict(tile_shape=list(pending[0][1].rect[2:]),count=len(pending),
                                    ports=plan['ports'],collection_group=plan['stop'],flush_after=plan['flush'],
                                    whole_port_pipeline=True,packed=plan.get('packed',False),minimum_ports=plan['minimum_ports']))
    tiles=[]
    for t,((ln,d),(key,r,pos)) in enumerate(zip(pending,plan['assign'])):
        if key not in jobs:
            assert not blobs.get(plan['specs'][key]['port']),'调度给出的收集孔已占用'
            jobs[key]=_job(S,plan['specs'][key],blobs,gx)
        job=jobs[key];spec=job['rows'][r]
        assert job.get('balanced',False) or (d.rect[2]==spec['widths'][pos] and d.rect[3]==spec['h']),('小块形状与排规格不符',d.rect,spec)
        d.S=S;d.waste_fk=job['port'];d.slot=spec['n']-1-pos;d.tile_area=area(d)
        d.waste_pipeline_clearance=True;assert 0<d.tile_area<=32
        d.entry_ticket=t;d.allow=() if job.get('strict_staging',False) else (f"BLOB{job['port']}",)
        pool.items.append((d,_tile_path(S,d,job,t,r,pos,gx),R._door_rule(gx)))
        tiles.append(d)
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

def cut_rounds(items,collect_three=False):
    """纯格数预测，使用与真实动作相同的近半树及空间预算。"""
    leaves=[_leaf_areas(ln,area(d)) for ln,d in items]
    assert leaves and all(len(q)==len(leaves[0]) for q in leaves)
    return [collections.Counter(_packet_shape(q[i]) for q in leaves) for i in range(len(leaves[0]))]

def _balanced_plans(items,final,gx,single_stop=False,fixed_edges=None):
    """每批16道同面积；4/8个缺角包组成一排，再以几何容量分配F孔。"""
    import itertools
    from scene import _gap
    leaves=[_leaf_areas(ln,area(d),(fixed_edges or {}).get(ln.id,()),5 if (getattr(d,'port_gap',None) or geom.running_gap(d.vol))==1 else 3) for ln,d in items]
    assert leaves and all(q==leaves[0] for q in leaves),'整列排废须同液量'
    count=len(items);assert count==16,'废液整排调度需要完整16道'
    specs=[]
    for n in leaves[0]:
        w,h=_packet_shape(n)
        choices=[m for m in range(1,count+1) if count%m==0 and w*m<=40 and h*(count//m)<=DOCK_BOTTOM-TRANSPORT_Y
                 and max(w*m,h*(count//m))<=2.5*min(w*m,h*(count//m))]
        assert choices,('原形小滴无法在单孔自然排成紧凑矩形',n,w,h,count)
        m=min(choices,key=lambda m:max(w*m,h*(count//m))/min(w*m,h*(count//m)))
        specs.append(dict(W=w*m,rows=[([w]*m,h,h)]*(count//m),balanced=True))
        assert w*m*h*(count//m)==n*count
    def place(group):
        if len(group)>len(PORTS):return None
        for ports in sorted(itertools.combinations(PORTS,len(group)),key=lambda c:(sum(c),c)):
            occupied=[];ok=True
            for spec,port in zip(group,ports):
                W=spec['W'];H=sum(row[2] for row in spec['rows'])
                assert W<=40 and H<=DOCK_BOTTOM-TRANSPORT_Y
                final_rc=(geom.HOLE_X[port]-W//2,DOCK_BOTTOM-H,W,H)
                for widths,h,rh in spec['rows']:
                    rs=_row_spec(W,widths,h,port,gx)
                    x0=min(rs['stage_x'][0],final_rc[0]);x1=max(rs['stage_x'][-1]+widths[-1],final_rc[0]+W)
                    stage=(x0,TRANSPORT_Y,x1-x0,max(h,rh))
                    if any(_gap(stage,b)<geom.MIN_GAP or _gap(final_rc,b)<geom.MIN_GAP for b in occupied):ok=False;break
                if not ok:break
                occupied.append(final_rc)
            if ok:return ports
        return None
    plans=[];i=0;stop=0
    while i<len(specs):
        best=None
        for j in range(i+1,min(len(specs),i+len(PORTS))+1):
            ports=place(specs[i:j])
            if ports is None:break
            best=(j,ports)
        assert best,'单批废液超出F1–F4几何容量'
        j,ports=best;stop+=1
        if single_stop:assert i==0 and j==len(specs),'整步废液无法安全一次装入F1–F4'
        shared={k:dict(specs[k],port=p) for k,p in zip(range(i,j),ports)}
        for k in range(i,j):
            m=len(specs[k]['rows'][0][0])
            plans.append(dict(stop=stop,flush=k==j-1 and (final or j<len(specs)),ports=[shared[k]['port']],
                assign=[(k,t//m,t%m) for t in range(count)],specs=shared,minimum_ports=1,balanced=True))
        i=j
    return leaves[0],plans

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

def _pack_single_stop(seq,gx):
    """整步小块（到达顺序 seq=[(w,h)…]）一次装入 F1–F4：按顺序切成至多4段，每段在一个孔旁拼成
    若干整排（同排同高、宽度之和恰为块宽 W），块高≤17、长宽比≤2；先送的段放远孔，
    后段的就绪区与已停靠汇液框保持 MIN_GAP。返回 (段数, 孔号总和, [(孔, W, 排列表)])，不可行返回 None。"""
    import itertools
    from functools import lru_cache
    from scene import _gap
    n=len(seq)
    @lru_cache(None)
    def layouts(i,j):
        out=[]
        for W in range(2,41):
            rows=[];cur=[];acc=0;ok=True
            for k in range(i,j):
                w,h=seq[k]
                if cur and h!=seq[cur[0]][1]:ok=False;break
                cur.append(k);acc+=w
                if acc==W:rows.append(tuple(cur));cur=[];acc=0
                elif acc>W:ok=False;break
            if not ok or cur:continue
            H=sum(seq[r[0]][1] for r in rows)
            if H<=189-TRANSPORT_Y and max(W,H)<=2*min(W,H):out.append((W,tuple(rows)))
        return tuple(out)
    def areas(W,rows,port):
        H=sum(seq[r[0]][1] for r in rows);final=(geom.HOLE_X[port]-W//2,189-H,W,H);st=[final]
        for r in rows:
            spec=_row_spec(W,[seq[k][0] for k in r],seq[r[0]][1],port,gx)
            x0=min(spec['stage_x'][0],final[0]);x1=max(spec['stage_x'][-1]+max(seq[k][0] for k in r),final[0]+W)
            st.append((x0,TRANSPORT_Y,x1-x0,seq[r[0]][1]))
        return final,st
    for k in range(1,len(PORTS)+1):
        best=None
        for cuts in itertools.combinations(range(1,n),k-1):
            b=(0,*cuts,n);chunks=[layouts(b[i],b[i+1]) for i in range(k)]
            if not all(chunks):continue
            for ports in sorted(itertools.combinations(sorted(PORTS,reverse=True),k),key=sum):
                if best and sum(ports)>=best[1]:break
                for choice in itertools.product(*chunks):
                    placed=[];ok=True
                    for (W,rows),port in zip(choice,ports):
                        final,st=areas(W,rows,port)
                        if any(_gap(a,q)<geom.MIN_GAP for a in st for q in placed):ok=False;break
                        placed.append(final)
                    if ok:
                        best=(k,sum(ports),[(port,W,rows) for (W,rows),port in zip(choice,ports)]);break
        if best:return best
    return None

def step_plans(rounds,final=True,gx=None,single_stop=False):
    """每批的收集计划：assign 按到达顺序给出 (孔任务键, 排, 排内序号)。
    默认：每批按 plan_batch 拼成紧凑块，port_schedule 跨批分配 F1–F4。
    single_stop：整步所有小块一次装入 F1–F4，只停靠吸液一次，反应区内不留液体。"""
    gx=geom.gap_x(1) if gx is None else gx
    plans=[]
    if not single_stop:
        sched=port_schedule(rounds,final,gx,True);specs={}
        for i,(shapes,s) in enumerate(zip(rounds,sched)):
            (w,h),count=next(iter(shapes.items()))
            pb=[dict(p,port=k) for p,k in zip(plan_batch(w,h,count),reversed(s['ports']))]
            assign=[]
            for j,pl in enumerate(reversed(pb)):            # 先远孔
                key=(i,j);specs[key]=dict(port=pl['port'],W=pl['columns']*w,rows=[([w]*pl['columns'],h)]*pl['rows'])
                assign+=[(key,c//pl['columns'],c%pl['columns']) for c in range(pl['count'])]
            plans.append(dict(stop=s['stop'],flush=s['flush'],ports=[pl['port'] for pl in pb],
                              assign=assign,specs=specs,minimum_ports=len(pb)))
        return plans
    seq=[];owner=[]
    for i,shapes in enumerate(rounds):
        assert len(shapes)==1,'整步调度要求每批小块同形'
        (w,h),count=next(iter(shapes.items()));seq+=[(w,h)]*count;owner+=[i]*count
    packed=_pack_single_stop(tuple(seq),gx)
    assert packed,'整步废液无法一次装入F1–F4'
    specs={};where={}
    for c,(port,W,rows) in enumerate(packed[2]):
        key=('pack',c);specs[key]=dict(port=port,W=W,rows=[([seq[k][0] for k in row],seq[row[0]][1]) for row in rows])
        for r,row in enumerate(rows):
            for pos,k in enumerate(row):where[k]=(key,r,pos)
    for i in range(len(rounds)):
        idx=[k for k in range(len(seq)) if owner[k]==i];keys=[]
        for k in idx:
            if where[k][0] not in keys:keys.append(where[k][0])
        plans.append(dict(stop=1,flush=final and i==len(rounds)-1,ports=[specs[q]['port'] for q in keys],
                          assign=[where[k] for k in idx],specs=specs,minimum_ports=len(keys),packed=True))
    return plans

def bites(S,items,blobs,final=True,pellets=None,collect_three=False,collect_a05=False,single_stop=False):
    """沿长边递归近半；先清右支，始终保留真实面积与足够厚度。"""
    import rigid as R
    items=list(items);lanes={ln.id:ln for ln,d in items}
    height=5 if geom.running_gap(items[0][1].vol)==1 else 3
    for ln,d in items:d.port_gap=geom.running_gap(d.vol)
    assert items and all(d.vol>0 and not getattr(d,'magnet_only',False) for ln,d in items)
    assert all(ln.c==items[0][0].c and ln.odd for ln,d in items),'排废当前支持单个奇数列'
    gx=geom.gap_x(geom.waste_gap(items[0][0].c))
    if collect_three:
        assert len(items)==16 and all(ln.c==1 and area(d)==100 for ln,d in items),'三批合组仅用于单列R04的5µL液相'
        assert not any(blobs.values()),'R04合批开始前必须无未吸出的旧废液'
    if collect_a05:
        assert len(items)==16 and all(ln.c==1 and area(d)==30 for ln,d in items)
        assert not any(blobs.values())
    assert not any(blobs.get(k) for k in PORTS),'排废调度开始前必须无未吸出的旧废液'
    item_names={d.name for ln,d in items};fixed_edges={}
    for ln,d in items:
        residents=[q for q in S.drops.values() if q.name not in item_names and q.vol>0
                   and ln.Y0<=q.rect[1]<ln.Y0+4 and ln.X0<=q.rect[0]<ln.X0+geom.LANE_L]
        assert all(q.rect[0]+q.rect[2]<=d.rect[0] for q in residents),'排废前保留液须停靠左侧'
        fixed_edges[ln.id]=tuple(q.rect[0]+q.rect[2] for q in residents)
    leaf_areas,schedule=_balanced_plans(items,final,gx,single_stop,fixed_edges);jobs={}
    rounds=[collections.Counter({_packet_shape(n):len(items)}) for n in leaf_areas]
    S.waste_schedule_events=getattr(S,'waste_schedule_events',[])
    S.waste_schedule_events.append(dict(rounds=[dict(r) for r in rounds],packet_areas=leaf_areas,
                                       schedule=schedule,step=S.nsteps,balanced=True))
    stacks={ln.id:[d] for ln,d in items};serial=0
    for index,expected in enumerate(leaf_areas):
        # 栈中的低层均是左兄弟，几何预算与纯规划函数一致。
        while True:
            active=[(ln,stacks[ln.id][-1]) for ln,d in items]
            n=area(active[0][1]);assert all(area(d)==n for ln,d in active)
            edges={ln.id:[*fixed_edges[ln.id],*(q.rect[0]+q.rect[2] for q in stacks[ln.id][:-1])] for ln,d in active}
            x,w=_cut_x(active[0][0],n,edges[active[0][0].id],height) if n>16 else (0,0)
            if n<=16 or x+w+2>active[0][0].X0+geom.LANE_L+geom.CH_W:break
            a,b,wa,wb,H=_split_widths(n,height);specs=[];touch=[]
            R.run(S,[(d,R.go(d,[('x',_cut_x(ln,n,edges[ln.id],height)[0])])) for ln,d in active])
            for ln,d in active:
                x,w=_cut_x(ln,n,edges[ln.id],height);y=geom.physical_zone(ln.c,ln.r)[1]+(1 if height==5 else 2)
                left=(x,y,wa,H);right=(x+wa,y,wb,H)
                cells=(_compact_cells(a,left,True),_compact_cells(b,right))
                _settled_shape(S,d,(x,y,w,H),cells[0]|cells[1])
                finals=((x-2,y,wa,H),(x+wa+2,y,wb,H))
                names=(d.name,f'WG{ln.id}_{serial}')
                actual=geom.cells(d.vol);pa=round(actual*a/(a+b));va=round(pa*.05,2)
                specs.append((d,finals,(va,round(d.vol-va,2)),names,cells))
                if pellets:touch.append((d.name,pellets[ln.id].name))
            S.step(geom.LONG_MS,changed=[d.name for ln,d in active],touching=touch)
            for (ln,d),(left,right) in zip(active,R.split_many(S,specs,touching=touch)):
                stacks[ln.id][-1:]=[left,right]
            serial+=1
        pieces=[]
        for ln,d in active:
            assert area(d)==expected,(index,area(d),expected)
            stacks[ln.id].pop();w=(expected+3)//4
            # 先保持本行厚度，到门口才把必要的30格包变成4×8。
            w=(expected+geom.LANE_H-1)//geom.LANE_H
            # 原生切出的完整矩形保持原形，到四格通道处才按需转形。
            assert len(d.target_cells())==d.rect[2]*d.rect[3]
            pieces.append((ln,d))
        S.step(geom.LONG_MS,changed=[d.name for ln,d in pieces])
        plan=schedule[index]
        # 流水线（全部排废步骤）：本批登记到后台；本批全部离开反应区和进液通道（到达底部运输带或已汇入）后，
        # 立即切下一批，与本批在底部的行进、靠拢、停靠并行。本次停靠的最后一批送完后才吸液。
        tiles=_send_pipeline(S,pieces,gx,blobs,plan,jobs)
        if plan['flush']:
            _drain(S,lambda:not S.waste_pool.items);flush(S,blobs)
        else:
            _drain(S,lambda:all(S.drops.get(d.name) is not d or d.rect[1]>=TRANSPORT_Y for d in tiles))
    assert not any(stacks.values()),'近半切分树仍有未排出的子滴'
    if pellets:
        for k,p in pellets.items():
            S.remove(p.name);p.name=lanes[k].name;p.allow=();p.magnet_only=(p.vol==0);S.add(p)
    if final:flush(S,blobs)

def egress_pieces(S,items,blob_state,final=True,collect_a05=False,single_stop=False):
    items=list(items)
    assert all(d.vol>0 and not getattr(d,'magnet_only',False) for ln,d in items),'磁珠排废必须先加入真实载液并重悬，禁止0µL磁珠块自行搬运'
    for ln,d in items:d.magnet_only=False
    bites(S,items,blob_state,final,collect_a05=collect_a05,single_stop=single_stop)

def supernatant_bites(S,lanes,blob_state,pellet_vol,final=True,collect_three=False):
    assert pellet_vol==0,'当前方案按用户要求仅留磁珠，不保留计划液相'
    lanes=list(lanes);pellets={};items=[]
    for ln in lanes:
        d=S.get(ln.name);S.remove(d.name);d.name=f'LIQ{ln.id}';S.add(d)
        p=S.add(Drop(f'PEL{ln.id}',ln.rect(ln.mag-1,3),0));p.magnet_only=True;pellets[ln.id]=p
        d.allow=(p.name,);p.allow=(d.name,);items.append((ln,d))
    S.step(geom.HOLD_MS,touching=[(d.name,pellets[ln.id].name) for ln,d in items])
    bites(S,items,blob_state,final,pellets,collect_three=collect_three)
