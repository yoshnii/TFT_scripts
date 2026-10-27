"""Horizontal binary cuts in the top band; six-cell transport/staging gaps.

All edge holes are loaded together. Two active port families expand together
where the top staging band fits them. Reaction fusion
still waits until every lane is ready. No extra liquid or loading tips.
"""
import geom,ops
from scene import Drop

# area-before-cut -> complete horizontal mother shape; final native leaf.
PLANS={
 'G4':(1,16,.1,32,{32:(8,4),16:(4,4),8:(4,2),4:(2,2)},(1,2),1),
 'AMP':(2,16,1.,320,{320:(32,10),160:(16,10),80:(16,5),40:(8,5)},(4,5),2),
 'G14':(4,16,.625,240,{240:(24,10),120:(24,5),60:(12,5),30:(6,5)},(3,5),4),
 'G8':(1,16,.7,240,{240:(24,10),120:(24,5),60:(12,5),30:(8,4)},(4,4),1),
 'LIG':(2,16,.7,240,{240:(24,10),120:(24,5),60:(12,5),30:(8,4)},(4,4),2),
 'EDTA':(1,16,.4,128,{128:(16,8),64:(16,4),32:(8,4),16:(4,4)},(2,4),1),
 'G16':(2,16,.88,320,{320:(32,10),160:(16,10),80:(16,5),40:(8,5)},(4,5),2),
}


def _arrange(S,nodes,W,H,columns=4,padding_limit=2):
    import rigid as R
    nodes=sorted(nodes,key=lambda d:(d.rect[1],d.rect[0]))
    columns=min(columns,len(nodes));rows=(len(nodes)+columns-1)//columns
    old_h=max(d.rect[3] for d in nodes)
    pitch_y=max(H,old_h)+6
    # At the eight-mother stage, move the left four down before sliding either
    # row sideways. Their horizontal six-cell gaps remain open throughout.
    keep_rows=columns==8 and len({d.rect[1] for d in nodes})>1
    positions=[(16+(i%columns)*(W+12),4+(i//columns if keep_rows else rows-1-i//columns)*pitch_y)
               for i in range(len(nodes))]
    assert max(y+max(H,old_h) for x,y in positions)<=23
    deferred=getattr(S,'top_deferred_parking',[])
    if getattr(S,'top_parallel_moves',False):
        jobs=[(d,R.go(d,[('y',y),('x',x)])) for d,(x,y) in zip(nodes,positions)]
        jobs.extend((d,R.go(d,[('x',x)])) for d,x in deferred)
        R.run(S,jobs)
    else:
        R.run(S,[(d,R.go(d,[('y',y)])) for d,(x,y) in zip(nodes,positions)])
        R.run(S,[(d,R.go(d,[('x',x)])) for d,(x,y) in zip(nodes,positions)])
    for d,(x,y) in zip(nodes,positions):
        before=len(d.target_cells());area=W*H
        assert area>=before and area-before<=padding_limit,('未批准的承接面积变化',d.name,before,area)
        assert W>=H and area%2==0
        d.electrode_area=area;d.margin_budget=area-geom.cells(d.vol)
        d.native_authorized_padding=d.margin_budget>0
    if getattr(S,'top_delivery_pool',None):
        while not all(R.rect_ok(S,d.name,(*d.rect[:2],W,H)) for d in nodes):
            S.step(geom.STEP_MS,changed=[])
    specs=[(d,(*d.rect[:2],W,H)) for d in nodes]
    for d,_ in deferred:
        assert len(d.target_cells())==W*H
        specs.append((d,(*d.rect[:2],W,H)))
    R.reshape_many(S,specs,dur=geom.LONG_MS)
    S.top_deferred_parking=[]
    return nodes


class DeliveryPool:
    """Advance already-cut small drops while the next G14 mothers are prepared."""
    def __init__(self,S):
        self.previous_background=getattr(S,'background',None)
        # A complete Protocol may retain its now-empty waste scheduler.
        # Preserve that callback for later waste sections.
        assert not getattr(getattr(S,'waste_pool',None),'items',[])
        self.S=S;self.items=[];self.frames=0;self.moving_frames=0;self.overlap_frames=0
        S.background=self.advance

    def advance(self,S):
        import rigid as R
        active=[item for item in self.items if getattr(item[0],'pending',None)!='done']
        if not active:
            S.top_delivery_advanced=False
            return [],[],0
        moved=getattr(S,'moved',0);pipeline=getattr(S,'pipeline_active',False)
        clock=getattr(S,'top_mother_clock',None)
        foreground=bool(clock and any(getattr(d,'top_slow_mother',False)
            and clock.previous.get(id(d),d.rect)!=d.rect for d in S.drops.values()))
        S.moved=0;S.pipeline_active=True
        try:
            changed,touch,dur=R.tick(S,active,record=False)
            S.top_delivery_advanced=bool(S.moved)
        finally:S.moved=moved;S.pipeline_active=pipeline
        self.frames+=1;self.moving_frames+=int(S.top_delivery_advanced)
        self.overlap_frames+=int(foreground and S.top_delivery_advanced)
        return changed,touch,dur

    def finish(self):
        S=self.S;stall=0
        while any(getattr(d,'pending',None)!='done' for d,_ in self.items):
            S.step(geom.STEP_MS,changed=[])
            stall=0 if S.top_delivery_advanced else stall+1
            assert stall<=60,'顶部小滴队列未能前进'
        for d,_ in self.items:d.pending=None
        S.background=self.previous_background;S.top_delivery_advanced=False


def deliver(S,reagent,vol,cols,tag,on_before_load=None,on_load=None,controlled=False):
    import top_mother_timing as timing
    try:
        return _deliver(S,reagent,vol,cols,tag,on_before_load,on_load,controlled)
    finally:
        timing.finish(S)
        S.top_parallel_moves=False
        if getattr(S,'top_delivery_pool',None):
            S.background=S.top_delivery_pool.previous_background;S.top_delivery_pool=None
        S.top_delivery_advanced=False
        S.top_deferred_parking=[]


def _deliver(S,reagent,vol,cols,tag,on_before_load=None,on_load=None,controlled=False):
    import rigid as R
    from native_packets import partition
    ports,n,pv,capacity,shapes,(w,h),per_lane=PLANS[reagent]
    large_cdna = reagent=='G4' and abs(vol-.4)<1e-8
    if large_cdna:
        # Restore the approved 0.40µL G4 mother/leaf geometry. Only these
        # mothers may add four carrier cells; real liquid is never increased.
        ports,n,pv,capacity,shapes,(w,h),per_lane=(
            2,8,.4,72,{72:(12,6),36:(10,4),20:(6,4)},(3,4),1)
    assert len(cols)==1 and abs(pv*per_lane-vol)<1e-8
    S.strict_transport=True
    c=cols[0];lanes=ops.lanes_all(cols)
    initial={ln.r:S.get(ln.name).vol for ln in lanes}
    if on_before_load:on_before_load()
    roots=[]
    for k in range(1,ports+1):
        rw,rh=(12,6) if large_cdna else (8,4) if capacity==32 else R.compact_receiving_shape(capacity)
        d=S.add(Drop(f'D{reagent}E{k}',(geom.HOLE_X[k]-rw//2,4,rw,rh),pv*n))
        d.electrode_area=capacity;d.margin_budget=capacity-geom.cells(d.vol)
        d.receiving=True;d.native_authorized_padding=d.margin_budget>0
        roots.append(d)
    S.step(geom.HOLD_MS,changed=[d.name for d in roots])
    info=dict(hole='/'.join(f'E{k}' for k in range(1,ports+1)),reagent=reagent,
        volume_ul=round(vol*16,10),per_lane_ul=vol,lanes=f'A1-A16',
        port_volumes={f'E{k}':d.vol for k,d in enumerate(roots,1)},batch=1,batches=1)
    if on_load:on_load(info)
    else:ops.hold_all(S,ops.T_DISP,geom.VOLT)
    S.step(geom.HOLD_MS,changed=[])
    for d in roots:d.receiving=False
    S.top_parallel_moves=reagent!='G4'
    import top_mother_timing as timing
    timing.start(S,reagent,roots)
    S.pipeline_events=getattr(S,'pipeline_events',[])
    S.native_leaf_events=getattr(S,'native_leaf_events',[])
    S.native_padding_events=getattr(S,'native_padding_events',[])
    S.pipeline_events.append(dict(kind='start',reagent=reagent,per_lane_ul=vol,
                                  initial_volumes=initial,step=S.nsteps))
    families=list(enumerate(roots,1))
    paired=reagent in ('AMP','LIG','G16')
    groups=([families[i:i+2] for i in range(0,len(families),2)]
            if reagent=='G14' or large_cdna or paired else [[family] for family in families])
    # Active mothers go directly into their common cutting layout. Only
    # genuinely later families need parking; G4 keeps its released path.
    active_count=len(groups[0]) if reagent!='G4' else 1
    parked=[(i,d) for i,d in enumerate(roots[1:]) if i+1>=active_count]
    if reagent=='G14':S.top_deferred_parking=[(d,136+32*i) for i,d in reversed(parked)]
    else:R.run(S,[(d,R.go(d,[('x',136+32*i)])) for i,d in reversed(parked)])
    pool=S.top_delivery_pool=DeliveryPool(S) if reagent=='G14' else None
    locks={};gx=geom.gap_x(geom.reagent_gap(c))
    for family_group in groups:
        if reagent!='G14' and not large_cdna and len(family_group)==1:
            root=family_group[0][1]
            R.run(S,[(root,R.go(root,[('x',16),('y',4)]))])
        nodes=[root for k,root in family_group]
        while len(nodes)<n*len(family_group):
            area=len(nodes[0].target_cells());W,H=shapes[area]
            # Paired families share each level; wide arrangements keep the
            # existing child shapes and six-cell gaps without leaf reshaping.
            columns=8 if (paired and len(nodes)>=8) or (reagent=='G14' and len(nodes)==16) or (large_cdna and len(nodes)==8) else 4
            nodes=_arrange(S,nodes,W,H,columns,padding_limit=4 if large_cdna else 2)
            assert all(d.rect[2]>=d.rect[3] for d in nodes)
            nodes=[child for pair in partition(S,nodes,2,'x',6) for child in pair]
        assert all(d.rect[2:]==(w,h) and abs(d.vol-pv)<1e-8 for d in nodes)
        lower=[d for d in nodes if d.rect[1]>min(q.rect[1] for q in nodes)]
        packets=[]
        for k,root in family_group:
            leaves=[d for d in nodes if d.name.startswith(root.name+'_')]
            assert len(leaves)==n
            count=n//per_lane;hi=16-(k-1)*count
            rows=[r for r in range(hi,hi-count,-1) for _ in range(per_lane)]
            for i,(d,row) in enumerate(zip(sorted(leaves,key=lambda q:(-q.rect[1],q.rect[0])),rows)):
                S.remove(d.name);d.name=f'{tag}E{k}_{row}_{i}';S.add(d)
                d.port=k;d.row=row;d.native_packet=True;d.native_leaf_shape=(w,h)
                d.c02_entry_exception=reagent=='G16'
                S.native_leaf_events.append(dict(reagent=reagent,name=d.name,row=row,
                    actual_ul=d.vol,shape=[w,h],cells=w*h,extra_cells=w*h-geom.cells(d.vol)))
                packets.append((d,ops.Lane(c,row)))
        S.step(geom.HOLD_MS,changed=[])
        def route(d,ln):
            d.controlled_entry=True
            if reagent=='G4' and d not in lower:
                while any(S.drops.get(q.name) is q and q.rect[1]<27 for q in lower):yield 'wait'
            yield from R.go(d,[('x',geom.SAFE_X)])
            if d.rect[1]<geom.SAFE_Y:yield from R.go(d,[('y',geom.SAFE_Y)])
            yield from R.go(d,[('x',gx),('y',ln.Y0)])
            while ln.id in locks:yield 'wait'
            locks[ln.id]=True;host=S.drops.get(f'{tag}{ln.id}')
            if host is None:
                x=S.get(ln.name).rect[0]-geom.PREFERRED_GAP-w
                yield from R.go(d,[('x',x)])
                S.remove(d.name);d.name=f'{tag}{ln.id}';S.add(d)
            else:
                d.allow=(host.name,)
                yield from R.go(d,[('x',host.rect[0]-w)])
                yield ('merge_native',(d.rect[0],ln.Y0,w+host.rect[2],h),host.name,d.vol)
            locks.pop(ln.id)
        S.pipeline_active=True
        try:
            jobs=[(d,route(d,ln)) for d,ln in packets]
            if pool:pool.items.extend(jobs)
            else:R.run(S,jobs)
        finally:S.pipeline_active=False
    if pool:
        pool.finish()
        S.pipeline_events.append(dict(kind='overlap_delivery',frames=pool.frames,
            moving_frames=pool.moving_frames,mother_and_packet_moving_frames=pool.overlap_frames))
    for ln in lanes:
        assert abs(S.get(f'{tag}{ln.id}').vol-vol)<1e-8
        assert S.get(ln.name).vol==initial[ln.r]
    S.pipeline_events.append(dict(kind='all_ready',rows=list(range(1,17)),step=S.nsteps))
    S.step(geom.HOLD_MS,changed=[]);ops.merge_all(S,lanes,tag)
    S.pipeline_events.append(dict(kind='finish',reagent=reagent,step=S.nsteps,
        final_volumes={ln.r:S.get(ln.name).vol for ln in lanes}))
    S.top_parallel_moves=False
