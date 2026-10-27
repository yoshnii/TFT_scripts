"""Two single-cell moves, then three additional seconds for approved E mothers.

Only timing changes. Descendants stay slow through the opening of their cut;
once a branch reaches <=30 carrier cells it stays fast, even if an already
approved small receiving reshape subsequently adds two cells (G8/LIG 30→32).
"""
APPROVED=frozenset(('AMP','G14','G8','LIG','EDTA','G16'))
REST_MS=3000
SMALL_CELLS=30


class MotherClock:
    def __init__(self,S,reagent,roots):
        self.reagent=reagent
        self.previous={}
        self.counts={}
        self.last=None
        self.events=[]
        for d in roots:
            d.top_slow_mother=True
            self.previous[id(d)]=d.rect
        S.top_mother_events=getattr(S,'top_mother_events',[])

    def rest(self,S,reason,relay=None):
        if not any(self.counts.values()):return
        target=relay if relay is not None else self.last
        assert target is not None
        target.dur+=REST_MS
        event=dict(reagent=self.reagent,reason=reason,extra_ms=REST_MS,
                   frame=S.nsteps if relay is not None else S.nsteps-1,
                   moved_cells=sorted(v for v in self.counts.values() if v))
        self.events.append(event);S.top_mother_events.append(event)
        self.counts={}

    def record(self,S,relay):
        current={id(d):d for d in S.drops.values() if getattr(d,'top_slow_mother',False)}
        moves={};stopped=False
        for key,old in self.previous.items():
            d=current.get(key)
            if d is None or d.rect[2:]!=old[2:]:
                stopped|=bool(self.counts.get(key));continue
            distance=abs(d.rect[0]-old[0])+abs(d.rect[1]-old[1])
            assert distance<=1,('大母滴不能跳格',self.reagent,old,d.rect)
            if distance:moves[key]=distance
            elif self.counts.get(key):stopped=True
        # An odd final move is settled before a reshape, split or other stage.
        if stopped:self.rest(S,'segment_end')
        for key,value in moves.items():self.counts[key]=self.counts.get(key,0)+value
        if any(v>=2 for v in self.counts.values()):self.rest(S,'two_moves',relay)
        self.previous={key:d.rect for key,d in current.items()}
        self.last=relay

    def finish_split(self,S,groups):
        self.rest(S,'split_end')
        for children in groups:
            for d in children:
                if len(d.target_cells())<=SMALL_CELLS:d.top_slow_mother=False

    def finish(self,S):
        self.rest(S,'delivery_end')


def start(S,reagent,roots):
    assert getattr(S,'top_mother_clock',None) is None
    if reagent in APPROVED:S.top_mother_clock=MotherClock(S,reagent,roots)


def finish(S):
    clock=getattr(S,'top_mother_clock',None)
    if clock:clock.finish(S)
    S.top_mother_clock=None
