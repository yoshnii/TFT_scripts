"""Remove proven preparatory detours; retain split shapes, timing and endpoints."""
import argparse,hashlib,json,re,subprocess
from pathlib import Path
import xml.etree.ElementTree as E
from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,last_hv,validate_scene
from update_parallel_local import register
from update_refill_local import FrameTrace
from audit_motion_routes import scan
ROOT=Path(__file__).resolve().parent.parent
BASE='a80e9c0a31ecf476357c0b6905b4862c7e1b9f0e'
REPORT='无效往返运动局部校验_20261028.json'
TARGET={50000,50002}


def build(root):
    old={int(f.get('ID')):f for f in root};fresh={};proof=[]
    for reagent,vol,host,fid,did,seg in (('G4',.4,1.6,1002,50000,'A'),('G14',2.5,2.5,2000,50002,'R1')):
        p=seed(old[fid][0][0],[('',host)])
        if reagent=='G4':p.S.relays[0].dur=int(old[fid][0][0].get('Duration'))
        trace=FrameTrace(p);record=p.S._record;roots={}
        def recorded(*args,**kwargs):
            record(*args,**kwargs)
            for d in p.S.drops.values():
                if d.name in [f'D{reagent}E{k}' for k in range(1,5)]:
                    values=roots.setdefault(d.name,[])
                    if not values or values[-1]!=d.rect:values.append(d.rect)
        p.S._record=recorded
        p.add_reagent(seg,old[fid].get('Name').split(' · ')[0],reagent,vol)
        fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows;assert len(fs)==2
        assert E.tostring(register(fs[0],fid,old))==E.tostring(old[fid]),'Manual receiving section changed'
        native=register(fs[1],did,old)
        assert native[0][0].get('RelayCode')==old[did][0][0].get('RelayCode')
        assert last_hv(native).get('RelayCode')==last_hv(old[did]).get('RelayCode')
        leaves=p.S.native_leaf_events
        assert all(abs(sum(e['actual_ul'] for e in leaves if e['row']==r)-vol)<1e-8 for r in range(1,17))
        assert all(abs(d.vol-host-vol)<1e-8 for d in p.S.drops.values())
        ready=next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
        assert all(e['contact_step']>ready for e in p.S.reaction_merge_events)
        paths=[]
        for name,rr in roots.items():
            xs=[rc[0] for rc in rr];right=sum(max(0,b-a) for a,b in zip(xs,xs[1:]));left=sum(max(0,a-b) for a,b in zip(xs,xs[1:]))
            paths.append(dict(name=name,start_x=xs[0],end_x=xs[-1],min_x=min(xs),max_x=max(xs),right_cells=right,left_cells=left,rect_path=[list(rc) for rc in rr]))
        if reagent=='G4':
            q=next(q for q in paths if q['name']=='DG4E2');assert (q['start_x'],q['end_x'],q['max_x'],q['right_cells'])==(102,40,102,0)
        else:
            q=next(q for q in paths if q['name']=='DG14E3');assert q['max_x']==158 and q['right_cells']==4
            q=next(q for q in paths if q['name']=='DG14E4');assert q['right_cells']==0
            assert all(e['rest_ms']==3000 for e in p.S.top_independent_rest_events)
        proof.append(dict(reagent=reagent,section_id=did,scene=validate_scene(p,reagent+' direct preparation'),root_routes=paths,
            real_ul_per_lane_added=vol,real_ul_per_lane_final=host+vol,leaf_count=len(leaves),leaf_shape_w_h=leaves[0]['shape'],leaf_ul=leaves[0]['actual_ul'],
            hold_trace=trace.proofs,split_count=len(p.S.split_events),
            split_shapes=[dict(parent=e['parent'],positions=e['positions']) for e in p.S.split_events],receiving_section_unchanged=True,start_end_masks_unchanged=True,
            all_ready_step=ready,reaction_contact_step=min(e['contact_step'] for e in p.S.reaction_merge_events),
            slow_mother_rest_ms=3000 if reagent=='G14' else None))
        fresh[did]=fs[1]
        print(reagent,old[did].get('Timeout'),'->',fs[1].timeout,flush=True)
    return fresh,proof


def update(folder):
    folder=Path(folder);path=folder/'PTseq_16_A.elab';baseline=path.read_text()
    assert baseline==subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode(),'Divergent baseline'
    root=E.fromstring(baseline);old={int(f.get('ID')):f for f in root};candidates=scan(path)
    groups={}
    for e in candidates:
        key=(e['section'],e['command'],e['name']);g=groups.setdefault(key,dict(section=key[0],command=key[1],name=key[2],candidate_count=0,max_reverse_cells=0))
        g['candidate_count']+=1;g['max_reverse_cells']=max(g['max_reverse_cells'],e['excess_cells'])
    for g in groups.values():
        g['review']=('closed mixing/washing path; keep' if '混匀' in g['name'] or '清洗' in g['name'] else
                     'inlet-channel routing/reshuffle; keep except the proven root detours' if '顶部' in g['name'] else
                     'symmetric split opening or required in-well staging; keep')
    fresh,proof=build(root);text=baseline;timings=[]
    for fid,f in fresh.items():
        text,n=re.subn(rf'<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>',lambda _:f.xml().strip(),text,count=1);assert n==1
        saved=int(old[fid].get('Timeout'))-f.timeout;assert saved>=0
        timings.append(dict(id=fid,section=old[fid].get('Name').split()[0],before_seconds=int(old[fid].get('Timeout')),after_seconds=f.timeout,saved_seconds=saved))
    after=E.fromstring(text)
    assert {int(f.get('ID')) for f in after if E.tostring(f)!=E.tostring(old[int(f.get('ID'))])}==TARGET
    for fid in TARGET:
        a,b=old[fid],after.find(f"Flow[@ID='{fid}']")
        assert {k:v for k,v in a.attrib.items() if k!='Timeout'}=={k:v for k,v in b.attrib.items() if k!='Timeout'}
        for x,y in zip(a,b):assert {k:v for k,v in x.attrib.items() if k!='Timeout'}=={k:v for k,v in y.attrib.items() if k!='Timeout'}
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    page=(folder/'手工加液运行.html').read_text()
    note=('<p id="direct-routes-20261028">2026-10-28：A03两个G4母滴直接向左进入分液位置，取消右侧母滴先到X136再返回X40的暂存。'
          'R02后批第三孔仅避让到X158（原X168），保留切口展开与6格间距，并按两格慢速节奏结束定位；第四孔沿原单向路径提前准备。'
          '实际体系、承接/分液形状、等待时序和全行同步融合不变；混匀、清洗及通道绕行保留。</p>')
    page=page.replace('<table',note+'<table',1)
    report=dict(status='passed',baseline_commit=BASE,baseline_sha256=hashlib.sha256(baseline.encode()).hexdigest(),script_sha256=hashlib.sha256(text.encode()).hexdigest(),
        modified_section_ids=sorted(TARGET),timings=timings,total_saved_seconds=sum(e['saved_seconds'] for e in timings),
        scan_limitations='Same-shape one-cell links only; reshapes break identity. Candidates require source review, not automatic deletion.',
        full_native_reverse_candidate_count=len(candidates),scan_review=list(groups.values()),cases=proof,
        actual_volumes_ratios_shapes_and_waits_unchanged=True,all_other_sections_unchanged=True,
        fixed_waste_collectors_unchanged=True,thermal_magnet_manual_boundaries_unchanged=True,
        workbook_modified=False,GUI_check=False,physical_validation=False)
    path.write_text(text);(folder/'main.elab').write_text(mt);(folder/'手工加液运行.html').write_text(page)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('SAVED_SECONDS',report['total_saved_seconds'],flush=True);return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);update(ap.parse_args().folder)
