"""Only R05: use both neighbouring channels for larger symmetric openings."""
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
ROOT=Path(__file__).resolve().parent.parent
BASE='bfbbd077a96b9a0dd0b46b6b2c1dfb42a50d038d'
REPORT='R05借用双通道分液局部校验_20261028.json'


def build(root):
    old={int(f.get('ID')):f for f in root};ordered=list(root)
    p=seed(last_hv(ordered[ordered.index(old[2013])-1]),[('',5.)]);p.magnet_state='up'
    audit=FixedAudit(p,80.);p.supernatant_off('R1',old[2013].get('Name').split(' · ')[0])
    flows=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
    assert len(flows)==2
    out=register(flows[0],2013,old);continuation=register(flows[1],51000,old)
    assert E.tostring(continuation)==E.tostring(old[51000]),'R06 content must stay identical'
    assert relay_key(last_hv(out))==relay_key(last_hv(old[2013]))
    assert [relay_key(r) for r in list(out[-1])[-24:]]==[relay_key(r) for r in list(old[2013][-1])[-24:]]
    assert [e['gap'] for e in p.S.r05_wide_events]==[24,16,16,24]
    fixed=audit.report();dwell=check_dwell(p,audit.trace)
    pending_shapes={}
    for row in range(1,17):
        name=f'LIQA{row}_1_0';seen=[]
        for relay,state in audit.trace.rows:
            for key,(n,rc,ul) in state.items():
                if n==name:
                    assert ul==1.25
                    shape=list(rc[2:])
                    if not seen or seen[-1]!=shape:seen.append(shape)
        assert seen==[[8,4],[6,5],[8,4]],(name,seen)
        pending_shapes[name]=seen
    # Real mass and per-hole counts are independently verified by FixedAudit.
    before=json.loads((ROOT/'projects/PTseq_SIRO16_A_10x/就绪补位并行局部校验_20261028.json').read_text())
    previous=next(c for c in before['cases'] if c['case']=='captured 5.0')['fixed']
    def totals(events):
        out={}
        for e in events:
            n,ul=out.get(e['port'],(0,0.));out[e['port']]=(n+1,round(ul+e['packet_ul'],8))
        return out
    assert totals(fixed['admission_events'])==totals(previous['admission_events'])
    report=dict(status='passed',baseline_commit=BASE,changed_sections=[2013],
        before_seconds=int(old[2013].get('Timeout')),after_seconds=flows[0].timeout,
        additional_seconds=flows[0].timeout-int(old[2013].get('Timeout')),
        gaps=[e['gap'] for e in p.S.r05_wide_events],opening_events=p.S.r05_wide_events,
        geometry=validate_scene(p,'R05 channel expansion; R06 preserved',80.,80.,r05_channel_events=True),
        extended_width_cells=44,extended_x_bounds=[6,50],original_well_x_bounds=[10,46],
        per_lane_ul=5.,original_waiting_local_gap=2,transport_gaps_unchanged=True,
        R04_native_identical=True,offline_visual_check=False,
        cut_dwell=dwell,fixed=fixed,
        temporary_waiting_carrier=dict(actual_ul=1.25,before_w_h=[8,4],waiting_w_h=[6,5],
            before_cells=32,waiting_cells=30,restore_w_h=[8,4],restored_cells=32,
            actual_liquid_cells=25,transport_w_h=[4,4],transport_ul=.625),
        splits=[{k:v for k,v in e.items() if not k.startswith('_')} for e in p.S.split_events],
        pending_carrier_shape_proofs=pending_shapes,R06_native_identical=True,suction_tail_identical=True,magnet_capture_unchanged=True,
        workbook_modified=False,GUI_check=False,physical_validation=False)
    return flows[0],report


def update(folder):
    path=folder/'PTseq_16_A.elab';text=path.read_text()
    assert text==subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
    root=E.fromstring(text);old={int(f.get('ID')):f for f in root};f,report=build(root)
    register(f,2013,old)
    text,n=re.subn(r'<Flow\b(?=[^>]*\bID="2013")[^>]*>[\s\S]*?</Flow>',lambda _:f.xml().strip(),text,count=1);assert n==1
    after=E.fromstring(text)
    assert {int(e.get('ID')) for e in after if E.tostring(e)!=E.tostring(old[int(e.get('ID'))])}=={2013}
    a,b=old[2013],after.find("Flow[@ID='2013']")
    assert {k:v for k,v in a.attrib.items() if k!='Timeout'}=={k:v for k,v in b.attrib.items() if k!='Timeout'}
    for x,y in zip(a,b):assert {k:v for k,v in x.attrib.items() if k!='Timeout'}=={k:v for k,v in y.attrib.items() if k!='Timeout'}
    headers,rows=read_csv(folder/'手工吸液顺序.csv')
    for row in rows:
        if int(row[headers[1]])==2013:
            assert float(row[headers[6]])==f.cmds[-1].manual_aspiration['volume_ul']
            row[headers[4]]=str(len(f.cmds[-1].relays))
    count=0
    def table(m):
        nonlocal count
        count+=1
        if count==1:return m[0]
        return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(str(row[h]))+'</td>' for h in headers)+'</tr>' for row in rows)+'</table>'
    page=re.sub(r'<table\b[^>]*>[\s\S]*?</table>',table,(folder/'手工加液运行.html').read_text());assert count==2
    note='<p id="r05-channels-20261028">R05临时借用左右各4格移液通道，横向准备范围X6–50；四级左右同步切口24/16/16/24格。先让位、再分液，孔距、2格暂存间隔、逐帧双侧外移和原保持不变；实际液量及原32→30→32格等待承接调整不变，最终4×4/0.625µL原形运输。前批运输清空后再借用通道；收尾前将2.50µL等待半滴从X6恢复X10，与原R06输入相同。R04/R06、F孔吸液收尾、布局和Excel不改。</p>'
    page=page.replace('<table',note+'<table',1)
    total=sum(int(e.get('Timeout'))*int(e.get('Repeats','1')) for e in after)
    main,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    report['script_sha256']=hashlib.sha256(text.encode()).hexdigest()
    path.write_text(text);(folder/'main.elab').write_text(main);(folder/'手工加液运行.html').write_text(page)
    with (folder/'手工吸液顺序.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        writer=csv.DictWriter(fh,headers);writer.writeheader();writer.writerows(rows)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('R05',report['before_seconds'],'->',report['after_seconds'],'gaps',report['gaps'],flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);update(ap.parse_args().folder)
