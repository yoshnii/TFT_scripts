"""Build the complete 16-sample project for four physical columns, starting from the released native command graph."""
import csv
import io
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

import geom
import ops
import rigid
import four_column as board
from native_packets import validate_symmetric_split
from project_structure import prepare_scripts
from six_column_schedule import transfer, waste, seconds
from update_six_column_local import BASE, FEEDS, retime
from update_top_mother_timing_local import CASES
from update_transport_cleanup_local import seed, last_hv
from waste_recovery import boxes

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'projects/PTseq_SIRO16_A_10x'
MODEL = board.MODEL
DEST = ROOT / 'projects/POCS_4col_16_10x'
SCRIPT = 'PTseq_16_A.elab'
BASELINE = '839e0f825e9a21fe04491be0675dd39ecf5c3899'
LEFT_SHIFT = board.COLUMNS[0]-10


def source_blob(rev, name):
    return subprocess.check_output(['git','show',f'{rev}:projects/{SOURCE.name}/{name}'],cwd=ROOT)


def shifted_str(code):
    rects=boxes(code)
    assert len(rects)==len(list(filter(None,code.split(';'))))
    return ';'.join(geom.emit((x+LEFT_SHIFT,y,w,h)) for x,y,w,h in rects)


def shifted_relay(r):
    r=deepcopy(r)
    r.set('RelayCode',shifted_str(r.get('RelayCode')))
    return r


def copy_controls(flow):
    flow=deepcopy(flow)
    for r in flow.iter('RelayItem'):
        # Only well-bound commands use this path. Boundary transport is regenerated.
        for x,y,w,h in boxes(r.get('RelayCode')):
            assert 6<=x and x+w<=50 and 24<=y<=169,(flow.get('Name'),x,y,w,h)
        r.set('RelayCode',shifted_str(r.get('RelayCode')))
    return flow


def export(p):
    return prepare_scripts(p.segs,MODEL,1)[0][0].flows


def install_frames(dst, src):
    for r in list(dst):dst.remove(r)
    for r in src:dst.append(deepcopy(r))
    dst.set('Timeout',src.get('Timeout'))


def use_native(template, generated):
    out=deepcopy(template)
    assert len(out)==len(generated.cmds)
    raw=ET.fromstring(generated.xml())
    for a,b in zip(out,raw):
        assert a.get('Component')==b.get('Component')=='HighVoltage'
        install_frames(a,b)
    out.set('Timeout',str(sum(seconds(c) for c in out)))
    return out


def layout_file(directory):
    with io.StringIO(source_blob(BASELINE,'ChipArea_192_256_96_SIRO.csv').decode('utf-8-sig')) as fi, (directory/(MODEL+'.csv')).open('w',newline='') as fo:
        reader=csv.DictReader(fi)
        writer=csv.DictWriter(fo,reader.fieldnames)
        writer.writeheader()
        for row in reader:
            kind=row['Type'];x=float(row['StartX']);y=float(row['StartY'])
            attached=kind in ('Rec','Magnet','Tec') or (kind=='ReagentWell' and 24<=y<=162 and any(x==10+40*c+d for c in range(6) for d in (12,22)))
            if attached:
                col=int((x-10)//40)
                if col>=4:continue
                row['StartX']=f'{x+board.COLUMNS[col]-(10+40*col):g}'
            writer.writerow(row)


def run():
    board.configure()
    original=ET.fromstring(source_blob(BASE,SCRIPT))
    release=ET.fromstring(source_blob(BASELINE,SCRIPT))
    baseline={f.get('Name').split()[0]:f for f in release}
    ref={f.get('Name').split()[0]:f for f in original}
    cases=[('G4',.4,1.6,1002,50000,'A')]+CASES
    by_id={int(f.get('ID')):k for k,f in ref.items()}
    fresh={};audit={}
    for reagent,vol,host,gid,did,segment in cases:
        gate,delivery=by_id[gid],by_id[did]
        p=seed(shifted_relay(ref[gate][0][0]),[('',host)])
        p.S.relays[0].dur=int(ref[gate][0][0].get('Duration'))
        if reagent=='AMP':
            for d in p.S.drops.values():d.a06_authorized_padding=True
        p.add_reagent(segment,gate,reagent,vol)
        fs=export(p)
        fresh[gate]=use_native(ref[gate],fs[0])
        f=fresh[delivery]=use_native(ref[delivery],fs[1])
        if reagent in ('G14','LIG'):
            expected=boxes(shifted_str(last_hv(ref[delivery]).get('RelayCode')))
            ops.move_rects(p.S,{ln.name:expected[i][0] for i,ln in enumerate(p.ALL)})
            tail=f[-1]
            for rel in p.S.flush():
                tail.append(ET.fromstring(rel.xml(len(tail)+1)))
            tail.set('Timeout',str(sum(int(r.get('Duration')) for r in tail)//1000))
            f.set('Timeout',tail.get('Timeout'))
        assert set(boxes(last_hv(f).get('RelayCode')))==set(boxes(shifted_str(last_hv(ref[delivery]).get('RelayCode')))),delivery
        leaves=p.S.native_leaf_events
        assert all(abs(sum(e['actual_ul'] for e in leaves if e['row']==ln.r)-vol)<1e-7 and abs(p.S.get(ln.name).vol-host-vol)<1e-7 for ln in p.ALL)
        ready=next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
        assert len(p.S.reaction_merge_events)==1 and p.S.reaction_merge_events[0]['contact_step']>ready
        for event in p.S.split_events:validate_symmetric_split(event)
        audit[delivery]=dict(packets=len(leaves),per_well_ul=vol,packet_w_h=leaves[0]['shape'],all_rows_ready=True)
        print(delivery,seconds(f),flush=True)
    for tag,second,vol,seg in [('R05','R06',5.,'R1'),('C05','C06',6.16,'C')]:
        p=seed(shifted_relay(last_hv(ref[tag[0]+'04'])),[('',vol)])
        if tag=='C05':p.S.flush()
        p.magnet_state='up';p.supernatant_off(seg,tag)
        for k,f in zip((tag,second),export(p),strict=True):fresh[k]=use_native(ref[k],f)
        assert all(d.vol==0 and d.magnet_only for d in p.S.drops.values())
        audit[tag+'+'+second]=audit_waste(p,16*vol)
    fresh['C17']=board.wash_channels(copy_controls(ref['C17']))
    for tag in ('A06','B07','R19','C15','C18'):
        f=ref[tag]
        if tag=='A06':
            p=seed(shifted_relay(f[0][0]),[('',2.)])
            p.S.relays[0].dur=int(f[0][0].get('Duration'))
            p.split_waste('A',tag,.5,'cDNA多余液体')
        elif tag=='B07':
            p=seed(shifted_relay(f[0][-1]),[('',2.),('K',.6),('BKWA',.6)])
            p.S.flush()
            rigid.egress_pieces(p.S,[(ln,p.S.get('BKWA'+ln.id)) for ln in p.ALL],p.blob)
            p.flow('B',tag,[p.cmd('F1')])
        else:
            prev=original[list(original).index(f)-1]
            initial=last_hv(fresh['C17']) if tag=='C18' else shifted_relay(last_hv(prev))
            p=seed(initial,[('',3.)])
            if set(p.S.relays[0].codes)!=set(shifted_str(f[0][0].get('RelayCode')).split(';')):p.S.flush()
            rigid.egress_pieces(p.S,[(ln,p.S.get(ln.name)) for ln in p.ALL],p.blob,final=True,single_stop=tag!='R19')
            p.flow(tag[0] if tag[0]=='C' else 'R1',tag,[p.cmd('F')])
        generated=export(p)
        assert len(generated)==1
        if tag=='B07':
            new=deepcopy(f); new[0][:]=list(copy_controls_command(f[0]))
            install_frames(new[-1],ET.fromstring(generated[0].xml())[-1])
            new.set('Timeout',str(sum(seconds(c) for c in new)))
        else:new=use_native(f,generated[0])
        fresh[tag]=new
        audit[tag]=audit_waste(p,24. if tag=='A06' else 9.6 if tag=='B07' else 48.)
    full=deepcopy(original)
    full.set('ChipsetType',MODEL)
    for i,k in enumerate(ref):full[i]=fresh[k] if k in fresh else copy_controls(ref[k])
    flows={f.get('Name').split()[0]:f for f in full}
    overlap={}
    for i,names in enumerate(FEEDS):
        overlap[names]=transfer(*(flows[n] for n in names.split()),78000+10*i)
    for a,b in [('R05','R06'),('C05','C06')]:overlap[a+' '+b]=waste(flows[a],flows[b])
    for f in full:
        for c in f:c.set('Name',c.get('Name').replace('A/C/E 列','A 列'))
    total=retime(full)
    with TemporaryDirectory(dir=ROOT/'projects') as tmp:
        folder=Path(tmp)
        layout_file(folder)
        project=ET.fromstring(source_blob(BASELINE,SOURCE.name+'.elabprj'))
        project.set('Target',DEST.name);project.set('ChipModel',MODEL)
        write_xml(folder/(DEST.name+'.elabprj'),project)
        main=ET.fromstring(source_blob(BASELINE,'main.elab'))
        main.set('ChipsetType',MODEL);main[0].set('Timeout',str(total))
        write_xml(folder/'main.elab',main)
        write_xml(folder/SCRIPT,full)
        for name in ('main.elab.layout',SCRIPT+'.layout'):
            data=source_blob(BASELINE,name)
            if name==SCRIPT+'.layout':
                layout=ET.fromstring(data)
                ui_flows={f.get('ID'):f for f in layout.findall('Flow')}
                assert set(ui_flows)=={f.get('ID') for f in full}
                for f in full:
                    ui=ui_flows[f.get('ID')]
                    assert ui.get('ID')==f.get('ID') and [c.get('ID') for c in ui]==[c.get('ID') for c in f]
                    ui.set('Name',f.get('Name'))
                    for a,b in zip(ui,f):a.set('Name',b.get('Name'))
                write_xml(folder/name,layout)
            else:(folder/name).write_bytes(data)
        manual_files(folder,full)
        checks=verify(folder,release,ET.parse(folder/SCRIPT).getroot())
        timing={k:dict(six=seconds(baseline[k]),four=seconds(flows[k])) for k in flows}
        report=dict(baseline=BASELINE,samples=16,volume_scale='10x',active_column=1,column_x=board.COLUMNS,
            full_s=total,baseline_s=retime(release),through_recovery_s=sum(seconds(f) for f in full[:list(full).index(flows['C12'])+1]),
            overlap_s=overlap,stages=timing,route_checks=audit,checks=checks,physical_run=False)
        from update_waste_inlet_local import INPUTS, transform, current_overview, historic_name
        revised=transform({key:(folder/key).read_bytes() for key in INPUTS},4,report)
        revised[historic_name(4)]=current_overview(4,report,revised,folder)
        from loading_sections import consolidate
        consolidate(revised,4,historic_name(4))
        from dual_outlet import update as update_outlet
        update_outlet(revised,historic_name(4))
        for key,data in revised.items():(folder/key).write_bytes(data)
        total=json.loads(revised[historic_name(4)])['full_s']
        DEST.mkdir(exist_ok=True)
        for file in sorted(folder.iterdir()):file.replace(DEST/file.name)
        print('Complete:',total,flush=True)


def copy_controls_command(cmd):
    dst=deepcopy(cmd)
    for r in dst:r.set('RelayCode',shifted_str(r.get('RelayCode')))
    return dst


def audit_waste(p,expected):
    actual=sum(info['volume_ul'] for _,info in p.S.manual_aspirations)
    assert abs(actual-expected)<1e-6,(actual,expected)
    for event in getattr(p.S,'split_events',[]):validate_symmetric_split(event)
    events=p.S.fixed_waste_events
    absorbed=[e for e in events if e['kind']=='absorb']
    assert abs(sum(e['packet_ul'] for e in absorbed)-expected)<1e-6
    return dict(waste_ul=actual,packets=len(absorbed),withdrawals=len(p.S.manual_aspirations))


def write_xml(path,element):
    ET.indent(element,space='  ')
    path.write_bytes(ET.tostring(element,encoding='utf-8',xml_declaration=path.suffix=='.elabprj')+b'\n')


def manual_files(folder,full):
    from html import escape
    by_id={f.get('ID'):f for f in full}
    tables=[]
    for filename in ('手工加液顺序.csv','手工吸液顺序.csv'):
        rows=list(csv.reader(io.StringIO(source_blob(BASELINE,filename).decode('utf-8-sig'))))
        for row in rows[1:]:
            flow=by_id[row[1]]
            assert row[3]==flow[-1].get('ID')
            row[2]=flow.get('Name')
            if filename.startswith('手工吸'):
                row[4]=str(len(flow[-1]))
                if row[7] and row[5].startswith('S'):row[7]=shifted_str(row[7])
            if row[-2]:row[-1]=by_id[row[-2]].get('Name')
        with (folder/filename).open('w',encoding='utf-8-sig',newline='') as stream:csv.writer(stream).writerows(rows)
        tables.append('<table>'+''.join('<tr>'+''.join(f'<{tag}>{escape(v)}</{tag}>' for v in row)+'</tr>'
            for i,row in enumerate(rows) for tag in ['th' if i==0 else 'td'])+'</table>')
    (folder/'手工加液运行.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>四列芯片手工操作</title>'
        '<style>body{font:16px/1.5 system-ui}table{border-collapse:collapse}td,th{padding:5px;border:1px solid #bbb}</style>'
        '<h1>四列芯片 · A列16样本 · 10×</h1><h2>手工加液</h2>'+tables[0]+'<h2>手工吸液</h2>'+tables[1]+'</html>\n')


def ingress_tracks(flow):
    previous=set(); dual_frames=0;used=set()
    for relay in flow.iter('RelayItem'):
        now=set(boxes(relay.get('RelayCode')))
        movers={(x,w) for x,y,w,h in now if 28<=y<=162 and x+w<=board.COLUMNS[0]
                and (x,y-1,w,h) in previous and (x,y,w,h) not in previous}
        dual_frames+=int(any(b>=a+w+6 for a,w in movers for b,_ in movers))
        used.update(x for x,_ in movers)
        previous=now
    return dict(descending_x=sorted(used),simultaneous_dual_descent_frames=dual_frames)


def verify(folder,release,full):
    from collections import Counter
    with (folder/(MODEL+'.csv')).open() as stream:rows=list(csv.DictReader(stream))
    counts=Counter(r['Type'] for r in rows)
    assert (counts['Rec'],counts['Tec'],counts['Magnet'],counts['ReagentWell'])==(64,4,64,152)
    def coords(row):return tuple(float(row[k]) for k in ('StartX','StartY','Width','Height'))
    wells={coords(r) for r in rows if r['Type']=='Rec'}
    assert wells=={(*geom.physical_zone(c,r),36,6) for c in range(1,5) for r in range(1,17)}
    holes=[coords(r) for r in rows if r['Type']=='ReagentWell']
    sample=set(geom.REACTION_WELLS);assert sample<=set(holes)
    assert [(f.get('ID'),f.get('Repeats')) for f in full]==[(f.get('ID'),f.get('Repeats')) for f in release]
    checked=set();count=0
    for f,base in zip(full,release,strict=True):
        step=f.get('Name').split()[0]
        for a,b in zip(f,base,strict=True):
            assert (a.get('ID'),a.get('Component'),a.get('PreTask'),a.get('Params'))==(b.get('ID'),b.get('Component'),b.get('PreTask'),b.get('Params'))
            if a.get('Component')!='HighVoltage' and not (a.get('Component')=='Delay' and step in ('B09','B16','C17')) or step=='A10':
                assert a.attrib==b.attrib
            if a.get('Component')!='HighVoltage':continue
            for r in a:
                count+=1
                rects=boxes(r.get('RelayCode',''));assert int(r.get('Duration'))>0
                occupancy={}
                for x,y,w,h in rects:
                    bitmask=((1<<w)-1)<<x
                    for line in range(y,y+h):
                        assert not occupancy.get(line,0)&bitmask,(step,r.get('ItemID'),line)
                        occupancy[line]=occupancy.get(line,0)|bitmask
                for rc in rects:
                    key=step,rc
                    if key in checked:continue
                    checked.add(key);x,y,w,h=rc
                    assert min(x,y)>=0 and w>0 and h>0 and x+w<=256 and y+h<=192
                    if geom.between_lanes(rc):
                        assert step in ('R11','R16','C09','C12') and r.get('RelayCode')==shifted_str(b[int(r.get('ItemID'))-1].get('RelayCode')),(step,rc)
                    assert not any(x<pos+36 and pos<x+w and y<172 and y+h>23 for pos in board.COLUMNS[1:]),(step,rc)
                    for hole in holes:
                        if geom.well_gap(rc,hole)>=1:continue
                        is_s=hole in sample and hole[0]==board.COLUMNS[0]+12
                        is_f=hole[1]==189.5
                        assert (is_s and step in ('R11','R16','C09','C12')) or (is_f and step in ('A06','B07','R05','R06','C05','C06','R19','C15','C18')),(step,rc,hole)
        for attr in ('UnBrokenBlockList','SyncEndingCmdList'):assert f.get(attr)==base.get(attr)
    read_main=ET.parse(folder/'main.elab').getroot()
    manifest=ET.parse(folder/(DEST.name+'.elabprj')).getroot()
    assert manifest.get('ChipModel')==full.get('ChipsetType')==read_main.get('ChipsetType')==MODEL
    assert [n.get('include') for n in manifest]==['main',full.get('Name')]
    assert read_main[0].get('Include')==SCRIPT and int(read_main[0].get('Timeout'))==retime(full)
    assert {coords(r)[:2] for r in rows if r['Type']=='Tec'}=={(x,23) for x in board.COLUMNS}
    magnets=[coords(r) for r in rows if r['Type']=='Magnet']
    assert {(round(x+w/2,3),round(y+h/2,3)) for x,y,w,h in magnets}=={(x+27,30+9*i) for x in board.COLUMNS for i in range(16)}
    rails={}
    for flow in full:
        step=flow.get('Name').split()[0]
        if step not in ('R02','A03','A08','B03','B11','B15','C02'):continue
        rails[step]=ingress_tracks(flow)
    assert rails['R02']['simultaneous_dual_descent_frames']>0
    suction={'R05','R06','C05','C06','A06','B07','C15','C18','R19'}
    def outlet_tail(flow):
        trace=[]
        for relay in flow[-1]:
            mask=frozenset(rc for rc in boxes(relay.get('RelayCode')) if rc[1]>=170)
            trace.extend([mask]*(int(relay.get('Duration'))//1000))
        return trace
    for f,b in zip(full,release,strict=True):
        if f.get('Name').split()[0] in suction:
            # Align on first shape that actually reaches a fixed F well,
            # after any optional full-pool hold; check to complete withdrawal.
            def suction_motion(flow):
                tr=outlet_tail(flow)
                start=next(i for i,m in enumerate(tr) if any(y+h>=190 for x,y,w,h in m))
                return tr[start:]
            assert suction_motion(f)==suction_motion(b),f.get('Name')
    return dict(ingress_tracks=rails,native_flows=len(full),native_relay_frames=count,reaction_zones=len(wells),holes=len(holes),
        layout_command_refs=True,real_time_instrument_measurement=False)


if __name__=='__main__':run()
