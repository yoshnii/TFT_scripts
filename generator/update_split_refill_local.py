"""Publish the six approved split/settle/refill edits on the pinned native baseline."""
import argparse,csv,hashlib,json,re,subprocess
from pathlib import Path
import xml.etree.ElementTree as E
from xml.sax.saxutils import escape
import geom,rigid
from native_packets import bisect_barcode
from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,last_hv,validate_scene
from update_parallel_local import register
from update_refill_local import FrameTrace
from update_fixed_waste_local import FixedAudit,relay_key
from update_workbook_volumes_local import read_csv
import update_captured_aqueous_local as captured

ROOT=Path(__file__).resolve().parent.parent
BASE='31f41bcc8cc385da64fb3857ff87a2e42d41c9aa'
REPORT='分液增距停留与排废续流局部校验_20261028.json'
WASTE={1019,2013,51000,2068,50004,4013,51002,4048,75020}
TOP={50002,50005,50006}
TARGET=WASTE|TOP

class RefillAudit(FixedAudit):
    def __init__(self,p,mass):
        super().__init__(p,mass);self.multi_batch_frames=0;self.refill_before_previous_bottom=0
        record=p.S._record
        def checked(*args,**kwargs):
            record(*args,**kwargs)
            ds=[d for d in p.S.drops.values() if getattr(d,'continuous_waste_exit',False)]
            ch=[d for d in ds if d.rect[0]==getattr(p.S,'batch_channel_x',None) and d.rect[1]<164]
            self.multi_batch_frames+=int(len({d.batch_round for d in ch})>1)
            for d in ch:
                if d.rect[1]<50 and any(q.batch_round<d.batch_round and q.rect[1]<164 for q in ch):
                    self.refill_before_previous_bottom+=1;break
        p.S._record=checked
    def report(self):
        out=super().report();out.update(multi_batch_channel_frames=self.multi_batch_frames,
            refill_before_previous_bottom_frames=self.refill_before_previous_bottom,
            arrival_assignments=getattr(self.p.S,'waste_arrival_events',[]))
        return out

def build(root):
    ordered=list(root);old={int(f.get('ID')):f for f in root};fresh={};proof=[]
    def publish(p,flows,ids,audit,label,initial,waste,preserve_a06=False):
        assert len(flows)==len(ids)
        for f,fid in zip(flows,ids):
            if preserve_a06:
                f.cmds[0].relays[0].dur=int(old[fid][0][0].get('Duration'))
                f.cmds[0].timeout=int(old[fid][0].get('Timeout'))
            e=register(f,fid,old)
            if preserve_a06:assert E.tostring(e[0])==E.tostring(old[fid][0])
            tail=22 if fid==50004 else 24
            assert [relay_key(r) for r in list(e[-1])[-tail:]]==[relay_key(r) for r in list(old[fid][-1])[-tail:]],('吸液收尾改变',fid)
            assert relay_key(last_hv(e))==relay_key(last_hv(old[fid]))
            fresh[fid]=f
        splits=[]
        for e in p.S.split_events:
            if e['parent'].endswith('A1') or e['parent'].startswith('LIQA1_') and not e['parent'].startswith(('LIQA10','LIQA11','LIQA12','LIQA13','LIQA14','LIQA15','LIQA16')):
                splits.append({k:v for k,v in e.items() if not k.startswith('_')})
        proof.append(dict(case=label,ids=ids,scene=validate_scene(p,label,initial,waste),fixed=audit.report(),split_samples=splits))
        print(label,[(fid,int(old[fid].get('Timeout')),fresh[fid].timeout) for fid in ids],flush=True)
    p=seed(old[1019][0][0],[('',2.)]);audit=RefillAudit(p,32.)
    p.split_waste('A',old[1019].get('Name').split(' · ')[0],.5,'cDNA多余液体')
    publish(p,prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows,[1019],audit,'A06',32.,24.,True)
    instances=[];original_seed=captured.seed
    def traced_seed(*args,**kwargs):
        q=original_seed(*args,**kwargs);instances.append((q,RefillAudit(q,sum(d.vol for d in q.S.drops.values()))));return q
    captured.seed=traced_seed
    try:caps,cut_proof=captured.generate(old,ordered)
    finally:captured.seed=original_seed
    for (p,audit),ids in zip(instances,((2013,51000),(4013,51002))):
        vol=5. if ids[0]==2013 else 6.16
        publish(p,[caps[i] for i in ids],list(ids),audit,'captured '+str(vol),vol*16,vol*16)
    p=seed(old[50004][0][0],[('',2.),('K',1.2)]);audit=RefillAudit(p,51.2)
    bisect_barcode(p.S,p.ALL);c=p.cmd(old[50004][0].get('Name'))
    rigid.egress_pieces(p.S,[(ln,p.S.get('BKWA'+ln.id)) for ln in p.ALL],p.blob)
    p.flow('B',old[50004].get('Name'),[c,p.cmd(old[50004][-1].get('Name'))])
    publish(p,prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows,[50004],audit,'B07',51.2,9.6)
    for fid,label in ((2068,'R19'),(4048,'C15'),(75020,'C18')):
        prev=ordered[ordered.index(old[fid])-1];p=seed(last_hv(prev),[('',3.)]);p.magnet_state='down'
        if set(p.S.relays[0].codes)!=set(old[fid][0][0].get('RelayCode').split(';')):p.S.flush()
        audit=RefillAudit(p,48.)
        rigid.egress_pieces(p.S,[(ln,p.S.get(ln.name)) for ln in p.ALL],p.blob,final=True,single_stop=label!='R19')
        p.flow('C' if label.startswith('C') else 'R1',old[fid].get('Name'),[p.cmd(old[fid][-1].get('Name'))])
        publish(p,prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows,[fid],audit,label,48.,48.)
    assert set(fresh)==WASTE
    tops=[]
    for reagent,vol,host,fid,did,seg in (('G14',2.5,2.5,2000,50002,'R1'),('LIG',1.4,2.6,3026,50005,'B'),('EDTA',.4,4.,3041,50006,'B')):
        p=seed(old[fid][0][0],[('',host)]);trace=FrameTrace(p)
        p.add_reagent(seg,old[fid].get('Name').split(' · ')[0],reagent,vol)
        fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
        assert E.tostring(register(fs[0],fid,old))==E.tostring(old[fid])
        new=register(fs[1],did,old);fresh[did]=fs[1]
        assert new[0][0].get('RelayCode')==old[did][0][0].get('RelayCode')
        assert last_hv(new).get('RelayCode')==last_hv(old[did]).get('RelayCode')
        ready=next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
        assert all(e['contact_step']>ready for e in p.S.reaction_merge_events)
        leaf_shape=p.S.native_leaf_events[0]['shape'];events=[e for e in p.S.split_events if e['positions'][-1][0][2:]==leaf_shape]
        assert events and all(e['requested_frame_ms']==[1000,1000,1000,3000] for e in events)
        assert all(abs(d.vol-host-vol)<1e-8 for d in p.S.drops.values())
        tops.append(dict(section_id=did,reagent=reagent,scene=validate_scene(p,reagent+' final split hold'),
            added_ul=vol,final_ul=host+vol,leaves=p.S.native_leaf_events,final_split_count=len(events),
            final_gap=6,split_hold_ms=3000,hold_trace=trace.proofs,all_ready_step=ready,
            first_contact_step=min(e['contact_step'] for e in p.S.reaction_merge_events)))
        print(reagent,int(old[did].get('Timeout')),'->',fs[1].timeout,flush=True)
    return fresh,proof,cut_proof,tops

def update(folder):
    path=folder/'PTseq_16_A.elab';baseline=path.read_text()
    assert baseline==subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode(),'Divergent baseline'
    root=E.fromstring(baseline);old={int(f.get('ID')):f for f in root};fresh,proof,cuts,tops=build(root)
    text=baseline;timings=[]
    for fid,f in fresh.items():
        text,n=re.subn(rf'<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>',lambda _:f.xml().strip(),text,count=1);assert n==1
        timings.append(dict(id=fid,section=old[fid].get('Name').split()[0],before_seconds=int(old[fid].get('Timeout')),after_seconds=f.timeout,saved_seconds=int(old[fid].get('Timeout'))-f.timeout))
    after=E.fromstring(text)
    assert {int(f.get('ID')) for f in after if E.tostring(f)!=E.tostring(old[int(f.get('ID'))])}==TARGET
    for fid in TARGET:
        a,b=old[fid],after.find(f"Flow[@ID='{fid}']")
        assert {k:v for k,v in a.attrib.items() if k!='Timeout'}=={k:v for k,v in b.attrib.items() if k!='Timeout'}
        assert len(a)==len(b)
        for x,y in zip(a,b):assert {k:v for k,v in x.attrib.items() if k!='Timeout'}=={k:v for k,v in y.attrib.items() if k!='Timeout'}
    ah,ar=read_csv(folder/'手工吸液顺序.csv')
    for row in ar:
        fid=int(row[ah[1]])
        if fid in WASTE:
            c=fresh[fid].cmds[-1];assert abs(float(row[ah[6]])-c.manual_aspiration['volume_ul'])<1e-7
            row[ah[4]]=str(len(c.relays))
    page=(folder/'手工加液运行.html').read_text();table_count=0
    def table(m):
        nonlocal table_count
        table_count+=1
        if table_count==1:return m[0]
        return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in ah)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(str(row[h]))+'</td>' for h in ah)+'</tr>' for row in ar)+'</table>'
    page=re.sub(r'<table\b[^>]*>[\s\S]*?</table>',table,page);assert table_count==2
    note='<p id="split-refill-20261028">2026-10-28：A06的8×4余液移到区内切分位置后左右拉开12格；B07接头6×4母滴右移4格后左右拉开12格，保留滴终点不变。R02/B11/B15拆成最终小滴后保持3秒。F孔排废按通道局部空位连续跟进，同一次吸液批内相同体积小滴在出口按实际到达顺序分配到原有F孔；保持各孔数量/体积、6格独立运输及已有局部间隙、捕获切分等待和孔旁吸液收尾。</p>'
    page=page.replace('<table',note+'<table',1)
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    report=dict(status='passed',baseline_commit=BASE,script_sha256=hashlib.sha256(text.encode()).hexdigest(),
        modified_section_ids=sorted(TARGET),timings=timings,total_saved_seconds=sum(t['saved_seconds'] for t in timings),
        cases=proof,top_holds=tops,captured_cut_checks=cuts,actual_volumes_ratios_unchanged=True,
        receiver_counts_volumes_shapes_and_suction_unchanged=True,workbook_modified=False,GUI_check=False,physical_validation=False)
    path.write_text(text);(folder/'main.elab').write_text(mt);(folder/'手工加液运行.html').write_text(page)
    with (folder/'手工吸液顺序.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.DictWriter(fh,ah);w.writeheader();w.writerows(ar)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('TOTAL_SAVED',report['total_saved_seconds'],flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);update(ap.parse_args().folder)
