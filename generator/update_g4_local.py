"""Regenerate only G4 receive/dispense; retain all unrelated released flows verbatim."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import protocol
from project_structure import prepare_scripts


def update(folder):
    path=folder/'PTseq_16_A.elab'
    before=path.read_text()
    old=ET.fromstring(before)
    previous=list(old)[0]
    following=list(old)[3]
    p=protocol.Protocol((1,))
    p.load_samples('A','A01 上样 1.60µL (S孔距一格承接)','S',1.60,'L','待测核酸')
    p.add_reagent('A','A02 加 G4 cDNA合成反应液 0.40µL','G4',.40)
    scripts,rows,_=prepare_scripts(p.segs,'ChipArea_192_256_96_SIRO',1)
    flows=scripts[0].flows
    assert [f.name.split()[0] for f in flows]==['A01','A02','A03']
    receive,dispense=flows[1:]
    assert ET.tostring(ET.fromstring(flows[0].xml()))==ET.tostring(previous).rstrip()
    ports=['(4,48)(6,12)','(4,102)(6,12)']
    gate=receive.cmds[-1].relays[-1]
    assert all(c in gate.codes for c in ports)
    assert set(gate.codes)==set(dispense.cmds[0].relays[0].codes)
    assert rows[1]['port_volumes']=={'E1':3.2,'E2':3.2}
    assert len(p.S.split_events)==14
    for event in p.S.split_events:
        assert len(event['volumes'])==2
        assert event['volumes'][0]==event['volumes'][1]
        dims=event['positions'][0][0][2:]
        expected={72:[6,6],40:[5,4],24:[3,4]}[event['area']]
        assert dims==expected,(event['area'],dims)
        assert all(0<=b-a<=1 for a,b in zip(event['gaps'],event['gaps'][1:]))
    assert all(d.vol==2. and d.rect==(36,28+9*i,8,5)
               for i,d in enumerate(p.S.drops.values()))
    old_dispense=next(f for f in old if f.get('Name').startswith('A03 '))
    assert set(dispense.cmds[-1].relays[-1].codes)==set(old_dispense[-1][-1].get('RelayCode').split(';'))-{''}
    for flow in (receive,dispense):
        pattern=rf'  <Flow ID="{flow.id}"[\s\S]*?  </Flow>'
        before,n=re.subn(pattern,lambda _:flow.xml(),before,count=1)
        assert n==1
    after=ET.fromstring(before)
    for a,b in zip(old,after):
        if a.get('ID') not in {str(receive.id),str(dispense.id)}:
            assert ET.tostring(a)==ET.tostring(b),'unrelated flow changed'
    assert ET.tostring(after[3])==ET.tostring(following)
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    main=folder/'main.elab'
    main_text=main.read_text()
    main_text,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',main_text,count=1)
    assert n==1
    path.write_text(before)
    main.write_text(main_text)
    report=dict(scope='A02/A03 and unchanged A01/A04 boundaries only',
        full_workflow_checked=False,visual_check=False,receiving_shape=[12,6],
        daughter_shapes=[[6,6],[5,4],[3,4]],actual_per_port_ul=3.2,
        mother_reshapes=[[6,6,10,4],[5,4,6,4]],mother_reshape_hold_ms=4000,
        electrode_extra_per_child=4,final_reaction_ul=2.0,final_reaction_cells=40,
        actual_per_well_ul=.4,receiving_electrodes_per_port=72,
        actual_cells_per_port=64,split_events_checked=14,
        unrelated_flows_unchanged=True,script_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (folder/'A02_A03局部校验_20261016.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('folder',type=Path)
    update(parser.parse_args().folder)
