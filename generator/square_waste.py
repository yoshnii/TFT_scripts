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
    original={ln.id:d.vol for ln,d in items}
    S.waste_schedule_events=getattr(S,'waste_schedule_events',[])
    S.waste_schedule_events.append(dict(kind=kind,packet_areas=[16]*count,
        actual_packet_ul=items[0][1].vol/count,schedule=plans,step=S.nsteps,
        equal_volume=True,no_packet_reshape=True,extra_carrier_cells_per_leaf=1))

    def send(leaves):
        nonlocal ticket
        pieces=[]
        for (ln,root),d in zip(items,leaves):
            assert d.rect[2:]==(4,4) and d.vol==original[ln.id]/count
            d.native_packet=True;d.native_leaf_shape=(4,4)
            pieces.append((ln,d))
        # Bead-carrying waste is never a channel-washing liquid. A separate
        # fresh-liquid wash is scheduled after the final bead-disposal flow.
        plan=plans[ticket];ticket+=1
        tiles=W._send_pipeline(S,pieces,gx,blobs,plan,jobs)
        if plan['flush']:
            W._drain(S,lambda:not S.waste_pool.items);W.flush(S,blobs)
        else:
            W._drain(S,lambda:all(S.drops.get(d.name) is not d or d.rect[1]>=W.TRANSPORT_Y for d in tiles))

    def quarter(branches):
        # Select the leftmost legal cutting station, considering actual held
        # liquid rather than the magnet marker. The cutting line, not the
        # entire subsequently opened gap, must avoid the magnet centre.
        active={d.name for d in branches};positions={}
        for (ln,_),d in zip(items,branches):
            others=[q.rect[0]+q.rect[2] for q in S.drops.values()
                    if q.name not in active and q.vol>0 and ln.Y0-1<=q.rect[1]<ln.Y0+5
                    and ln.X0<=q.rect[0]<ln.X0+36]
            minimum=max(ln.X0+geom.CUT_GAP,max(others,default=ln.X0-geom.MIN_GAP)+geom.MIN_GAP+geom.CUT_GAP)
            choices=[x for x in range(minimum,ln.X0+36-16+1) if x+8!=ln.mag]
            assert choices,('区内切分站放不下',ln.id,others)
            positions[d.name]=min(choices)
        # A held 60-cell half uses the hole-free left 10×6 rectangle. Restore
        # its cutting frame there before moving; never slide a six-high drop
        # beneath S/B holes.
        converted=[]
        for (ln,_),d in zip(items,branches):
            if d.rect[3]==6:
                d.electrode_area=64;d.margin_budget=64-geom.cells(d.vol)
                d.native_authorized_padding=True;d.rect=(d.rect[0],ln.Y0,16,4)
                converted.append(d.name)
        if converted:S.step(geom.LONG_MS,changed=converted)
        ops.move_rects(S,positions)
        S.approved_mother_shapes=getattr(S,'approved_mother_shapes',[])
        for (ln,_),d in zip(items,branches):
            before=list(d.rect);before_cells=len(d.target_cells())
            x=positions[d.name]
            target=(x,ln.Y0,16,4)
            assert d.rect[0]<x+16 and x<d.rect[0]+d.rect[2]
            S.approved_mother_shapes.append(dict(reagent=kind,name=d.name,frame=S.nsteps,
                actual_ul=d.vol,before=before,after=list(target),before_cells=before_cells,
                after_cells=64,hold_ms=geom.LONG_MS,
                previous_leaf_carrier_cells=15,leaf_carrier_cells=16,leaves=4,
                additional_cells_over_previous_leaf_plan=4))
            d.electrode_area=64;d.margin_budget=64-geom.cells(d.vol)
            d.native_authorized_padding=True;d.native_fractional_volume=True;d.rect=target
        S.step(geom.LONG_MS,changed=[d.name for d in branches])
        halves=partition(S,branches,2,'x',geom.CUT_GAP)
        # Each eight-wide mother travels to the outlet only once. The held
        # sibling remains where it was split, instead of returning to X0.
        for side in (1,0):
            mothers=[pair[side] for pair in halves]
            active={d.name for d in mothers};targets={}
            for (ln,_),d in zip(items,mothers):
                ends=[q.rect[0]+q.rect[2] for q in S.drops.values() if q.name not in active
                      and q.vol>0 and ln.Y0-1<=q.rect[1]<ln.Y0+5 and ln.X0<=q.rect[0]<ln.X0+36]
                minimum=max(ln.X0+4,max(ends,default=ln.X0-3)+7)
                candidates=[x for x in range(minimum,ln.X0+36-8+1) if x+4!=ln.mag]
                assert candidates,('区内末级切分站放不下',ln.id,ends)
                targets[d.name]=min(candidates)
            ops.move_rects(S,targets)
            leaves=partition(S,mothers,2,'x',geom.CUT_GAP)
            send([pair[1] for pair in leaves])
            send([pair[0] for pair in leaves])

    roots=[d for ln,d in items]
    if count==8:
        ops.move_rects(S,{d.name:ln.X0+10 for ln,d in items})
        halves=partition(S,roots,2,'x',geom.CUT_GAP)
        held=[pair[0] for pair in halves]
        ops.move_rects(S,{d.name:ln.X0 for (ln,_),d in zip(items,held)})
        R.reshape_many(S,[(d,(ln.X0,geom.physical_zone(ln.c,ln.r)[1],10,6))
                          for (ln,_),d in zip(items,held) if len(d.target_cells())==60])
        quarter([pair[1] for pair in halves])
        quarter(held)
    else:quarter(roots)
    assert ticket==count and not any(blobs.values())
    if pellets:
        for ln,d in items:
            p=pellets[ln.id];S.remove(p.name);p.name=ln.name;p.allow=();p.magnet_only=True;S.add(p)
