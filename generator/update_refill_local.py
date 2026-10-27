"""R02 only: refill top-band space while the previous G14 family is in transit."""
import argparse,hashlib,json,re,subprocess
from pathlib import Path
import xml.etree.ElementTree as E
from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,last_hv,validate_scene
from update_parallel_local import register

ROOT=Path(__file__).resolve().parent.parent
BASE='2b5f7252b2df5f4aa159c4c0c96d935de208b597'
REPORT='R02连续补位局部校验_20261027.json'


class FrameTrace:
    def __init__(self,p):
        self.rows=[];self.objects={};self.proofs=[]
        record=p.S._record;command=p.cmd
        def capture(*args,**kwargs):
            record(*args,**kwargs)
            state={}
            for d in p.S.drops.values():
                self.objects[id(d)]=d
                state[id(d)]=(d.name,d.rect,d.vol)
            self.rows.append((p.S.relays[-1],state))
        def checked(name):
            active={id(r) for r in p.S.relays}
            rows=[(r,s) for r,s in self.rows if id(r) in active]
            if rows:self.proofs.append(self.check(rows))
            return command(name)
        p.S._record=capture;p.cmd=checked

    @staticmethod
    def check(rows):
        elapsed=0;previous={};holds={};events=[];moves=0
        for frame,(relay,state) in enumerate(rows):
            for key,before in previous.items():
                after=state.get(key)
                if after is None or after[1:]!=before[1:]:
                    assert elapsed>=holds.get(key,0),('形变不足4秒',before,after,elapsed,holds.get(key))
                if after is None:holds.pop(key,None);continue
                if after[2]==before[2] and after[1][2:]!=before[1][2:]:
                    holds[key]=elapsed+4000
                    events.append(dict(name=after[0],frame=frame,at_ms=elapsed,
                                       shape_w_h=list(after[1][2:]),hold_ms=4000))
                elif after[2]==before[2] and after[1][2:]==before[1][2:]:
                    distance=abs(after[1][0]-before[1][0])+abs(after[1][1]-before[1][1])
                    assert distance<=1,('跳格移动',before,after)
                    moves+=int(distance>0)
            previous=state;elapsed+=relay.dur
        assert elapsed>=max(holds.values(),default=0)
        return dict(raw_frames=len(rows),elapsed_ms=elapsed,single_cell_moves=moves,
                    reshape_events=events,all_reshape_holds_at_least_4000ms=True)


def build(root):
    old={int(f.get('ID')):f for f in root}
    p=seed(old[2000][0][0],[('',2.5)]);trace=FrameTrace(p)
    p.add_reagent('R1',old[2000].get('Name').split(' · ')[0],'G14',2.5)
    fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
    assert len(fs)==2
    gate=register(fs[0],2000,old)
    assert E.tostring(gate)==E.tostring(old[2000]),'R01 receiving gate changed'
    new=register(fs[1],50002,old)
    assert last_hv(new).get('RelayCode')==last_hv(old[50002]).get('RelayCode')
    assert all(abs(d.vol-5.)<1e-8 for d in p.S.drops.values())
    leaves=p.S.native_leaf_events
    assert len(leaves)==64 and all(e['actual_ul']==.625 and e['shape']==[3,5] for e in leaves)
    assert all(sum(e['actual_ul'] for e in leaves if e['row']==r)==2.5 for r in range(1,17))
    ready=next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
    assert all(e['contact_step']>ready for e in p.S.reaction_merge_events)
    overlap=next(e for e in p.S.pipeline_events if e['kind']=='overlap_delivery')
    assert overlap['mother_and_packet_moving_frames']>0
    assert len(p.S.split_events)==60
    assert not p.S.background and p.S.top_delivery_pool is None and not p.S.top_parallel_moves
    reshapes=[e for proof in trace.proofs for e in proof['reshape_events']]
    first_roots=[next(e for e in reshapes if e['name']==f'DG14E{k}') for k in range(1,5)]
    assert len({e['at_ms'] for e in first_roots})==1,'Four initial mothers should reshape together'
    scene=validate_scene(p,'R02 next family fills vacant top space')
    evidence=dict(scene=scene,overlap=overlap,raw_trace=trace.proofs,initial_root_reshapes=first_roots,
        all_ready_step=ready,first_reaction_contact_step=min(e['contact_step'] for e in p.S.reaction_merge_events),
        true_ul_per_port=10.,true_ul_per_lane_added=2.5,true_ul_per_lane_final=5.,
        leaf_count=64,leaf_ul=.625,leaf_shape_w_h=[3,5],symmetric_splits=60,
        per_mother_rest_ms=3000,reshape_ms=4000,
        ordinary_transport_gap=6,carrier_area_plans_unchanged=True)
    return fs[1],evidence


def update(folder):
    folder=Path(folder);path=folder/'PTseq_16_A.elab';baseline=path.read_text()
    expected=subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
    assert baseline==expected,'Do not overwrite divergent native files'
    root=E.fromstring(baseline);old={int(f.get('ID')):f for f in root}
    f,evidence=build(root)
    text,n=re.subn(r'<Flow\b(?=[^>]*\bID="50002")[^>]*>[\s\S]*?</Flow>',lambda _:f.xml().strip(),baseline,count=1)
    assert n==1
    new=E.fromstring(text)
    assert {int(e.get('ID')) for e in new if E.tostring(e)!=E.tostring(old[int(e.get('ID'))])}=={50002}
    target=new.find("Flow[@ID='50002']")
    assert {k:v for k,v in target.attrib.items() if k!='Timeout'}=={k:v for k,v in old[50002].attrib.items() if k!='Timeout'}
    for a,b in zip(old[50002],target):
        assert {k:v for k,v in a.attrib.items() if k!='Timeout'}=={k:v for k,v in b.attrib.items() if k!='Timeout'}
    saved=int(old[50002].get('Timeout'))-f.timeout;assert saved>0
    total=sum(int(e.get('Timeout'))*int(e.get('Repeats','1')) for e in new)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    page=(folder/'手工加液运行.html').read_text()
    note=('<p id="refill-r02-20261027">2026-10-27 R02连续补位：前两孔分出的小滴运输与后两孔母滴准备重叠，'
          '后两孔暂存移动与前两孔定位同时执行，四孔首次形变共用4秒保持。'
          '每孔10µL、每行加2.50µL、最终5.00µL，64滴0.625µL/3×5承接不变；全部就绪后同步加入反应体系。'
          '单滴3秒停留、4秒形变、6格独立运输间距保持；只改R02，排废及其他section不变。</p>')
    page=page.replace('<table',note+'<table',1)
    report=dict(status='passed',baseline_commit=BASE,baseline_sha256=hashlib.sha256(baseline.encode()).hexdigest(),
        script_sha256=hashlib.sha256(text.encode()).hexdigest(),modified_section_ids=[50002],section='R02',
        before_seconds=int(old[50002].get('Timeout')),after_seconds=f.timeout,total_saved_seconds=saved,
        cumulative_saved_since_5445be1_seconds=1821+saved,evidence=evidence,
        all_other_sections_unchanged=True,manual_receiving_gates_unchanged=True,
        actual_volumes_ratios_and_leaf_shapes_unchanged=True,thermal_magnet_incubation_unchanged=True,
        waste_collection_and_recovery_unchanged=True,workbook_unchanged=True,
        physical_validation=False,GUI_check=False)
    path.write_text(text);(folder/'main.elab').write_text(mt)
    (folder/'手工加液运行.html').write_text(page)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('R02',report['before_seconds'],'->',f.timeout,'SAVED',saved,flush=True)
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path)
    update(ap.parse_args().folder)
