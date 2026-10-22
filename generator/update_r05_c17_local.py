"""Only R05 scheduling and C17's closed per-cycle channel wash."""
import argparse,copy,csv,hashlib,io,json,re
from pathlib import Path
import xml.etree.ElementTree as E
from xml.sax.saxutils import escape

from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,validate_scene,last_hv,codes
from update_workbook_volumes_local import remap
from update_symmetric_splits_local import audit_symmetric

REPORT='R05并行与C17循环清洗局部校验_20261022.json'


def operator_metadata(folder,last_frame):
    path=folder/'手工吸液顺序.csv'
    reader=csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig')))
    headers=reader.fieldnames;rows=list(reader)
    row=next(r for r in rows if r[headers[1]]=='2013')
    row[headers[4]]=str(last_frame)
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,headers)
    writer.writeheader();writer.writerows(rows)
    page=folder/'手工加液运行.html'
    pattern=r'(<tr><td>PTseq_16_A\.elab</td><td>2013</td><td>[^<]*</td><td>2014</td><td>)\d+(</td>)'
    html,n=re.subn(pattern,lambda m:m[1]+str(last_frame)+m[2],page.read_text())
    assert n==1
    return {path:stream.getvalue(),page:html}


def same_frames(a,b):
    return len(a)==len(b) and all(codes(x)==codes(y) and x.get('Duration')==y.get('Duration')
                                and x.get('Amp')==y.get('Amp') for x,y in zip(a,b))


def build(folder):
    path=folder/'PTseq_16_A.elab';text=path.read_text();before=E.fromstring(text)
    old={int(f.get('ID')):f for f in before}
    p=seed(last_hv(old[2009]),[('',5.)]);p.magnet_state='up'
    p.supernatant_off('R1',old[2013].get('Name').split(' · ')[0])
    scene=validate_scene(p,'R05 parallel with R06 retained',80.,80.)
    fs=prepare_scripts(p.segs,before.get('ChipsetType'),1)[0][0].flows
    assert len(fs)==2
    for f,fid,cid in zip(fs,(2013,51000),(2014,60000)):remap(f,fid,[cid])
    r05=E.fromstring(fs[0].xml());r06=E.fromstring(fs[1].xml())
    assert same_frames(r06[0],old[51000][0]),'R06 must not change'
    assert [i['volume_ul'] for _,i in p.S.manual_aspirations]==[40.,40.]
    assert all(i['port_volumes']=={f'F{k}':10. for k in range(1,5)} for _,i in p.S.manual_aspirations)

    q=seed(old[75010][0][0],[('',3.)])
    q.mix('C',old[75010].get('Name'),2,complete_rounds=2,cleanup_channels=True)
    wash=q.segs['C'][0];remap(wash,75010,[75011,75012,75013])
    c17=E.fromstring(wash.xml());loop=c17[1]
    assert loop.get('Params')=='SCRIPTCYCLE_TRUE' and codes(loop[0])==codes(loop[-1])
    assert int(c17[2].get('Timeout'))*1000==2*sum(int(r.get('Duration')) for r in loop)
    assert q.S.cleanup_sweep_events[-1]['every_cycle']
    replacements={2013:r05,75010:c17};new=text;changes=[]
    for fid,fresh in replacements.items():
        native=old[fid];node=copy.deepcopy(native)
        assert len(native)==len(fresh)
        if fid==2013:
            assert codes(native[0][0])==codes(fresh[0][0]) and codes(native[0][-1])==codes(fresh[0][-1])
        else:
            for index in (0,1):
                assert codes(native[index][0])==codes(fresh[index][0]) and codes(native[index][-1])==codes(fresh[index][-1])
            assert native.get('SyncEndingCmdList')==fresh.get('SyncEndingCmdList')
        for a,b in zip(node,fresh):
            assert a.get('ID')==b.get('ID') and a.get('PreTask')==b.get('PreTask')
            assert a.get('Component')==b.get('Component') and a.get('Params')==b.get('Params')
            a[:]=[copy.deepcopy(r) for r in b];a.set('Timeout',b.get('Timeout'))
            if fid==75010:a.set('Name',b.get('Name'))
        node.set('Timeout',fresh.get('Timeout'))
        pattern=rf'<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>'
        new,n=re.subn(pattern,lambda _:E.tostring(node,encoding='unicode').strip(),new,count=1);assert n==1
        changes.append(dict(section=native.get('Name').split()[0],id=fid,before_seconds=int(native.get('Timeout')),
                            after_seconds=int(node.get('Timeout')),saved_seconds=int(native.get('Timeout'))-int(node.get('Timeout'))))
    after=E.fromstring(new)
    for a,b in zip(before,after):
        if int(a.get('ID')) not in replacements:assert E.tostring(a)==E.tostring(b)
    audit=audit_symmetric(after,{2013});assert audit['symmetric_horizontal_splits']==64
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    layout=folder/'PTseq_16_A.elab.layout';lt=layout.read_text()
    for c in c17:
        name=escape(c.get('Name'),{'"':'&quot;'})
        lt,n=re.subn(rf'(<Command\b(?=[^>]*\bID="{c.get("ID")}")[^>]*\bName=")[^"]*(")',lambda m:m[1]+name+m[2],lt)
        assert n==1
    report=dict(status='passed',baseline_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(new.encode()).hexdigest(),scope=['R05','C17'],changes=changes,
        r05_scene=scene,r05_exported_frame_audit=audit,r06_unchanged=True,other_sections_unchanged=True,
        wash_loop_ms=sum(int(r.get('Duration')) for r in loop),wash_timer_seconds=int(c17[2].get('Timeout')),
        wash_planned_rounds=2,channel_coverage_every_cycle=True,extra_liquid_ul=0,
        GUI_checked=False,hardware_verified=False)
    files={path:new,folder/'main.elab':mt,layout:lt,folder/REPORT:json.dumps(report,ensure_ascii=False,indent=2)+'\n'}
    files.update(operator_metadata(folder,len(r05[0])))
    for name in ('A02整列并行局部校验.json','R02双孔并行局部校验_20261022.json'):
        fp=folder/name;obj=json.loads(fp.read_text());obj['script_sha256']=report['script_sha256']
        files[fp]=json.dumps(obj,ensure_ascii=False,indent=2)+'\n'
    gp=folder/'双侧同步分液局部校验_20261022.json';obj=json.loads(gp.read_text())
    assert obj['script_sha256'] in (report['baseline_sha256'],report['script_sha256'])
    obj['script_sha256']=report['script_sha256']
    obj['exported_frame_audit']['frames']+=sum(len(c) for f in after if int(f.get('ID')) in replacements for c in f)-sum(len(c) for f in before if int(f.get('ID')) in replacements for c in f)
    for row in obj['changes']:
        if row['id']==2013:row['after_seconds']=int(r05.get('Timeout'))
    for i,row in enumerate(obj['cases']):
        if row['case']=='5.0 aqueous waste':obj['cases'][i]=dict(scene,case=row['case'])
    obj['follow_up_R05_C17_report']=REPORT
    obj.pop('temperature_mix_timers_and_PCR_unchanged',None)
    obj['biochemical_mix_timers_temperature_and_PCR_unchanged']=True
    obj['final_bead_free_C17_wash_timer_exception_seconds']=int(c17[2].get('Timeout'))
    files[gp]=json.dumps(obj,ensure_ascii=False,indent=2)+'\n'
    return files,report


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--write',action='store_true')
    args=ap.parse_args();files,report=build(args.folder)
    if args.write:
        for path,text in files.items():path.write_text(text)
    print(json.dumps(report,ensure_ascii=False,indent=2))
