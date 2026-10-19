"""Correct only A02 sample splitting and A03's receiving handoff."""
import argparse,csv,hashlib,json,re,tempfile
from pathlib import Path
import xml.etree.ElementTree as E
import protocol
from cdna_aliquot import load_and_aliquot
from project_structure import prepare_scripts
from update_workbook_volumes_local import remap,replace_flow
from audit_binary_frames import audit


def update(folder):
    path=folder/'PTseq_16_A.elab';before=path.read_text();old=E.fromstring(before)
    byid={int(f.get('ID')):f for f in old}
    p=protocol.Protocol((1,));load_and_aliquot(p)
    p.add_reagent('A','A03 G4 cDNA反应液0.10µL/道：母液1.60µL四级对半','G4',.1)
    scripts,_,_=prepare_scripts(p.segs,'ChipArea_192_256_96_SIRO',1)
    flows=scripts[0].flows
    selected=[flows[1],flows[2]]
    for flow,fid,cids in [(selected[0],70000,[70001]),(selected[1],1002,[1003,1004])]:
        remap(flow,fid,cids);flow.name=byid[fid].get('Name')
        for cmd,raw in zip(flow.cmds,byid[fid]):cmd.name=raw.get('Name')
    assert E.tostring(E.fromstring(selected[1].cmds[-1].xml())).strip()==E.tostring(byid[1002][-1]).strip()
    assert set(selected[1].cmds[-1].relays[-1].codes)==set(byid[50000][0][0].get('RelayCode').split(';'))
    assert set(selected[0].cmds[0].relays[0].codes)==set(byid[1000][-1][-1].get('RelayCode').split(';'))
    events=p.S.split_events[:48]
    assert len(events)==48 and all(e['volumes'][0]==e['volumes'][1] for e in events)
    assert all(all(0<=b-a<=1 for a,b in zip(e['gaps'],e['gaps'][1:])) for e in events)
    assert sum(info['volume_ul'] for _,info in p.S.manual_aspirations)==19.2
    # Independently audit only the two changed native sections and their boundaries.
    context=E.Element('Script')
    for flow in selected:context.append(E.fromstring(flow.xml()))
    with tempfile.TemporaryDirectory(prefix='tft-sample-bounds-') as tmp:
        file=Path(tmp)/'scope.elab';E.ElementTree(context).write(file)
        result=audit(file)
    assert not result['non_equal_binary_transitions'],result
    text=before
    for flow in selected:text=replace_flow(text,flow.id,flow.xml())
    after=E.fromstring(text)
    for a,b in zip(old,after):
        if int(a.get('ID')) not in (70000,1002):assert E.tostring(a).strip()==E.tostring(b).strip()
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    main=folder/'main.elab';mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',main.read_text(),count=1);assert n==1
    order=folder/'手工吸液顺序.csv'
    with order.open(encoding='utf-8-sig',newline='') as fh:
        reader=csv.DictReader(fh);headers=reader.fieldnames;rows=list(reader)
    gate=selected[0].cmds[-1]
    for row in rows:
        if row['吸液流程组编号']=='70000':row['末尾帧号']=str(len(gate.relays))
    proof=dict(status='passed',changed_sections=['A02','A03'],
        baseline_script_sha256=hashlib.sha256(before.encode()).hexdigest(),
        script_sha256=hashlib.sha256(text.encode()).hexdigest(),
        boundary_rule='sample splitting/staging entirely inside its own 36x6 reaction well; intentional waste transport uses the designated waste channel',
        first_column_bounds_x=[10,46],quarter_final_x=[11,17,23,29],
        sample_mother_ul=1.6,sample_retained_ul=.4,sample_waste_ul_per_lane=1.2,
        equal_split_events=48,exported_frame_audit=result,other_native_sections_unchanged=True,
        G4_receiving_frame_unchanged=True,hardware_verified=False,vm_verified=False)
    path.write_text(text);main.write_text(mt)
    with order.open('w',encoding='utf-8-sig',newline='') as fh:
        writer=csv.DictWriter(fh,headers);writer.writeheader();writer.writerows(rows)
    (folder/'取样区内劈裂局部校验_20261019.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path);update(parser.parse_args().folder)
