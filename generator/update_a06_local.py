"""Regenerate A06 and its AMP handoff only; no full build, backup or GUI check."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import geom
import protocol
from scene import Drop,Scene
from model import Script
from manual_sections import split_sections,align_high_voltage_timing


def update(folder):
    path=folder/'PTseq_16_A.elab'
    text=path.read_text();old=ET.fromstring(text)
    before_sha=hashlib.sha256(path.read_bytes()).hexdigest()
    byid={int(f.get('ID')):f for f in old}
    previous=byid[1010]
    final_codes=next(c for c in previous if c.get('ID')=='1018')[-1].get('RelayCode')
    rects=[(int(x),int(y),int(w),int(h)) for y,x,h,w in
           re.findall(r'\((\d+),(\d+)\)\((\d+),(\d+)\)',final_codes)]
    assert len(rects)==16
    p=protocol.Protocol((1,));S=p.S
    for ln,rc in zip(p.ALL,rects):S.add(Drop(ln.name,rc,2.))
    S.step(1000,50);S.flush()
    original=Scene._record;receivers={};last={};frames=0
    retained_paths={ln.name:[] for ln in p.ALL};checking_a06=True
    def record(s,dur=1000,amp=55,touching=(),changed=None):
        nonlocal frames,last
        original(s,dur,amp,touching,changed)
        if s is not S:return
        frames+=1;current={}
        for d in s.drops.values():
            if checking_a06 and d.name in retained_paths and d.vol==.5:
                retained_paths[d.name].append(d.rect[0])
            if getattr(d,'receiving',False):receivers[id(d)]=d
            rc=d.rect;prior=last.get(id(d))
            if prior and prior[0] is d and prior[1]==d.vol:
                oldrc=prior[2]
                if rc[2:]==oldrc[2:]:
                    assert abs(rc[0]-oldrc[0])+abs(rc[1]-oldrc[1])<=1
                elif getattr(d,'native_packet',False):
                    raise AssertionError(('transport packet reshaped',d.name,oldrc,rc))
                else:assert dur>=4000,('reshape shorter than 4s',d.name)
            x,y,w,h=rc
            if y<172 and y+h>23:
                assert not any(x<10+40*c+18 and x+w>10+40*c for c in range(1,6))
            current[id(d)]=(d,d.vol,rc)
        loaded=32.+sum(d.vol for d in receivers.values())
        removed=sum(e['volume_ul'] for _,e in S.manual_aspirations)
        assert abs(sum(d.vol for d in S.drops.values())+removed-loaded)<1e-7
        last=current
    Scene._record=record
    try:
        p.split_waste('A','A05 劈裂 留25%(0.50µL) 弃1.50µL',.5,'cDNA 弃液 1.50µL')
        assert all(S.get(ln.name).vol==.5 and S.get(ln.name).rect==(11,ln.Y0,3,4) for ln in p.ALL)
        assert all(xs[0]==15 and xs[-1]==11 and all(b<=a for a,b in zip(xs,xs[1:]))
                   for xs in retained_paths.values()),'retained aliquot rebounds at left edge'
        asp=[e for _,e in S.manual_aspirations]
        assert len(asp)==1 and asp[0]['hole']=='F3/F1' and asp[0]['volume_ul']==24.
        assert sorted(asp[0]['rects'])==[[46,172,16,16],[154,172,16,16]]
        events=list(S.split_events)
        assert len(events)==48
        for e in events:
            assert e['volumes'][0]==e['volumes'][1]
            assert e['positions'][0][0][2:]=={40:[5,4],24:[3,4],32:[4,4]}[e['area']]
            assert all(0<=b-a<=1 for a,b in zip(e['gaps'],e['gaps'][1:]))
            if e['area']==32:
                assert e['positions'][0][0][0]==34,'waste mother moved to edge before cut'
        checking_a06=False
        assert not getattr(S,'waste_morph_events',[]),'A06 waste should merge without reshaping'
        p.add_reagent('A','A06 加靶向扩增反应液 2.00µL(台面预混)','AMP',2.)
        assert all(d.vol==2.5 and d.rect==(33,28+9*i,10,5) for i,d in enumerate(S.drops.values()))
    finally:Scene._record=original
    script=Script('PTseq_16_A',p.segs['A'],'ChipArea_192_256_96_SIRO',1019)
    split_sections([script]);align_high_voltage_timing([script])
    remap={1020:1019,1023:1022,1025:50001}
    changed=[]
    for f in script.flows:
        fid=remap[f.cmds[0].id];baseline=byid[fid]
        f.id=fid;f.name=baseline.get('Name')
        assert [c.id for c in f.cmds]==[int(c.get('ID')) for c in baseline]
        pattern=rf'  <Flow ID="{fid}"[\s\S]*?  </Flow>'
        text,n=re.subn(pattern,lambda _:f.xml(),text,count=1);assert n==1
        changed.append(fid)
    assert changed==[1019,1022,50001]
    after=ET.fromstring(text)
    for a,b in zip(old,after):
        if int(a.get('ID')) not in changed:assert ET.tostring(a)==ET.tostring(b)
    first=script.flows[0].cmds[0].relays[0]
    assert first.dur>=4000 and first.codes[0]=='(28,15)(4,10)'
    assert set(script.flows[-1].cmds[-1].relays[-1].codes)==set(byid[50001][-1][-1].get('RelayCode').split(';'))-{''}
    assert script.flows[1].cmds[-1].relays[-1].codes==script.flows[2].cmds[0].relays[0].codes
    csv_path=folder/'手工吸液顺序.csv'
    rows=list(csv.reader(io.StringIO(csv_path.read_text(encoding='utf-8-sig'))))
    gate=script.flows[0].cmds[-1]
    matches=0
    for row in rows[1:]:
        if row[1]=='1019':
            row[4]=str(len(gate.relays));row[7]=';'.join(geom.emit(rc) for rc in asp[0]['rects']);matches+=1
    assert matches==1
    stream=io.StringIO(newline='');csv.writer(stream).writerows(rows)
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    main=folder/'main.elab'
    main_text,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',main.read_text(),count=1);assert n==1
    path.write_text(text);main.write_text(main_text)
    csv_path.write_text(stream.getvalue(),encoding='utf-8-sig',newline='')
    report=dict(scope='A06 plus necessary A07/A08 handoff; unchanged A05/A09',
        full_workflow_checked=False,visual_check=False,frames_checked=frames,
        old_script_sha256=before_sha,script_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        actual_per_lane_input_ul=2.,retained_per_lane_ul=.5,waste_per_lane_ul=1.5,
        retained_shape=[3,4],retained_extra_cells=2,waste_merged_shape=[8,4],
        waste_packet_shape=[4,4],waste_packet_ul=.75,waste_packet_extra_cells=1,
        equal_split_events=48,no_waste_packet_reshape=True,split_gap_increment_max=1,
        retained_no_edge_rebound=True,waste_split_in_place_x=34,
        actual_aspirated_ul=24.,aspiration_rectangles=asp[0]['rects'],
        actual_post_AMP_per_lane_ul=2.5,changed_flow_ids=changed,unrelated_flows_unchanged=True)
    (folder/'A06局部校验_20261016.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    update(parser.parse_args().folder)
