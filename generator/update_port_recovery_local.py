"""Patch only four S recovery tails and eight F aspiration terminal masks."""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as E

import geom
from update_elution_recovery_local import transform as s_transform, boxes, REPORT as S_REPORT
from update_pcr_block_local import replace_flow
from waste_recovery import waste_path, waste_relays

F_TARGETS = {'A06':(1019,1021), 'R05':(2013,2014), 'R06':(51000,60000),
             'R19':(2068,2069), 'B07':(50004,3020), 'C05':(4013,4014),
             'C06':(51002,60001), 'C15':(4048,4049)}
REPORT = '孔旁回收排废局部校验_20261018.json'


def transform(text, layout, main, mapping, rows):
    original_text = text
    original = E.fromstring(text)
    before_layout = E.fromstring(layout)
    text,layout,main,mapping,rows,sreport = s_transform(text,layout,main,mapping,rows)
    fs = {f.get('ID'):f for f in original}
    ls = {f.get('ID'):f for f in before_layout if f.tag=='Flow'}
    changed = set(str(c['flow_id']) for c in sreport['cases'])
    fcases = []; delta = 0
    for label,(fid,cid) in F_TARGETS.items():
        old = fs[str(fid)];flow = copy.deepcopy(old);cmd = flow[-1]
        assert old.get('Name').split()[0] == label and cmd.get('ID') == str(cid)
        assert cmd.get('Params') == 'SCRIPTCYCLE_FALSE'
        row = next(r for r in rows if r['吸液流程组编号']==str(fid))
        rects = boxes(row['待吸液电极'])
        ports = [int(n[1:]) for n in row['吸液孔'].split('/')]
        assert len(rects)==len(ports) and all(rc[1]==172 for rc in rects)
        end = cmd[-1];old_codes = boxes(end.get('RelayCode',''))
        assert set(rects) <= set(old_codes) and end.get('Duration')=='1000'
        retained = [geom.emit(rc) for rc in old_codes if rc not in rects]
        relays = waste_relays(rects,[geom.HOLE_X[k] for k in ports],retained)
        prefix = [E.tostring(r) for r in cmd]
        # Preserve all existing frames verbatim, including the initial wait mask.
        for i,relay in enumerate(relays[1:],len(cmd)+1):
            node = E.fromstring(relay.xml(i));node.tail='\n      ';cmd.append(node)
        cmd[-1].tail='\n    '
        assert [E.tostring(r).strip() for r in list(cmd)[:len(prefix)-1]] == [r.strip() for r in prefix[:-1]]
        added = sum(r.dur for r in relays[1:])//1000
        cmd.set('Timeout',str(int(cmd.get('Timeout'))+added))
        cmd.set('Name',cmd.get('Name')+' · 孔旁收尾：覆盖F孔→逐侧撤电→终框保持；运行期间手工吸液')
        flow.set('Timeout',str(int(flow.get('Timeout'))+added))
        text = replace_flow(text,fid,flow)
        node = copy.deepcopy(ls[str(fid)])
        next(c for c in node if c.get('ID')==str(cid)).set('Name',cmd.get('Name'))
        layout = replace_flow(layout,fid,node)
        changed.add(str(fid));delta += added
        paths = [waste_path(rc,geom.HOLE_X[k]) for rc,k in zip(rects,ports)]
        finals = [p[-1] for p in paths]
        row['末尾帧号']=str(len(cmd));row['待吸液电极']=';'.join(map(geom.emit,finals))
        fcases.append(dict(section=label,flow_id=fid,command_id=cid,ports=ports,
            actual_total_ul=float(row['本次总液体µL（16道合计；排珠载液不计文库）']),
            initial_rects=rects,final_rects=finals,paths=paths,
            preserved_prefix_frames=len(prefix),tail_frames=len(relays),
            frame_ms=[r.dur for r in relays],duration_seconds=sum(r.dur for r in relays)//1000,
            added_seconds=added,retained_electrodes=retained,
            full_target_hole_coverage=True,final_mask_remains_on=True))
    main,count = re.subn(r'Timeout="(\d+)"',lambda m:f'Timeout="{int(m[1])+delta}"',main,count=1)
    assert count==1
    after = E.fromstring(text)
    for old,new in zip(original,after):
        assert old.get('ID')==new.get('ID')
        if old.get('ID') not in changed:assert E.tostring(old)==E.tostring(new)
        else:
            assert [c.get('ID') for c in old]==[c.get('ID') for c in new]
            assert all(E.tostring(a)==E.tostring(b) for a,b in zip(old[:-1],new[:-1]))
    for node in E.fromstring(layout):
        if node.tag=='Flow' and node.get('ID') not in changed:
            assert E.tostring(node).strip()==E.tostring(ls[node.get('ID')]).strip()
    sha=hashlib.sha256(text.encode()).hexdigest();sreport['source_sha256']=sha
    report=dict(status='passed',base_commit='ccadf3f',source_sha256=sha,
        original_sha256=hashlib.sha256(original_text.encode()).hexdigest(),
        s_cases=sreport['cases'],f_cases=fcases,
        total_nominal_change_seconds=delta+sreport['total_nominal_change_seconds'],
        only_port_tails_changed=True,unrelated_sections_byte_identical=True,
        existing_s_preceding_commands_unchanged=True,existing_f_frames_unchanged=True,
        liquid_volumes_mixes_incubations_magnet_commands_unchanged=True,
        hardware_aspiration_verified=False,
        notice='外部吸液撤电包络；缩框不代表测得残余体积；仅目标孔允许覆盖，其他孔距规则不变')
    return text,layout,main,mapping,rows,sreport,report


def update(folder):
    names=['PTseq_16_A.elab','PTseq_16_A.elab.layout','main.elab','步骤编号映射_20261016.json']
    texts=[(folder/n).read_text() for n in names]
    with (folder/'手工吸液顺序.csv').open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);headers=reader.fieldnames;rows=list(reader)
    text,layout,main,mapping,rows,sreport,report=transform(*texts[:3],json.loads(texts[3]),rows)
    for name,content in zip(names,[text,layout,main,json.dumps(mapping,ensure_ascii=False,indent=2)+'\n']):
        (folder/name).write_text(content)
    with (folder/'手工吸液顺序.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=headers);writer.writeheader();writer.writerows(rows)
    for name,data in [(S_REPORT,sreport),(REPORT,report)]:
        (folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('s_cases','f_cases')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    update(parser.parse_args().folder)
