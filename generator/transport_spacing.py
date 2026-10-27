"""Independent transport uses six cells; reaction-well operations keep their gap."""
import geom
from functools import lru_cache


def well(rect):
    return _well(tuple(rect))


@lru_cache(maxsize=32768)
def _well(rect):
    x,y,w,h=rect
    for c in range(1,7):
        for r in range(1,17):
            X,Y=geom.physical_zone(c,r)
            if X<=x and x+w<=X+36 and Y<=y and y+h<=Y+6:return c,r
    return None


TRAIN_GAP=5


def in_channel(S,r):
    gx=getattr(S,'batch_channel_x',None)
    # The channel itself plus the door strip a packet crosses from its well.
    return (gx is not None and gx-10<=r[0] and r[0]+r[2]<=gx+geom.CH_W
            and r[1]>=23 and r[1]+r[3]<=171)


def required(S,a,ra,b,rb):
    if not getattr(S,'strict_transport',False):return geom.MIN_GAP
    local=local_cut_gap(S,a,ra,b,rb)
    if local is not None:return local
    if well(ra) is not None and well(rb) is not None:return geom.MIN_GAP
    # Approved C02-only door transition: a packet entering its reaction well
    # may pass a waiting guest in the adjacent well at the original clearance.
    # Two independent channel packets still require six cells.
    for d,r,other in ((a,ra,rb),(b,rb,ra)):
        if (getattr(d,'c02_entry_exception',False) and well(other) is not None
                and 27<=r[1]<172 and 6<=r[0]<10):return geom.MIN_GAP
    if ra[1]+ra[3]<=23 and rb[1]+rb[3]<=23:return 6
    moving=lambda d:getattr(d,'native_packet',False) or getattr(d,'waste_grid_packet',False)
    # 20261021 user-approved exception: one whole-column waste batch enters
    # the outlet channel together and descends as a train; packets of the
    # SAME batch keep the 9-row well pitch, i.e. five cells for 4-high
    # packets. Only inside the vertical channel above the Y172 band.
    if (moving(a) and moving(b) and getattr(a,'batch_round',None) is not None
            and getattr(a,'batch_round',None)==getattr(b,'batch_round',None)
            and getattr(S,'c05_cut_gap',None)!=2
            and in_channel(S,ra) and in_channel(S,rb)):
        return TRAIN_GAP
    if moving(a) and moving(b):
        # Include the last-row channel mouth (Y162 onwards) when the other
        # object is already in Y172 outlet staging. Two packets higher up, or
        # two packets in the vertical queue, still require six cells.
        if min(ra[1],rb[1])>=162 and max(ra[1],rb[1])>=172 and getattr(S,'outlet_staging_gap',None):
            return S.outlet_staging_gap
        return 6
    return geom.MIN_GAP


def local_cut_gap(S,a,ra,b,rb):
    """Approved captured-aqueous waiting gaps; never outside one well."""
    if ((getattr(S,'c05_cut_gap',None)==2 or getattr(S,'aqueous_wait_gap',None)==2) and well(ra) is not None
            and well(ra)==well(rb) and a.vol>0 and b.vol>0
            and a.name.startswith('LIQ') and b.name.startswith('LIQ')):
        return 2
    return None


def queue_exit(S,d,gx,path):
    """Release deepest rows in order; only the active head lowers one cell.

    A four-high packet can use the sixth physical well row before entering the
    corridor, giving six cells to the unchanged packet in the preceding row.
    """
    if not getattr(S,'strict_transport',False):
        yield from path;return
    import rigid as R
    if getattr(d,'batch_round',None) is not None and getattr(S,'c05_cut_gap',None)!=2:
        yield from _batch_exit(S,d,gx,path);return
    state=getattr(S,'waste_entry_state',None)
    if state is None:state=S.waste_entry_state=dict(issued=0,next=0)
    ticket=state['issued'];state['issued']+=1
    # Pre-advance (compact collection only): while waiting for its exit
    # ticket, a packet may shift one cell per frame toward the exit inside
    # its own reaction well, stopping six cells short of the channel so it
    # never narrows the gap to packets descending the channel. Each step is
    # checked with the exact rect_ok/door rule the pool applies, so a
    # yielded step always executes. Exit order is unchanged.
    home=well(d.rect) if getattr(d,'pre_advance_exit',False) else None
    rule=R._door_rule(gx)
    while state['next']!=ticket:
        if home is not None:
            cand=(d.rect[0]+1,d.rect[1],d.rect[2],d.rect[3])
            if (cand[0]+cand[2]<=gx-6 and well(cand)==home
                    and R.rect_ok(S,d.name,cand,frozenset(getattr(d,'allow',())))
                    and rule(d,cand)):
                yield '+X';continue
        yield 'wait'
    origin=d.rect[1];released=False
    if well(d.rect) is not None and d.rect[3]<=4:
        yield from R.go(d,[('y',origin+1)])
    for action in path:
        yield action
        # Pre-advanced packets hand over as soon as the head is inside the
        # channel (it only moves away from the door afterwards); others keep
        # the original two-cell descent condition.
        if not released and home is not None and d.rect[0]==gx:
            state['next']+=1;released=True
        if not released and d.rect[0]==gx and d.rect[1]>=origin+2:
            state['next']+=1;released=True
    if not released:state['next']+=1


def _round_done(S,members):
    import waste_grid as W
    return all(S.drops.get(q.name) is not q or q.rect[1]>=W.TRANSPORT_Y for q in members)


def _batch_exit(S,d,gx,path):
    """Whole-column batch (user approved 20261021).

    Every packet of one send waits, pre-advanced inside its own well, until
    earlier batches reach Y172 (R05 only may start at Y164, with every actual
    movement still subject to rectangle clearance and door checks). All packets of the
    batch lower one cell and slide into the channel in the same frames and
    descend as one train at the well pitch. Spacing, door and channel checks
    are still applied to every single-cell step by the pool (rect_ok/rule).
    """
    import rigid as R
    rounds=S.batch_members;me=d.batch_round;rule=R._door_rule(gx)
    home=well(d.rect)
    def earlier_done():
        if getattr(d,'early_exit',False):
            return all(S.drops.get(q.name) is not q or q.rect[1]>=164
                       for k in rounds if k<me for q in rounds[k])
        return all(_round_done(S,rounds[k]) for k in rounds if k<me)
    while True:
        if home is not None:
            cand=(d.rect[0]+1,d.rect[1],d.rect[2],d.rect[3])
            if (cand[0]+cand[2]<=gx-6 and well(cand)==home
                    and R.rect_ok(S,d.name,cand,frozenset(getattr(d,'allow',())))
                    and rule(d,cand)):
                d.batch_parked=False
                yield '+X';continue
        d.batch_parked=True
        if earlier_done() and all(getattr(q,'batch_parked',False) or S.drops.get(q.name) is not q
                                   for q in rounds[me]):
            break
        yield 'wait'
    S.batch_events=getattr(S,'batch_events',[])
    if not any(e['round']==me for e in S.batch_events):
        S.batch_events.append(dict(round=me,start_frame=S.nsteps,packets=len(rounds[me])))
    if home is not None and d.rect[3]<=4:
        yield from R.go(d,[('y',d.rect[1]+1)])
    yield from path
