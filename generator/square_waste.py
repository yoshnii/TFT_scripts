"""Bead-liquid waste: binary mother cuts, native 4×4 leaves, no packet reshape.

Reserve one additional carrier cell per final leaf, without adding liquid.
Keep the unprocessed half compact; pad only the half currently being cut.
"""
import geom
import ops
import rigid as R
from native_packets import partition


def bites(S,items,blobs,kind,pellets=None,single_stop=False):
    import native_waste
    import waste_grid as W
    count=4 if kind=='cleanup' else 8
    plans=native_waste.schedule(count,4,4,kind)
    if single_stop:assert sum(p['flush'] for p in plans)==1,'整步废液无法安全一次装入F1–F4'
    gx=geom.gap_x(geom.waste_gap(items[0][0].c));jobs={};ticket=0
    captured=pellets is not None and kind in ('R04','C04')
    expanded_c=kind=='C04' and all(abs(d.vol-6.16)<1e-8 for ln,d in items)
    gap=2 if captured or expanded_c else geom.MIN_GAP
    cut_gap=6 if captured else geom.CUT_GAP
    cut_times=(1000,3000,3000,5000) if captured else None
    S.waste_schedule_events=getattr(S,'waste_schedule_events',[])
    S.waste_schedule_events.append(dict(kind=kind,packet_areas=[16]*count,
        actual_packet_ul=items[0][1].vol/count,schedule=plans,step=S.nsteps,
        equal_volume=True,no_packet_reshape=True,extra_carrier_cells_per_leaf=1))

    def quarter(branches):
        nonlocal ticket
        from ready_waste import quarter as ready_quarter
        ready_quarter(S,items,branches,plans,ticket,gx,blobs,jobs,kind,expanded_c,gap,cut_gap,cut_times)
        ticket+=4
    roots=[d for ln,d in items]
    if captured and kind=='R04' and getattr(S,'r05_parallel',False):
        from wide_r05 import first_half
        held=first_half(S,items,blobs,plans,jobs,gx)
        ticket=4
        from captured_channels import quarter as channel_quarter
        channel_quarter(S,items,held,plans,4,gx,blobs,jobs,'R06')
        ticket=8
    elif captured and expanded_c:
        from captured_channels import c05_c06
        c05_c06(S,items,blobs,plans,jobs,gx)
        ticket=8
    elif count==8:
        ops.move_rects(S,{d.name:ln.X0+(2 if expanded_c else 10) for ln,d in items})
        halves=partition(S,roots,2,'x',geom.CUT_GAP)
        # The first large split retains its published gap and 1000ms timing.
        # Only the following in-well cuts use two-cell waiting clearances.
        if captured:S.aqueous_wait_gap=2
        held=[pair[0] for pair in halves]
        moves={d.name:ln.X0 for (ln,_),d in zip(items,held)}
        if kind=='R04' and getattr(S,'r05_parallel',False):
            moves.update({pair[1].name:ln.X0+15 for (ln,_),pair in zip(items,halves)})
        ops.move_rects(S,moves)
        if expanded_c:
            for (ln,_),d in zip(items,held):
                d.electrode_area=66 if captured else 65
                d.margin_budget=d.electrode_area-geom.cells(d.vol)
                d.native_authorized_padding=True
                d.rect=((ln.X0,geom.physical_zone(ln.c,ln.r)[1],11,6) if captured
                        else (ln.X0,ln.Y0,13,5))
            S.step(geom.LONG_MS,changed=[d.name for d in held])
        R.reshape_many(S,[(d,(ln.X0,geom.physical_zone(ln.c,ln.r)[1],10,6))
                          for (ln,_),d in zip(items,held) if len(d.target_cells())==60])
        quarter([pair[1] for pair in halves])
        quarter(held)
    else:quarter(roots)
    assert ticket==count and not any(blobs.values())
    if pellets:
        for ln,d in items:
            p=pellets[ln.id];S.remove(p.name);p.name=ln.name;p.allow=();p.magnet_only=True;S.add(p)
