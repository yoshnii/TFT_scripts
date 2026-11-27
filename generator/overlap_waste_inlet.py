"""Schedule existing native second-by-second traces across the well-exit boundary."""
from six_column_schedule import apart, frames, write_frames, seconds

EMPTY=frozenset()


def ordinary(flow):
    assert all(c.get('Component')=='HighVoltage' for c in flow)
    return [s for c in flow for s in frames(c)]


def mix_trace(flow):
    start,cycle,timer,tail=flow
    rounds,r=divmod(seconds(timer),seconds(cycle))
    assert not r and 0<rounds<10 and cycle.get('Params')=='SCRIPTCYCLE_TRUE'
    return frames(start)+frames(cycle)*rounds+frames(tail)


def split_inlet(state,left):
    setting,rects=state
    incoming=frozenset(r for r in rects if r[1]+r[3]<=27 or r[0]+r[2]<=left)
    return (setting,rects-incoming),incoming


def last_exit(trace,left,retained):
    """Half-open footprint intersection includes a packet straddling a doorway."""
    last=-1
    for i,(_,rects) in enumerate(trace):
        for row in range(16):
            y=27+9*row
            count=sum(x<left+36 and left<x+w and ry<y+6 and y<ry+h for x,ry,w,h in rects)
            if count>retained:
                last=i
                break
    assert 0<last<len(trace)-1
    return last+1


def partition_post(post,left):
    host=[];guest=[]
    for state in post:
        (s,h),g=split_inlet(state,left)
        if len(h)!=16 or not g:break
        if len({y for x,y,w,d in h})!=16:break
        host.append(h);guest.append(g)
    assert guest
    return host,guest,post[len(host):]


def put(flow,trace):
    """Preserve existing command graph and boundaries, resize only the last command."""
    offset=0
    for cmd in list(flow)[:-1]:
        width=seconds(cmd)
        write_frames(cmd,trace[offset:offset+width])
        offset+=width
    assert offset<len(trace)
    write_frames(flow[-1],trace[offset:])
    flow.set('Timeout',str(len(trace)))
    assert ordinary(flow)==trace


class Feed:
    def __init__(self,states,initial):
        assert states and states[0]==initial
        self.states=states
        self.position=0
        self.held=initial

    def tick(self,occupied,allow=True):
        if allow and self.position<len(self.states):
            nxt=self.states[self.position]
            if apart(nxt,occupied):
                self.held=nxt
                self.position+=1
        assert apart(self.held,occupied),('parked inlet blocks moving stream',self.position)
        return self.held


def handoff(feed,host_trace,landing,waste_tail,setting):
    # The slowest required host movement and its native final dwell precede entry.
    last=max((i for i in range(1,len(host_trace)) if host_trace[i]!=host_trace[i-1]),default=0)
    required=min(last+4,len(host_trace))
    delay=next(d for d in range(len(waste_tail)+1) if all(
        apart(host_trace[min(i-d,required-1)],waste_tail[i])
        for i in range(d,len(waste_tail))))
    result=[]
    entry=None
    delivered=0
    parked=host_trace[required-1]
    while delivered<len(landing):
        i=len(result)
        h=host_trace[min(max(0,i-delay),required-1)]
        w=waste_tail[i] if i<len(waste_tail) else EMPTY
        assert i<delay or apart(h,w)
        if feed.position<len(feed.states):
            active=h|feed.tick(h|w)
        else:
            s,scheduled=landing[delivered]
            assert s==setting
            # Move incoming packets independently of resident positioning after
            # clearance. At contact, replay the original simultaneous well merges.
            if i>=delay+required:
                active=scheduled
                independent=scheduled
            elif parked<=scheduled and apart(scheduled-parked,h):
                independent=scheduled-parked
                active=independent|h
            else:
                active=None
            # A resident waiting at its original position already has an intact
            # native separation from the outgoing stream. Check the added packets.
            if active is None or not apart(independent,w):
                previous=feed.held if not delivered else landing[delivered-1][1]-parked
                assert apart(previous,h|w)
                active=previous|h
            else:
                if entry is None:entry=i
                delivered+=1
        result.append((setting,active|w))
        assert len(result)<=len(feed.states)+len(host_trace)+len(landing)+len(waste_tail)+10
    i=len(result)
    while i<len(waste_tail):
        w=waste_tail[i]
        assert apart(landing[-1][1],w)
        result.append((setting,landing[-1][1]|w));i+=1
    return result,entry


def cdna(f,left,overlap, *, schedule_handoff=handoff):
    waste,bridge,delivery=[f[k] for k in ('A06','A07','A08')]
    raw=ordinary(waste)
    resident=[];inlet=[]
    for state in raw:
        host,g=split_inlet(state,left)
        resident.append(host);inlet.append(g)
    assert len({s for s,_ in raw+ordinary(bridge)+ordinary(delivery)})==1
    start=len(raw)-(overlap+1)
    assert len(set(inlet[:start+1]))==1 and len(inlet[0])==2
    clear=last_exit(resident,left,1)
    anchors=resident[-1][1]
    ready=max(clear,1+max(i for i,(_,r) in enumerate(resident) if not anchors<=r))
    host,guests,landing=partition_post(ordinary(bridge)+ordinary(delivery),left)
    assert host[0]==anchors
    feed=Feed(inlet[start:]+guests,inlet[0])
    result=[]
    for s,r in resident[:ready]:
        result.append((s,r|feed.tick(r)))
    waiting=feed.position
    rest,entry=schedule_handoff(feed,host,landing,[r-anchors for _,r in resident[ready:]],raw[0][0])
    result+=rest
    # The aspiration command keeps its original duration and every F-port mask.
    for (_,new),(_,old) in zip(result,raw):
        assert {r for r in new if r[1]>=170}=={r for r in old if r[1]>=170}
    boundary=len(raw);bridge_end=boundary+seconds(bridge)
    assert len(result)>bridge_end
    put(waste,result[:boundary]);put(bridge,result[boundary:bridge_end]);put(delivery,result[bridge_end:])
    final=landing[-1][1]
    assert len(final)==16
    merge_s=next(i for i,(_,r) in enumerate(result) if final<=r)
    assert all(not (r & final) for _,r in result[:merge_s])
    return dict(exit_s=clear,retained_ready_s=ready,first_entry_s=ready+entry,all_wells_final_frame_s=merge_s,
                waste_end_s=boundary,prepared_trace_seconds_before_release=waiting)


def barcode(f,left,overlap):
    load,waste,merge,mix,bridge,delivery=[f[k] for k in ('B06','B07','B08','B09','B10','B11')]
    raw=ordinary(waste);old_merge=ordinary(merge)
    mixing=mix_trace(mix)
    hosts=[];guests=[]
    for state in mixing:
        (s,h),g=split_inlet(state,left)
        assert len(h)==16
        hosts.append(h);guests.append(g)
    entry,prep,landing=partition_post(ordinary(bridge)+ordinary(delivery),left)
    initial=old_merge[-1][1]-old_merge[-2][1]
    assert len(initial)==2 and initial==guests[0]
    start=len(mixing)-(overlap+1)
    assert len(set(guests[:start+1]))==1
    feed=Feed(guests[start:]+prep,initial)
    clear=last_exit(raw,left,2);anchors=raw[-1][1]
    ready=max(clear,1+max(i for i,(_,r) in enumerate(raw) if not anchors<=r))
    fusion=old_merge[:-1]
    assert fusion[0][1]==anchors and len(fusion[-1][1])==16
    assert ready+len(fusion)<len(raw)
    setting=raw[0][0]
    assert all(s==setting for s,_ in raw+old_merge+mixing+landing)
    gate=ordinary(load)
    assert all(s==setting and apart(r,initial) for s,r in gate)
    put(load,[(s,r|initial) for s,r in gate])
    # Park only where the entire unchanged mixing excursion can pass.
    swept=frozenset().union(*hosts)
    result=[];first_move=None
    for i,(s,r) in enumerate(raw):
        if i>=ready:
            new=fusion[min(i-ready,len(fusion)-1)][1]
            outlet=r-anchors
            assert apart(new,outlet)
            r=outlet|new
        g=feed.tick(r|swept)
        if first_move is None and g!=initial:first_move=i
        result.append((s,r|g))
    assert first_move is not None and first_move<clear
    for (_,old),(_,new) in zip(raw,result,strict=True):
        assert {r for r in old if r[1]>=170}=={r for r in new if r[1]>=170}
    first_join=next(i for i,(_,r) in enumerate(fusion) if r==fusion[-1][1])
    assert all(not (r & fusion[-1][1]) for _,r in fusion[:first_join])
    put(waste,result)
    # The repeating native electrode command must have one identical guest mask
    # on every iteration. The existing unrolled final rounds can advance again.
    suffix=len(mixing)-seconds(mix[-1])
    new_mix=[]
    for i,((s,old),h) in enumerate(zip(mixing,hosts)):
        g=feed.tick(swept,allow=i>=suffix)
        new_mix.append((s,h|g))
    a,b,t,c=mix;lead,period=seconds(a),seconds(b)
    # Use B08 for the first native positioning moves after withdrawal.
    prefix=len(merge)
    assert 0<prefix<lead
    for i,command in enumerate(merge):write_frames(command,new_mix[i:i+1])
    merge.set('Timeout',str(prefix))
    mix.set('Timeout',str(seconds(mix)-prefix))
    write_frames(a,new_mix[prefix:lead]);write_frames(b,new_mix[lead:lead+period])
    write_frames(c,new_mix[suffix:])
    assert ordinary(merge)+mix_trace(mix)==new_mix
    after,entry_idx=handoff(feed,entry,landing,[],setting)
    cut=seconds(bridge)
    put(bridge,after[:cut]);put(delivery,after[cut:])
    load.set('Name',load.get('Name')+' · E1/E2同时手工加连接预混液')
    load[-1].set('Name',load[-1].get('Name')+'；E1/E2 各11.20µL连接预混液')
    merge.set('Name',merge.get('Name').split(' · ')[0]+' · 接头合并后开始混匀定位')
    for c in merge:c.set('Name','接头混匀前定位，入口试剂待命')
    waste.set('Name',waste.get('Name')+' · 接头同步合并与连接液预分装')
    return dict(exit_s=clear,barcode_merge_start_s=ready,barcode_merge_final_frame_s=ready+first_join,
                ligation_prep_first_move_s=first_move,waste_end_s=len(raw),ligation_entry_after_mix_s=entry_idx)
