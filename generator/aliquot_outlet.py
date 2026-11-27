"""Compare row-ready outlet schedules through the same downstream reagent merge."""
from copy import deepcopy
import json
import subprocess
from xml.etree import ElementTree as ET

from build_four_column import board, DEST, ROOT, BASE, SCRIPT, shifted_relay, source_blob, seed, rigid, export, audit_waste
from six_column_schedule import apart, frames, seconds, write_frames
from overlap_waste_inlet import barcode, cdna, ordinary, split_inlet
from loading_sections import OVERLAP, json_bytes
from update_ready_parallel_local import FrameTrace, check_dwell
from update_waste_inlet_local import PROJECTS, check_footprints
from dual_outlet import publish, xml, replace_flow, retime

SECTIONS={'A06':('A06','A07','A08'),'B07':('B06','B07','B08','B09','B10','B11')}
CURRENT={'A06':('A06','A08'),'B07':('B07','B08','B09','B11')}


def flows(data):
    return {f.get('Name').split()[0]:f for f in ET.fromstring(data)}


def fifo_tail(states):
    bottom=[(s,frozenset(r for r in rects if r[1]>=170)) for s,rects in states]
    touch=next(i for i,(_,rs) in enumerate(bottom) if any(y+h>=190 for x,y,w,h in rs))
    return bottom[touch-1:]


def simulate(stage,outer,source,baseline,overlap):
    f=source[stage]
    if stage=='A06':
        p=seed(shifted_relay(f[0][0]),[('',2.)])
        p.S.relays[0].dur=int(f[0][0].get('Duration'))
    else:
        p=seed(shifted_relay(f[0][-1]),[('',2.),('K',.6),('BKWA',.6)])
        p.S.flush()
    if outer is not None:p.S.aliquot_outer_rows=outer
    p.S.split_events=[]
    trace=FrameTrace(p)
    if stage=='A06':
        p.split_waste('A',stage,.5,'cDNA多余液体')
        expected={'F3':12.,'F1':12.}
        assert all(p.S.get(ln.name).vol==.5 for ln in p.ALL)
    else:
        rigid.egress_pieces(p.S,[(ln,p.S.get('BKWA'+ln.id)) for ln in p.ALL],p.blob)
        p.flow('B',stage,[p.cmd('F1')])
        expected={'F1':9.6}
        assert all((p.S.get(ln.name).vol,p.S.get('K'+ln.id).vol)==(2.,.6) for ln in p.ALL)
    raw=ET.fromstring(export(p)[0].xml())
    audit=audit_waste(p,sum(expected.values()))
    assert audit['withdrawals']==1 and p.S.manual_aspirations[0][1]['port_volumes']==expected
    audit.update(check_dwell(p,trace))
    cut=[(e['parent'],e['volumes'],e['gaps'],e['positions'],e.get('requested_frame_ms')) for e in p.S.split_events]
    previous=frozenset()
    simultaneous=0
    for _,rs in frames(raw[-1]):
        rails={x for x,y,w,h in rs if x in (58,68) and 28<=y<=163 and (x,y-1,w,h) in previous and (x,y,w,h) not in previous}
        simultaneous+=len(rails)==2
        previous=rs
    group={k:deepcopy(baseline[k]) for k in SECTIONS[stage]}
    waste=group[stage]
    tail=fifo_tail(frames(waste[-1]))
    if stage=='A06':
        # Reuse the AMP preparation trace already present in the release. Only
        # its idle prefix changes length when the waste transport clock changes.
        old=ordinary(waste)
        guest=[split_inlet(state,22)[1] for state in old]
        n=overlap['A05 A06 A07 A08']+1
        assert len(set(guest[:-n+1]))==1 and seconds(raw)>=n
        guests=[guest[0]]*(seconds(raw)-n)+guest[-n:]
        states=ordinary(raw)
        assert all(apart(r,g) for (_,r),g in zip(states,guests,strict=True))
        combined=[(s,r|g) for (s,r),g in zip(states,guests)]
        offset=0
        for c,raw_cmd in zip(waste,raw,strict=True):
            width=seconds(raw_cmd)
            write_frames(c,combined[offset:offset+width]);offset+=width
        waste.set('Timeout',str(offset))
        info=cdna(group,22,n-1)
    else:
        write_frames(waste[-1],frames(raw[-1]))
        waste.set('Timeout',str(sum(seconds(c) for c in waste)))
        info=barcode(group,22,overlap['B08 B09 B10 B11'])
    assert fifo_tail(frames(waste[-1]))==tail,stage
    total=sum(seconds(flow) for flow in group.values())
    for flow in group.values():check_footprints(flow)
    report=dict(window_s=total,waste_s=seconds(raw),dual_descent_s=simultaneous,port_ul=expected,**info,**audit)
    return group,report,cut


def update(data,overview_name):
    board.configure()
    overview=json.loads(data[overview_name])
    assert 'aliquot_row_outlet' not in overview
    text=data[SCRIPT].decode()
    current=flows(text)
    before=retime(ET.fromstring(text))
    assert before==overview['full_s']
    revision=PROJECTS[4][1]
    baseline=flows(subprocess.check_output(['git','show',f'{revision}:projects/{DEST.name}/{SCRIPT}'],cwd=ROOT))
    source=flows(source_blob(BASE,SCRIPT))
    trials={};changes={};detail=json.loads(data[OVERLAP])
    for stage,single,dual in [('A06',(0,0),(8,0)),('B07',(0,),(1,))]:
        results={};best=None
        policies=[('baseline',None),('single',single),('dual',dual)]
        if stage=='B07':policies.append(('dual_8_by_8',(8,)))
        for policy,outer in policies:
            group,result,cuts=simulate(stage,outer,source,baseline,overview['overlap_s'])
            commands={c.get('ID'):c for f in group.values() for c in f}
            results[policy]=result
            assert (result['dual_descent_s']>0)==policy.startswith('dual')
            if policy=='baseline':
                refcuts=cuts
                assert result['window_s']==sum(seconds(current[k]) for k in CURRENT[stage])
                for k in CURRENT[stage]:
                    for old in current[k]:
                        new=commands[old.get('ID')]
                        assert seconds(new)==seconds(old) and [e.attrib for e in old]==[e.attrib for e in new]
            else:
                assert cuts==refcuts
                first,last=current[CURRENT[stage][0]][0],current[CURRENT[stage][-1]][-1]
                assert frames(commands[first.get('ID')])[0]==frames(first)[0]
                assert frames(commands[last.get('ID')])[-1]==frames(last)[-1]
            if best is None or result['window_s']<results[best]['window_s']:
                best=policy;winner=commands
        trials[stage]=dict(selected=best,trials=results)
        if best=='baseline':continue
        detail[stage]={k:results[best][k] for k in detail[stage]}
        for k in CURRENT[stage]:
            old=current[k];new=deepcopy(old)
            for c in new:
                chosen=winner[c.get('ID')]
                c[:]=list(chosen);c.set('Timeout',chosen.get('Timeout'))
            # A06/A08 are sequential electrode commands. Retain B09's native
            # parallel timer and loop when attributing any section delta.
            delta=sum(seconds(c)-seconds(prev) for c,prev in zip(new,old))
            new.set('Timeout',str(seconds(old)+delta))
            if ET.tostring(old)==ET.tostring(new):continue
            changes[k]=dict(before_s=seconds(old),after_s=seconds(new))
            text=replace_flow(text,new.get('ID'),xml(new))
            overview['stages'][k]['four']=seconds(new)
        assert results['baseline']['window_s']-results[best]['window_s']==sum(
            changes[k]['before_s']-changes[k]['after_s'] for k in CURRENT[stage] if k in changes)
    overview['aliquot_row_outlet']=trials
    data[OVERLAP]=json_bytes(detail)
    publish(data,overview_name,overview,ET.fromstring(data[SCRIPT]),before,text,changes)
    from amp_row_nine import update_amp, direct_mix_handoff
    update_amp(data,overview_name)
    direct_mix_handoff(data,overview_name)
    from bead_inlet import update as update_bead_inlet
    update_bead_inlet(data,overview_name)
    return trials
