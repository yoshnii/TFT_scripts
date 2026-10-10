"""废液原形逐格移到TEC下方；整批选最少孔，整排拼接，不旋转、不重塑。"""
from functools import lru_cache
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

def _collect_port_pipeline(S,port_items,gx,blobs,plan,w,h):
    """整个孔的所有反应区同时纳入流水线；只在共享通道冲突处等待，不按4滴启动一批。"""
    import rigid as R
    fk=plan['port'];columns=plan['columns'];rows=plan['rows'];W=columns*w
    final_x=geom.HOLE_X[fk]-W//2
    stage_width=W+(columns-1)*geom.CUT_GAP
    stage_left=max(gx+geom.CH_W+geom.MIN_GAP,geom.HOLE_X[fk]-stage_width//2)
    assert stage_left+stage_width<=geom.GRID_W
    entry_front=[0];completed_rows=[0];arrivals={r:[] for r in range(rows)}
    event=dict(port=fk,scheduled_drops=len(port_items),start_frame=S.nsteps,entries=[],row_merges=[])
    S.waste_parallel_events=getattr(S,'waste_parallel_events',[]);S.waste_parallel_events.append(event)
    batch=[]
    for ticket,(ln,d) in enumerate(port_items):
        row_index=ticket//columns;col=columns-1-ticket%columns
        d.S=S;d.waste_fk=fk;d.slot=row_index*columns+col;d.tile_area=area(d)
        d.waste_pipeline_clearance=True
        assert d.tile_area in PACKET_AREAS
        d.tile_columns=columns;d.entry_ticket=ticket;d.allow=(f'BLOB{fk}',)
        stage_x=stage_left+col*(w+geom.CUT_GAP)
        def path(d=d,stage_x=stage_x,row_index=row_index):
            origin_y=d.rect[1]
            while entry_front[0]!=d.entry_ticket:yield 'wait'
            event['entries'].append(dict(ticket=d.entry_ticket,frame=S.nsteps))
            def release_entry():
                if entry_front[0]==d.entry_ticket:entry_front[0]+=1
            yield from R.go(d,[('x',gx)])
            for action in R.go(d,[('y',TRANSPORT_Y)]):
                yield action
                if d.rect[1]>=origin_y+h+geom.MIN_GAP:release_entry()
            while completed_rows[0]!=row_index:yield 'wait'
            for action in R.go(d,[('x',stage_x)]):
                yield action
                if d.rect[0]>=gx+geom.CH_W+geom.MIN_GAP:release_entry()
            release_entry()
        batch.append((d,path()))
    def arrived(d):
        ri=d.slot//columns;ds=arrivals[ri];ds.append(d)
        if len(ds)!=columns:return
        assert completed_rows[0]==ri
        ds.sort(key=lambda q:q.slot)
        for q in ds:
            q.allow=(f'BLOB{fk}',*(p.name for p in ds if p is not q));q.pending=None
        R.run(S,[(q,R.go(q,[('x',stage_left+i*w)])) for i,q in enumerate(ds)])
        union=frozenset().union(*(q.target_cells() for q in ds));rc=contour.bounds(union)
        assert len(union)==rc[2]*rc[3] and contour.connected(union)
        vol=round(sum(q.vol for q in ds),2)
        for q in ds:S.remove(q.name)
        row=S.add(Drop(f'WROW{fk}',rc,vol));row.set_cells(union);row.allow=(f'BLOB{fk}',)
        S.step(geom.HOLD_MS,changed=[row.name],touching=[(row.name,f'BLOB{fk}')])
        R.run(S,[(row,R.go(row,[('x',final_x),('y',189-(ri+1)*h)]))])
        host=blobs.get(fk);cells=row.target_cells()
        if host:
            union=host.target_cells()|cells
            assert len(union)==area(host)+area(row) and contour.connected(union)
            S.remove(row.name);host.vol=round(host.vol+row.vol,2)
            host.rect=contour.bounds(union);host.set_cells(union);host.tile_count+=len(ds)
        else:
            S.remove(row.name);host=blobs[fk]=S.add(Drop(f'BLOB{fk}',contour.bounds(cells),row.vol))
            host.set_cells(cells);host.tile_area=ds[0].tile_area;host.tile_shape=(w,h);host.tile_count=len(ds)
        assert area(host)==host.rect[2]*host.rect[3]
        for q in ds:
            S.waste_grid_events.append(dict(port=fk,tile_area=q.tile_area,slot=q.slot,row=ri,column=q.slot%columns,
                step=S.nsteps,merged_without_reshape=True,tile_shape=[w,h],transport_y=TRANSPORT_Y,
                row_ready=True,row_piece_count=len(ds),actual_area=area(host),rect=list(host.rect),
                whole_port_pipeline=True))
        event['row_merges'].append(S.nsteps)
        S.step(geom.HOLD_MS,changed=[host.name]);completed_rows[0]+=1
    R.run(S,batch,rule=R._door_rule(gx),on_done=arrived)
    assert completed_rows[0]==rows
    event['end_frame']=S.nsteps

def send(S,pieces,gx,blobs,final=False,retained_port=None,collection_group=None,retained_ports=None,parallel=False):
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
    # 默认每批吸走；R04三批合组时为本批保留一个未占用孔，不提前吸走前批。
    if retained_port is not None:retained_ports=[retained_port]
    if retained_ports is None:
        flush(S,blobs)
    else:
        assert all(k in PORTS and not blobs.get(k) for k in retained_ports),'合批收集孔已占用'
        assert len({d.rect[2:] for ln,d in pending})==1,'保留孔的一批必须同形'
    while pending:
        w,h=pending[0][1].rect[2:]
        group=[t for t in pending if t[1].rect[2:]==(w,h)]
        pending=[t for t in pending if t[1].rect[2:]!=(w,h)]
        plans=plan_batch(w,h,len(group))
        if retained_ports is not None:
            assert len(plans)==len(retained_ports),'合批保留孔容量不足，不能改变原汇液框'
            plans=[dict(p,port=k) for p,k in zip(plans,retained_ports)]
        S.waste_batch_events.append(dict(tile_shape=[w,h],count=len(group),minimum_ports=len(plans),plans=plans,
                                        collection_group=collection_group,retained_port=retained_port,
                                        retained_ports=retained_ports,whole_port_pipeline=parallel))
        # 先处理远孔，完成后不再穿过其停靠矩形。
        for plan in reversed(plans):
            fk=plan['port'];columns=plan['columns'];rows=plan['rows'];W=columns*w
            port_items=group[:plan['count']];del group[:plan['count']]
            if parallel:
                _collect_port_pipeline(S,port_items,gx,blobs,plan,w,h)
                continue
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
        if retained_ports is None:flush(S,blobs)

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
    for ln,d in items:
        n=area(d)
        if n==100:
            R.run(S,[(d,R.go(d,[('x',ln.X0+9)]))])
            R.reshape(S,d,(ln.X0+9,ln.Y0,25,4))
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
            wr=(4,4,3,4,3,3,4)[index] if collect_three else (2 if n==118 else geom.CH_W)
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
        if collect_three:
            # 仅改变收集调度：每批仍是16个原形小滴，先远孔后近孔，已到齐的框一直保留。
            retained_port=(3,2,1)[index%3] if index<6 else 1
            group=index//3+1
            send(S,pieces,gx,blobs,retained_port=retained_port,collection_group=group,parallel=True)
            if index%3==2 or not rem:
                flush(S,blobs)
        elif collect_a05:
            send(S,pieces,gx,blobs,retained_ports=[2,3] if index==0 else [1],
                 collection_group='A05',parallel=True)
            if not rem:flush(S,blobs)
        else:
            send(S,pieces,gx,blobs)
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
