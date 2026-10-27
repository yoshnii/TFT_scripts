"""Publish concurrent top dispensing and early waste exits on the approved baseline."""
import argparse,csv,hashlib,json,re,subprocess
from pathlib import Path
import xml.etree.ElementTree as E
from xml.sax.saxutils import escape
import rigid
from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,last_hv,validate_scene
from update_workbook_volumes_local import remap,read_csv
from update_captured_aqueous_local import generate as captured,bottom_key
from update_top_mother_timing_local import CASES

ROOT=Path(__file__).resolve().parent.parent
BASE='5445be10bb62a7fe6efdf0376045ca0bfa3ab3ed'
REPORT='并行排程局部校验_20261027.json'
CHANGED={50001,50002,50003,50005,50006,50007,51000,2068,4048,75020}


def register(flow,fid,old):
    remap(flow,fid,[int(c.get('ID')) for c in old[fid]])
    flow.name=old[fid].get('Name')
    for c,native in zip(flow.cmds,old[fid]):c.name=native.get('Name')
    exported=E.fromstring(flow.xml());exported.tail=old[fid].tail
    return exported


def generate(root):
    ordered=list(root);old={int(f.get('ID')):f for f in root}
    fresh={};proof=[]
    for reagent,vol,host,fid,did,seg in CASES:
        p=seed(old[fid][0][0],[('',host)])
        if reagent=='AMP':
            for d in p.S.drops.values():d.a06_authorized_padding=True
        p.add_reagent(seg,old[fid].get('Name').split(' · ')[0],reagent,vol)
        fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
        assert len(fs)==2
        gate=register(fs[0],fid,old)
        assert E.tostring(gate)==E.tostring(old[fid]),('Manual gate changed',reagent)
        exported=register(fs[1],did,old)
        assert last_hv(exported).get('RelayCode')==last_hv(old[did]).get('RelayCode'),('Downstream mask changed',reagent)
        assert all(abs(d.vol-host-vol)<1e-8 for d in p.S.drops.values())
        ready=next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
        assert p.S.reaction_merge_events[-1]['contact_step']>ready
        leaves=p.S.native_leaf_events
        assert len(leaves)==16*len([e for e in leaves if e['row']==1])
        assert all(abs(sum(e['actual_ul'] for e in leaves if e['row']==r)-vol)<1e-8 for r in range(1,17))
        rests=p.S.top_independent_rest_events
        assert rests and all(e['resume_not_before_ms']-e['move_end_ms']==3000 for e in rests)
        scene=validate_scene(p,reagent+' concurrent dispensing')
        proof.append(dict(kind='top',reagent=reagent,section_id=did,scene=scene,
            input_gate_unchanged=True,downstream_mask_unchanged=True,all_ready_step=ready,
            fusion_step=p.S.reaction_merge_events[-1]['contact_step'],per_lane_added_ul=vol,
            per_lane_final_ul=host+vol,leaf_shape_w_h=leaves[0]['shape'],leaf_actual_ul=leaves[0]['actual_ul'],
            total_leaves=len(leaves),independent_rest_count=len(rests),rest_ms=3000,
            split_events=len(p.S.split_events),shape_hold_ms=4000))
        fresh[did]=fs[1]
        print(reagent,old[did].get('Timeout'),'->',fs[1].timeout,flush=True)
    # Retain both captured-water first cuts, C05/C06 and their strict queue.
    caps,cap_proof=captured(old,ordered)
    for fid in (2013,4013,51002):
        f=caps[fid]
        exported=E.fromstring(f.xml());exported.tail=old[fid].tail
        assert E.tostring(exported)==E.tostring(old[fid]),('Protected captured-water section changed',fid)
    fresh[51000]=caps[51000]
    proof.append(dict(kind='captured_water',section_id=51000,cases=cap_proof,
        first_cuts_unchanged=True,C05_C06_unchanged=True,cut_gaps=[0,2,4,6],cut_ms=[1000,3000,3000,5000]))
    for fid,label in ((2068,'R19'),(4048,'C15'),(75020,'C18')):
        prev=ordered[ordered.index(old[fid])-1]
        p=seed(last_hv(prev),[('',3.)]);p.magnet_state='down'
        if set(p.S.relays[0].codes)!=set(old[fid][0][0].get('RelayCode').split(';')):p.S.flush()
        rigid.egress_pieces(p.S,[(ln,p.S.get(ln.name)) for ln in p.ALL],p.blob,
                            final=True,single_stop=label!='R19')
        p.flow('C' if label.startswith('C') else 'R1',old[fid].get('Name'),[p.cmd('parallel waste')])
        fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
        assert len(fs)==1
        exported=register(fs[0],fid,old)
        assert [bottom_key(r) for r in list(exported[-1])[-24:]]==[bottom_key(r) for r in list(old[fid][-1])[-24:]]
        assert last_hv(exported).get('RelayCode')==last_hv(old[fid]).get('RelayCode')
        proof.append(dict(kind='cleanup',section_id=fid,scene=validate_scene(p,label,48.,48.),
                          actual_waste_ul=48.,packet_shape_w_h=[4,4],outlet_tail_unchanged=True))
        fresh[fid]=fs[0]
        print(label,old[fid].get('Timeout'),'->',fs[0].timeout,flush=True)
    assert set(fresh)==CHANGED
    return fresh,proof


def update(folder):
    folder=Path(folder);path=folder/'PTseq_16_A.elab';baseline=path.read_text()
    expected=subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
    assert baseline==expected,'Do not overwrite a divergent native baseline'
    root=E.fromstring(baseline);old={int(f.get('ID')):f for f in root}
    fresh,proof=generate(root);text=baseline;timings=[]
    for fid,f in fresh.items():
        # Preserve the preceding section's tail whitespace as well as its XML.
        text,n=re.subn(rf'<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>',
                       lambda _:f.xml().strip(),text,count=1)
        assert n==1
        timings.append(dict(section=old[fid].get('Name').split()[0],id=fid,
            before_seconds=int(old[fid].get('Timeout')),after_seconds=f.timeout,
            saved_seconds=int(old[fid].get('Timeout'))-f.timeout))
    after=E.fromstring(text)
    assert {int(f.get('ID')) for f in after if E.tostring(f)!=E.tostring(old[int(f.get('ID'))])}==CHANGED
    for f in after:
        if int(f.get('ID')) not in CHANGED:assert E.tostring(f)==E.tostring(old[int(f.get('ID'))])
    # Names, graph dependencies, non-HV commands, repeats and section boundaries remain released.
    for fid in CHANGED:
        a=old[fid];b=after.find(f"Flow[@ID='{fid}']")
        assert {k:v for k,v in a.attrib.items() if k!='Timeout'}=={k:v for k,v in b.attrib.items() if k!='Timeout'}
        for x,y in zip(a,b):
            assert {k:v for k,v in x.attrib.items() if k not in ('Timeout',)}=={k:v for k,v in y.attrib.items() if k not in ('Timeout',)}
            if x.get('Component')!='HighVoltage':assert E.tostring(x)==E.tostring(y)
    ah,ar=read_csv(folder/'手工吸液顺序.csv')
    for row in ar:
        fid=int(row[ah[1]])
        if fid in fresh and fresh[fid].cmds[-1].manual_aspiration:
            c=fresh[fid].cmds[-1];assert float(row[ah[6]])==c.manual_aspiration['volume_ul']
            row[ah[4]]=str(len(c.relays))
    page=(folder/'手工加液运行.html').read_text();count=0
    def table(m):
        nonlocal count
        count+=1
        if count==1:return m[0]
        return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in ah)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(str(r[h]))+'</td>' for h in ah)+'</tr>' for r in ar)+'</table>'
    page=re.sub(r'<table\b[^>]*>[\s\S]*?</table>',table,page);assert count==2
    note=('<p id="parallel-20261027">2026-10-27并行排程：A08、R02、B03、B11、B15、C02按容量并行拆分；'
          '各母滴独立执行两格移动后停3秒，其他母滴可继续。形变4秒、左右对半、6格运输间隙和全行就绪后同步融合保持。'
          'R06、R19、C15、C18提前衔接运输；C05/C06、A02/A03、孔旁回收、实际体积和温磁控时间不变。</p>')
    page=page.replace('<table',note+'<table',1)
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    report=dict(status='passed',baseline_commit=BASE,baseline_sha256=hashlib.sha256(baseline.encode()).hexdigest(),
        script_sha256=hashlib.sha256(text.encode()).hexdigest(),modified_section_ids=sorted(CHANGED),
        timings=timings,total_saved_seconds=sum(r['saved_seconds'] for r in timings),cases=proof,
        actual_volumes_and_ratios_unchanged=True,all_other_sections_unchanged=True,
        manual_gates_unchanged=True,thermal_magnet_incubation_unchanged=True,
        small_packet_shapes_unchanged=True,physical_validation=False,GUI_check=False)
    path.write_text(text);(folder/'main.elab').write_text(mt)
    with (folder/'手工吸液顺序.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.DictWriter(fh,ah);w.writeheader();w.writerows(ar)
    (folder/'手工加液运行.html').write_text(page)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path)
    r=update(ap.parse_args().folder)
    print(json.dumps({k:v for k,v in r.items() if k!='cases'},ensure_ascii=False,indent=2))
