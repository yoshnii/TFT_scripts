"""Use existing real cleanup liquid for a second sweep; never spawn electrodes."""
import geom
import ops


def _move(S,items,targets,phase):
    before={d.name:(d.vol,len(d.target_cells()),d.rect[2:]) for _,d in items}
    start=S.nsteps
    for positions in targets:
        ops.move_rects(S,{d.name:positions(ln,d) for ln,d in items})
    assert before=={d.name:(d.vol,len(d.target_cells()),d.rect[2:]) for _,d in items}
    events=getattr(S,'cleanup_sweep_events',[])
    events.append(dict(phase=phase,start_frame=start,end_frame=S.nsteps,
        droplets=len(items),actual_volume_ul=sum(d.vol for _,d in items),
        electrode_cells=sum(len(d.target_cells()) for _,d in items),
        shape=list(items[0][1].rect[2:]),extra_liquid_ul=0,extra_electrodes=0))
    S.cleanup_sweep_events=events


def wash_region(S,lanes):
    items=[(ln,S.get(ln.name)) for ln in lanes]
    assert all(d.vol==3. and d.rect[2:]==(12,5) for _,d in items)
    # The same 12x5 frame enters both adjacent four-cell channels, never the
    # next column. This closed path is the repeating wash cycle itself;
    # return to the original left endpoint so every repetition stays closed.
    _move(S,items,[lambda ln,d:ln.X0-geom.CH_W,
                   lambda ln,d:ln.X0+geom.LANE_L+geom.CH_W-d.rect[2],
                   lambda ln,d:ln.X0], 'carrier_channel_wash')


def departure_resweep(S,items,gx):
    items=list(items)
    assert all(d.vol==.75 and d.rect[2:]==(4,4) for _,d in items)
    others=[d for d in S.drops.values() if d.name not in {q.name for _,q in items} and d.rects()]
    assert all(d.rect[1]>=172 for d in others),'返扫前其他载液须已离开反应区'
    # All previous leaves are already in the bottom band. The last actual
    # quarter-drop reaches the outlet, sweeps back, then exits on the old path.
    _move(S,items,[lambda ln,d:gx,lambda ln,d:ln.X0-geom.CH_W,
                   lambda ln,d:gx], 'last_real_packet_resweep')
