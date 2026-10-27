"""Horizontal binary cuts in the top band; six-cell transport/staging gaps.

All edge holes are loaded together. G14 expands two port families together;
other reagents keep their existing one-family layout. Reaction fusion
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
    R.run(S,[(d,R.go(d,[('y',y)])) for d,(x,y) in zip(nodes,positions)])
    R.run(S,[(d,R.go(d,[('x',x)])) for d,(x,y) in zip(nodes,positions)])
    for d,(x,y) in zip(nodes,positions):
        before=len(d.target_cells());area=W*H
        assert area>=before and area-before<=padding_limit,('未批准的承接面积变化',d.name,before,area)
        assert W>=H and area%2==0
        d.electrode_area=area;d.margin_budget=area-geom.cells(d.vol)
        d.native_authorized_padding=d.margin_budget>0
    R.reshape_many(S,[(d,(*d.rect[:2],W,H)) for d in nodes],dur=geom.LONG_MS)
    return nodes


def deliver(S,reagent,vol,cols,tag,on_before_load=None,on_load=None,controlled=False):
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
    S.pipeline_events=getattr(S,'pipeline_events',[])
    S.native_leaf_events=getattr(S,'native_leaf_events',[])
    S.native_padding_events=getattr(S,'native_padding_events',[])
    S.pipeline_events.append(dict(kind='start',reagent=reagent,per_lane_ul=vol,
                                  initial_volumes=initial,step=S.nsteps))
    # Reserve a right-hand parking strip for the other loaded mothers.
    R.run(S,[(d,R.go(d,[('x',136+32*i)])) for i,d in reversed(list(enumerate(roots[1:])))])
    families=list(enumerate(roots,1))
    groups=([families[i:i+2] for i in range(0,len(families),2)]
            if reagent=='G14' or large_cdna else [[family] for family in families])
    for family_group in groups:
        if reagent!='G14' and not large_cdna:
            root=family_group[0][1]
            R.run(S,[(root,R.go(root,[('x',16),('y',4)]))])
        nodes=[root for k,root in family_group]
        while len(nodes)<n*len(family_group):
            area=len(nodes[0].target_cells());W,H=shapes[area]
            # G14 only: two families share each binary level. The final
            # sixteen mothers fit as two rows of eight; no leaf reshaping.
            columns=8 if (reagent=='G14' and len(nodes)==16) or (large_cdna and len(nodes)==8) else 4
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
        locks={};gx=geom.gap_x(geom.reagent_gap(c))
        def route(d,ln):
            d.controlled_entry=True
            if d not in lower:
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
        try:R.run(S,[(d,route(d,ln)) for d,ln in packets])
        finally:S.pipeline_active=False
    for ln in lanes:
        assert abs(S.get(f'{tag}{ln.id}').vol-vol)<1e-8
        assert S.get(ln.name).vol==initial[ln.r]
    S.pipeline_events.append(dict(kind='all_ready',rows=list(range(1,17)),step=S.nsteps))
    S.step(geom.HOLD_MS,changed=[]);ops.merge_all(S,lanes,tag)
    S.pipeline_events.append(dict(kind='finish',reagent=reagent,step=S.nsteps,
        final_volumes={ln.r:S.get(ln.name).vol for ln in lanes}))
