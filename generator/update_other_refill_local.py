"""Only B07: pool one barcode-waste batch while the next is in transit."""
import argparse,csv,hashlib,json,re,subprocess
from pathlib import Path
import xml.etree.ElementTree as E
from xml.sax.saxutils import escape
import rigid
from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,last_hv,validate_scene
from update_parallel_local import register
from update_refill_local import FrameTrace
from update_workbook_volumes_local import read_csv

ROOT=Path(__file__).resolve().parent.parent
BASE='bad15f570c2a4473cee7e3a0753f10ba85ccf456'
REPORT='其他步骤跨批次补位局部校验_20261027.json'


def relay_key(r):return {k:v for k,v in r.attrib.items() if k!='ItemID'}


def build(root):
    old={int(f.get('ID')):f for f in root};raw=old[50004][0]
    p=seed(raw[0],[('',2.),('K',1.2)]);trace=FrameTrace(p)
    original=p.S._record;prior={};stats=dict(large_long_axis_moves=0,simultaneous_moves=0)
    def recorded(*args,**kwargs):
        original(*args,**kwargs);small_moved=large_moved=False
        for d in p.S.drops.values():
            before=prior.get(id(d))
            if before and before[0]==d.vol and before[1][2:]==d.rect[2:]:
                dx=d.rect[0]-before[1][0];dy=d.rect[1]-before[1][1]
                assert abs(dx)+abs(dy)<=1
                if dx or dy:
                    if d.rect[2]*d.rect[3]>16:
                        w,h=d.rect[2:]
                        assert w>=h and dy==0,('大矩形必须短边在前沿长轴运输',d.name,d.rect,dx,dy)
                        stats['large_long_axis_moves']+=1;large_moved=True
                    elif d.name.startswith('BKWA'):small_moved=True
            assert not(d.rect[0]<68 and d.rect[0]+d.rect[2]>50 and d.rect[1]<172)
        stats['simultaneous_moves']+=int(small_moved and large_moved)
        prior.clear();prior.update({id(d):(d.vol,d.rect) for d in p.S.drops.values()})
    p.S._record=recorded
    rigid.bisect_many(p.S,[p.S.get('K'+ln.id) for ln in p.ALL],names=[('K'+ln.id,'BKWA'+ln.id) for ln in p.ALL])
    c=p.cmd(raw.get('Name'))
    rigid.egress_pieces(p.S,[(ln,p.S.get('BKWA'+ln.id)) for ln in p.ALL],p.blob)
    p.flow('B',old[50004].get('Name'),[c,p.cmd(old[50004][-1].get('Name'))])
    fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
    assert len(fs)==1;f=fs[0];exported=register(f,50004,old)
    assert E.tostring(exported[0])==E.tostring(old[50004][0]),'Original binary split changed'
    assert [relay_key(r) for r in list(exported[-1])[-22:]]==[relay_key(r) for r in list(old[50004][-1])[-22:]]
    assert last_hv(exported).get('RelayCode')==last_hv(old[50004]).get('RelayCode')
    assert len(p.S.barcode_batch_events)==2 and all(e['actual_volume_ul']==4.8 and e['transport_shape']==[12,8] for e in p.S.barcode_batch_events)
    assert all(e['far_quarter_cells_moved_during_near_preparation']>0 for e in p.S.barcode_overlap_events)
    first=next(e for e in p.S.barcode_pipeline_events if e['kind']=='prepare_quarter')
    assert len(first['staged_tickets'])<16
    assert next(e for e in p.S.barcode_pipeline_events if e['kind']=='mouth_cleared')['remaining_drop_min_x']>=60
    assert stats['simultaneous_moves']>0
    assert not p.S.waste_pool.items
    proof=dict(scene=validate_scene(p,'B07 across-batch pooling',51.2,9.6),
        batches=p.S.barcode_batch_events,quarter_overlap=p.S.barcode_overlap_events,
        pipeline_events=p.S.barcode_pipeline_events,motion=stats,hold_trace=trace.proofs,
        original_split_unchanged=True,F1_last_22_frames_unchanged=True,
        batch_actual_ul=4.8,total_waste_ul=9.6,leaf_ul=.6,leaf_shape_w_h=[3,4],
        transport_shape_w_h=[12,8],final_pool_w_h=[12,16],reshape_hold_ms=4000,
        independent_transport_gap=6,bottom_staging_exception_gap=4,
        first_batch_preparation_before_all_16_arrive=True)
    return f,proof


def update(folder):
    folder=Path(folder);path=folder/'PTseq_16_A.elab';baseline=path.read_text()
    expected=subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
    assert baseline==expected,'Do not overwrite divergent native files'
    root=E.fromstring(baseline);old={int(f.get('ID')):f for f in root};f,proof=build(root)
    text,n=re.subn(r'<Flow\b(?=[^>]*\bID="50004")[^>]*>[\s\S]*?</Flow>',lambda _:f.xml().strip(),baseline,count=1);assert n==1
    after=E.fromstring(text)
    assert {int(e.get('ID')) for e in after if E.tostring(e)!=E.tostring(old[int(e.get('ID'))])}=={50004}
    exported=after.find("Flow[@ID='50004']")
    assert {k:v for k,v in exported.attrib.items() if k!='Timeout'}=={k:v for k,v in old[50004].attrib.items() if k!='Timeout'}
    for a,b in zip(old[50004],exported):
        assert {k:v for k,v in a.attrib.items() if k!='Timeout'}=={k:v for k,v in b.attrib.items() if k!='Timeout'}
    ah,ar=read_csv(folder/'手工吸液顺序.csv')
    for row in ar:
        if int(row[ah[1]])==50004:
            assert float(row[ah[6]])==9.6
            row[ah[4]]=str(len(f.cmds[-1].relays))
    page=(folder/'手工加液运行.html').read_text();count=0
    def table(m):
        nonlocal count
        count+=1
        if count==1:return m[0]
        return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in ah)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(str(r[h]))+'</td>' for h in ah)+'</tr>' for r in ar)+'</table>'
    page=re.sub(r'<table\b[^>]*>[\s\S]*?</table>',table,page);assert count==2
    note=('<p id="b07-refill-20261027">2026-10-27 B07跨批次补位：第一批汇集时第二批继续运输；'
          '孔旁准备区Y180，先到的四滴先汇集，再与后到的四滴接合。保持0.60µL/3×4小滴、每批4.80µL，'
          '12×8短边在前运输、F1最终12×16及原撤电吸液收尾。体积、运输6格和既有孔旁准备4格例外、4秒形变均不改。</p>')
    page=page.replace('<table',note+'<table',1)
    total=sum(int(e.get('Timeout'))*int(e.get('Repeats','1')) for e in after)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    saved=int(old[50004].get('Timeout'))-f.timeout;assert saved>0
    report=dict(status='passed',baseline_commit=BASE,baseline_sha256=hashlib.sha256(baseline.encode()).hexdigest(),
        script_sha256=hashlib.sha256(text.encode()).hexdigest(),modified_section_ids=[50004],section='B07',
        before_seconds=int(old[50004].get('Timeout')),after_seconds=f.timeout,total_saved_seconds=saved,
        cumulative_saved_since_5445be1_seconds=2341+saved,evidence=proof,
        scan=[dict(sections='A08/B03/B11/B15/C02',result='already prepared in one parallel cohort; no separate next family'),
              dict(sections='A06',result='no additional timing benefit'),
              dict(sections='R05/R06/R19/C15/C18',result='arrival-order slot trial slower; retained baseline'),
              dict(sections='C05/C06',result='earlier all-column cut/send trial congested outlet; retained baseline')],
        all_other_sections_unchanged=True,volumes_ratios_and_waits_unchanged=True,
        workbook_unchanged=True,thermal_magnet_manual_boundaries_unchanged=True,
        physical_validation=False,GUI_check=False)
    path.write_text(text);(folder/'main.elab').write_text(mt)
    with (folder/'手工吸液顺序.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        w=csv.DictWriter(fh,ah);w.writeheader();w.writerows(ar)
    (folder/'手工加液运行.html').write_text(page)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('B07',report['before_seconds'],'->',f.timeout,'SAVED',saved,flush=True)
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);update(ap.parse_args().folder)
