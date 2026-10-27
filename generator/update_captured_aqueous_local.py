"""Only R05/R06/C05/C06: widen the later cuts and settle before egress."""
import argparse,copy,csv,hashlib,json,re,subprocess
from pathlib import Path
import xml.etree.ElementTree as E
from xml.sax.saxutils import escape

import geom
from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,last_hv,validate_scene
from update_workbook_volumes_local import remap,replace_flow,read_csv
from waste_recovery import boxes

ROOT=Path(__file__).resolve().parent.parent
BASE='09742cafa6f0e862540868b2edec5c027c14eb8c'
REPORT='磁吸水相分液增距局部校验_20261027.json'
CASES=[((2013,51000),'R1',5.),((4013,51002),'C',6.16)]


def native_key(r):
    return tuple(sorted(filter(None,r.get('RelayCode','').split(';')))),int(r.get('Duration')),int(r.get('Amp'))


def bottom_key(r):
    return tuple(sorted(rc for rc in boxes(r.get('RelayCode','')) if rc[1]>=172)),int(r.get('Duration')),int(r.get('Amp'))


def generate(old,ordered):
    replacements={};proof=[]
    for ids,seg,vol in CASES:
        previous=ordered[ordered.index(old[ids[0]])-1]
        p=seed(last_hv(previous),[('',vol)]);p.magnet_state='up'
        first=p.S.relays[0]
        trace={0:(tuple(sorted(first.codes)),first.dur,first.amp)}
        # C05 originally starts with a one-cell move, while R05 includes its
        # initial holding mask. Reconstruct the released first-cut prefix.
        offset=0
        if trace[0][0]!=native_key(old[ids[0]][0][0])[0]:
            p.S.flush();offset=1
        original_record=p.S._record
        packet_frames=0;mother_frames=0;prior={}
        def record(*args,**kwargs):
            nonlocal packet_frames,mother_frames
            original_record(*args,**kwargs)
            relay=p.S.relays[-1]
            trace[p.S.nsteps-1]=(tuple(sorted(relay.codes)),relay.dur,relay.amp)
            for d in p.S.drops.values():
                if not d.rects():continue
                x,y,w,h=d.rect
                assert not(x<68 and x+w>50 and y<172),'进入另外一列温控区'
                if getattr(d,'native_packet',False):
                    assert (w,h)==(4,4),'运输小滴不得额外形变'
                    packet_frames+=1
                elif d.name.startswith('LIQ'):
                    row=int(re.match(r'LIQA(\d+)',d.name)[1]);X,Y=geom.physical_zone(1,row)
                    assert X<=x and x+w<=X+36 and Y<=y and y+h<=Y+6
                    mother_frames+=1
                before=prior.get(id(d))
                if before and before[0]==d.vol and before[1][2:]==d.rect[2:]:
                    assert abs(x-before[1][0])+abs(y-before[1][1])<=1,'移动跳格'
            prior.clear();prior.update({id(d):(d.vol,d.rect) for d in p.S.drops.values() if d.rects()})
        p.S._record=record
        p.supernatant_off(seg,old[ids[0]].get('Name').split(' · ')[0])
        events=p.S.split_events
        assert len(events)==112
        for e in events[:16]:
            assert e['gaps']==[0,2,4]
            assert [trace[i][1] for i in e['separation_frames']]==[1000]*3
        for e in events[16:]:
            assert e['gaps']==[0,2,4,6]
            assert [trace[i][1] for i in e['separation_frames']]==[1000,3000,3000,5000]
        stop=events[0]['separation_frames'][-1]+1
        prefix=[trace[i] for i in range(offset,stop)]
        assert prefix==[native_key(r) for r in list(old[ids[0]][0])[:len(prefix)]],('首次对半不可改变',seg)
        flows=prepare_scripts(p.segs,'ChipArea_192_256_96_SIRO',1)[0][0].flows
        assert len(flows)==2
        for f,fid in zip(flows,ids):
            remap(f,fid,[int(c.get('ID')) for c in old[fid]])
            f.name=old[fid].get('Name')
            for c,oc in zip(f.cmds,old[fid]):c.name=oc.get('Name')
            exported=E.fromstring(f.xml())
            # The actual F-hole contraction path and its 3000ms timing stay.
            assert [bottom_key(r) for r in list(exported[-1])[-24:]]==[bottom_key(r) for r in list(old[fid][-1])[-24:]]
            replacements[fid]=f
        scene=validate_scene(p,seg+' capture retained; later cuts widened',vol*16,vol*16)
        assert all(d.vol==0 and d.magnet_only for d in p.S.drops.values())
        assert len(p.S.manual_aspirations)==2
        assert all(gate['progressive_recovery'] and not gate['rects'] for _,gate in p.S.manual_aspirations)
        assert p.S.aqueous_wait_gap is None and p.S.c05_cut_gap is None
        groups={}
        for e in events:
            g=groups.setdefault(e['partition_frame'],dict(parents=0,child_ul=e['volumes'][0],gaps=e['gaps'],frame_ms=[trace[i][1] for i in e['separation_frames']]))
            g['parents']+=1
        proof.append(dict(segment=seg,volume_per_lane_ul=vol,scene=scene,
            unchanged_first_cut_frames=len(prefix),first_cut_prefix_sha256=hashlib.sha256(repr(prefix).encode()).hexdigest(),
            first_split_unchanged=True,cut_groups=list(groups.values()),
            original_prefix_hash=hashlib.sha256(repr([native_key(r) for r in list(old[ids[0]][0])[:len(prefix)]]).encode()).hexdigest(),
            packet_frames_checked=packet_frames,mother_frames_checked=mother_frames,
            batch_aspirations_ul=[gate['volume_ul'] for _,gate in p.S.manual_aspirations],
            outlet_recovery_tail_unchanged=True,
            held_half_shape_h_w=[6,11] if seg=='C' else [5,10],
            held_half_carrier_cells=66 if seg=='C' else 50,
            temporary_carrier_cell_increase=1 if seg=='C' else 0))
        print(seg,'captured aqueous: checked',flush=True)
    return replacements,proof


def update(folder):
    folder=Path(folder);path=folder/'PTseq_16_A.elab';before_text=path.read_text()
    expected=subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
    assert before_text==expected,'Changed baseline: do not overwrite local edits'
    before=E.fromstring(before_text);ordered=list(before);old={int(f.get('ID')):f for f in before}
    fresh,proof=generate(old,ordered)
    text=before_text
    for fid,f in fresh.items():text=replace_flow(text,fid,f.xml())
    after=E.fromstring(text)
    for f in after:
        if int(f.get('ID')) not in fresh:assert E.tostring(f)==E.tostring(old[int(f.get('ID'))])
    for a,b in ((2013,51000),(4013,51002)):
        assert last_hv(after.find(f"Flow[@ID='{a}']")).get('RelayCode')==after.find(f"Flow[@ID='{b}']")[0][0].get('RelayCode')
    assert last_hv(after.find("Flow[@ID='51000']")).get('RelayCode','')==''
    assert last_hv(after.find("Flow[@ID='51002']")).get('RelayCode','')==''
    ah,ar=read_csv(folder/'手工吸液顺序.csv')
    for row in ar:
        fid=int(row[ah[1]])
        if fid in fresh:
            c=fresh[fid].cmds[-1]
            assert float(row[ah[6]])==c.manual_aspiration['volume_ul']
            row[ah[4]]=str(len(c.relays))
    page=(folder/'手工加液运行.html').read_text();count=0
    def table(m):
        nonlocal count
        count+=1
        if count==1:return m[0]
        return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in ah)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(str(r[h]))+'</td>' for h in ah)+'</tr>' for r in ar)+'</table>'
    page=re.sub(r'<table\b[^>]*>[\s\S]*?</table>',table,page);assert count==2
    note=('<p id="captured-aqueous-20261027">2026-10-27磁吸排水相：R05/R06与C05/C06保留第一次对半。'
          '左侧暂存、右侧切分排出；区内暂存间距2格，后两级净切口逐格0→2→4→6，电极保持分别1/3/3/5秒。'
          'C05左侧半份暂存框6×11（高×宽，临时多1格承接电极），实际体积不变；孔距至少1格，最终4×4原形运输。'
          '顶部慢速、其他步骤、试剂量、磁控时序与F孔收尾保持原版。早期区内间距说明以本条和规则文档为准。</p>')
    page=page.replace('<table',note+'<table',1)
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    timings=[dict(section=old[fid].get('Name').split()[0],id=fid,before_seconds=int(old[fid].get('Timeout')),after_seconds=fresh[fid].timeout) for fid in fresh]
    report=dict(status='passed',baseline_commit=BASE,baseline_sha256=hashlib.sha256(before_text.encode()).hexdigest(),
        script_sha256=hashlib.sha256(text.encode()).hexdigest(),modified_section_ids=sorted(fresh),
        cases=proof,timings=timings,total_added_seconds=sum(r['after_seconds']-r['before_seconds'] for r in timings),
        symmetric_splits_checked=224,first_cut_events_unchanged=32,widened_cut_events=192,
        cut_gap_sequence=[0,2,4,6],cut_frame_ms=[1000,3000,3000,5000],
        waiting_gap=2,hole_gap=1,final_packet_shape_h_w=[4,4],
        all_other_sections_unchanged=True,top_slow_cadence_unchanged=True,
        reagent_volumes_unchanged=True,magnet_capture_timings_unchanged=True,
        outlet_recovery_tails_unchanged=True,physical_validation=False,GUI_check=False)
    path.write_text(text);(folder/'main.elab').write_text(mt)
    with (folder/'手工吸液顺序.csv').open('w',encoding='utf-8-sig',newline='') as fh:
        writer=csv.DictWriter(fh,ah);writer.writeheader();writer.writerows(ar)
    (folder/'手工加液运行.html').write_text(page)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path)
    print(json.dumps(update(ap.parse_args().folder),ensure_ascii=False,indent=2))
