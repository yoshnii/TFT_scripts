"""Only R02: expand and route two G14 port families together."""
import argparse
import collections
import copy
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as E

import geom,ops,rigid
from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,validate_scene,last_hv,codes
from update_workbook_volumes_local import remap
from update_symmetric_splits_local import audit_symmetric
from waste_recovery import boxes

REPORT='R02双孔并行局部校验_20261022.json'


def build(folder):
    path=folder/'PTseq_16_A.elab';text=path.read_text();root=E.fromstring(text)
    old={int(f.get('ID')):f for f in root};native=old[50002]
    assert native.get('Name').startswith('R02 ')
    p=seed(old[2000][0][0],[('',2.5)])
    p.add_reagent('R1',old[2000].get('Name').split(' · ')[0],'G14',2.5)
    target=boxes(last_hv(native).get('RelayCode'));byrow={rc[1]:rc for rc in target}
    rigid.reshape_many(p.S,[(p.S.get(ln.name),(*p.S.get(ln.name).rect[:2],*byrow[ln.Y0][2:])) for ln in p.ALL])
    ops.move_rects(p.S,{ln.name:byrow[ln.Y0][0] for ln in p.ALL})
    tail=p.cmd('衔接原下游反应区位置')
    if tail:p.segs['R1'][-1].cmds[-1].relays.extend(tail.relays)
    scene=validate_scene(p,'R02 paired G14 families')
    leaves=p.S.native_leaf_events
    assert len(leaves)==64 and all(e['shape']==[3,5] and e['cells']==15 and e['actual_ul']==.625 for e in leaves)
    levels=list(collections.Counter(e['partition_frame'] for e in p.S.split_events).values())
    assert levels==[2,4,8,16]*2 and len(p.S.split_events)==60
    assert p.S.reaction_merge_events[-1]['rows']==list(range(1,17))
    ready=next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
    assert p.S.reaction_merge_events[-1]['contact_step']>ready
    assert all(d.vol==5. for d in p.S.drops.values())
    scripts,_,_=prepare_scripts(p.segs,root.get('ChipsetType'),1)
    flows=scripts[0].flows;assert len(flows)==2
    # R01 remains published verbatim; confirm its gate and the R02 input.
    assert set(flows[0].cmds[-1].relays[-1].codes)==codes(old[2000][-1][-1])
    model=flows[1];remap(model,50002,[2003]);fresh=E.fromstring(model.xml())
    assert codes(fresh[0][0])==codes(native[0][0])
    assert codes(fresh[0][-1])==codes(native[0][-1])
    replacement=copy.deepcopy(native)
    replacement[0][:]=[copy.deepcopy(r) for r in fresh[0]]
    replacement[0].set('Timeout',fresh[0].get('Timeout'))
    replacement.set('Timeout',fresh.get('Timeout'))
    assert sum(int(r.get('Duration')) for r in replacement[0])==int(replacement[0].get('Timeout'))*1000
    part=E.tostring(replacement,encoding='unicode').strip()
    new,count=re.subn(r'<Flow\b(?=[^>]*\bID="50002")[^>]*>[\s\S]*?</Flow>',lambda _:part,text,count=1)
    assert count==1
    after=E.fromstring(new)
    for a,b in zip(root,after):
        if a.get('ID')!='50002':assert E.tostring(a)==E.tostring(b)
    audit=audit_symmetric(after,{50002});assert audit['symmetric_horizontal_splits']==60
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    mt,count=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1)
    assert count==1
    report=dict(status='passed',baseline_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(new.encode()).hexdigest(),scope=['R02','R01 output','R03 input'],
        before_seconds=int(native.get('Timeout')),after_seconds=int(replacement.get('Timeout')),
        saved_seconds=int(native.get('Timeout'))-int(replacement.get('Timeout')),
        before_frames=len(native[0]),after_frames=len(replacement[0]),
        ports_per_group=2,parallel_mothers_by_level=levels,
        actual_leaf_ul=.625,leaf_shape=[3,5],leaf_cells=15,leaf_count=64,
        reagent_ul_per_well=2.5,receiving_port_volumes_ul={f'E{k}':10. for k in range(1,5)},
        synchronous_reaction_merge=True,scene=scene,exported_frame_audit=audit,
        all_other_sections_unchanged=True,GUI_checked=False,hardware_verified=False)
    files={path:new,folder/'main.elab':mt,folder/REPORT:json.dumps(report,ensure_ascii=False,indent=2)+'\n'}
    # Carry forward prior local proofs: unchanged A02, and prior global scan
    # plus this R02-only follow-up. No unrelated section is regenerated.
    a02=folder/'A02整列并行局部校验.json'
    if a02.exists():
        proof=json.loads(a02.read_text());proof['script_sha256']=report['script_sha256']
        files[a02]=json.dumps(proof,ensure_ascii=False,indent=2)+'\n'
    global_path=folder/'双侧同步分液局部校验_20261022.json'
    if global_path.exists():
        proof=json.loads(global_path.read_text())
        assert proof['script_sha256'] in (report['baseline_sha256'],report['script_sha256'])
        prior=audit_symmetric(root,{50002})
        assert prior['symmetric_horizontal_splits']==audit['symmetric_horizontal_splits']==60
        proof['script_sha256']=report['script_sha256']
        proof['exported_frame_audit']['frames']+=audit['frames']-prior['frames']
        for row in proof['changes']:
            if row['id']==50002:row['after_seconds']=report['after_seconds']
        for i,row in enumerate(proof['cases']):
            if row['case']=='G14':proof['cases'][i]=dict(scene,case='G14')
        proof['follow_up_R02_report']=REPORT
        files[global_path]=json.dumps(proof,ensure_ascii=False,indent=2)+'\n'
    return files,report


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--write',action='store_true')
    args=ap.parse_args();files,report=build(args.folder)
    if args.write:
        for path,text in files.items():path.write_text(text)
    print(json.dumps(report,ensure_ascii=False,indent=2))
