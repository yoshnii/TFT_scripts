"""Overlay independent native electrode streams in the 16-well, six-column layout."""
import re
from copy import deepcopy
from functools import lru_cache
from itertools import groupby
from operator import itemgetter
from xml.etree import ElementTree as ET

FIELDS = ('Amp', 'WaveForm', 'Freq')
RECT = re.compile(r'\((\d+),(\d+)\)\((\d+),(\d+)\)')


def frames(cmd):
    result = []
    for relay in cmd:
        rects = []
        for token in filter(None, relay.get('RelayCode').split(';')):
            y,x,h,w = map(int, RECT.fullmatch(token).groups())
            rects.append((x,y,w,h))
        duration, remainder = divmod(int(relay.get('Duration')),1000)
        assert not remainder and len(set(rects)) == len(rects)
        result.extend([(tuple(relay.get(k) for k in FIELDS), frozenset(rects))]*duration)
    assert len(result) == int(cmd.get('Timeout'))
    return result


def write_frames(cmd, states):
    assert states
    del cmd[:]
    for i, ((setting,rects), group) in enumerate(groupby(states),1):
        code = ';'.join(f'({y},{x})({h},{w})' for x,y,w,h in sorted(rects,key=itemgetter(1,0,2,3)))
        ET.SubElement(cmd,'RelayItem',dict(ItemID=str(i),RelayCode=code,
                      Duration=str(1000*sum(1 for _ in group)),**dict(zip(FIELDS,setting))))
    cmd.set('Timeout',str(len(states)))
    assert frames(cmd) == states


@lru_cache(maxsize=32000)
def apart(a,b):
    return all(max(max(x[i],y[i])-min(x[i]+x[i+2],y[i]+y[i+2]) for i in (0,1))>=6
               for x in a for y in b)


def union(a,b):
    assert a[0]==b[0] and apart(a[1],b[1])
    return a[0], a[1]|b[1]


def seconds(flow):
    return int(flow.get('Timeout'))


def native(flow):
    if flow.get('SyncEndingCmdList'):
        start,cycle,timer = flow
        assert cycle.get('Params')=='SCRIPTCYCLE_TRUE' and seconds(timer)==10*seconds(cycle)
        result=frames(start)+frames(cycle)*10
    else:
        result=[s for c in flow for s in frames(c)]
    assert len(result)==seconds(flow)
    return result


def in_well(r, margin=0):
    x,y,w,h=r
    return 10-margin<=x and x+w<=46+margin and 27<=y<168 and (y-27)%9+h<=6


def transfer(load_at,work,bridge,delivery,new_id):
    """Load before work. Keep every resident movement and time every guest frame once."""
    original=native(work)
    between=native(bridge)
    post=between+native(delivery)
    initial=between[-1][1]-frozenset(r for r in between[-1][1] if in_well(r))
    assert len(initial)==2
    initial=(between[-1][0],initial)
    hosts=[]; guests=[]
    for setting,rects in post:
        host=frozenset(r for r in rects if in_well(r))
        guest=rects-host
        if not guest and not guests:
            guest=initial[1]
        if len(host)!=16 or not guest or any(y+h>23 and x+w>10 for x,y,w,h in guest):
            break
        assert len({(r[1]-27)//9 for r in host})==16
        hosts.append((setting,host))
        guests.append((setting,guest))
    keep=max((i for i in range(1,len(hosts)) if hosts[i]!=hosts[i-1]),default=0)+4
    for n in range(min(len(hosts)-max(keep,len(between)),len(original)),0,-1):
        resident=original[-n:]+hosts[:-n]
        if all(a[0]==b[0] and apart(a[1],b[1]) for a,b in zip(resident,guests)):
            break
    else:
        raise ValueError('No disjoint reagent preparation window')
    assert hosts[-n-1]==hosts[-1]
    combined=[union(s,initial) for s in original[:-n]]
    combined.extend(union(s,g) for s,g in zip(original[-n:],guests[:n]))
    continuation=[union(s,g) for s,g in zip(hosts[:-n],guests[n:])]+post[len(hosts):]
    assert len(continuation)==len(post)-n and len(combined)==len(original)
    for actual,reference in zip(combined,original,strict=True):
        assert reference[1]<=actual[1] and actual[0]==reference[0]

    # Pausing at the existing end-of-section manual loading boundary now occurs
    # one section earlier. Retain the preceding stage's final electrode footprint.
    previous=next(c for c in reversed(list(load_at)) if c.get('Component')=='HighVoltage')
    tail=frames(previous)[-1][1]
    load=deepcopy(bridge[-1])
    load.set('ID',str(new_id))
    parent=load_at.get('SyncEndingCmdList','').split(',')[0] or load_at[-1].get('ID')
    load.set('PreTask',parent+',FS')
    write_frames(load,[union((initial[0],tail),initial)])
    load_at.append(load)
    load_at.set('Timeout',str(seconds(load_at)+1))
    load_at.set('Name',load_at.get('Name')+' · E1/E2提前手工加液')
    bridge.set('Name',bridge.get('Name').split(' · ')[0]+' · 配送续行')
    for c in bridge:
        c.set('Name','配送续行 · 样本定位与试剂并行')
    if work.get('SyncEndingCmdList'):
        start,cycle,timer=work
        leadin,period=seconds(start),seconds(cycle)
        rounds=(len(original)-n-leadin)//period
        assert 1<=rounds<10
        boundary=leadin+rounds*period
        write_frames(start,combined[:leadin])
        write_frames(cycle,combined[leadin:leadin+period])
        assert combined[leadin:boundary]==frames(cycle)*rounds
        timer.set('Timeout',str(rounds*period))
        timer.set('Name',f'混匀前{rounds}轮 · 同步结束')
        # The final rounds have a different reagent mask every second.
        finish=deepcopy(start)
        finish.set('ID',str(new_id+1))
        finish.set('PreTask',timer.get('ID')+',FS')
        finish.set('Name',f'混匀余下{10-rounds}轮 · 与试剂配送并行（共10轮）')
        write_frames(finish,combined[boundary:])
        work.append(finish)
        assert frames(start)+frames(cycle)*rounds+frames(finish)==combined
    else:
        offset=0
        for cmd in work:
            size=seconds(cmd)
            write_frames(cmd,combined[offset:offset+size])
            offset+=size
        assert offset==len(combined)
    offset=0
    for cmd in bridge:
        size=seconds(cmd)
        write_frames(cmd,continuation[offset:offset+size])
        offset+=size
    assert len(delivery)==1
    write_frames(delivery[0],continuation[offset:])
    delivery.set('Timeout',delivery[0].get('Timeout'))
    return n-1


def waste(first,second):
    a,b=native(first),native(second)
    anchors=a[-1][1]
    assert len(anchors)==16 and all(in_well(r) for r in anchors)
    tail=0
    for _,rects in reversed(a):
        if not anchors<=rects:break
        tail+=1
    prep=0
    for _,rects in b:
        if not all(in_well(r,4) for r in rects):break
        prep+=1
    for n in range(min(prep,tail-1,len(b)-1),0,-1):
        # Break at the end of a settled footprint, before the next movement.
        if n<2 or b[n-1]!=b[n-2] or b[n]==b[n-1]:continue
        joint=[]
        for (s,rects),ready in zip(a[-n:],b[:n]):
            outgoing=(s,rects-anchors)
            if s!=ready[0] or not apart(outgoing[1],ready[1]):break
            joint.append(union(outgoing,ready))
        if len(joint)==n:break
    else:
        raise ValueError('No settled waste handoff')
    assert joint[-1]==b[n-1]
    write_frames(first[0],a[:-n]+joint)
    write_frames(second[0],b[n:])
    second.set('Timeout',str(len(b)-n))
    return n
