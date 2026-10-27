"""Publish timing-only changes for six approved top-reagent dispensing flows."""
import argparse,copy,hashlib,json,re,subprocess
from pathlib import Path
import xml.etree.ElementTree as E

from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,validate_scene
from update_workbook_volumes_local import replace_flow

ROOT=Path(__file__).resolve().parent.parent
BASE='2c00697345a79421717d0a92d2b4539a53135ecf'
REPORT='顶部大母滴慢速局部校验_20261027.json'
CASES=[('AMP',2.,.5,1022,50001,'A'),('G14',2.5,2.5,2000,50002,'R1'),
       ('G8',.7,1.3,3002,50003,'B'),('LIG',1.4,2.6,3026,50005,'B'),
       ('EDTA',.4,4.,3041,50006,'B'),('G16',1.76,4.4,4000,50007,'C')]


def key(r):return tuple(sorted(filter(None,r.get('RelayCode','').split(';'))))


def update(folder):
    folder=Path(folder);path=folder/'PTseq_16_A.elab'
    baseline=subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
    assert path.read_text()==baseline,'Expected approved EDTA baseline; do not replace divergent edits'
    root=E.fromstring(baseline);old={int(f.get('ID')):f for f in root}
    text=baseline;rows=[];all_events=[];scenes=[]
    for reagent,vol,host,fid,did,seg in CASES:
        p=seed(old[fid][0][0],[('',host)])
        if reagent=='AMP':
            for d in p.S.drops.values():d.a06_authorized_padding=True
        p.add_reagent(seg,old[fid].get('Name').split(' · ')[0],reagent,vol)
        flows=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
        assert len(flows)==2
        for generated,target_id in zip(flows,(fid,did)):
            native=old[target_id];replacement=copy.deepcopy(native)
            assert len(generated.cmds)==len(native)
            for gc,nc,rc in zip(generated.cmds,native,replacement):
                assert len(gc.relays)==len(nc),(reagent,target_id,len(gc.relays),len(nc))
                for g,n,r in zip(gc.relays,nc,rc):
                    assert tuple(sorted(g.codes))==key(n),'Geometry/order must remain exactly published'
                    assert g.amp==int(n.get('Amp'))
                    delta=g.dur-int(n.get('Duration'))
                    assert delta>=0 and delta%3000==0,(reagent,n.attrib,g.dur)
                    r.set('Duration',str(g.dur))
                rc.set('Timeout',str(sum(int(r.get('Duration')) for r in rc)//1000))
            replacement.set('Timeout',str(sum(int(c.get('Timeout')) for c in replacement)))
            if target_id==fid:
                assert E.tostring(replacement)==E.tostring(native),'Manual receiving sections stay unchanged'
            else:
                text=replace_flow(text,did,E.tostring(replacement,encoding='unicode'))
                before=int(native.get('Timeout'));after=int(replacement.get('Timeout'))
                rows.append(dict(reagent=reagent,receiving_section=old[fid].get('Name').split()[0],
                    receiving_id=fid,section=native.get('Name').split()[0],section_id=did,
                    command_id=int(native[0].get('ID')),before_seconds=before,after_seconds=after,
                    added_seconds=after-before,pause_count=len(p.S.top_mother_events),
                    geometry_and_frame_count_unchanged=True,all_small_rows_ready_before_merge=True))
        assert all(abs(d.vol-host-vol)<1e-8 for d in p.S.drops.values())
        assert p.S.reaction_merge_events[-1]['contact_step']>next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
        scenes.append(validate_scene(p,reagent+' slow mother timing'))
        all_events.extend(p.S.top_mother_events)
        print(reagent,rows[-1]['before_seconds'],'->',rows[-1]['after_seconds'],'seconds',flush=True)
    after=E.fromstring(text);changed={case[4] for case in CASES}
    for f in after:
        if int(f.get('ID')) not in changed:assert E.tostring(f)==E.tostring(old[int(f.get('ID'))])
    assert E.tostring(after.find("Flow[@ID='50000']"))==E.tostring(old[50000])
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    main=folder/'main.elab';mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',main.read_text(),count=1);assert n==1
    report=dict(status='passed',baseline_commit=BASE,baseline_sha256=hashlib.sha256(baseline.encode()).hexdigest(),
        script_sha256=hashlib.sha256(text.encode()).hexdigest(),modified_section_ids=sorted(changed),
        cases=rows,total_added_seconds=sum(r['added_seconds'] for r in rows),
        movement_cell_ms=1000,movement_cells_per_group=2,additional_rest_ms=3000,
        reshape_ms=4000,small_stage_carrier_cells=30,small_stage_latches_fast=True,
        symmetric_split_count=sum(s['equal_x_splits'] for s in scenes),scenes=scenes,
        pauses=all_events,receiving_sections_unchanged=True,A02_A03_unchanged=True,
        geometry_volumes_areas_split_ratios_unchanged=True,all_other_flows_unchanged=True,
        physical_validation=False,GUI_check=False)
    path.write_text(text);main.write_text(mt)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path)
    report=update(ap.parse_args().folder)
    print(json.dumps({k:v for k,v in report.items() if k not in ('pauses','scenes')},ensure_ascii=False,indent=2))
