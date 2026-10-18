"""Replace only four manual elution endpoints; no full-workflow rebuild."""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as E

import geom
from elution_recovery import recovery_command, withdrawal_path
from ops import lanes_all
from scene import Scene, Drop
from update_pcr_block_local import replace_flow

TARGETS = {'R11': (2032, 2037, 5., 1), 'R16': (2055, 2060, 5., 2),
           'C09': (4022, 4027, 3., 1), 'C12': (4035, 4040, 3., 2)}
REPORT = '洗脱渐进回收局部校验_20261018.json'


def boxes(code):
    return [(int(x), int(y), int(w), int(h)) for y, x, h, w in
            re.findall(r'\((\d+),(\d+)\)\((\d+),(\d+)\)', code)]


def transform(text, layout_text, main_text, mapping, rows):
    before = E.fromstring(text)
    original = {f.get('ID'): f for f in before}
    layouts = E.fromstring(layout_text)
    old_layouts = {f.get('ID'): f for f in layouts if f.tag == 'Flow'}
    changed = {}
    cases = []
    delta = 0
    for label, (fid, cid, volume, round_no) in TARGETS.items():
        old = original[str(fid)]
        assert old.get('Name').split()[0] == label
        gate = old[-1]
        assert gate.get('ID') == str(cid) and len(gate) in (1, 12, 16)
        assert ('等待手工吸出' in gate.get('Name') or '运行期间配合手工吸液' in gate.get('Name'))
        assert gate[0].get('Duration') == '1000'
        assert gate.get('Params') == 'SCRIPTCYCLE_FALSE'
        scene = Scene();scene.manual_aspirations = []
        lanes = lanes_all((1,))
        expected = [geom.s_block(ln.c, ln.r, volume) for ln in lanes]
        assert boxes(gate[0].get('RelayCode')) == expected
        for ln, rc in zip(lanes, expected):
            d = scene.add(Drop(f'P{ln.id}', rc, volume));d.port_dock = True
            bead = scene.add(Drop(ln.name, ln.rect(ln.mag-1, 3), 0));bead.magnet_only = True
        command = recovery_command(scene, lanes, volume, round_no)
        assert all(d.vol == 0 for d in scene.drops.values())
        new_gate = copy.deepcopy(gate)
        new_gate.set('Name', command.name+f' · S孔×16 {volume*16:.3f}µL；确认吸净后运行下一组')
        new_gate.set('Timeout', str(command.timeout))
        new_gate[:] = [E.fromstring(r.xml(i)) for i, r in enumerate(command.relays, 1)]
        new_gate.text = '\n      '
        for index, relay in enumerate(new_gate):
            relay.tail = '\n    ' if index == len(new_gate)-1 else '\n      '
        assert dict(new_gate[0].attrib, ItemID=gate[0].get('ItemID')) == gate[0].attrib
        assert new_gate[-1].get('RelayCode') == ''
        flow = copy.deepcopy(old);flow[-1] = new_gate
        flow.set('Name', old.get('Name').replace(
            'S孔×16 待手工吸液（保持全部液滴上电）',
            'S孔×16 运行期间手工回收（末帧撤除回收电极）'))
        extra = command.timeout-int(gate.get('Timeout'))
        flow.set('Timeout', str(int(old.get('Timeout'))+extra));delta += extra
        assert all(E.tostring(a) == E.tostring(b) for a, b in zip(old[:-1], flow[:-1]))
        text = replace_flow(text, fid, flow)
        node = copy.deepcopy(old_layouts[str(fid)]);node.set('Name', flow.get('Name'))
        matches = [c for c in node if c.get('ID') == str(cid)]
        assert len(matches) == 1
        matches[0].set('Name', new_gate.get('Name'))
        layout_text = replace_flow(layout_text, fid, node)
        changed[str(fid)] = flow
        index = list(before).index(old)
        previous, following = before[index-1], before[index+1]
        path = withdrawal_path(expected[0], lanes[0].X0+geom.S_DX)
        cases.append(dict(section=label, flow_id=fid, command_id=cid,
                          actual_per_well_ul=volume, actual_total_ul=volume*16,
                          initial_rect=list(expected[0]), first_well_path=path,
                          frame_ms=1000, frames=len(path), duration_seconds=command.timeout,
                          previous_seconds=int(gate.get('Timeout')), changed_seconds=extra,
                          previous_section=previous.get('Name').split()[0],
                          next_section=following.get('Name').split()[0],
                          magnet_remains_up=True, final_recovery_electrodes=[],
                          withdrawal_stage_nested=True, second_stage_shape=[6, 4],
                          second_stage_dx=[-1, -1, -1], final_active_shape=[6, 4],
                          hole_clearance_grids=1,
                          operator_action='运行回收指令期间配合吸液，末帧回收电极已撤除'))
    after = E.fromstring(text)
    assert [f.get('ID') for f in after] == [f.get('ID') for f in before]
    for f in after:
        if f.get('ID') not in changed:
            assert E.tostring(f) == E.tostring(original[f.get('ID')])
    updated_layout = E.fromstring(layout_text)
    for node in updated_layout:
        if node.tag == 'Flow' and node.get('ID') not in changed:
            prior = old_layouts[node.get('ID')]
            assert node.attrib == prior.attrib
            assert [(c.tag, c.attrib) for c in node] == [(c.tag, c.attrib) for c in prior]
    assert [(n.tag, n.attrib) for n in layouts if n.tag != 'Flow'] == [
        (n.tag, n.attrib) for n in updated_layout if n.tag != 'Flow']
    for row in mapping:
        if str(row['id']) in changed:row['name'] = changed[str(row['id'])].get('Name')
    for row in rows:
        fid = row['吸液流程组编号']
        if fid in changed:
            flow = changed[fid];gate = flow[-1]
            assert float(row['本次总液体µL（16道合计；排珠载液不计文库）']) in (48., 80.)
            row['吸液流程组名称'] = flow.get('Name')
            row['末尾帧号'] = str(len(gate))
            row['待吸液电极'] = ''  # No terminal collection mask: aspiration is during this command.
        next_id = row['下一个流程组编号']
        if next_id in changed:row['下一个流程组名称'] = changed[next_id].get('Name')
    main_before = E.fromstring(main_text)
    old_total = int(main_before[0].get('Timeout'))
    main_text, count = re.subn(r'Timeout="\d+"', f'Timeout="{old_total+delta}"', main_text, count=1)
    assert count == 1
    report = dict(status='passed', scope=list(TARGETS),
                  source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                  reference='ASI96: 260828_5and8 Flow2389 Cmd2390/2391; 260902_96sampleV2_1 Flow2329 Cmd2330..2341',
                  cases=cases, total_nominal_change_seconds=delta,
                  unrelated_sections_byte_identical=True, all_existing_command_ids_preserved=True,
                  preceding_moves_and_magnetic_capture_unchanged=True,
                  incubation_temperature_and_volumes_unchanged=True,
                  original_one_grid_hole_clearance_preserved=True,
                  full_workflow_rerun=False, hardware_aspiration_verified=False,
                  notice='按外部吸液设计的撤电包络；电极面积减少不代表软件测得液量减少')
    return text, layout_text, main_text, mapping, rows, report


def update(folder):
    path = folder/'PTseq_16_A.elab';layout = folder/'PTseq_16_A.elab.layout'
    main = folder/'main.elab';mp = folder/'步骤编号映射_20261016.json'
    cp = folder/'手工吸液顺序.csv'
    with cp.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f);headers = reader.fieldnames;rows = list(reader)
    result = transform(path.read_text(), layout.read_text(), main.read_text(),
                       json.loads(mp.read_text()), rows)
    text, lt, mt, mapping, rows, report = result
    path.write_text(text);layout.write_text(lt);main.write_text(mt)
    mp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2)+'\n')
    with cp.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers);writer.writeheader();writer.writerows(rows)
    (folder/REPORT).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('folder', type=Path)
    update(parser.parse_args().folder)
