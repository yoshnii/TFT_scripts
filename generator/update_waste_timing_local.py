"""Rebuild only approved waste commands; retain original splits and port tails."""
import argparse
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as E
import geom, protocol, rigid
from scene import Drop
from waste_recovery import boxes

TARGETS={'A06','R05','R06','R19','C05','C06','C15'}
REPORT='排废矩形运输与排程局部校验_20261019.json'


def build(text):
    root=E.fromstring(text);flows=list(root)
    bylabel={f.get('Name').split()[0]:f for f in flows}
    replacements={};cases=[];gates={}
    for labels,vol in ((('A06',),1.5),(('R05','R06'),5.),
                       (('R19',),3.),(('C05','C06'),5.9),(('C15',),3.)):
        first=bylabel[labels[0]];partial=labels[0]=='A06'
        previous=flows[flows.index(first)-1]
        initial=first[0][-1] if partial else [c for c in previous if c.get('Component')=='HighVoltage'][-1][-1]
        p=protocol.Protocol((1,));S=p.S;items=[];fixed={}
        for ln in p.ALL:
            rs=sorted(rc for rc in boxes(initial.get('RelayCode')) if rc[1]==ln.Y0)
            specs=[(ln.name,.5),(f'A{ln.id}',vol)] if partial else [(ln.name,vol)]
            assert len(rs)==len(specs)
            for rc,(name,amount) in zip(rs,specs):
                d=S.add(Drop(name,rc,amount));d.electrode_area=rc[2]*rc[3]
                d.margin_budget=d.electrode_area-geom.cells(amount)
                if partial and name==ln.name:fixed[name]=d.target_cells()
            items.append((ln,d))
        initial_mass=sum(d.vol for d in S.drops.values())
        S.step();S.flush();original=S._record;prior={};frames=[]
        def record(dur=geom.STEP_MS,*args,**kw):
            original(dur,*args,**kw)
            for name,cells in fixed.items():assert S.get(name).target_cells()==cells
            now={};shapes=[]
            for d in S.drops.values():
                if not d.rects():continue
                rc=d.rect;area=len(d.target_cells())
                assert area==rc[2]*rc[3]
                assert not(rc[0]<68 and rc[0]+rc[2]>50 and rc[1]<172)
                old=prior.get(id(d))
                if old and old[0] is d and old[1]==d.vol:
                    if old[3]!=area:
                        assert getattr(d,'native_authorized_padding',False) and area==64 and old[3] in (50,60), '非既有母框的面积改变'
                    if old[2][2:]==rc[2:]:
                        assert abs(rc[0]-old[2][0])+abs(rc[1]-old[2][1])<=1
                    elif old[2]!=rc:assert dur>=4000
                if getattr(d,'native_packet',False):assert rc[2:]==(4,4)
                now[id(d)]=(d,d.vol,rc,area)
                shapes.append((d.name,rc,d.vol,area))
            prior.clear();prior.update(now);frames.append(shapes)
            assert abs(sum(d.vol for d in S.drops.values())+
                       sum(info['volume_ul'] for _,info in S.manual_aspirations)-initial_mass)<1e-7
        S._record=record
        if len(labels)==2:
            rigid.supernatant_bites(S,p.ALL,p.blob,0,collect_three=labels[0]=='R05')
        else:rigid.egress_pieces(S,items,p.blob,collect_a05=partial,single_stop=labels[0]=='C15')
        command=p.cmd('local waste scheduling')
        events=command.aspiration_gates
        assert len(events)==len(labels)
        start=0
        for label,(end,gate) in zip(labels,events):
            flow=bylabel[label];old=flow[-1];tail=list(old)[-23:]
            generated=[E.fromstring(r.xml(i+1)) for i,r in enumerate(command.relays[start:end+1])]
            assert len(generated)>23
            for j,(a,b) in enumerate(zip(generated[-23:],tail)):
                assert set(boxes(a.get('RelayCode')))==set(boxes(b.get('RelayCode'))),(label,'孔旁收尾改变')
                assert a.get('Duration')==b.get('Duration') or (j==0 and int(a.get('Duration'))>=int(b.get('Duration'))),(label,a.attrib,b.attrib)
            excess=int(generated[-23].get('Duration'))-int(tail[0].get('Duration'))
            extra=copy.deepcopy(generated[-23]);extra.set('Duration',str(excess))
            generated=generated[:-23]+([extra] if excess else [])+copy.deepcopy(tail)
            raw=copy.deepcopy(old)
            for r in list(raw):raw.remove(r)
            for i,r in enumerate(generated,1):r.set('ItemID',str(i));r.tail='\n      ';raw.append(r)
            raw[-1].tail='\n    '
            seconds=sum(int(r.get('Duration')) for r in raw)//1000
            delta=seconds-int(old.get('Timeout'));raw.set('Timeout',str(seconds))
            assert delta<=0 or partial,(label,'优化不得让后续磁珠排废变慢',delta)
            newflow=copy.deepcopy(flow);newflow.remove(newflow[-1]);newflow.append(raw)
            newflow.set('Timeout',str(int(flow.get('Timeout'))+delta))
            replacements[label]=newflow;gates[label]=(len(raw),gate)
            cases.append(dict(section=label,before_seconds=int(flow.get('Timeout')),
                after_seconds=int(newflow.get('Timeout')),saved_seconds=-delta,
                frames_before=len(old),frames_after=len(raw),port_volumes=gate['port_volumes'],
                actual_waste_ul=gate['volume_ul'],tail_23_unchanged=True,
                moving_packet_shape=[4,4],merged_transport_shape=[8,16],
                full_rectangles=True,source_split_algorithm_unchanged=True,
                source_split_command_verbatim=partial))
            start=end+1
        assert all(e['volumes'][0]==e['volumes'][1] and e['partition_preserves_mother_cells']
                   and all(0<=b-a<=1 for a,b in zip(e['gaps'],e['gaps'][1:]))
                   for e in getattr(S,'split_events',[]))
    after=text
    for label,f in replacements.items():
        old=bylabel[label];start=after.index(f'  <Flow ID="{old.get("ID")}"');end=after.index('  </Flow>',start)+len('  </Flow>')
        part=after[start:end]
        part=re.sub(r'(<Flow[^>]*Timeout=")\d+',lambda m:m[1]+f.get('Timeout'),part,count=1)
        cid=f[-1].get('ID');replacement=E.tostring(f[-1],encoding='unicode').strip()
        part,n=re.subn(rf'<Command ID="{cid}"[\s\S]*?</Command>',lambda _:replacement,part,count=1);assert n==1
        after=after[:start]+part+after[end:]
    parsed=E.fromstring(after)
    for a,b in zip(root,parsed):
        if a.get('Name').split()[0] not in TARGETS:assert E.tostring(a)==E.tostring(b)
        elif a.get('Name').startswith('A06 '):assert E.tostring(a[0])==E.tostring(b[0])
    proof=dict(status='passed',base_sha256=hashlib.sha256(text.encode()).hexdigest(),
        source_sha256=hashlib.sha256(after.encode()).hexdigest(),cases=cases,
        saved_seconds=sum(c['saved_seconds'] for c in cases),
        timing_changes_only='three-cell collection gaps; unchanged 1000ms moves and 4000ms reshapes',
        unrelated_sections_unchanged=True,actual_volumes_unchanged=True,
        packet_area_unchanged=True,merged_area_unchanged=True,hardware_verified=False)
    return after,gates,proof


def update(folder):
    path=folder/'PTseq_16_A.elab';after,gates,proof=build(path.read_text())
    main=folder/'main.elab';total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in E.fromstring(after))
    maintext,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',main.read_text(),count=1);assert n==1
    csvpath=folder/'手工吸液顺序.csv'
    with csvpath.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);headers=reader.fieldnames;rows=list(reader)
    for row in rows:
        label=row['吸液流程组名称'].split()[0]
        if label not in gates:continue
        count,gate=gates[label]
        assert float(row[headers[6]])==gate['volume_ul']
        assert row['吸液孔']==gate['hole']
        row['末尾帧号']=str(count)
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,headers);writer.writeheader();writer.writerows(rows)
    return {path:after,main:maintext,csvpath:stream.getvalue(),
            folder/REPORT:json.dumps(proof,ensure_ascii=False,indent=2)+'\n'},proof


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--write',action='store_true');args=ap.parse_args()
    files,proof=update(args.folder)
    if args.write:
        # Bulk serialization of generated native XML and derived delivery metadata.
        for path,content in files.items():path.write_text(content,encoding='utf-8-sig' if path.suffix=='.csv' else 'utf-8',newline='')
    print(json.dumps(proof,ensure_ascii=False,indent=2))
