"""Cut arrived mothers while the remaining mothers are still travelling.

Only a level's existing destinations/rectangles are used. The swept opening
of each selected cut is reserved until both children have settled.
"""
import geom
import rigid as R
from scene import _gap, _overlap


def cut_level(S,nodes,W,H,columns,padding_limit,times=None):
    from native_packets import partition
    nodes=sorted(nodes,key=lambda d:(d.rect[1],d.rect[0]))
    columns=min(columns,len(nodes));rows=(len(nodes)+columns-1)//columns
    old_h=max(d.rect[3] for d in nodes);pitch=max(H,old_h)+6
    keep_rows=columns==8 and len({d.rect[1] for d in nodes})>1
    targets={d.name:(16+i%columns*(W+12),4+(i//columns if keep_rows else rows-1-i//columns)*pitch)
             for i,d in enumerate(nodes)}
    jobs=[(d,R.go(d,[('y',targets[d.name][1]),('x',targets[d.name][0])])) for d in nodes]
    old_background=getattr(S,'background',None)
    clock=getattr(S,'top_mother_clock',None)
    if clock:clock.begin_run(S)
    remaining=list(nodes);children=[];active_names=set();stall=0
    S.ready_top_events=getattr(S,'ready_top_events',[])

    def background(scene):
        active=[(d,g) for d,g in jobs if d in remaining and d.name not in active_names]
        changed,touch,dur=R.tick(scene,active,record=False) if active else ([],[],0)
        if old_background:
            c,t,ms=old_background(scene);changed+=c;touch+=t;dur=max(dur,ms)
        return changed,touch,dur

    def clear(d):
        x,y=targets[d.name];sweep=(x-3,y,W+6,max(H,d.rect[3]))
        return all(q is d or all(not _overlap(sweep,r) and _gap(sweep,r)>=6 for r in q.rects())
                   for q in S.drops.values())

    S.background=background
    try:
        while remaining:
            ready=[d for d in remaining if getattr(d,'pending',None)=='done' and (not clock or clock.can_move(d)) and clear(d)]
            travelling=[d for d in remaining if d not in ready and getattr(d,'pending',None)!='done']
            if ready and travelling:
                remaining_ms=max((abs(d.rect[0]-targets[d.name][0])+abs(d.rect[1]-targets[d.name][1]))*(2500 if clock and getattr(d,'top_slow_mother',False) else 1000) for d in travelling)
                if remaining_ms<18000:ready=[]
            if not ready:
                S.step(1000,changed=[]);stall+=1
                assert stall<1500,('顶部就绪切分未能前进',[d.name for d in remaining])
                continue
            stall=0;active_names={d.name for d in ready}
            reservations=[]
            for d in ready:
                x,y=targets[d.name]
                reservations.append((frozenset((d.name,d.name+'_0',d.name+'_1')),(x-3,y,W+6,max(H,d.rect[3]))))
            S.ready_cut_reservations=reservations
            for d in ready:
                before=len(d.target_cells());assert 0<=W*H-before<=padding_limit
                d.electrode_area=W*H;d.margin_budget=W*H-geom.cells(d.vol)
                d.native_authorized_padding=d.margin_budget>0
            R.reshape_many(S,[(d,(*d.rect[:2],W,H)) for d in ready],dur=4000)
            S.ready_top_events.append(dict(frame=S.nsteps,parents=[d.name for d in ready],
                others_travelling=[d.name for d in remaining if d not in ready and d.pending!='done']))
            pairs=partition(S,ready,2,'x',6,frame_ms=times)
            children.extend(c for pair in pairs for c in pair)
            for d in ready:remaining.remove(d)
            active_names=set();S.ready_cut_reservations=[]
        if clock:clock.end_run(S)
    finally:
        S.background=old_background;S.ready_cut_reservations=[]
    return children
