"""Regenerate only A02 and its timing/operator metadata."""
import argparse
import csv
import hashlib
import io
import json
import re
import tempfile
from pathlib import Path
import xml.etree.ElementTree as E
from xml.sax.saxutils import escape

import protocol
from cdna_aliquot import load_and_aliquot
from project_structure import prepare_scripts
from update_workbook_volumes_local import replace_flow, remap
from update_transport_cleanup_local import validate_scene
from audit_binary_frames import audit

REPORT = 'A02整列并行局部校验.json'


def build(text):
    before = E.fromstring(text)
    old = before.find("Flow[@ID='70000']")
    assert old is not None and old.get('Name').startswith('A02 ')
    p = protocol.Protocol((1,))
    load_and_aliquot(p)
    scripts, _, _ = prepare_scripts(p.segs, before.get('ChipsetType'), 1)
    flow = scripts[0].flows[1]
    remap(flow, 70000, [70001])
    flow.name = old.get('Name')
    fresh = E.fromstring(flow.xml())
    # Preserve native IDs/dependencies and both electrical endpoints.
    assert {k:v for k,v in fresh.attrib.items() if k != 'Timeout'} == {
        k:v for k,v in old.attrib.items() if k != 'Timeout'}
    assert {k:v for k,v in fresh[0].attrib.items() if k not in ('Timeout','Name')} == {
        k:v for k,v in old[0].attrib.items() if k not in ('Timeout','Name')}
    for index in (0, -1):
        assert {k:v for k,v in fresh[0][index].attrib.items() if k != 'ItemID'} == {
            k:v for k,v in old[0][index].attrib.items() if k != 'ItemID'}
    common_tail = 0
    for a,b in zip(reversed(list(old[0])), reversed(list(fresh[0]))):
        aa = {k:v for k,v in a.attrib.items() if k != 'ItemID'}
        bb = {k:v for k,v in b.attrib.items() if k != 'ItemID'}
        if aa != bb:break
        common_tail += 1
    assert common_tail >= 21, 'Existing F-hole recovery tail must remain intact'
    scene = validate_scene(p, 'A02 whole-column waste', 25.6, 19.2)
    assert [e['count'] for e in p.S.waste_batch_events] == [16,16]
    assert [e['tile_shape'] for e in p.S.waste_batch_events] == [[4,4],[2,4]]
    assert [e['packets'] for e in p.S.batch_events] == [16,16]
    assert len(p.S.a02_exit_events) == 32
    assert [(e['shape'],e['actual_ul']) for e in p.S.a02_exit_events] == [([4,4],.8)]*16+[([2,4],.4)]*16
    assert not getattr(p.S,'waste_morph_events',[])
    overlapping=sum(e['first_batch_still_in_channel'] for e in p.S.a02_exit_events)
    assert overlapping>0, 'The next batch must enter before the first clears Y172'
    assert len(p.S.split_events) == 32
    assert max(e['frame'] for e in p.S.a02_exit_events[:16]) <= p.S.split_events[16]['partition_frame']
    assert all(d.vol == .4 and len(d.target_cells()) == 8 for d in p.S.drops.values())
    assert len(p.S.manual_aspirations) == 1
    assert p.S.manual_aspirations[0][1]['port_volumes'] == {'F3':9.6,'F1':9.6}
    new = replace_flow(text, 70000, flow.xml())
    after = E.fromstring(new)
    assert len(before) == len(after)
    for a,b in zip(before,after):
        if a.get('ID') != '70000':assert E.tostring(a) == E.tostring(b)
    # Audit A02 plus immediate input/output boundaries, not unrelated steps.
    context = E.Element('Script')
    i = list(after).index(after.find("Flow[@ID='70000']"))
    previous = after[i-1]
    context.append(E.fromstring(E.tostring(previous)))
    context.append(E.fromstring(E.tostring(fresh)))
    neighbor = E.SubElement(context,'Flow',dict(after[i+1].attrib))
    command = E.SubElement(neighbor,'Command',dict(after[i+1][0].attrib))
    command.append(E.fromstring(E.tostring(after[i+1][0][0])))
    with tempfile.TemporaryDirectory(prefix='tft-a02-') as tmp:
        path=Path(tmp)/'context.elab';E.ElementTree(context).write(path)
        result=audit(path)
    assert not result['non_equal_binary_transitions'], result
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    report=dict(status='passed',scope=['A02','A01 output','A03 input'],
        baseline_sha256=hashlib.sha256(text.encode()).hexdigest(),
        script_sha256=hashlib.sha256(new.encode()).hexdigest(),
        before_frames=len(old[0]),after_frames=len(fresh[0]),
        before_seconds=int(old.get('Timeout')),after_seconds=flow.timeout,
        saved_seconds=int(old.get('Timeout'))-flow.timeout,
        batch_drop_counts=[16,16],packet_areas_cells=[16,8],
        packet_shapes=[[4,4],[2,4]],packet_volumes_ul=[.8,.4],
        transport_reshapes=0,first_split_x=[17,25],second_split_x=[15,21],downstream_x=11,
        right_half_enters_channel_before_left_half_split=True,
        symmetric_left_right_splits=True,
        second_batch_packets_entering_before_first_clears=overlapping,
        outlet_slots_allocated_in_actual_arrival_order=True,
        retained_ul_per_well=.4,waste_ul_per_port={'F3':9.6,'F1':9.6},
        unchanged_recovery_suffix_frames=common_tail,scene=scene,
        exported_frame_audit=result,unrelated_flows_unchanged=True,
        physical_liquid_verified=False,GUI_checked=False)
    return new,total,report,len(fresh[0])


def update(folder):
    path=folder/'PTseq_16_A.elab'
    text=path.read_text();new,total,report,last_frame=build(text)
    main=folder/'main.elab'
    mt,count=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',main.read_text(),count=1)
    assert count==1
    asp=folder/'手工吸液顺序.csv'
    reader=csv.DictReader(io.StringIO(asp.read_text(encoding='utf-8-sig')))
    headers=reader.fieldnames;rows=list(reader)
    row=next(r for r in rows if r[headers[1]]=='70000')
    row[headers[4]]=str(last_frame)
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,headers)
    writer.writeheader();writer.writerows(rows)
    html=folder/'手工加液运行.html'
    page=html.read_text()
    pattern=r'(<tr><td>PTseq_16_A\.elab</td><td>70000</td><td>[^<]*</td><td>70001</td><td>)\d+(</td>)'
    page,count=re.subn(pattern,lambda m:m[1]+str(last_frame)+m[2],page)
    assert count==1
    layout=folder/'PTseq_16_A.elab.layout'
    command_name=E.fromstring(new).find("Flow[@ID='70000']/Command").get('Name')
    escaped=escape(command_name, {'"':'&quot;'})
    lt,count=re.subn(r'(<Command\b(?=[^>]*\bID="70001")[^>]*\bName=")[^"]*(")',
                    lambda m:m[1]+escaped+m[2],layout.read_text())
    assert count==1
    # Generated artifacts only; layout geometry and workbook are untouched.
    path.write_text(new);main.write_text(mt)
    layout.write_text(lt)
    asp.write_text(stream.getvalue(),encoding='utf-8-sig',newline='')
    html.write_text(page)
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path)
    update(ap.parse_args().folder)
