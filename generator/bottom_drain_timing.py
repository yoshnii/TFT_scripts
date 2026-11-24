"""Replay all nine F-waste sections with an ideal drain at the channel's lower end."""
import argparse
import copy
import csv
import hashlib
import json
import shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from tempfile import TemporaryDirectory
import subprocess
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import geom
import rigid
from native_packets import bisect_barcode
from project_structure import prepare_scripts
from update_parallel_local import register
from update_parallel_mix_local import replace_flow
from update_transport_cleanup_local import last_hv, seed
from update_refill_local import FrameTrace
from update_ready_parallel_local import check_dwell
from waste_grid import TRANSPORT_Y

ROOT=Path(__file__).resolve().parents[1]
MAIN=ROOT/'projects/PTseq_SIRO16_A_10x'
TEST=ROOT/'projects/PTseq_SIRO16_A_10x_bottom_drain'
CASES={'A06':(1019,), 'R05':(2013,51000), 'R19':(2068,), 'B07':(50004,),
       'C05':(4013,51002), 'C15':(4048,), 'C18':(75020,)}
EXPECTED={1019:(32,24.),2013:(64,40.),51000:(64,40.),2068:(64,48.),50004:(16,9.6),
          4013:(64,49.28),51002:(64,49.28),4048:(64,48.),75020:(64,48.)}
SCRIPT='PTseq_16_A.elab'


class BottomSink:
    def __init__(self,endpoint):
        self.endpoint=endpoint
        self.receipts=[]
        self.sections=[]
        self.frames=[]

    def finish_path(self,scene,drop,channel_x):
        assert drop.rect[:2]==(channel_x,164) and drop.vol>0
        shape=drop.rect[2:]
        last_y=geom.GRID_H-shape[1] if self.endpoint=='edge' else TRANSPORT_Y
        yield from rigid.go(drop,[('y',last_y)])
        assert drop.rect==(channel_x,last_y,*shape)
        assert geom.emit(drop.rect) in scene.relays[-1].codes
        self.receipts.append(dict(packet=drop.name,key=id(drop),frame=scene.nsteps,rect=drop.rect,volume_ul=drop.vol))
        scene.remove(drop.name)

    def end_section(self,scene):
        assert not scene.waste_pool.items and self.receipts
        assert not any(getattr(d,'fixed_waste_receiver',False) for d in scene.drops.values())
        self.sections.append(self.receipts)
        self.receipts=[]
        self.frames.append(scene.flush())


def simulate(label,endpoint):
    ids=CASES[label]
    root=ET.parse(MAIN/SCRIPT).getroot()
    old={int(f.get('ID')):f for f in root}
    previous=list(root)[list(root).index(old[ids[0]])-1]
    initial=old[ids[0]][0][0] if label in ('A06','B07') else last_hv(previous)
    volumes={'A06':[('',2.)],'B07':[('',2.),('K',1.2)],'R05':[('',5.)],'C05':[('',6.16)]}.get(label,[('',3.)])
    p=seed(initial,volumes)
    if label=='C05' or (label in ('R19','C15','C18') and set(p.S.relays[0].codes)!=set(old[ids[0]][0][0].get('RelayCode').split(';'))):
        p.S.flush()
    sink=BottomSink(endpoint) if endpoint!='main' else None
    p.S.bottom_sink=sink
    trace=FrameTrace(p)
    title=old[ids[0]].get('Name')
    if label=='A06':
        p.split_waste('A',title,.5,'cDNA多余液体')
    elif label in ('R05','C05'):
        p.magnet_state='up'
        p.supernatant_off('R1' if label=='R05' else 'C',title)
    else:
        if label=='B07':
            bisect_barcode(p.S,p.ALL)
            first=p.cmd(old[ids[0]][0].get('Name'))
            items=[(ln,p.S.get('BKWA'+ln.id)) for ln in p.ALL]
        else:
            first=None
            items=[(ln,p.S.get(ln.name)) for ln in p.ALL]
        rigid.egress_pieces(p.S,items,p.blob,final=True,single_stop=label in ('C15','C18'))
        p.flow('B' if label=='B07' else 'R1' if label=='R19' else 'C',title,[first,p.cmd(old[ids[0]][-1].get('Name'))])
    dwell=check_dwell(p,trace)
    cuts=[(e['parent'],e['axis'],e['gaps'],e['volumes'],e['positions'],e.get('requested_frame_ms')) for e in p.S.split_events]
    signature=hashlib.sha256(json.dumps(sorted(cuts,key=lambda e:e[0])).encode()).hexdigest()
    if sink is None:
        flows=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
        assert len(flows)==len(ids)
        for f,fid in zip(flows,ids):
            if fid==1019:
                f.cmds[0].relays[0].dur=int(old[fid][0][0].get('Duration'))
                f.cmds[0].timeout=int(old[fid][0].get('Timeout'))
            assert ET.tostring(register(f,fid,old))==ET.tostring(old[fid]),('baseline mismatch',fid)
        return {},dict(cuts=signature,dwell=dwell,verified_native=True)
    assert len(sink.frames)==len(ids)==len(sink.sections) and not p.S.manual_aspirations
    # The outgoing rectangle must still be present at the last driven frame.
    # From the next frame on its mass belongs to the sink, exactly once.
    exits={};states=dict(zip(trace.steps,(state for _,state in trace.rows)))
    for batch in sink.sections:
        for e in batch:
            k,f=e['key'],e['frame']
            assert states[f-1][k][1:]==(e['rect'],e['volume_ul']) and k not in states[f]
            exits[f]=exits.get(f,0.)+e['volume_ul']
    removed=0.
    initial_mass=sum(v for _,v in volumes)*len(p.ALL)
    for frame,state in states.items():
        removed+=exits.get(frame,0.)
        assert abs(sum(v for name,rect,v in state.values())+removed-initial_mass)<1e-7
    output={};timings=[]
    for fid,relays,receipts in zip(ids,sink.frames,sink.sections):
        count,volume=EXPECTED[fid]
        assert len(receipts)==count and abs(sum(e['volume_ul'] for e in receipts)-volume)<1e-7
        assert len({e['packet'] for e in receipts})==count
        new=copy.deepcopy(old[fid])
        cmd=new[-1]
        reference=cmd[-1].get('RelayCode')
        assert set(relays[-1].codes)==set(filter(None,reference.split(';'))),('handoff',fid)
        del cmd[:]
        compressed=[]
        for relay in relays:
            if compressed and compressed[-1].codes==relay.codes and compressed[-1].amp==relay.amp:
                compressed[-1].dur+=relay.dur
            else:compressed.append(copy.copy(relay))
        milliseconds=sum(r.dur for r in compressed)
        assert milliseconds%1000==0
        for i,relay in enumerate(compressed):
            cmd.append(ET.fromstring(relay.xml(i+int(old[fid][-1][0].get('ItemID')))))
        cmd.set('Timeout',str(milliseconds//1000))
        cmd.set('Name','废液原形逐格下行至底边，逐滴撤电（理想排液测试）')
        base_name=old[fid].get('Name').split(' · F')[0].replace('、孔旁收尾后撤电','')
        new.set('Name',base_name+' · 底边逐滴排出（计时测试）')
        assert all(c.get('Component')=='HighVoltage' for c in new)
        new.set('Timeout',str(sum(int(c.get('Timeout')) for c in new)))
        output[fid]=new
        timings.append(dict(id=fid,section=new.get('Name').split()[0],packets=count,volume_ul=volume,
                            old_s=int(old[fid].get('Timeout')),new_s=int(new.get('Timeout')),
                            endpoint=receipts[0]['rect'],final_mask_matches=True))
    return output,dict(cuts=signature,dwell=dwell,timings=timings)


BASE='d20d41d1eebc349bef67bec993979004e4e4f8a9'


def run_case(label):
    _,baseline=simulate(label,'main')
    flows,edge=simulate(label,'edge')
    _,band=simulate(label,'band')
    assert baseline['cuts']==edge['cuts']==band['cuts']
    assert baseline['dwell']==edge['dwell']==band['dwell']
    for a,b in zip(edge['timings'],band['timings']):
        assert a['id']==b['id'] and a['packets']==b['packets']
        a['band_s']=b['new_s']
    return flows,edge['timings']


def instructions(folder,flow_names):
    tables=[]
    for filename in ('手工加液顺序.csv','手工吸液顺序.csv'):
        with (MAIN/filename).open(encoding='utf-8-sig',newline='') as source:
            rows=list(csv.reader(source))
        next_id=rows[0].index('下一个流程组编号')
        next_name=rows[0].index('下一个流程组名称')
        selected=[rows[0]]
        for row in rows[1:]:
            if filename=='手工吸液顺序.csv' and row[5].startswith('F'):
                assert int(row[1]) in EXPECTED
                continue
            if row[next_id]:
                row[next_name]=flow_names[int(row[next_id])]
            selected.append(row)
        if filename=='手工吸液顺序.csv':
            assert len(selected)==5 and all(r[5].startswith('S') for r in selected[1:])
        with (folder/filename).open('w',encoding='utf-8-sig',newline='') as dest:
            csv.writer(dest).writerows(selected)
        tables.append('<h2>'+filename[:-4]+'</h2><table border="1">'+''.join(
            '<tr>'+''.join('<td>'+escape(value)+'</td>' for value in row)+'</tr>' for row in selected)+'</table>')
    (folder/'手工加液运行.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8">'
        '<title>底边直接排液计时测试</title><h1>底边直接排液计时测试</h1>'
        '<p>模型假设每滴废液的前缘到达最后一行电极时，底边装置立即将其带离芯片。'
        '真实芯片排液能力未验证。下表列出原有的手工加液及四处产物回收。</p>'+''.join(tables)+'</html>')


def publish(folder,results):
    data=(MAIN/SCRIPT).read_bytes()
    assert data==subprocess.check_output(['git','show',BASE+':projects/'+MAIN.name+'/'+SCRIPT],cwd=ROOT)
    text=data.decode();src=ET.fromstring(text)
    changes={};rows=[]
    for flows,timings in results:
        assert not changes.keys() & flows.keys()
        changes.update(flows);rows.extend(timings)
    assert changes.keys()==EXPECTED.keys()
    # The following B08 still handles the retained sample and keeps all of its relays.
    b08=copy.deepcopy(src.find("Flow[@ID='51001']"))
    b08.set('Name',b08.get('Name').replace('吸液完成后的续行','底边排废后继续融合'))
    changes[51001]=b08
    layout=ET.parse(MAIN/(SCRIPT+'.layout'))
    for fid,flow in changes.items():
        text=replace_flow(text,fid,ET.tostring(flow,encoding='unicode').strip())
        drawing=layout.getroot().find(f"Flow[@ID='{fid}']")
        drawing.set('Name',flow.get('Name'))
        for cmd in flow:
            drawing.find(f"Command[@ID=\"{cmd.get('ID')}\"]").set('Name',cmd.get('Name'))
    new=ET.fromstring(text)
    assert [f.get('ID') for f in src]==[f.get('ID') for f in new]
    for a,b in zip(src,new):
        fid=int(a.get('ID'))
        if fid not in changes:assert ET.tostring(a)==ET.tostring(b)
        elif fid==51001:
            assert [ET.tostring(c) for c in a]==[ET.tostring(c) for c in b]
        else:
            assert len(a)==len(b) and all(ET.tostring(c)==ET.tostring(d) for c,d in zip(a[:-1],b[:-1]))
            assert {k:v for k,v in a[-1].attrib.items() if k not in ('Timeout','Name')}=={
                k:v for k,v in b[-1].attrib.items() if k not in ('Timeout','Name')}
            assert sum(int(r.get('Duration')) for r in b[-1])==1000*int(b[-1].get('Timeout'))
    total=lambda doc:sum(int(f.get('Timeout'))*int(f.get('Repeats')) for f in doc)
    saved=sum(t['old_s']-t['new_s'] for t in rows)
    assert total(src)-total(new)==saved
    assert sum(t['packets'] for t in rows)==496
    assert abs(sum(t['volume_ul'] for t in rows)-356.16)<1e-7
    (folder/SCRIPT).write_text(text)
    layout.write(folder/(SCRIPT+'.layout'),encoding='utf-8')
    main=ET.parse(MAIN/'main.elab')
    assert int(main.getroot()[0].get('Timeout'))==total(src)
    main.getroot()[0].set('Timeout',str(total(new)))
    main.write(folder/'main.elab',encoding='utf-8')
    project=ET.parse(MAIN/(MAIN.name+'.elabprj'))
    project.getroot().set('Target',TEST.name)
    project.getroot().set('Description','理想底边排液离线计时测试')
    project.write(folder/(TEST.name+'.elabprj'),encoding='utf-8',xml_declaration=True)
    for name in ('main.elab.layout',project.getroot().get('ChipModel')+'.csv'):
        shutil.copyfile(MAIN/name,folder/name)
    instructions(folder,{int(f.get('ID')):f.get('Name') for f in new})
    band_saved=sum(t['old_s']-t['band_s'] for t in rows)
    proof=dict(baseline=BASE,main_seconds=total(src),test_seconds=total(new),saved_seconds=saved,
               bottom_band_saved_seconds=band_saved,physical_bottom_last_electrode_y=geom.GRID_H-1,
               band_top_y=TRANSPORT_Y,packet_count=496,volume_ul=356.16,sections=rows,
               baseline_native_reproduced=True,identical_split_geometry_volume_and_dwell=True,
               wet_run=False,script_sha256=hashlib.sha256((folder/SCRIPT).read_bytes()).hexdigest())
    (folder/'底边排废计时对照.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results=list(pool.map(run_case,CASES))
    with TemporaryDirectory(dir=TEST.parent) as directory:
        staging=Path(directory)
        report=publish(staging,results)
        TEST.mkdir(exist_ok=True)
        for path in staging.iterdir():path.replace(TEST/path.name)
    print('Main / edge / saved / band saved (s):',report['main_seconds'],report['test_seconds'],
          report['saved_seconds'],report['bottom_band_saved_seconds'])
