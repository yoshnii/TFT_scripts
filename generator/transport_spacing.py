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


def required(S,a,ra,b,rb):
    if not getattr(S,'strict_transport',False):return geom.MIN_GAP
    if well(ra) is not None and well(rb) is not None:return geom.MIN_GAP
    # Approved C02-only door transition: a packet entering its reaction well
    # may pass a waiting guest in the adjacent well at the original clearance.
    # Two independent channel packets still require six cells.
    for d,r,other in ((a,ra,rb),(b,rb,ra)):
        if (getattr(d,'c02_entry_exception',False) and well(other) is not None
                and 27<=r[1]<172 and 6<=r[0]<10):return geom.MIN_GAP
    if ra[1]+ra[3]<=23 and rb[1]+rb[3]<=23:return 6
    moving=lambda d:getattr(d,'native_packet',False) or getattr(d,'waste_grid_packet',False)
    if moving(a) and moving(b):
        # Include the last-row channel mouth (Y162 onwards) when the other
        # object is already in Y172 outlet staging. Two packets higher up, or
        # two packets in the vertical queue, still require six cells.
        if min(ra[1],rb[1])>=162 and max(ra[1],rb[1])>=172 and getattr(S,'outlet_staging_gap',None):
            return S.outlet_staging_gap
        return 6
    return geom.MIN_GAP


def queue_exit(S,d,gx,path):
    """Release deepest rows in order; only the active head lowers one cell.

    A four-high packet can use the sixth physical well row before entering the
    corridor, giving six cells to the unchanged packet in the preceding row.
    """
    if not getattr(S,'strict_transport',False):
        yield from path;return
    import rigid as R
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
