"""Patch command 1021 only; freeze its last 23 aspiration frames verbatim."""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as E
import geom,protocol,rigid
from scene import Drop
from waste_recovery import boxes

REPORT='A06流水线局部校验_20261019.json'


def build(text):
    root=E.fromstring(text);flow=next(f for f in root if f.get('ID')=='1019')
    assert flow.get('Name').startswith('A06 ')
    p=protocol.Protocol((1,));S=p.S;items=[];fixed={};stats=dict(moves_8x8=0,frames=0)
    for ln in p.ALL:
        rects=sorted(r for r in boxes(flow[0][-1].get('RelayCode')) if r[1]==ln.Y0)
        assert len(rects)==2
        for rc,name,vol in zip(rects,(ln.name,f'A{ln.id}'),(.5,1.5)):
            d=S.add(Drop(name,rc,vol));d.electrode_area=rc[2]*rc[3]
            d.margin_budget=d.electrode_area-geom.cells(vol)
        fixed[ln.name]=S.get(ln.name).target_cells();items.append((ln,d))
    S.step();S.flush();previous={};original=S._record
    def record(dur=geom.STEP_MS,*a,**kw):
        original(dur,*a,**kw);stats['frames']+=1
        for name,cells in fixed.items():assert S.get(name).target_cells()==cells
        now={}
        for d in S.drops.values():
            if not d.rects():continue
            rc=d.rect
            if rc[1]<172:assert not(rc[0]<68 and rc[0]+rc[2]>50),'触及其他列TEC'
            if getattr(d,'a06_transport_square',False):
                assert rc[2:]==(8,8) and d.vol==3.
            old=previous.get(id(d))
            if old and old[0] is d and old[1][2:]==rc[2:]:
                dist=abs(rc[0]-old[1][0])+abs(rc[1]-old[1][1])
                assert dist<=1,'跳格'
                if dist and rc[1]>=172:
                    assert rc[2:]==(8,8) or rc[2:]==(4,4),'底部只运输小方块'
                    stats['moves_8x8']+=dist*(rc[2:]==(8,8))
                if getattr(d,'a06_fixed_quilt',False):assert not dist,'已汇集方块移动'
            now[id(d)]=(d,rc)
        previous.clear();previous.update(now)
        assert abs(sum(d.vol for d in S.drops.values())+sum(i['volume_ul'] for r,i in S.manual_aspirations)-32.)<1e-8
    S._record=record
    rigid.egress_pieces(S,items,p.blob,collect_a05=True)
    command=p.cmd(flow[-1].get('Name'))
    gate=S.manual_aspirations[-1][0]
    end=next(i for i,r in enumerate(command.relays) if r is gate)
    relays=command.relays[:end+1]
    old=flow[-1];tail=list(old)[-23:]
    assert len(tail)==23 and [int(r.get('Duration')) for r in tail]==[1000]*4+[3000]*19
    generated=[E.fromstring(r.xml(i+1)) for i,r in enumerate(relays)]
    # Last 22 frames are the original F-hole withdrawal mask sequence.
    for a,b in zip(generated[-22:],tail[1:]):
        assert set(boxes(a.get('RelayCode')))==set(boxes(b.get('RelayCode')))
        assert a.get('Duration')==b.get('Duration')
    generated=generated[:-23]+copy.deepcopy(tail)
    raw=copy.deepcopy(old)
    for r in list(raw):raw.remove(r)
    for i,r in enumerate(generated,1):r.set('ItemID',str(i));r.tail='\n      ';raw.append(r)
    raw[-1].tail='\n    '
    new_seconds=sum(int(r.get('Duration')) for r in raw)//1000
    raw.set('Timeout',str(new_seconds))
    newflow=copy.deepcopy(flow);newflow.remove(newflow[-1]);newflow.append(raw)
    delta=new_seconds-int(old.get('Timeout'))
    newflow.set('Timeout',str(int(flow.get('Timeout'))+delta))
    # Replace only the command body and A06's timeout, preserving every other byte.
    start=text.index('  <Flow ID="1019"');end=text.index('  </Flow>',start)+len('  </Flow>')
    part=text[start:end]
    part=re.sub(r'(<Flow[^>]*Timeout=")\d+',lambda m:m[1]+newflow.get('Timeout'),part,count=1)
    replacement=E.tostring(raw,encoding='unicode').strip()
    part,n=re.subn(r'<Command ID="1021"[\s\S]*?</Command>',lambda m:replacement,part,count=1);assert n==1
    after=text[:start]+part+text[end:]
    parsed=E.fromstring(after)
    for a,b in zip(root,parsed):
        if a.get('ID')!='1019':assert E.tostring(a)==E.tostring(b)
        else:assert E.tostring(a[0])==E.tostring(b[0])
    assert [dict(r.attrib,ItemID='') for r in list(parsed.find("Flow[@ID='1019']")[-1])[-23:]]==[dict(r.attrib,ItemID='') for r in tail]
    proof=dict(status='passed',scope=['A06/1021','A05 last frame','A07 first frame'],
        source_sha256=hashlib.sha256(after.encode()).hexdigest(),base_sha256=hashlib.sha256(text.encode()).hexdigest(),
        before_seconds=int(flow.get('Timeout')),after_seconds=int(newflow.get('Timeout')),
        saved_seconds=-delta,before_command_seconds=int(old.get('Timeout')),after_command_seconds=new_seconds,
        frames_before=len(old),frames_after=len(raw),stats=stats,
        ports=['F3','F1'],port_ul=12.,total_waste_ul=24.,actual_packet_ul=.75,
        block_ul=3.,block_shape=[8,8],port_shape=[16,16],original_tail_frames=23,
        other_flows_unchanged=True,split_1020_unchanged=True,tail_masks_and_durations_unchanged=True,
        stationary_quilt_steps_authorized=True,hardware_verified=False,
        collection=[j['event'] for j in S.a06_collect_jobs.values()])
    return after,delta,len(raw),proof


def update(folder):
    path=folder/'PTseq_16_A.elab';text,delta,count,proof=build(path.read_text())
    main=folder/'main.elab';maintext,n=re.subn(r'Timeout="(\d+)"',lambda m:f'Timeout="{int(m[1])+delta}"',main.read_text(),count=1);assert n==1
    csvpath=folder/'手工吸液顺序.csv'
    with csvpath.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);headers=reader.fieldnames;rows=list(reader)
    for row in rows:
        if row['吸液流程组编号']=='1019':row['末尾帧号']=str(count)
    path.write_text(text);main.write_text(maintext)
    with csvpath.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,headers);writer.writeheader();writer.writerows(rows)
    (folder/REPORT).write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);update(p.parse_args().folder)
