"""A08 small aliquot approaches fixed reagent; A09 positioning starts there."""
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
    event=p.S.reaction_merge_events[-1]
    assert event['mode']=='small_aliquot_approaches_fixed_AMP'
    assert event['rows']==list(range(1,17))
    assert all(d.vol==2.5 and d.rect==(31,ln.Y0,10,5) for ln,d in
               ((ln,p.S.get(ln.name)) for ln in p.ALL))
    p.mix('A','A07 往复混匀')
    script=Script('PTseq_16_A',p.segs['A'],'ChipArea_192_256_96_SIRO',1022)
    split_sections([script]);align_high_voltage_timing([script])
    receive,delivery,mixing=script.flows
    delivery.id=50001;delivery.name=byid[50001].get('Name')
    mixing.name=byid[1026].get('Name')
    assert mixing.id==1026
    before=list(byid[50001][0]);after=delivery.cmds[0].relays
    assert len(after)==len(before)-2
    def same(a,b):
        return (set(a.codes)==set(b.get('RelayCode').split(';'))-{''}
                and a.dur==int(b.get('Duration')) and a.amp==int(b.get('Amp')))
    assert all(same(a,b) for a,b in zip(after[:-5],before[:-7]))
    # Last four approach frames move only the 0.5µL aliquots; 2µL stays x=31.
    for relay,x in zip(after[-5:-1],(42,41,40,39)):
        for ln in p.ALL:
            assert f'({ln.Y0},31)(5,8)' in relay.codes
            assert f'({ln.Y0},{x})(4,3)' in relay.codes
    assert all(f'({ln.Y0},31)(5,10)' in after[-1].codes for ln in p.ALL)
    assert after[-1].dur>=4000
    assert set(after[0].codes)==set(byid[1022][-1][-1].get('RelayCode').split(';'))-{''}
    old_position=list(byid[1026][0]);new_position=mixing.cmds[0].relays
    assert len(new_position)==len(old_position)-2
    assert all(same(a,b) for a,b in zip(new_position,old_position[2:]))
    for c,original in zip(mixing.cmds[1:],list(byid[1026])[1:]):
        assert ET.tostring(ET.fromstring(c.xml()))==ET.tostring(original).rstrip()
    for f in (delivery,mixing):
        text,n=re.subn(rf'  <Flow ID="{f.id}"[\s\S]*?  </Flow>',lambda _:f.xml(),text,count=1)
        assert n==1
    new=ET.fromstring(text)
    for a,b in zip(old,new):
        if a.get('ID') not in ('50001','1026'):assert ET.tostring(a)==ET.tostring(b)
    main=folder/'main.elab';total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in new)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',main.read_text(),count=1);assert n==1
    path.write_text(text);main.write_text(mt)
    report=dict(scope='A08 small-to-large fusion and A09 positioning only',passed=True,
        old_script_sha256=oldsha,script_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        fixed_AMP_ul=2.,moving_aliquot_ul=.5,actual_final_per_well_ul=2.5,
        AMP_rectangle=[31,28,8,5],fusion_rectangle=[31,28,10,5],
        all_16_wells_merge_together=True,no_post_merge_return=True,
        earlier_A08_frames_unchanged=True,A09_mix_cycle_and_timer_unchanged=True,
        A09_positioning_frames_removed=2,full_workflow_rerun=False,visual_check=False)
    (folder/'A08小滴靠拢局部校验_20261016.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    update(parser.parse_args().folder)
