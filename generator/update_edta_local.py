"""EDTA 0.40µL and exact 0.4× second-purification beads; scoped native update.

cDNA, all other chemistry, chip orientation and manual aspiration tails stay
unchanged. Real volumes and carrier electrodes are accounted separately.
"""
import argparse, csv, hashlib, json, re, subprocess, tempfile
from pathlib import Path
import xml.etree.ElementTree as E
from xml.sax.saxutils import escape

import protocol
from project_structure import prepare_scripts
from manual_sections import write_aspiration_order
from update_transport_cleanup_local import seed, validate_scene, last_hv
from update_workbook_volumes_local import remap, replace_flow, read_csv
from update_cdna_2ul_local import table
from waste_recovery import boxes

IDS=[3041,50006,3045,4000,50007,4004,4009,4013,51002]
REPORT='EDTA四百纳升与纯化2局部校验_20261026.json'
BASE='c6ef71d2a385d3fed7bc1650822e7168f7aba04e'


def generate(old):
    p=seed(old[3041][0][0],[('',4.)])
    p.add_reagent('B','B14 加 EDTA 终止液 0.40µL','EDTA',.4)
    p.mix('B',old[3045].get('Name'),cycles=20,seconds=660)
    p.add_reagent('C','C01 加磁珠(0.4××4.40µL) 1.76µL','G16',1.76)
    p.bead_capture('C',old[4009].get('Name'),
                   mix_cycles=protocol.BEAD_BIND_MIX['C'],mix_span=19)
    p.supernatant_off('C','C05 液相移离磁珠 → 排废全部6.16µL（不保留计划液相）')
    flows=prepare_scripts(p.segs,'ChipArea_192_256_96_SIRO',1)[0][0].flows
    assert len(flows)==len(IDS)
    for f,fid in zip(flows,IDS):
        remap(f,fid,[int(c.get('ID')) for c in old[fid]])
        f.name=old[fid].get('Name').replace('0.20µL','0.40µL').replace('4.20µL','4.40µL').replace('1.70µL','1.76µL').replace('5.90µL','6.16µL')
    # Every actual binary child, not a frame's apparent area, must meet 0.40µL.
    events=p.S.split_events
    assert len(events)==157 and min(min(e['volumes']) for e in events)==.4
    assert all(len(e['volumes'])==2 and e['volumes'][0]==e['volumes'][1] for e in events)
    for reagent,volume,count in (('EDTA',.4,16),('G16',.88,32)):
        leaves=[e for e in p.S.native_leaf_events if e['reagent']==reagent]
        assert len(leaves)==count and all(e['actual_ul']==volume for e in leaves)
    assert len(p.S.manual_aspirations)==2
    for _,gate in p.S.manual_aspirations:
        assert gate['volume_ul']==49.28
        assert all(abs(v-12.32)<1e-8 for v in gate['port_volumes'].values())
        assert gate['progressive_recovery'] and not gate['rects']
    assert all(d.vol==0 and d.magnet_only for d in p.S.drops.values())
    scene=validate_scene(p,'B14–C06 EDTA and 0.4× beads',98.56,98.56)
    assert abs(1.76/4.4-.4)<1e-12
    return p,flows,scene


def update(folder):
    folder=Path(folder);path=folder/'PTseq_16_A.elab'
    original=path.read_text();before=E.fromstring(original)
    assert before.get('ChipsetType')=='ChipArea_192_256_96_SIRO'
    baseline=subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=Path(__file__).resolve().parent.parent).decode()
    old={int(f.get('ID')):f for f in E.fromstring(baseline)}
    assert any(v in old[3041].get('Name') for v in ('0.20µL','0.40µL')),'Expected EDTA receiving flow'
    p,flows,scene=generate(old);fresh={f.id:f for f in flows}
    text=original
    for f in flows:text=replace_flow(text,f.id,f.xml())
    after=E.fromstring(text);ordered=list(after)
    for f in after:
        fid=int(f.get('ID'))
        if fid not in fresh:assert E.tostring(f)==E.tostring(old[fid]),('unrelated flow changed',fid)
    assert old[3041][0][0].get('RelayCode')==after.find("Flow[@ID='3041']")[0][0].get('RelayCode')
    assert last_hv(after.find("Flow[@ID='51002']")).get('RelayCode','')==last_hv(old[51002]).get('RelayCode','')==''
    for a,b in zip(IDS,IDS[1:]):
        end=last_hv(after.find(f"Flow[@ID='{a}']")).get('RelayCode')
        start=after.find(f"Flow[@ID='{b}']")[0][0].get('RelayCode')
        if a in (3041,4000):assert end==start,('loading gate mask',a,b)
        elif end!=start:
            aa=sorted(boxes(end),key=lambda r:(r[1],r[0]));bb=sorted(boxes(start),key=lambda r:(r[1],r[0]))
            assert len(aa)==len(bb)==16
            assert all(x[2:]==y[2:] and abs(x[0]-y[0])<=1 and abs(x[1]-y[1])<=1 for x,y in zip(aa,bb)),('boundary single-cell continuation',a,b)
    ids=[n.get('ID') for f in after for n in (f,*f)]
    assert len(ids)==len(set(ids)) and len({f.get('Name').split()[0] for f in after})==len(after)
    for f in after:
        for c in f:
            assert c.get('PreTask','-1,UnTask').split(',')[0] in ['-1',*ids]
    for fid in (3045,4004):
        a=after.find(f"Flow[@ID='{fid}']")
        assert a.find("Command[@Component='Delay']").get('Timeout')==old[fid].find("Command[@Component='Delay']").get('Timeout')
        assert a.get('SyncEndingCmdList')==old[fid].get('SyncEndingCmdList')
    assert [c.get('Timeout') for c in after.find("Flow[@ID='4009']") if c.get('ID') in ('4010','4012')]==['180','120']

    lh,lr=read_csv(folder/'手工加液顺序.csv');ah,ar=read_csv(folder/'手工吸液顺序.csv')
    lr=[r for r in lr if int(r[lh[1]]) not in fresh]
    for f in flows:
        cmd=f.cmds[-1];info=getattr(cmd,'manual_load',None)
        if info:
            values=['PTseq_16_A.elab',f.id,f.name,cmd.id,info['hole'],info['reagent'],
                    info['volume_ul'],info['port_volumes'],info['lanes'],'1/1','','','']
            lr.append(dict(zip(lh,map(str,values))))
    with tempfile.TemporaryDirectory(prefix='tft-edta-orders-') as tmp:
        holder=type('NativeScript',(),{'name':'PTseq_16_A','flows':flows})()
        write_aspiration_order(tmp,[holder])
        _,asp=read_csv(Path(tmp)/'手工吸液顺序.csv')
    ar=[r for r in ar if int(r[ah[1]]) not in fresh]+asp
    positions={int(f.get('ID')):i for i,f in enumerate(after)}
    for headers,rows in ((lh,lr),(ah,ar)):
        rows.sort(key=lambda r:positions[int(r[headers[1]])])
        for row in rows:
            i=positions[int(row[headers[1]])];f=ordered[i];nxt=ordered[i+1] if i+1<len(ordered) else None
            row[headers[2]]=f.get('Name')
            row[headers[-3]]='PTseq_16_A.elab' if nxt is not None else ''
            row[headers[-2]]=str(nxt.get('ID')) if nxt is not None else ''
            row[headers[-1]]=nxt.get('Name') if nxt is not None else '工程结束'
    assert float(next(r for r in lr if r[lh[1]]=='3041')[lh[6]])==6.4
    assert float(next(r for r in lr if r[lh[1]]=='4000')[lh[6]])==28.16
    lt=(folder/'PTseq_16_A.elab.layout').read_text()
    for i,f in enumerate(after):
        fid=int(f.get('ID'))
        if fid in fresh:lt=replace_flow(lt,fid,fresh[fid].layout(i))
    layout=E.fromstring(lt)
    assert {f.get('ID') for f in layout if f.tag=='Flow'}=={f.get('ID') for f in after}
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{sum(int(f.get("Timeout"))*int(f.get("Repeats","1")) for f in after)}"',(folder/'main.elab').read_text(),count=1);assert n==1
    page=(folder/'手工加液运行.html').read_text()
    page=re.sub(r'<p>2026-10-26：A03及cDNA保持[\s\S]*?</p>','',page)
    tables=iter('<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr>'+table(rows,headers)+'</table>' for headers,rows in ((lh,lr),(ah,ar)))
    page,n=re.subn(r'<table\b[^>]*>[\s\S]*?</table>',lambda _:next(tables),page);assert n==2
    page=page.replace('EDTA为2×2/0.20µL','EDTA为2×4/0.40µL')
    page=page.replace('纯化2磁珠每道两滴3×6/0.85µL','纯化2磁珠每道两滴4×5/0.88µL')
    page=page.replace('纯化2实际5.90µL用120格承接，再二分成8滴0.7375µL/3×5','纯化2实际6.16µL用128格承接，逐级对半为8滴0.77µL/4×4')
    page=page.replace('<table','<p>2026-10-26：A03及cDNA保持1.60＋0.40µL；每道EDTA实际加量改为0.40µL，取消0.20µL分液。连接终止液4.40µL，磁珠严格0.4×＝1.76µL，总量6.16µL，32×4完整电极框承接。仅C05/C06区内切分和等待允许2格间距；6.16µL可跨中线但不出本well，孔距仍1格。每道8滴0.77µL原形排废，两批各49.28µL、每孔12.32µL。承接面积余量不增加实际试剂量；温控、混匀和孵育计时不变。旧日期说明为修订记录，以本段和下表为准。</p><table',1)
    mapping=json.loads((folder/'步骤编号映射_20261016.json').read_text())
    for row in mapping:
        if row['id'] in fresh:row.update(name=fresh[row['id']].name,previous=old[row['id']].get('Name'))
    report=dict(status='passed',scope='B14–B16 and C01–C06, adjacent B13/C07 unchanged',
        baseline_commit=BASE,baseline_sha256=hashlib.sha256(baseline.encode()).hexdigest(),script_sha256=hashlib.sha256(text.encode()).hexdigest(),
        edta_ul_per_lane=.4,terminated_ul_per_lane=4.4,beads_ul_per_lane=1.76,beads_ratio=.4,
        binding_ul_per_lane=6.16,edta_leaf_ul=.4,bead_leaf_ul=.88,waste_leaf_ul=.77,
        root_carrier_cells=128,actual_volume_cell_equivalent=123.2,root_shape=[32,4],
        waste_leaf_shape=[4,4],waste_batches=2,waste_ul_per_batch=49.28,waste_ul_per_port=12.32,
        scene=scene,c05_c06_only_local_wait_gap=2,minimum_hole_gap=1,transport_gap=6,
        cdna_and_A03_unchanged=True,all_other_flows_unchanged=True,
        mixing_timers_seconds=[660,288],binding_incubation_seconds=180,capture_seconds=120,
        chip_orientation_unchanged=True,physical_validation=False)
    path.write_text(text);(folder/'PTseq_16_A.elab.layout').write_text(lt);(folder/'main.elab').write_text(mt)
    for filename,headers,rows in (('手工加液顺序.csv',lh,lr),('手工吸液顺序.csv',ah,ar)):
        with (folder/filename).open('w',encoding='utf-8-sig',newline='') as fh:
            writer=csv.DictWriter(fh,headers);writer.writeheader();writer.writerows(rows)
    (folder/'手工加液运行.html').write_text(page)
    (folder/'步骤编号映射_20261016.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+'\n')
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    print(json.dumps(update(parser.parse_args().folder),ensure_ascii=False,indent=2))
