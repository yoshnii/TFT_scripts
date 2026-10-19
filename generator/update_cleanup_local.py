"""Only cleanup names, adjacent-channel wash and final-real-packet resweep."""
import argparse
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as E
import geom,protocol,rigid
from scene import Drop
from waste_recovery import boxes
from cleanup_sweep import wash_region

TARGETS={'R17','R18','R19','C13','C14','C15'}
REPORT='清理命名与返扫局部校验_20261019.json'


def replace_command(text,old,new):
    raw=E.tostring(new,encoding='unicode').strip()
    text,n=re.subn(rf'<Command ID="{old.get("ID")}"[\s\S]*?</Command>',lambda _:raw,text,count=1)
    assert n==1
    return text


def build(folder):
    path=folder/'PTseq_16_A.elab';text=path.read_text();before=E.fromstring(text)
    by={f.get('Name').split()[0]:f for f in before};proof=[];newcmds={};deltas={}
    def scene_from(relay):
        p=protocol.Protocol((1,))
        rs=boxes(relay.get('RelayCode'))
        assert len(rs)==16
        for ln in p.ALL:
            rc=next(rc for rc in rs if rc[1]==ln.Y0)
            d=p.S.add(Drop(ln.name,rc,3.));d.electrode_area=60;d.margin_budget=0
        p.S.step();p.S.flush()
        return p
    for label in ('R18','C14'):
        f=by[label];old=f[1];p=scene_from(f[0][-1])
        wash_region(p.S,p.ALL);p.S.step(geom.HOLD_MS)
        cmd=p.cmd('wash');raw=copy.deepcopy(old)
        for r in list(raw):raw.remove(r)
        for i,r in enumerate(cmd.relays,1):
            node=E.fromstring(r.xml(i));node.tail='\n      ';raw.append(node)
        raw[-1].tail='\n    '
        raw.set('Name','[外部磁控]磁铁下降后：清理载液扫过本反应区两侧过液通道 → 左端定位（面积不变）')
        raw.set('Timeout',str(sum(int(r.get('Duration')) for r in raw)//1000))
        assert raw[-1].get('RelayCode')==old[-1].get('RelayCode'),'原混匀入口应完全相同'
        for r in raw:
            rs=boxes(r.get('RelayCode'));assert len(rs)==16 and all(rc[2:]==(12,5) for rc in rs)
            assert sum(rc[2]*rc[3] for rc in rs)==960
        delta=int(raw.get('Timeout'))-int(old.get('Timeout'));deltas[label]=delta
        newcmds[old.get('ID')]=raw;text=replace_command(text,old,raw)
        proof.append(dict(section=label,command=old.get('ID'),extra_seconds=delta,
            frame_shape=[12,5],cells_per_drop=60,actual_per_drop_ul=3.,
            left_channel_x=[6,10],right_channel_x=[46,50],origin_and_final_x=[27,10],
            sweep_extremes_x=[6,38],no_new_electrodes=True,events=p.S.cleanup_sweep_events))
    for label in ('R19','C15'):
        f=by[label];old=f[-1];p=scene_from(by['R18' if label=='R19' else 'C14'][-2][-1])
        items=[(ln,p.S.get(ln.name)) for ln in p.ALL]
        rigid.egress_pieces(p.S,items,p.blob,single_stop=label=='C15')
        cmd=p.cmd('cleanup waste');gate_index,gate=cmd.aspiration_gates[-1]
        assert len(cmd.aspiration_gates)==1 and gate['volume_ul']==48.
        generated=[E.fromstring(r.xml(i+1)) for i,r in enumerate(cmd.relays[:gate_index+1])]
        tail=list(old)[-23:]
        for j,(a,b) in enumerate(zip(generated[-23:],tail)):
            assert set(boxes(a.get('RelayCode')))==set(boxes(b.get('RelayCode')))
            assert a.get('Duration')==b.get('Duration') or j==0
        excess=int(generated[-23].get('Duration'))-int(tail[0].get('Duration'));assert excess>=0
        extra=copy.deepcopy(generated[-23]);extra.set('Duration',str(excess))
        generated=generated[:-23]+([extra] if excess else [])+copy.deepcopy(tail)
        raw=copy.deepcopy(old)
        for r in list(raw):raw.remove(r)
        for i,r in enumerate(generated,1):r.set('ItemID',str(i));r.tail='\n      ';raw.append(r)
        raw[-1].tail='\n    '
        raw.set('Name',old.get('Name').replace('3.00µL载液与磁珠一起排至底边孔',
            '清理：末组4×4真实载液到出口后返扫本反应区再排出；3.00µL载液与磁珠一起排至底边孔'))
        raw.set('Timeout',str(sum(int(r.get('Duration')) for r in raw)//1000))
        delta=int(raw.get('Timeout'))-int(old.get('Timeout'));deltas[label]=delta
        newcmds[old.get('ID')]=raw;text=replace_command(text,old,raw)
        assert len(p.S.cleanup_sweep_events)==1
        event=p.S.cleanup_sweep_events[0];assert event['electrode_cells']==256 and event['actual_volume_ul']==12.
        proof.append(dict(section=label,command=old.get('ID'),extra_seconds=delta,
            packet_shape=[4,4],packet_ul=.75,packet_cells=16,
            sweep_x=[38,46,6,46],bottom_tail_23_unchanged=True,
            actual_waste_ul=48.,port_volumes=gate['port_volumes'],
            final_rows=len(raw),events=p.S.cleanup_sweep_events))
    names={
        'R17':'R17 纯化1后清理 · 加入排珠载液3.00µL/道（非文库） · 加液承接',
        'R18':'R18 纯化1后清理 · 载液扫洗两侧过液通道、重悬磁珠',
        'R19':'R19 纯化1后清理 · 末组返扫、排出磁珠及载液 · F4/F3/F2/F1 待手工吸液',
        'C13':'C13 纯化2后清理 · 加入排珠载液3.00µL/道（非文库） · 加液承接',
        'C14':'C14 纯化2后清理 · 载液扫洗两侧过液通道、重悬磁珠',
        'C15':'C15 纯化2后清理 · 末组返扫、排出磁珠及载液 · F4/F3/F2/F1 待手工吸液'}
    for label,name in names.items():
        f=by[label];fid=f.get('ID')
        text,n=re.subn(rf'(<Flow ID="{fid}" Name=")[^"]*',lambda m:m[1]+name,text,count=1);assert n==1
        if label in deltas:
            text,n=re.subn(rf'(<Flow ID="{fid}"[^>]*Timeout=")\d+',lambda m:m[1]+str(int(f.get('Timeout'))+deltas[label]),text,count=1);assert n==1
    after=E.fromstring(text)
    for a,b in zip(before,after):
        label=a.get('Name').split()[0]
        if label not in TARGETS:assert E.tostring(a)==E.tostring(b)
        else:
            assert [c.get('ID') for c in a]==[c.get('ID') for c in b]
            for old,new in zip(a,b):
                if old.get('ID') not in newcmds:assert E.tostring(old)==E.tostring(new)
    layoutpath=folder/'PTseq_16_A.elab.layout';layout=layoutpath.read_text()
    for f in after:
        if f.get('Name').split()[0] not in TARGETS:continue
        for node in [f,*list(f)]:
            tag=node.tag;oid=node.get('ID');name=node.get('Name')
            # Only display names; preserve node coordinates and arrows verbatim.
            layout,n=re.subn(rf'(<{tag}\b[^>]*\bID="{oid}"\b[^>]*\bName=")[^"]*',lambda m:m[1]+name,layout,count=1)
            if not n:
                layout,n=re.subn(rf'(<{tag}\b[^>]*\bID="{oid}"[^>]*\bName=")[^"]*',lambda m:m[1]+name,layout,count=1)
            assert n==1,(tag,oid)
    current_names={f.get('ID'):f.get('Name') for f in after}
    mappingpath=folder/'步骤编号映射_20261016.json';mapping=json.loads(mappingpath.read_text())
    for row in mapping:
        if str(row['id']) in current_names and current_names[str(row['id'])].split()[0] in TARGETS:
            row['name']=current_names[str(row['id'])]
    csvpath=folder/'手工吸液顺序.csv'
    with csvpath.open(encoding='utf-8-sig',newline='') as fh:
        reader=csv.DictReader(fh);headers=reader.fieldnames;rows=list(reader)
    for row in rows:
        for idcol,namecol in [('吸液流程组编号','吸液流程组名称'),('下一个流程组编号','下一个流程组名称')]:
            if row[idcol] in current_names and current_names[row[idcol]].split()[0] in TARGETS:row[namecol]=current_names[row[idcol]]
        if row['末尾指令'] in newcmds:
            raw=newcmds[row['末尾指令']];row['末尾帧号']=str(len(raw))
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,headers);writer.writeheader();writer.writerows(rows)
    mainpath=folder/'main.elab';total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    main,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',mainpath.read_text(),count=1);assert n==1
    report=dict(status='passed',base_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),scope=sorted(TARGETS),cases=proof,
        added_nominal_seconds=sum(deltas.values()),existing_mixes_and_incubations_unchanged=True,
        true_elution_recovery_unchanged=True,volumes_and_ratios_unchanged=True,
        reference='ASI96 260828_5and8: Command2418 rows9-17/37-60 and Command2362 repeated sweep masks',
        adaptation='same real carrier and last quarter-drop resweep; no duplicated fictitious droplet',
        hardware_cleanliness_verified=False)
    return {path:text,layoutpath:layout,mappingpath:json.dumps(mapping,ensure_ascii=False,indent=2)+'\n',
        csvpath:stream.getvalue(),mainpath:main,folder/REPORT:json.dumps(report,ensure_ascii=False,indent=2)+'\n'},report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--write',action='store_true');a=p.parse_args()
    files,report=build(a.folder)
    if a.write:
        for path,content in files.items():path.write_text(content,encoding='utf-8-sig' if path.suffix=='.csv' else 'utf-8',newline='')
    print(json.dumps(report,ensure_ascii=False,indent=2))
