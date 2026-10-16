"""Only parallelize the two initial AMP mothers; verify remaining frames identical."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import protocol
from scene import Drop
from model import Script
from manual_sections import split_sections,align_high_voltage_timing


def update(folder):
    path=folder/'PTseq_16_A.elab';text=path.read_text();old=ET.fromstring(text)
    oldsha=hashlib.sha256(path.read_bytes()).hexdigest()
    byid={int(f.get('ID')):f for f in old}
    p=protocol.Protocol((1,))
    for ln in p.ALL:
        d=p.S.add(Drop(ln.name,(11,ln.Y0,3,4),.5))
        d.electrode_area=12;d.margin_budget=2;d.a06_authorized_padding=True
    p.S.step();p.S.flush()
    p.add_reagent('A','A06 加靶向扩增反应液 2.00µL(台面预混)','AMP',2.)
    script=Script('PTseq_16_A',p.segs['A'],'ChipArea_192_256_96_SIRO',1022)
    split_sections([script]);align_high_voltage_timing([script])
    receive,delivery=script.flows
    receive.name=byid[1022].get('Name')
    delivery.id=50001;delivery.name=byid[50001].get('Name')
    # A07 contains the prior manual-aspiration carry frame; do not regenerate it.
    old_gate=byid[1022][-1][-1]
    assert set(receive.cmds[-1].relays[-1].codes)==set(old_gate.get('RelayCode').split(';'))-{''}
    before=list(byid[50001][0]);after=delivery.cmds[0].relays
    assert delivery.cmds[0].id==1025 and len(after)==len(before)-1
    for i,relay in enumerate(after):
        original=before[0 if i==0 else i+1]
        assert set(relay.codes)==set(original.get('RelayCode').split(';'))-{''}
        assert relay.dur==int(original.get('Duration'))
        assert relay.amp==int(original.get('Amp'))
    assert after[1].dur>=4000
    assert '(7,38)(10,32)' in after[1].codes and '(7,92)(10,32)' in after[1].codes
    assert set(receive.cmds[-1].relays[-1].codes)==set(after[0].codes)
    assert sum(r.dur for r in after)==sum(int(r.get('Duration')) for r in before)-4000
    text,n=re.subn(r'  <Flow ID="50001"[\s\S]*?  </Flow>',lambda _:delivery.xml(),text,count=1)
    assert n==1
    new=ET.fromstring(text)
    for a,b in zip(old,new):
        if a.get('ID')!='50001':assert ET.tostring(a)==ET.tostring(b)
    assert after[-1].codes==byid[50001][0][-1].get('RelayCode').rstrip(';').split(';')
    main=folder/'main.elab'
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in new)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',main.read_text(),count=1)
    assert n==1
    path.write_text(text);main.write_text(mt)
    report=dict(scope='A08 initial two AMP mother reshapes only',passed=True,
        old_script_sha256=oldsha,script_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        parallel_mothers=2,shape_from=[20,16],shape_to=[32,10],hold_ms=4000,
        saved_ms=4000,remaining_frames_unchanged=True,A07_unchanged=True,A09_unchanged=True,
        actual_per_port_ul=16.,actual_per_well_AMP_ul=2.,full_workflow_rerun=False,visual_check=False)
    (folder/'A08并行形变局部校验_20261016.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    update(parser.parse_args().folder)
