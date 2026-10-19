"""已审核的母框分液：先规划叶子矩形，运输时不再逐滴变形。

实际µL与电极承接面积分别记账；只允许逐级二分，不能把逻辑五等份
当作实际对半分裂。G14的0.625µL及排废承接余量为本次明确授权。
"""
import math
import geom
from scene import Drop, _overlap
import ops


def partition(S, parents, count=2, axis='x', gap=3, names=None):
    """同体积、同面积原位分区，逐界面每帧开一格缝；最右/下子滴不动。

    每次移动一个前缀，使每条缝只增加1格，不先移出大缝再缩回。
    调用方须为展开后的子滴预留空间；不会自动挪回一个固定切割站。
    """
    parents=list(parents);groups=[];events=[];pairs=[];idx=0 if axis=='x' else 1
    assert count==2,'禁止多份预登记后逐缝剥离；每次只能对半分裂'
    assert axis in ('x','y')
    S.split_events=getattr(S,'split_events',[])
    for i,p in enumerate(parents):
        x,y,w,h=p.rect;extent=(w,h)[idx]
        assert extent%count==0,('不能等面积切成完整矩形',p.name,p.rect,count)
        width=extent//count;cells=p.target_cells()
        assert len(cells)==w*h
        S.remove(p.name);children=[]
        for j in range(count):
            rc=(x+j*width,y,width,h) if idx==0 else (x,y+j*width,w,width)
            v=round(p.vol/count,10)
            name=names[i][j] if names else f'{p.name}_{j}'
            d=S.add(Drop(name,rc,v));d.electrode_area=rc[2]*rc[3]
            d.margin_budget=d.electrode_area-geom.cells(v)
            assert d.margin_budget>=0
            d.native_authorized_padding=getattr(p,'native_authorized_padding',False)
            d.native_fractional_volume=getattr(p,'native_fractional_volume',False)
            d.compact_mother_clearance=getattr(p,'compact_mother_clearance',False)
            d.allow=tuple(getattr(p,'allow',()))
            children.append(d)
        assert abs(sum(d.vol for d in children)-p.vol)<1e-8
        assert frozenset().union(*(d.target_cells() for d in children))==cells
        for a in children:
            pairs.extend((a.name,b.name) for b in children if a is not b)
            pairs.extend((a.name,n) for n in a.allow)
        e=dict(parent=p.name,children=[d.name for d in children],axis=axis,
               area=len(cells),volumes=[d.vol for d in children],arity=count,
               partition_frame=S.nsteps,partition_preserves_mother_cells=True,
               _parent_key=id(p),_child_keys=[id(d) for d in children],
               gaps=[],positions=[],separation_frames=[])
        groups.append(children);events.append(e);S.split_events.append(e)
    def record(changed):
        S.step(geom.STEP_MS,changed=changed,touching=pairs)
        for ds,e in zip(groups,events):
            gaps=[ds[j+1].rect[idx]-ds[j].rect[idx]-ds[j].rect[2+idx]
                  for j in range(count-1)]
            if e['gaps']:assert 0<=min(gaps)-e['gaps'][-1]<=1
            e['gaps'].append(min(gaps));e['positions'].append([list(d.rect) for d in ds])
            e['separation_frames'].append(S.nsteps-1)
    record([d.name for ds in groups for d in ds])
    for _ in range(gap):
        for interface in range(count-1):
            changed=[]
            for ds in groups:
                for d in ds[:interface+1]:
                    rc=list(d.rect);rc[idx]-=1;d.rect=rc;changed.append(d.name)
            record(changed)
    return groups


def _configuration(reagent):
    # port_count, leaves_per_port, actual_leaf_ul, leaf_width, leaf_height,
    # repeated leaves per reaction. No extra dispensing tips/batches are added.
    # G4 only: approved +4 carrier cells per 1.6/.8/.4µL child.
    return {'G4':(2,8,.4,3,4,1),'AMP':(2,16,1.,4,5,2),
            'G14':(4,16,.625,3,5,4),'G8':(1,16,.7,4,4,1),
            'LIG':(2,16,.7,4,4,2),'EDTA':(1,16,.2,2,2,1),
            'G16':(2,16,.85,4,5,2)}[reagent]


def quarter_cdna(S,lanes,tag,away_tag):
    """A06: 2→1+1→0.5+0.5；3×4保留，5×4+3×4自然组成8×4废液。"""
    import rigid as R
    lanes=list(lanes);ops.settle_loaded(S)
    ops.move_rects(S,{f'{tag}{ln.id}':max(S.get(f'{tag}{ln.id}').rect[0],ln.X0+4) for ln in lanes})
    R.reshape_many(S,[(S.get(f'{tag}{ln.id}'),(S.get(f'{tag}{ln.id}').rect[0],ln.Y0,10,4)) for ln in lanes])
    partition(S,[S.get(f'{tag}{ln.id}') for ln in lanes],2,'x',4,
              [(f'{tag}{ln.id}',f'{away_tag}{ln.id}') for ln in lanes])
    # Leave four cells at the outlet; merge and cut there without edge staging.
    ops.move_rects(S,{f'{away_tag}{ln.id}':ln.X0+27 for ln in lanes})
    # The second cut ends its retained half at X0+1, with no left-edge rebound.
    ops.move_rects(S,{f'{tag}{ln.id}':ln.X0+5 for ln in lanes})
    S.approved_mother_shapes=getattr(S,'approved_mother_shapes',[])
    for ln in lanes:
        d=S.get(f'{tag}{ln.id}')
        S.approved_mother_shapes.append(dict(reagent='A06',name=d.name,frame=S.nsteps,
            actual_ul=d.vol,before=list(d.rect),after=[d.rect[0],ln.Y0,6,4],
            before_cells=len(d.target_cells()),after_cells=24,hold_ms=geom.LONG_MS))
        d.electrode_area=24;d.margin_budget=4;d.native_authorized_padding=True
        d.rect=(d.rect[0],ln.Y0,6,4)
    S.step(geom.LONG_MS,changed=[f'{tag}{ln.id}' for ln in lanes])
    partition(S,[S.get(f'{tag}{ln.id}') for ln in lanes],2,'x',4,
              [(f'{tag}{ln.id}',f'{away_tag}extra{ln.id}') for ln in lanes])
    pairs=[(f'{away_tag}{ln.id}',f'{away_tag}extra{ln.id}') for ln in lanes]
    ops.move_rects(S,{f'{away_tag}extra{ln.id}':ln.X0+24 for ln in lanes},touching=pairs)
    for ln in lanes:
        d=S.get(f'{away_tag}{ln.id}');g=S.get(f'{away_tag}extra{ln.id}')
        cells=d.target_cells()|g.target_cells();assert len(cells)==32
        d.vol=1.5;d.electrode_area=32;d.margin_budget=2;d.native_authorized_padding=True
        d.rect=(ln.X0+24,ln.Y0,8,4);d.set_cells(cells);S.remove(g.name)
        S.get(f'{tag}{ln.id}').a06_authorized_padding=True
    S.step(geom.HOLD_MS)


def deliver(S,reagent,vol,cols,tag,on_before_load=None,on_load=None,controlled=False):
    import rigid as R
    assert len(cols)==1 and geom.odd(cols[0]),'当前原形子滴路由只用于单奇数列'
    if controlled:assert reagent in ('G8','EDTA'),'只改变已点名的B02/B10配送'
    small_g4 = reagent == 'G4' and abs(vol-.10)<1e-8
    c=cols[0]
    ports,n,pv,w,h,per_lane=(1,16,.10,1,2,1) if small_g4 else _configuration(reagent)
    assert abs(pv*per_lane-vol)<1e-8
    initial={r:S.get(ops.Lane(c,r).name).vol for r in range(1,17)}
    if on_before_load:on_before_load()
    roots=[];fields=[];left=geom.SAFE_X;spacing=3
    for k in range(1,ports+1):
        capacity=72 if reagent=='G4' and not small_g4 else 240 if reagent in ('G8','LIG') else w*h*n
        actual=round(pv*n,10)
        rw,rh=(8,4) if small_g4 else R.compact_receiving_shape(capacity)
        if reagent=='G4' and not small_g4:rw,rh=12,6  # Legacy approved receiving frame.
        elif reagent in ('G8','LIG'):rw,rh=20,12
        elif reagent=='G16':rw,rh=20,16
        d=S.add(Drop(f'D{reagent}E{k}',(geom.HOLE_X[k]-rw//2,geom.E_TANG_Y,rw,rh),actual))
        d.electrode_area=capacity;d.margin_budget=capacity-geom.cells(actual)
        d.native_authorized_padding=d.margin_budget>0;d.receiving=True
        roots.append(d)
        nx=n//2;fw=nx*w+(nx-1)*spacing
        fields.append(dict(port=k,left=left,nx=nx,w=w,h=h,root=d))
        left+=fw+spacing
    assert left-spacing<=geom.GRID_W-4
    S.native_padding_events=getattr(S,'native_padding_events',[])
    for d in roots:
        if d.margin_budget:S.native_padding_events.append(dict(kind='mother_reserve',
            name=d.name,actual_ul=d.vol,cells=d.electrode_area,extra_cells=d.margin_budget,step=S.nsteps))
    S.step(geom.HOLD_MS,changed=[d.name for d in roots])
    info=dict(hole='/'.join(f'E{k}' for k in range(1,ports+1)),reagent=reagent,
        volume_ul=round(vol*16,10),per_lane_ul=vol,lanes=f'{"ABCDEF"[c-1]}1-{"ABCDEF"[c-1]}16',
        port_volumes={f'E{k}':d.vol for k,d in enumerate(roots,1)},batch=1,batches=1)
    if on_load:on_load(info)
    else:ops.hold_all(S,ops.T_DISP,geom.VOLT)
    # 人工加液结束后，下一section从完全相同的承接电极状态接续。
    S.step(geom.HOLD_MS,changed=[])
    S.pipeline_events=getattr(S,'pipeline_events',[])
    S.pipeline_events.append(dict(kind='start',reagent=reagent,per_lane_ul=vol,initial_volumes=initial,step=S.nsteps))
    # Only mothers are reshaped. Layout accounts for gaps before splitting.
    parallel_amp=[]
    for f in fields:
        if reagent in ('G8','LIG','G16'):continue
        W,H=(12,6) if reagent=='G4' and not small_g4 else (20,12) if reagent=='G14' else (f['nx']*w,2*h)
        d=f['root'];cy=d.rect[1]+(d.rect[3]-H)//2
        target=(geom.HOLE_X[f['port']]-W//2,cy,W,H)
        if reagent=='AMP':parallel_amp.append((d,target))
        else:R.reshape(S,d,target)
    if parallel_amp:R.reshape_many(S,parallel_amp,dur=geom.LONG_MS)
    R.run(S,[(f['root'],R.go(f['root'],[('y',7),('x',f['root'].rect[0] if reagent=='G4'
                                       else f['left']+27 if reagent=='G14'
                                       else f['left']+(f['nx']-1)*spacing)]))
             for f in fields if reagent not in ('G8','LIG','G16','G14')])
    if reagent in ('G8','LIG','G16'):
        from compact_cascade import generate
        generate(S,fields,reagent)
    elif reagent=='G4' and not small_g4:
        # Mother reshapes are parallel and add four cells, never remove area.
        # Descendants stay native 3×4 until the existing chemical fusion.
        def expand_mothers(nodes,width,height):
            S.approved_mother_shapes=getattr(S,'approved_mother_shapes',[])
            for d in nodes:
                x,y,ow,oh=d.rect
                assert width*height==ow*oh+4
                assert R.rect_ok(S,d.name,(x,y,width,height))
            for d in nodes:
                x,y,_,_=d.rect
                S.approved_mother_shapes.append(dict(reagent='G4',name=d.name,frame=S.nsteps,
                    actual_ul=d.vol,before=list(d.rect),after=[x,y,width,height],
                    before_cells=len(d.target_cells()),after_cells=width*height,hold_ms=geom.LONG_MS))
                d.electrode_area=width*height
                d.margin_budget=d.electrode_area-geom.cells(d.vol)
                d.native_authorized_padding=True
                d.rect=(x,y,width,height)
                S.native_padding_events.append(dict(kind='g4_mother_expand',
                    name=d.name,actual_ul=d.vol,cells=d.electrode_area,
                    extra_cells=d.margin_budget,step=S.nsteps))
            S.step(geom.LONG_MS,changed=[d.name for d in nodes])
        groups=partition(S,roots,2,'x',spacing)
        nodes=[d for ds in groups for d in ds]
        R.run(S,[(d,R.go(d,[('x',x)])) for d,x in zip(nodes,(30,54,84,108))])
        expand_mothers(nodes,10,4)
        nodes=[d for ds in partition(S,nodes,2,'x',spacing) for d in ds]
        R.run(S,[(d,R.go(d,[('x',20+16*i)])) for i,d in enumerate(nodes)])
        expand_mothers(nodes,6,4)
        leaves=[d for ds in partition(S,nodes,2,'x',spacing) for d in ds]
        for i,f in enumerate(fields):f['leaves']=leaves[i*8:(i+1)*8]
    elif reagent=='G14':
        # 10→5→2.5→1.25→0.625µL. Four native 3×5 packets per lane.
        R.run(S,[(d,R.go(d,[('y',4),('x',30+60*i)])) for i,d in enumerate(roots)])
        nodes=[d for ds in partition(S,roots,2,'x',4) for d in ds]
        R.run(S,[(d,R.go(d,[('x',20+60*i+25*j),('y',8)]))
                 for i in range(ports) for j,d in enumerate(nodes[2*i:2*i+2])])
        nodes=[d for ds in partition(S,nodes,2,'y',3) for d in ds]
        R.reshape_many(S,[(d,(d.rect[0],d.rect[1],12,5)) for d in nodes])
        nodes=[d for ds in partition(S,nodes,2,'x',4) for d in ds]
        positions=[(14+60*i+14*(2*half+side),4+9*row)
                   for i in range(ports) for half in range(2) for row in range(2) for side in range(2)]
        R.run(S,[(d,R.go(d,[('y',y),('x',x)])) for d,(x,y) in zip(nodes,positions)])
        leaves=[d for ds in partition(S,nodes,2,'x',4) for d in ds]
        for i,f in enumerate(fields):f['leaves']=leaves[i*16:(i+1)*16]
        for d in leaves:d.native_fractional_volume=True
    else:
        for f in fields:f['nodes']=[(f['root'],0,f['nx']-1,0,1)]
        while any(a<b or ya<yb for f in fields for d,a,b,ya,yb in f['nodes']):
            candidates={'x':[],'y':[]};finished=[]
            for f in fields:
                for d,a,b,ya,yb in f['nodes']:
                    if a==b and ya==yb:finished.append((f,(d,a,b,ya,yb)));continue
                    axis='y' if a==b else 'x' if ya==yb or d.rect[2]>=d.rect[3] else 'y'
                    candidates[axis].append((f,d,a,b,ya,yb))
                f['nodes']=[]
            for f,node in finished:f['nodes'].append(node)
            for axis,group in candidates.items():
                if not group:continue
                children=partition(S,[q[1] for q in group],2,axis,spacing)
                for (f,d,a,b,ya,yb),(l,r) in zip(group,children):
                    if axis=='x':
                        mid=(a+b+1)//2;nodes=((l,a,mid-1,ya,yb),(r,mid,b,ya,yb))
                    else:
                        mid=(ya+yb+1)//2;nodes=((l,a,b,ya,mid-1),(r,a,b,mid,yb))
                    f['nodes'].extend(nodes)
            R.run(S,[(d,R.go(d,[('x',f['left']+a*(w+spacing)+(b-a)*spacing),
                                ('y',4+ya*(h+spacing)+(yb-ya)*spacing)]))
                     for f in fields for d,a,b,ya,yb in f['nodes']])
        for f in fields:f['leaves']=[d for d,a,b,ya,yb in f['nodes']]
    packets=[]
    for f in fields:
        count=n//per_lane;hi=16-(f['port']-1)*count
        rows=[r for r in range(hi,hi-count,-1) for _ in range(per_lane)]
        for i,(d,row) in enumerate(zip(sorted(f['leaves'],key=lambda q:(-q.rect[1],q.rect[0])),rows)):
            assert d.rect[2:]==(w,h) and abs(d.vol-pv)<1e-8
            S.remove(d.name);d.name=f'{tag}E{f["port"]}_{row}_{i}';S.add(d)
            d.port=f['port'];d.row=row;d.part='';d.native_packet=True;d.native_leaf_shape=(w,h)
            S.native_leaf_events=getattr(S,'native_leaf_events',[])
            S.native_leaf_events.append(dict(reagent=reagent,name=d.name,row=row,
                actual_ul=d.vol,shape=[w,h],cells=w*h,extra_cells=w*h-geom.cells(d.vol)))
            packets.append((d,ops.Lane(c,row)))
    S.step(geom.HOLD_MS,changed=[])
    locks={};gx=geom.gap_x(geom.reagent_gap(c))
    def path(d,ln):
        d.controlled_entry=True
        yield from R.go(d,[('x',geom.SAFE_X)])
        if d.rect[1]<geom.SAFE_Y:yield from R.go(d,[('y',geom.SAFE_Y)])
        lane_y=geom.physical_zone(ln.c,ln.r)[1] if h==6 else ln.Y0
        yield from R.go(d,[('x',gx),('y',lane_y)])
        while ln.id in locks:yield 'wait'
        locks[ln.id]=True;name=f'{tag}{ln.id}';host=S.drops.get(name)
        right=S.get(ln.name).rect[0]-geom.PREFERRED_GAP
        if host is None:
            yield from R.go(d,[('x',right-w)])
            S.remove(d.name);d.name=name;S.add(d)
            S.pipeline_events.append(dict(kind='reagent_collect',port=d.port,row=d.row,incoming_ul=d.vol,total_ul=d.vol,step=S.nsteps))
        else:
            d.allow=(host.name,)
            yield from R.go(d,[('x',host.rect[0]-w)])
            assert d.rect[1]==host.rect[1] and d.rect[3]==host.rect[3]
            yield ('merge_native',(d.rect[0],lane_y,w+host.rect[2],h),host.name,d.vol)
        locks.pop(ln.id)
    S.pipeline_active=True
    try:R.run(S,[(d,path(d,ln)) for d,ln in sorted(packets,key=lambda p:(-p[1].r,-p[0].rect[1],p[0].rect[0]))])
    finally:S.pipeline_active=False
    lanes=ops.lanes_all(cols)
    for ln in lanes:
        assert abs(S.get(ln.name).vol-initial[ln.r])<1e-8
        assert abs(S.get(f'{tag}{ln.id}').vol-vol)<1e-8
    S.pipeline_events.append(dict(kind='all_ready',rows=list(range(1,17)),step=S.nsteps))
    S.step(geom.HOLD_MS,changed=[]);ops.merge_all(S,lanes,tag)
    if reagent=='G4' and not small_g4:
        # Preserve the released A03 endpoint and its one-cell transition into A04.
        ops.move_rects(S,{ln.name:ln.X0+26 for ln in lanes})
    elif reagent=='AMP' and all(getattr(S.get(ln.name),'a06_authorized_padding',False) for ln in lanes):
        # A08 now ends at the nearby fusion site; A09 starts its leftward
        # positioning there instead of forcing a return to an old endpoint.
        for ln in lanes:delattr(S.get(ln.name),'a06_authorized_padding')
    final={r:S.get(ops.Lane(c,r).name).vol for r in range(1,17)}
    assert all(abs(final[r]-initial[r]-vol)<1e-8 for r in final)
    S.pipeline_events.append(dict(kind='finish',reagent=reagent,final_volumes=final,step=S.nsteps))
