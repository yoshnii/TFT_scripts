"""Two single-cell moves, then three additional seconds for approved E mothers.

Independent travel jobs may work during another mother's three-second rest.
Synchronized cutting keeps its shared hold. Descendants stay slow through the opening of their cut;
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
        self.elapsed_ms=0
        self.parallel_run=False
        self.ready={};self.run_counts={}
        for d in roots:
            d.top_slow_mother=True
            self.previous[id(d)]=d.rect
        S.top_mother_events=getattr(S,'top_mother_events',[])

    def rest(self,S,reason,relay=None):
        if not any(self.counts.values()):return
        target=relay if relay is not None else self.last
        assert target is not None
        target.dur+=REST_MS
        if relay is None:self.elapsed_ms+=REST_MS
        event=dict(reagent=self.reagent,reason=reason,extra_ms=REST_MS,
                   frame=S.nsteps if relay is not None else S.nsteps-1,
                   moved_cells=sorted(v for v in self.counts.values() if v))
        self.events.append(event);S.top_mother_events.append(event)
        self.counts={}

    def record(self,S,relay):
        if self.parallel_run:
            self.record_parallel(S,relay)
            return
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
        self.elapsed_ms+=relay.dur

    def begin_run(self,S):
        assert not self.parallel_run
        self.rest(S,'before_independent_run')
        self.parallel_run=True;self.ready={};self.run_counts={}
        S.top_independent_rest_events=getattr(S,'top_independent_rest_events',[])

    def can_move(self,d):
        return not getattr(d,'top_slow_mother',False) or self.elapsed_ms>=self.ready.get(id(d),0)

    def record_parallel(self,S,relay):
        current={id(d):d for d in S.drops.values() if getattr(d,'top_slow_mother',False)}
        for key,d in current.items():
            old=self.previous.get(key,d.rect)
            distance=abs(d.rect[0]-old[0])+abs(d.rect[1]-old[1])
            assert d.rect[2:]==old[2:] and distance<=1
            if distance:
                assert self.elapsed_ms>=self.ready.get(key,0),'大母滴未完成3秒停留'
                count=self.run_counts.get(key,0)+1
                self.run_counts[key]=count
                if count==2:
                    self.ready[key]=self.elapsed_ms+relay.dur+REST_MS
                    self.run_counts[key]=0
                    S.top_independent_rest_events.append(dict(reagent=self.reagent,name=d.name,
                        move_frame=S.nsteps,move_end_ms=self.elapsed_ms+relay.dur,
                        resume_not_before_ms=self.ready[key],rest_ms=REST_MS,moved_cells=2))
            elif self.run_counts.get(key) and getattr(d,'pending',None)=='done':
                self.ready[key]=max(self.ready.get(key,0),self.elapsed_ms+REST_MS)
                self.run_counts[key]=0
                S.top_independent_rest_events.append(dict(reagent=self.reagent,name=d.name,
                    move_frame=S.nsteps-1,move_end_ms=self.elapsed_ms,
                    resume_not_before_ms=self.ready[key],rest_ms=REST_MS,moved_cells=1))
        self.previous={key:d.rect for key,d in current.items()}
        self.last=relay;self.elapsed_ms+=relay.dur

    def end_run(self,S):
        remaining=max((self.ready.get(id(d),0)-self.elapsed_ms for d in S.drops.values()
                       if getattr(d,'top_slow_mother',False)),default=0)
        if remaining>0:S.step(remaining,changed=[])
        self.parallel_run=False;self.ready={};self.run_counts={};self.counts={}

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
