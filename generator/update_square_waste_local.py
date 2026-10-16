"""Regenerate the eight approved waste sections and check their immediate boundaries."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET

import geom,ops,protocol,rigid
from scene import Drop,Relay
from model import Script,Cmd
from manual_sections import split_sections,align_high_voltage_timing
from audit_binary_frames import PATTERN,audit
from audit_bottom_motion import audit as audit_bottom

TARGETS={'A06','R05','R06','R19','B07','C05','C06','C15'}


def boxes(relay):
    return [(int(x),int(y),int(w),int(h)) for y,x,h,w in PATTERN.findall(relay.get('RelayCode',''))]


def update(folder):
    path=folder/'PTseq_16_A.elab';text=path.read_text();before=ET.fromstring(text)
    base_sha=hashlib.sha256(path.read_bytes()).hexdigest()
    bylabel={f.get('Name').split()[0]:f for f in before};replacements={};gates={};cases=[]
    for labels,vol,seg in ((('A06',),1.5,'A'),(('B07',),.6,'B'),
                            (('R05','R06'),5.,'R1'),(('C05','C06'),5.9,'C'),
                            (('R19',),3.,'R1'),(('C15',),3.,'C')):
        first=bylabel[labels[0]];index=list(before).index(first)
        previous=before[index-1]
        partial=labels[0] in ('A06','B07')
        initial=first[0][-1] if partial else [c for c in previous if c.get('Component')=='HighVoltage'][-1][-1]
        p=protocol.Protocol((1,));p.magnet_state='up' if len(labels)==2 else 'down'
        items=[]
        for ln in p.ALL:
            rects=sorted((rc for rc in boxes(initial) if rc[1]==ln.Y0),key=lambda q:q[0])
            specs=([(ln.name,.5),(f'A{ln.id}',1.5)] if labels[0]=='A06' else
                   [(ln.name,2.),(f'K{ln.id}',.6),(f'BKWA{ln.id}',.6)] if labels[0]=='B07' else [(ln.name,vol)])
            assert len(rects)==len(specs)
            for rc,(name,amount) in zip(rects,specs):
                d=p.S.add(Drop(name,rc,amount));d.electrode_area=rc[2]*rc[3]
                d.margin_budget=d.electrode_area-geom.cells(amount)
            items.append((ln,d))
        initial_mass=sum(d.vol for d in p.S.drops.values())
        p.S.step();p.S.flush()
        original=p.S._record;prior={};stats=dict(frames=0,mother_cell_moves=0,
            thin_mother_cell_moves=0,packet_reshape_frames=0,bottom_long_axis_moves=0)
        def record(dur=geom.STEP_MS,*args,**kwargs):
            original(dur,*args,**kwargs);stats['frames']+=1
            current={}
            for d in p.S.drops.values():
                if not d.rects():continue
                cells=d.target_cells();rc=d.rect
                assert len(cells)==rc[2]*rc[3]==geom.cells(d.vol)+d.margin_budget
                assert not(rc[0]<68 and rc[0]+rc[2]>50 and rc[1]<172),'触及下一列TEC'
                old=prior.get(id(d))
                if getattr(d,'native_packet',False):
                    assert rc[2:] in ((4,4),(3,4)),'小滴在运输中形变'
                if old and old[0] is d and old[1]==d.vol and old[2][2:]==rc[2:]:
                    distance=abs(rc[0]-old[2][0])+abs(rc[1]-old[2][1])
                    assert distance<=1,'跳格移动'
                    if distance and rc[1]>=172 and rc[2]*rc[3]>16 and max(rc[2:])/min(rc[2:])>1.5:
                        dx=abs(rc[0]-old[2][0]);dy=abs(rc[1]-old[2][1])
                        assert not (rc[2]>rc[3] and dx or rc[3]>rc[2] and dy),('底部合块沿长轴移动',d.name,old[2],rc)
                    if rc[2]*rc[3]>16:
                        stats['mother_cell_moves']+=distance
                        if max(rc[2:])/min(rc[2:])>=3:stats['thin_mother_cell_moves']+=distance
                elif old and old[0] is d and old[1]==d.vol and old[2]!=rc:
                    assert dur>=4000,'母滴形变未稳定4秒'
                current[id(d)]=(d,d.vol,rc)
            assert abs(sum(d.vol for d in p.S.drops.values())+
                       sum(info['volume_ul'] for relay,info in p.S.manual_aspirations)-initial_mass)<1e-8
            prior.clear();prior.update(current)
        p.S._record=record
        if partial:
            rigid.egress_pieces(p.S,items,p.blob,collect_a05=labels[0]=='A06')
            raw=first[0]
            original_cmd=Cmd(raw.get('Name'),raw.get('Component'),raw.get('Action'),int(raw.get('Timeout')),raw.get('Params'),
                [Relay([geom.emit(rc) for rc in boxes(r)],int(r.get('Duration')),int(r.get('Amp'))) for r in raw])
            p.flow(seg,first.get('Name'),[original_cmd,p.cmd(first[-1].get('Name'))])
        elif len(labels)==2:
            title='R04 液相移离磁珠 → 排废全部5.00µL（不保留计划液相）' if seg=='R1' else 'C04 液相移离磁珠 → 排废全部5.90µL（不保留计划液相）'
            p.supernatant_off(seg,title)
        else:
            rigid.egress_pieces(p.S,[(ln,p.S.get(ln.name)) for ln in p.ALL],p.blob,
                                final=True,single_stop=seg=='C')
            p.flow(seg,first.get('Name'),[p.cmd('3.00µL载液与磁珠一起排至底边孔，等待手工吸出；不回收文库')])
        script=Script('PTseq_16_A',p.segs[seg],'ChipArea_192_256_96_SIRO',int(first.get('ID')))
        split_sections([script]);align_high_voltage_timing([script])
        assert len(script.flows)==len(labels)
        for label,flow in zip(labels,script.flows):
            old=bylabel[label];assert len(flow.cmds)==len(old)
            flow.id=int(old.get('ID'));flow.name=old.get('Name')
            for cmd,raw in zip(flow.cmds,old):
                cmd.id=int(raw.get('ID'));cmd.name=raw.get('Name')
            cmd=flow.cmds[-1]
            replacements[label]=flow;gates[label]=cmd.manual_aspiration
        assert all(len(e['volumes'])==2 and e['volumes'][0]==e['volumes'][1] for e in getattr(p.S,'split_events',[]))
        assert all(e['partition_preserves_mother_cells'] and
                   all(0<=b-a<=1 for a,b in zip(e['gaps'],e['gaps'][1:])) for e in getattr(p.S,'split_events',[]))
        assert all(e['tile_shape']==([3,4] if labels[0]=='B07' else [4,4]) for e in p.S.waste_grid_events)
        morphs=getattr(p.S,'waste_morph_events',[])
        assert morphs and all(e['before'][2:]==([12,8] if labels[0]=='B07' else [16,8]) and
                              e['after'][2:]==([6,16] if labels[0]=='B07' else [8,16]) and
                              e['settle_ms']>=4000 for e in morphs),'只允许整批半柱等面积转形'
        assert stats['thin_mother_cell_moves']==0,'16×4长母滴应原地切开而非搬运'
        assert abs(sum(d.vol for d in p.S.drops.values())-(initial_mass-vol*16))<1e-8
        leaves=8 if len(labels)==2 else 2 if labels[0]=='A06' else 1 if labels[0]=='B07' else 4
        cases.append(dict(sections=labels,actual_per_lane_ul=vol,packet_ul=vol/leaves,
                          packet_shape=[3,4] if labels[0]=='B07' else [4,4],leaf_count_per_lane=8 if len(labels)==2 else 2 if labels[0]=='A06' else 1 if labels[0]=='B07' else 4,
                          carrier_cells_per_lane=leaves*(12 if labels[0]=='B07' else 16),
                          actual_extra_liquid_ul=0,
                          aspiration_volumes_ul=[info['volume_ul'] for relay,info in p.S.manual_aspirations],
                          split_events=len(getattr(p.S,'split_events',[])),collection_reshapes=morphs,stats=stats))
    for label,flow in replacements.items():
        pattern=rf'  <Flow ID="{flow.id}"[\s\S]*?  </Flow>'
        text,n=re.subn(pattern,lambda _:flow.xml(),text,count=1);assert n==1
    after=ET.fromstring(text)
    for a,b in zip(before,after):
        if a.get('Name').split()[0] not in TARGETS:assert ET.tostring(a)==ET.tostring(b)
        elif a.get('Name').split()[0] in ('A06','B07'):assert ET.tostring(a[0])==ET.tostring(b[0]),'原分液指令必须保持不变'
    # Check only changed sections plus the last/first electrode frame of neighbors.
    context=ET.Element('Script')
    afterflows=list(after)
    for i,flow in enumerate(afterflows):
        label=flow.get('Name').split()[0]
        if label in TARGETS:context.append(ET.fromstring(ET.tostring(flow)))
        elif i+1<len(afterflows) and afterflows[i+1].get('Name').split()[0] in TARGETS:
            hv=[c for c in flow if c.get('Component')=='HighVoltage']
            if hv:
                f=ET.SubElement(context,'Flow',Name=flow.get('Name'));c=ET.SubElement(f,'Command',Component='HighVoltage',ID=hv[-1].get('ID'))
                c.append(ET.fromstring(ET.tostring(hv[-1][-1])))
        elif i and afterflows[i-1].get('Name').split()[0] in TARGETS:
            hv=[c for c in flow if c.get('Component')=='HighVoltage']
            if hv:
                f=ET.SubElement(context,'Flow',Name=flow.get('Name'));c=ET.SubElement(f,'Command',Component='HighVoltage',ID=hv[0].get('ID'))
                c.append(ET.fromstring(ET.tostring(hv[0][0])))
    with tempfile.TemporaryDirectory(prefix='tft-square-check-') as temp:
        checkpath=Path(temp)/'context.elab';ET.ElementTree(context).write(checkpath,encoding='utf-8')
        binary=audit(checkpath)
    assert not binary['non_equal_binary_transitions'],binary
    bottom=audit_bottom(after,TARGETS)
    assert not bottom['violations'],bottom
    csvpath=folder/'手工吸液顺序.csv'
    with csvpath.open(encoding='utf-8-sig',newline='') as fh:
        reader=csv.DictReader(fh);headers=reader.fieldnames;rows=list(reader)
    for row in rows:
        label=row['吸液流程组名称'].split()[0]
        if label not in TARGETS:continue
        gate=gates[label];cmd=replacements[label].cmds[-1]
        assert float(row[headers[6]])==gate['volume_ul'],'手工吸液量改变'
        assert row['吸液孔']==gate['hole'],'手工吸液孔改变'
        row['末尾帧号']=str(len(cmd.relays));row['待吸液电极']=';'.join(geom.emit(rc) for rc in gate['rects'])
    proof=dict(status='passed',date='2026-10-17',scope=sorted(TARGETS),base_script_sha256=base_sha,
               script_sha256=hashlib.sha256(text.encode()).hexdigest(),cases=cases,
               unrelated_sections_unchanged=True,upstream_downstream_boundary_audit=binary,
               bottom_component_motion_audit=bottom,
               full_workflow_rerun=False,visual_check=False,hardware_verified=False,vm_verified=False,
               no_local_backup_created=True)
    main=folder/'main.elab';total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    maintext,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',main.read_text(),count=1);assert n==1
    path.write_text(text);main.write_text(maintext)
    with csvpath.open('w',encoding='utf-8-sig',newline='') as fh:
        writer=csv.DictWriter(fh,headers);writer.writeheader();writer.writerows(rows)
    (folder/'底部紧凑汇液局部校验_20261017.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);update(ap.parse_args().folder)
