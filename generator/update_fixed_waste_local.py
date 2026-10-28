"""Publish all active F-hole collectors as fixed final receiving electrodes."""
import argparse,copy,csv,hashlib,json,re,subprocess
from pathlib import Path
import xml.etree.ElementTree as E
from xml.sax.saxutils import escape
import geom,rigid
from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,last_hv,validate_scene
from update_parallel_local import register
from update_workbook_volumes_local import read_csv
from update_refill_local import FrameTrace
import update_captured_aqueous_local as captured
from waste_recovery import boxes

ROOT=Path(__file__).resolve().parent.parent
BASE='fd6cd24b6d51c23ce251f1c075968c47166762e9'
REPORT='固定废液孔逐滴汇入局部校验_20261028.json'
TARGET={1019,2013,51000,2068,50004,4013,51002,4048,75020}


def relay_key(r):
    a=dict(r.attrib);a.pop('ItemID',None)
    a['RelayCode']=';'.join(sorted(filter(None,a.get('RelayCode','').split(';'))))
    return a


class FixedAudit:
    def __init__(self,p,initial_mass):
        self.p=p;self.initial_mass=initial_mass;self.rects={};self.objects={}
        self.frames=0;self.mask_cells=0;self.external_overlap_frames=0
        self.trace=FrameTrace(p);record=p.S._record
        def checked(*args,**kwargs):
            record(*args,**kwargs);self.frames+=1
            mass=sum(d.vol for d in p.S.drops.values())+sum(info['volume_ul'] for _,info in p.S.manual_aspirations)
            assert abs(mass-initial_mass)<1e-7,('实际液量不守恒',mass,initial_mass)
            wanted=set()
            for d in p.S.drops.values():
                if not d.rects():continue
                self.objects[id(d)]=d;wanted.update(d.target_cells())
                assert not(d.rect[0]<68 and d.rect[0]+d.rect[2]>50 and d.rect[1]<172)
                assert not d.name.startswith(('WBLOCK','WCOL','WROW','WBAR','B07_HALF','B07_PAIR','B07_QUARTER'))
                if getattr(d,'fixed_waste_receiver',False):
                    before=self.rects.setdefault(id(d),d.rect);assert d.rect==before,'接收框不得移动或形变'
                    assert len(d.target_cells())==d.electrode_area
                    assert d.electrode_area in (192,256)
                receiver=p.S.drops.get(getattr(d,'waste_receiver',None))
                if receiver and d.target_cells()&receiver.target_cells():self.external_overlap_frames+=1
            actual=set();count=0
            for code in p.S.relays[-1].codes:
                for x,y,w,h in boxes(code):
                    cs={(i,j) for i in range(x,x+w) for j in range(y,y+h)}
                    assert not actual&cs,'交接电极重复输出'
                    actual.update(cs);count+=len(cs)
            assert actual==wanted,'交接时遗漏液体覆盖或提前撤去运输电极'
            self.mask_cells+=count
        p.S._record=checked
    def report(self):
        events=self.p.S.fixed_waste_events
        pre=[e for e in events if e['kind']=='preopen'];ads=[e for e in events if e['kind']=='absorb']
        assert pre and all(e['actual_ul']==0 for e in pre)
        assert all(e['count']<=16 and e['packet_ul']>0 for e in ads)
        assert all(d.vol>=0 for d in self.p.S.drops.values())
        return dict(scene_frames=self.frames,union_cells_checked=self.mask_cells,
            actual_mass_ul=self.initial_mass,mass_conserved_each_scene_frame=True,
            receiver_footprints_unchanged=True,full_coverage_and_no_duplicate_codes=True,
            partial_entry_frames=self.external_overlap_frames,preopen_events=pre,
            admission_events=ads,admitted_packets=len(ads),receiver_count=len(pre),
            no_intermediate_collection_blocks=True,hold_trace=self.trace.proofs)


def build(root):
    ordered=list(root);old={int(f.get('ID')):f for f in root};fresh={};proof=[]
    def publish(p,flows,ids,audit,label,initial,waste,preserve_first=False):
        assert len(flows)==len(ids)
        for f,fid in zip(flows,ids):
            e=register(f,fid,old)
            if preserve_first:
                # A06 starts from the already-shaped input mask in this local
                # seed. Retain its released 4-second first shape hold verbatim.
                assert len(e[0])==len(old[fid][0])
                for a,b in zip(e[0],old[fid][0]):
                    assert {k:v for k,v in a.attrib.items() if k!='Duration'}=={k:v for k,v in b.attrib.items() if k!='Duration'}
                f.cmds[0].relays[0].dur=int(old[fid][0][0].get('Duration'))
                f.cmds[0].timeout=int(old[fid][0].get('Timeout'))
                e=register(f,fid,old)
                assert E.tostring(e[0])==E.tostring(old[fid][0])
            tail=22 if fid==50004 else 24
            assert [relay_key(r) for r in list(e[-1])[-tail:]]==[relay_key(r) for r in list(old[fid][-1])[-tail:]],('吸液收尾改变',fid)
            assert last_hv(e).get('RelayCode')==last_hv(old[fid]).get('RelayCode')
            fresh[fid]=f
        proof.append(dict(case=label,ids=ids,scene=validate_scene(p,label,initial,waste),fixed=audit.report()))
        print(label,[(fid,int(old[fid].get('Timeout')),fresh[fid].timeout) for fid in ids],flush=True)
    # A06: retain its actual two binary cuts and entire split command.
    p=seed(old[1019][0][0],[('',2.)]);a=FixedAudit(p,32.)
    p.split_waste('A',old[1019].get('Name').split(' · ')[0],.5,'cDNA多余液体')
    fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
    publish(p,fs,[1019],a,'A06',32.,24.,True)
    # R05/R06/C05/C06: keep original first cut and later 1/3/3/5s cuts.
    instances=[];original_seed=captured.seed
    def traced_seed(*args,**kwargs):
        q=original_seed(*args,**kwargs);mass=sum(d.vol for d in q.S.drops.values())
        instances.append((q,FixedAudit(q,mass)));return q
    captured.seed=traced_seed
    try:caps,cut_proof=captured.generate(old,ordered)
    finally:captured.seed=original_seed
    for (p,a),ids in zip(instances,((2013,51000),(4013,51002))):
        vol=5. if ids[0]==2013 else 6.16
        publish(p,[caps[i] for i in ids],list(ids),a,'captured '+str(vol),vol*16,vol*16)
    # Barcode waste: preserve its original 0.6/0.6 equal split.
    raw=old[50004][0];p=seed(raw[0],[('',2.),('K',1.2)]);a=FixedAudit(p,51.2)
    rigid.bisect_many(p.S,[p.S.get('K'+ln.id) for ln in p.ALL],names=[('K'+ln.id,'BKWA'+ln.id) for ln in p.ALL])
    c=p.cmd(raw.get('Name'))
    rigid.egress_pieces(p.S,[(ln,p.S.get('BKWA'+ln.id)) for ln in p.ALL],p.blob)
    p.flow('B',old[50004].get('Name'),[c,p.cmd(old[50004][-1].get('Name'))])
    fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
    publish(p,fs,[50004],a,'B07',51.2,9.6,True)
    for fid,label in ((2068,'R19'),(4048,'C15'),(75020,'C18')):
        prev=ordered[ordered.index(old[fid])-1];p=seed(last_hv(prev),[('',3.)]);p.magnet_state='down'
        if set(p.S.relays[0].codes)!=set(old[fid][0][0].get('RelayCode').split(';')):p.S.flush()
        a=FixedAudit(p,48.)
        rigid.egress_pieces(p.S,[(ln,p.S.get(ln.name)) for ln in p.ALL],p.blob,final=True,single_stop=label!='R19')
        p.flow('C' if label.startswith('C') else 'R1',old[fid].get('Name'),[p.cmd(old[fid][-1].get('Name'))])
        fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
        publish(p,fs,[fid],a,label,48.,48.)
    assert set(fresh)==TARGET
    assert sum(x['fixed']['admitted_packets'] for x in proof)==496
    assert sum(x['fixed']['receiver_count'] for x in proof)==31
    return fresh,proof,cut_proof


def update(folder):
    folder=Path(folder);path=folder/'PTseq_16_A.elab';baseline=path.read_text()
    assert baseline==subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode(),'Divergent baseline'
    root=E.fromstring(baseline);old={int(f.get('ID')):f for f in root};fresh,proof,cut_proof=build(root)
    text=baseline;timings=[]
    for fid,f in fresh.items():
        text,n=re.subn(rf'<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>',lambda _:f.xml().strip(),text,count=1);assert n==1
        timings.append(dict(id=fid,section=old[fid].get('Name').split()[0],before_seconds=int(old[fid].get('Timeout')),after_seconds=f.timeout,saved_seconds=int(old[fid].get('Timeout'))-f.timeout))
    after=E.fromstring(text)
    assert {int(e.get('ID')) for e in after if E.tostring(e)!=E.tostring(old[int(e.get('ID'))])}==TARGET
    for fid in TARGET:
        a,b=old[fid],after.find(f"Flow[@ID='{fid}']")
        assert {k:v for k,v in a.attrib.items() if k!='Timeout'}=={k:v for k,v in b.attrib.items() if k!='Timeout'}
        for x,y in zip(a,b):assert {k:v for k,v in x.attrib.items() if k!='Timeout'}=={k:v for k,v in y.attrib.items() if k!='Timeout'}
    for a,b in ((2013,51000),(4013,51002)):
        assert last_hv(after.find(f"Flow[@ID='{a}']")).get('RelayCode')==after.find(f"Flow[@ID='{b}']")[0][0].get('RelayCode')
    ah,ar=read_csv(folder/'手工吸液顺序.csv')
    for row in ar:
        fid=int(row[ah[1]])
        if fid in fresh:
            c=fresh[fid].cmds[-1];assert float(row[ah[6]])==c.manual_aspiration['volume_ul']
            row[ah[4]]=str(len(c.relays))
    page=(folder/'手工加液运行.html').read_text();count=0
    def table(m):
        nonlocal count
        count+=1
        if count==1:return m[0]
        return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in ah)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(str(r[h]))+'</td>' for h in ah)+'</tr>' for r in ar)+'</table>'
    page=re.sub(r'<table\b[^>]*>[\s\S]*?</table>',table,page);assert count==2
    note=('<p id="fixed-waste-20261028">2026-10-28：全部F孔排废使用固定最终承接框逐滴接收。'
          '各孔按远至近、空间清空后预开最终框；预开时实际液量为0，小滴保持原形逐格接触/进入，完全覆盖后撤运输电极并计入实际量。'
          '不再拼4滴块、移动大块或进行孔旁汇液形变。各孔总量、分液/运输间距和等待、原吸液收尾不变。此前孔旁拼块说明以本条为准。</p>')
    page=page.replace('<table',note+'<table',1)
    total=sum(int(e.get('Timeout'))*int(e.get('Repeats','1')) for e in after)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    report=dict(status='passed',baseline_commit=BASE,baseline_sha256=hashlib.sha256(baseline.encode()).hexdigest(),script_sha256=hashlib.sha256(text.encode()).hexdigest(),
        modified_section_ids=sorted(TARGET),timings=timings,total_saved_seconds=sum(x['saved_seconds'] for x in timings),
        cases=proof,captured_cut_checks=cut_proof,admitted_packets=496,fixed_receiver_cycles=31,
        total_waste_ul=356.16,receiver_capacity_is_not_real_liquid=True,
        all_other_sections_unchanged=True,actual_volumes_ratios_and_waits_unchanged=True,
        suction_tails_unchanged=True,workbook_unchanged=True,physical_validation=False,GUI_check=False)
    path.write_text(text);(folder/'main.elab').write_text(mt)
    with (folder/'手工吸液顺序.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.DictWriter(fh,ah);w.writeheader();w.writerows(ar)
    (folder/'手工加液运行.html').write_text(page);(folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('SAVED_SECONDS',report['total_saved_seconds'],flush=True)
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);update(ap.parse_args().folder)
