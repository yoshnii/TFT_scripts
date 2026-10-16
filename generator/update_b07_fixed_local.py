"""Rebuild B07 waste only; preserve its split and every other section."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET
import contour
import geom
import protocol
import rigid
from scene import Drop
from model import Script, Cmd
from manual_sections import split_sections, align_high_voltage_timing
from audit_binary_frames import PATTERN, audit
from audit_bottom_motion import audit as audit_bottom


def boxes(relay):
    return [tuple(map(int, (x, y, w, h)))
            for y, x, h, w in PATTERN.findall(relay.get('RelayCode', ''))]


def update(folder):
    path = folder / 'PTseq_16_A.elab'
    text = path.read_text()
    before = ET.fromstring(text)
    flow = next(f for f in before if f.get('Name').startswith('B07 '))
    p = protocol.Protocol((1,))
    items = []
    fixed = {}
    for ln in p.ALL:
        rects = sorted((r for r in boxes(flow[0][-1]) if r[1] == ln.Y0))
        assert len(rects) == 3
        for rc, name, vol in zip(rects, (ln.name, f'K{ln.id}', f'BKWA{ln.id}'), (2., .6, .6)):
            d = p.S.add(Drop(name, rc, vol))
            d.electrode_area = rc[2] * rc[3]
            assert d.electrode_area == geom.cells(vol)
            if name.startswith('BKWA'):
                items.append((ln, d))
            else:
                fixed[name] = d.target_cells()
    p.S.step()
    p.S.flush()
    original = p.S._record
    stats = dict(frames=0, native_packet_moves=0, stationary_collection_checks=0,
                 long_axis_only_moves=0,rectangle_frames=0)
    previous = {}
    previous_objects = {}

    def record(*args, **kwargs):
        original(*args, **kwargs)
        stats['frames'] += 1
        for name, cells in fixed.items():
            assert p.S.get(name).target_cells() == cells
        for d in p.S.drops.values():
            assert not (d.rect[0] < 68 and d.rect[0] + d.rect[2] > 50 and d.rect[1] < 172)
            assert len(d.target_cells())==d.rect[2]*d.rect[3]
            stats['rectangle_frames']+=1
            old=previous_objects.get(id(d))
            if old and old[0] is d and old[1]==d.vol and old[2][2:]==d.rect[2:]:
                dx,dy=d.rect[0]-old[2][0],d.rect[1]-old[2][1]
                assert abs(dx)+abs(dy)<=1,'跳格移动'
                if (dx or dy) and d.rect[2]*d.rect[3]>16:
                    w,h=d.rect[2:]
                    assert not (w>h and dy or h>w and dx),'长边整面迎向移动'
                    stats['long_axis_only_moves']+=1
            elif old and old[0] is d and old[1]==d.vol and old[2]!=d.rect:
                duration=args[0] if args else kwargs.get('dur',geom.STEP_MS)
                assert duration>=4000,'形变未保持4秒'
            previous_objects[id(d)]=(d,d.vol,d.rect)
            if getattr(d, 'native_packet', False):
                assert d.rect[2:] == (3, 4) and abs(d.vol - .6) < 1e-8
                old = previous.get(d.name, d.rect)
                distance = abs(old[0] - d.rect[0]) + abs(old[1] - d.rect[1])
                assert distance <= 1
                stats['native_packet_moves'] += distance
                previous[d.name] = d.rect
            if d.name == 'BLOB1':
                stats['stationary_collection_checks'] += 1
        assert abs(sum(d.vol for d in p.S.drops.values()) +
                   sum(info['volume_ul'] for r, info in p.S.manual_aspirations) - 51.2) < 1e-8
    p.S._record = record
    rigid.egress_pieces(p.S, items, p.blob)
    command = p.cmd(flow[-1].get('Name'))
    dummy = Cmd('original split', 'HighVoltage', '', 1)
    p.flow('B', flow.get('Name'), [dummy, command])
    script = Script('PTseq_16_A', p.segs['B'], 'ChipArea_192_256_96_SIRO', 1)
    split_sections([script])
    align_high_voltage_timing([script])
    assert len(script.flows) == 1 and len(script.flows[0].cmds) == 2
    command = script.flows[0].cmds[-1]
    command.id = int(flow[-1].get('ID'))
    command.pre = (dummy, 'FS')
    dummy.id = int(flow[0].get('ID'))
    raw = ET.fromstring(command.xml())
    # Retain original command label and all scheduling attributes except duration.
    for key, value in flow[-1].attrib.items():
        if key != 'Timeout':
            raw.set(key, value)
    old_flow_timeout = int(flow.get('Timeout'))
    flow.remove(flow[-1])
    flow.append(raw)
    flow.set('Timeout', str(int(flow[0].get('Timeout')) + int(raw.get('Timeout'))))
    start = text.index('  <Flow ID="' + flow.get('ID') + '"')
    end = text.index('  </Flow>', start) + len('  </Flow>')
    # Replace only command 3020 and its parent's timeout, keeping the split verbatim.
    fragment = text[start:end]
    fragment, count = re.subn(r'(<Flow[^>]*Timeout=")\d+',
        lambda m: m[1] + flow.get('Timeout'), fragment, count=1)
    assert count == 1
    fragment, count = re.subn(r'    <Command ID="3020"[\s\S]*?</Command>',
        lambda m: '    ' + command.xml(), fragment, count=1)
    assert count == 1
    text = text[:start] + fragment + text[end:]
    after = ET.fromstring(text)
    original_root = ET.fromstring(path.read_text())
    for a, b in zip(original_root, after):
        if a.get('Name').startswith('B07 '):
            assert ET.tostring(a[0]) == ET.tostring(b[0])
        else:
            assert ET.tostring(a) == ET.tostring(b), a.get('Name')
    motion = audit_bottom(after, {'B07'}, allow_long_axis_labels={'B07'})
    assert not motion['violations'], motion
    context = ET.Element('Script')
    flows = list(after)
    i = next(i for i, f in enumerate(flows) if f.get('Name').startswith('B07 '))
    context.append(ET.fromstring(ET.tostring(flows[i])))
    for j, last in ((i-1, True), (i+1, False)):
        f = ET.SubElement(context, 'Flow', Name=flows[j].get('Name'))
        cmds = [c for c in flows[j] if c.get('Component') == 'HighVoltage']
        c = ET.SubElement(f, 'Command', Component='HighVoltage', ID=cmds[-1 if last else 0].get('ID'))
        c.append(ET.fromstring(ET.tostring(cmds[-1 if last else 0][-1 if last else 0])))
    with tempfile.TemporaryDirectory(prefix='tft-b07-') as temp:
        check = Path(temp) / 'context.elab'
        ET.ElementTree(context).write(check, encoding='utf-8')
        binary = audit(check)
    assert not binary['non_equal_binary_transitions'], binary
    gate = command.manual_aspiration
    assert gate['rects'] == [[48, 172, 12, 16]] and gate['volume_ul'] == 9.6
    assert len(p.S.barcode_batch_events) == 2
    assert not getattr(p.S, 'waste_morph_events', [])
    proof = dict(status='passed', scope=['B07', 'B06 last frame', 'B08 first frame'],
        base_script_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(text.encode()).hexdigest(),
        unchanged_split_3019=True, all_other_sections_unchanged=True,
        actual_packet_ul=.6, packet_shape=[3,4], final_rectangle=[48,172,12,16],
        aspiration_volume_ul=9.6, all_frames_complete_rectangles=True,
        only_B07_long_axis_transport_authorized=True,short_edge_leads=True,
        fixed_resident_cells_unchanged=True, stats=stats,
        collection_events=p.S.barcode_batch_events, bottom_motion_audit=motion,
        boundary_binary_audit=binary, full_workflow_rerun=False, wet_run_verified=False)
    csvpath = folder / '手工吸液顺序.csv'
    with csvpath.open(encoding='utf-8-sig', newline='') as fh:
        reader = csv.DictReader(fh)
        headers, rows = reader.fieldnames, list(reader)
    for row in rows:
        if row['吸液流程组名称'].startswith('B07 '):
            assert row['吸液孔'] == 'F1' and float(row[headers[6]]) == 9.6
            row['末尾帧号'] = str(len(command.relays))
    path.write_text(text)
    main = folder / 'main.elab'
    maintext = main.read_text()
    delta = int(flow.get('Timeout')) - old_flow_timeout
    maintext = re.sub(r'Timeout="(\d+)"', lambda m: f'Timeout="{int(m[1])+delta}"', maintext, count=1)
    main.write_text(maintext)
    with csvpath.open('w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.DictWriter(fh, headers)
        writer.writeheader()
        writer.writerows(rows)
    (folder / 'B07固定孔旁汇液局部校验_20261017.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(proof, ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('folder', type=Path)
    update(ap.parse_args().folder)
