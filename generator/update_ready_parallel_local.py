"""Publish approved visual scheduling improvements on the 5724441 baseline."""
import argparse,csv,hashlib,json,re,subprocess
from pathlib import Path
import xml.etree.ElementTree as E
from xml.sax.saxutils import escape
import geom,rigid,ops
from native_packets import bisect_barcode
from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,last_hv,validate_scene
from update_parallel_local import register
from update_fixed_waste_local import FixedAudit,relay_key
from update_refill_local import FrameTrace
from update_workbook_volumes_local import read_csv

ROOT=Path(__file__).resolve().parent.parent
BASE='57244411878f67c288bfb59411e87becd7446b04'
REPORT='就绪补位并行局部校验_20261028.json'
WASTE={1019,2013,51000,2068,50004,4013,51002,4048,75020}
TARGET=WASTE|{1022,51001,50002}


def check_dwell(p,trace):
    rows=trace.rows;index={f:i for i,f in enumerate(trace.steps)};elapsed=[0]
    for relay,state in rows:elapsed.append(elapsed[-1]+relay.dur)
    count=0
    for event in p.S.split_events:
        frames=[index[f] for f in event['separation_frames']]
        requested=event.get('requested_frame_ms',[1000]*len(frames))
        for i,(a,b) in enumerate(zip(frames,frames[1:])):
            assert elapsed[b]-elapsed[a]>=requested[i],('split gap hold shortened',event['parent'],i)
        a=frames[-1];deadline=elapsed[a]+requested[-1]
        for key in event['_child_keys']:
            initial=rows[a][1][key][1:]
            next_change=next((i for i in range(a+1,len(rows)) if key not in rows[i][1] or rows[i][1][key][1:]!=initial),len(rows))
            assert elapsed[next_change]>=deadline,('final split hold shortened',event['parent'],key,elapsed[next_change],deadline)
        count+=1
    return dict(splits=count,per_child_gap_and_final_holds_passed=True)


def build(root):
    old={int(f.get('ID')):f for f in root};ordered=list(root);fresh={};cases=[];tops=[]
    def publish(p,flows,ids,audit,label,mass,waste,preserve_a06=False,allow_retained=False):
        for f,fid in zip(flows,ids):
            if preserve_a06:
                f.cmds[0].relays[0].dur=int(old[fid][0][0].get('Duration'))
                f.cmds[0].timeout=int(old[fid][0].get('Timeout'))
            e=register(f,fid,old)
            if fid in WASTE:
                if preserve_a06:assert E.tostring(e[0])==E.tostring(old[fid][0])
                n=22 if fid==50004 else 24
                before=list(old[fid][-1])[-n:];after=list(e[-1])[-n:]
                if not allow_retained:assert [relay_key(r) for r in after]==[relay_key(r) for r in before]
                else:
                    from waste_recovery import boxes
                    def bottom(r):return sorted(rc for rc in boxes(r.get('RelayCode','')) if rc[1]>=172),r.get('Duration'),r.get('Amp')
                    assert [bottom(r) for r in after]==[bottom(r) for r in before]
            fresh[fid]=f
            print(label,fid,int(old[fid].get('Timeout')),'->',f.timeout,flush=True)
        if audit:
            trace=audit.report()
            splits=[{k:v for k,v in e.items() if not k.startswith('_')} for e in p.S.split_events]
            cases.append(dict(case=label,ids=ids,scene=validate_scene(p,label,mass,waste),fixed=trace,
                splits=splits,cut_dwell=check_dwell(p,audit.trace),row_ready_events=getattr(p.S,'row_ready_events',[]),retained_ready_events=getattr(p.S,'retained_ready_events',[])))
    p=seed(old[1019][0][0],[('',2.)]);original_record=p.S._record;original_cmd=p.cmd;audit=FixedAudit(p,32.)
    p.split_waste('A',old[1019].get('Name').split(' · ')[0],.5,'cDNA多余液体')
    fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
    publish(p,fs,[1019],audit,'A06',32.,24.,True,True)
    p.S._record=original_record;p.cmd=original_cmd
    p.add_reagent('A',old[1022].get('Name').split(' · ')[0],'AMP',2.)
    fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
    assert len(fs)==3
    publish(p,[fs[1]],[1022],None,'A07',None,None)
    assert E.tostring(register(fs[2],50001,old))==E.tostring(old[50001]),'A08 must retain native contents'
    for ids,seg,vol in (((2013,51000),'R1',5.),((4013,51002),'C',6.16)):
        prev=ordered[ordered.index(old[ids[0]])-1];p=seed(last_hv(prev),[('',vol)]);p.magnet_state='up'
        if set(p.S.relays[0].codes)!=set(old[ids[0]][0][0].get('RelayCode').split(';')):p.S.flush()
        audit=FixedAudit(p,vol*16)
        p.supernatant_off(seg,old[ids[0]].get('Name').split(' · ')[0])
        fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
        publish(p,fs,list(ids),audit,'captured '+str(vol),vol*16,vol*16)
    p=seed(old[50004][0][0],[('',2.),('K',1.2)]);audit=FixedAudit(p,51.2)
    bisect_barcode(p.S,p.ALL);c=p.cmd(old[50004][0].get('Name'))
    rigid.egress_pieces(p.S,[(ln,p.S.get('BKWA'+ln.id)) for ln in p.ALL],p.blob)
    p.flow('B',old[50004].get('Name'),[c,p.cmd(old[50004][-1].get('Name'))])
    fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
    publish(p,fs,[50004],audit,'B07',51.2,9.6,allow_retained=True)
    ops.merge_all(p.S,p.ALL,'K');p.flow('B',old[51001].get('Name'),[p.cmd(old[51001][0].get('Name'))])
    fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
    publish(p,[fs[1]],[51001],None,'B08',None,None)
    assert relay_key(last_hv(register(fs[1],51001,old)))==relay_key(last_hv(old[51001]))
    for fid,label in ((2068,'R19'),(4048,'C15'),(75020,'C18')):
        p=seed(last_hv(ordered[ordered.index(old[fid])-1]),[('',3.)]);p.magnet_state='down'
        if set(p.S.relays[0].codes)!=set(old[fid][0][0].get('RelayCode').split(';')):p.S.flush()
        audit=FixedAudit(p,48.)
        rigid.egress_pieces(p.S,[(ln,p.S.get(ln.name)) for ln in p.ALL],p.blob,final=True,single_stop=label!='R19')
        p.flow('C' if label.startswith('C') else 'R1',old[fid].get('Name'),[p.cmd(old[fid][-1].get('Name'))])
        publish(p,prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows,[fid],audit,label,48.,48.)
    for reagent,vol,host,fid,did,seg in (('G4',.4,1.6,1002,50000,'A'),('G14',2.5,2.5,2000,50002,'R1')):
        p=seed(old[fid][0][0],[('',host)])
        if reagent=='G4':p.S.relays[0].dur=int(old[fid][0][0].get('Duration'))
        trace=FrameTrace(p)
        p.add_reagent(seg,old[fid].get('Name').split(' · ')[0],reagent,vol)
        fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
        assert E.tostring(register(fs[0],fid,old))==E.tostring(old[fid])
        new=register(fs[1],did,old)
        assert relay_key(last_hv(new))==relay_key(last_hv(old[did]))
        ready=next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
        assert all(e['contact_step']>ready for e in p.S.reaction_merge_events)
        assert all(abs(d.vol-host-vol)<1e-8 for d in p.S.drops.values())
        fresh[did]=fs[1]
        tops.append(dict(section_id=did,reagent=reagent,scene=validate_scene(p,reagent),
            added_ul=vol,final_ul=host+vol,leaves=p.S.native_leaf_events,
            splits=[{k:v for k,v in e.items() if not k.startswith('_')} for e in p.S.split_events],
            ready_cut_events=getattr(p.S,'ready_top_events',[]),hold_trace=trace.proofs,cut_dwell=check_dwell(p,trace),
            all_ready_step=ready,first_contact_step=min(e['contact_step'] for e in p.S.reaction_merge_events)))
        print(reagent,int(old[did].get('Timeout')),'->',fs[1].timeout,flush=True)
    return fresh,cases,tops


def update(folder):
    path=folder/'PTseq_16_A.elab';baseline=path.read_text()
    assert baseline==subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
    root=E.fromstring(baseline);old={int(f.get('ID')):f for f in root}
    fresh,cases,tops=build(root)
    fresh.pop(50000,None) # The reviewed A03 queue candidate produces no change on this baseline.
    assert set(fresh)==TARGET
    text=baseline;timings=[]
    for fid,f in fresh.items():
        register(f,fid,old) # Later prepare_scripts calls also renumber shared Flow objects.
        text,n=re.subn(rf'<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>',lambda _:f.xml().strip(),text,count=1);assert n==1
        timings.append(dict(id=fid,section=old[fid].get('Name').split()[0],before_seconds=int(old[fid].get('Timeout')),after_seconds=f.timeout,saved_seconds=int(old[fid].get('Timeout'))-f.timeout))
    after=E.fromstring(text)
    changed={int(f.get('ID')) for f in after if E.tostring(f)!=E.tostring(old[int(f.get('ID'))])}
    assert changed==TARGET,(changed,TARGET)
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
    page=(folder/'手工加液运行.html').read_text();count=0
    def table(m):
        nonlocal count
        count+=1
        if count==1:return m[0]
        return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in ah)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(str(row[h]))+'</td>' for h in ah)+'</tr>' for row in ar)+'</table>'
    page=re.sub(r'<table\b[^>]*>[\s\S]*?</table>',table,page);assert count==2
    note='<p id="ready-parallel-20261028">2026-10-28：R02后批母滴在右侧先对半，再与前批运输重叠准备；A03出队候选未产生原生变化，保留原样。磁吸排液及末段清理按本行空位准备后续切分，原形小滴仍按原列车间距入通道。A06/B07保留滴在排废期间预定位，不提前融合；全部反应区就绪后同步融合，原体积、孔旁吸液收尾、手工边界、慢速和保持不变。其余顶部到位即拆试算无提速，保留原同步排程。</p>'
    page=page.replace('<table',note+'<table',1)
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    report=dict(status='passed',baseline_commit=BASE,script_sha256=hashlib.sha256(text.encode()).hexdigest(),
        modified_section_ids=sorted(TARGET),timings=timings,total_saved_seconds=sum(t['saved_seconds'] for t in timings),cases=cases,tops=tops,
        actual_volumes_ratios_unchanged=True,receiver_counts_volumes_shapes_and_suction_unchanged=True,
        unchanged_top_schedules=['A03','A08','B03','B11','B15','C02'],
        rejected_full_ready_cut_trial_seconds={'A03':524,'A08':1616,'B03':659,'B11':1250,'B15':593,'C02':1448},
        workbook_modified=False,GUI_check=False,offline_visual_check=False,physical_validation=False)
    path.write_text(text);(folder/'main.elab').write_text(mt);(folder/'手工加液运行.html').write_text(page)
    with (folder/'手工吸液顺序.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.DictWriter(fh,ah);w.writeheader();w.writerows(ar)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('TOTAL_SAVED',report['total_saved_seconds'],flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);update(ap.parse_args().folder)
