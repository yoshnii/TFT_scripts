"""Publish only approved R06/C05/C06 borrowed-channel split preparation."""
import argparse,csv,hashlib,json,re,subprocess
from pathlib import Path
from xml.sax.saxutils import escape
import xml.etree.ElementTree as E
from project_structure import prepare_scripts
from update_parallel_local import register
from update_transport_cleanup_local import seed,last_hv,validate_scene
from update_fixed_waste_local import FixedAudit,relay_key
from update_ready_parallel_local import check_dwell
from update_workbook_volumes_local import read_csv
from waste_recovery import boxes

ROOT=Path(__file__).resolve().parent.parent
BASE='e3962fd2eaae0294f977394e9aff968bbffda06d'
REPORT='R06_C05_C06双通道分液局部校验_20261029.json'
TARGET={51000,4013,51002}
GAPS={'R06':[28,26,36],'C05':[12,14,14,22],'C06':[28,26,36]}


def native_holds(flows,events):
    frames=[r for f in flows for r in E.fromstring(f.xml()).iter('RelayItem')]
    masks=[set(boxes(r.get('RelayCode',''))) for r in frames];count=0
    for e in events:
        requested=e.get('requested_frame_ms',[1000]*len(e['positions']))
        for guess,positions,hold in zip(e['separation_frames'],e['positions'],requested):
            wanted={tuple(rc) for rc in positions}
            matches=[i for i in range(max(0,guess-16),min(len(frames),guess+4)) if wanted<=masks[i]]
            assert matches,('原生导出未找到切分覆盖',e['parent'],guess,positions)
            i=min(matches,key=lambda i:abs(i-guess))
            assert int(frames[i].get('Duration'))>=hold,('原生切口保持不足',e['parent'],i,hold)
            count+=1
    return dict(exported_split_frames_checked=count,all_requested_holds_passed=True)


def build(root):
    ordered=list(root);old={int(f.get('ID')):f for f in root};fresh={};cases=[]
    for fid,other,seg,vol in ((2013,51000,'R1',5.),(4013,51002,'C',6.16)):
        prev=ordered[ordered.index(old[fid])-1]
        p=seed(last_hv(prev),[('',vol)]);p.magnet_state='up'
        if fid==4013:p.S.flush() # preserve the original C05 one-cell entry
        audit=FixedAudit(p,vol*16)
        p.supernatant_off(seg,old[fid].get('Name').split(' · ')[0])
        fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
        assert len(fs)==2
        for f,i in zip(fs,(fid,other)):
            e=register(f,i,old)
            assert relay_key(last_hv(e))==relay_key(last_hv(old[i]))
            tail=[relay_key(r) for r in list(e[-1])[-24:]];before=[relay_key(r) for r in list(old[i][-1])[-24:]]
            assert tail==before,('F孔收尾改变',i,[(j,a,b) for j,(a,b) in enumerate(zip(before,tail)) if a!=b][:1])
            if i==2013:assert E.tostring(e)==E.tostring(old[i]),'R05 must stay unchanged'
            else:fresh[i]=f
        events=p.S.captured_channel_events
        for stage in dict.fromkeys(e['stage'] for e in events):
            assert [e['gap'] for e in events if e['stage']==stage]==GAPS[stage]
        assert not getattr(p.S,'captured_channel_stage',None)
        assert all(not hasattr(d,'captured_prep_row') for d in p.S.drops.values())
        assert all(d.vol==0 and d.magnet_only for d in p.S.drops.values())
        fixed=audit.report()
        packet=vol/8
        assert fixed['admitted_packets']==128 and fixed['receiver_count']==8
        assert all(e['packet_ul']==packet and e['packet_rect'][2:]==[4,4] for e in fixed['admission_events'])
        totals={}
        for e in fixed['admission_events']:totals[e['port']]=round(totals.get(e['port'],0)+e['packet_ul'],8)
        assert totals=={i:round(vol*4,8) for i in range(1,5)}
        geometry=validate_scene(p,seg+' borrowed captured-water channels',vol*16,vol*16,
                                r05_channel_events=True,captured_channel_events=True)
        cases.append(dict(ids=[fid,other],per_lane_ul=vol,geometry=geometry,
            opening_events=events,splits=[{k:v for k,v in e.items() if not k.startswith('_')} for e in p.S.split_events],
            cut_dwell=check_dwell(p,audit.trace),native_holds=native_holds(fs,p.S.split_events),
            fixed=fixed,port_totals_ul=totals,magnet_capture_unchanged=True,
            prepared_leaves_return_to_original_doorway=True,transport_shape_w_h=[4,4],
            carrier_areas_unchanged=True,upstream_and_final_masks_unchanged=True,F_suction_tail_identical=True))
        print('CHECKED',seg,[(f.name.split()[0],f.timeout) for f in fs],flush=True)
    assert set(fresh)==TARGET
    return fresh,cases


def update(folder):
    path=folder/'PTseq_16_A.elab';text=path.read_text()
    assert text==subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
    root=E.fromstring(text);old={int(f.get('ID')):f for f in root}
    fresh,cases=build(root);timings=[]
    for fid,f in fresh.items():
        register(f,fid,old)
        text,n=re.subn(rf'<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>',lambda _:f.xml().strip(),text,count=1);assert n==1
        timings.append(dict(section=old[fid].get('Name').split()[0],id=fid,
            before_seconds=int(old[fid].get('Timeout')),after_seconds=f.timeout,
            additional_seconds=f.timeout-int(old[fid].get('Timeout'))))
    after=E.fromstring(text)
    assert {int(f.get('ID')) for f in after if E.tostring(f)!=E.tostring(old[int(f.get('ID'))])}==TARGET
    for fid in TARGET:
        a=old[fid];b=after.find(f"Flow[@ID='{fid}']")
        assert {k:v for k,v in a.attrib.items() if k!='Timeout'}=={k:v for k,v in b.attrib.items() if k!='Timeout'}
        assert len(a)==len(b)
        for x,y in zip(a,b):assert {k:v for k,v in x.attrib.items() if k!='Timeout'}=={k:v for k,v in y.attrib.items() if k!='Timeout'}
    headers,rows=read_csv(folder/'手工吸液顺序.csv')
    for row in rows:
        fid=int(row[headers[1]])
        if fid in fresh:
            assert float(row[headers[6]])==fresh[fid].cmds[-1].manual_aspiration['volume_ul']
            row[headers[4]]=str(len(fresh[fid].cmds[-1].relays))
    count=0
    def table(m):
        nonlocal count
        count+=1
        if count==1:return m[0]
        return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(str(row[h]))+'</td>' for h in headers)+'</tr>' for row in rows)+'</table>'
    page=re.sub(r'<table\b[^>]*>[\s\S]*?</table>',table,(folder/'手工加液运行.html').read_text());assert count==2
    note='<p id="captured-channels-20261029">R06/C05/C06借用左右各4格通道，准备范围X6–50。净切口分别28/26/36、12/14/14/22、28/26/36格；原实际液量、承接面积、形变4秒、切口2/4格保持3秒及终态5秒不变，C05首次大对半仍每帧1秒。先让前批通过再借道，拉开并保持后逐格归还通道、接原4×4运输与F孔收尾。R05及其他section、Excel和布局不改。</p>'
    page=page.replace('<table',note+'<table',1)
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    main,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    report=dict(status='passed',baseline_commit=BASE,changed_sections=sorted(TARGET),
        script_sha256=hashlib.sha256(text.encode()).hexdigest(),gaps=GAPS,timings=timings,
        total_added_seconds=sum(t['additional_seconds'] for t in timings),cases=cases,
        preparation_x_bounds=[6,50],original_well_x_bounds=[10,46],
        actual_volumes_ratios_carrier_areas_unchanged=True,transport_clearances_unchanged=True,
        R05_native_identical=True,all_other_sections_unchanged=True,workbook_modified=False,
        offline_visual_check=False,GUI_check=False,physical_validation=False)
    path.write_text(text);(folder/'main.elab').write_text(main);(folder/'手工加液运行.html').write_text(page)
    with (folder/'手工吸液顺序.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.DictWriter(fh,headers);w.writeheader();w.writerows(rows)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('TIMINGS',timings,'TOTAL_ADDED',report['total_added_seconds'],flush=True)
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);update(ap.parse_args().folder)
