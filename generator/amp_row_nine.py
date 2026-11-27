"""Schedule the four-column AMP inlet against the unchanged A06 waste stream."""
from collections import Counter
from copy import deepcopy
import json
from xml.etree import ElementTree as ET

from aliquot_outlet import flows
from build_four_column import BASE, SCRIPT, board, export, ingress_tracks, install_frames, seed, shifted_relay, source_blob, use_native
from dual_outlet import publish, replace_flow, retime, xml
from loading_sections import OVERLAP, json_bytes
from overlap_waste_inlet import cdna, ordinary
from six_column_schedule import apart, frames, seconds, transfer, write_frames
from update_ready_parallel_local import FrameTrace, check_dwell
from update_waste_inlet_local import check_footprints


def parked_handoff(feed, hosts, landing, waste, setting):
    fixed = hosts[0]
    assert all(host == fixed for host in hosts)
    # The retained sample footprints are already present in the native waste
    # trace. Only new or moved rectangles need clearance from that trace.
    path = [(setting, fixed | feed.held)]
    path.extend((setting, fixed | guest) for guest in feed.states[feed.position:])
    entrance_index = len(path)
    path.extend(landing)
    last = len(path) - 1
    parents = []
    reachable = {0}
    for tick in range(len(waste) + len(path)):
        outgoing = waste[tick] if tick < len(waste) else frozenset()
        next_states = {}
        for previous in sorted(reachable, reverse=True):
            for index in (previous + 1, previous):
                if index <= last and index not in next_states and apart(path[index][1] - fixed, outgoing):
                    next_states[index] = previous
        if not next_states:
            raise ValueError(f'No admissible AMP frame at handoff second {tick}')
        parents.append(next_states)
        if last in next_states:
            break
        reachable = next_states
    else:
        raise ValueError('AMP delivery did not finish')
    index = last
    timeline = []
    for choices in reversed(parents):
        timeline.append(index)
        index = choices[index]
    assert index == 0
    timeline.reverse()
    timeline.extend([last] * (len(waste) - len(timeline)))
    result = []
    for tick, index in enumerate(timeline):
        outgoing = waste[tick] if tick < len(waste) else frozenset()
        assert apart(path[index][1] - fixed, outgoing)
        result.append((setting, path[index][1] | outgoing))
    return result, next(i for i, index in enumerate(timeline) if index >= entrance_index)


def update_amp(data, overview_name):
    board.configure()
    overview = json.loads(data[overview_name])
    source = flows(source_blob(BASE, SCRIPT))
    current = flows(data[SCRIPT])
    old = ordinary(current['A06']) + ordinary(current['A08'])
    waste = deepcopy(source['A06'])
    first = waste[0][0]
    p = seed(shifted_relay(first), [('', 2.)])
    p.S.relays[0].dur = int(first.get('Duration'))
    p.S.aliquot_outer_rows = (8, 0)
    p.split_waste('A', 'A06', .5, 'cDNA多余液体')
    raw = ET.fromstring(export(p)[0].xml())
    for dest, cmd in zip(waste, raw, strict=True):
        install_frames(dest, cmd)
    waste.set('Timeout', raw.get('Timeout'))

    start = source['A07'][0][0]
    p = seed(shifted_relay(start), [('', .5)])
    p.S.relays[0].dur = int(start.get('Duration'))
    p.S.amp_hold_residents = True
    for drop in p.S.drops.values():
        drop.a06_authorized_padding = True
    trace = FrameTrace(p)
    p.add_reagent('A', 'A07', 'AMP', 2.)
    assert check_dwell(p, trace)['splits'] == 30
    assert Counter((e['row'], e['actual_ul']) for e in p.S.native_leaf_events) == {
        (row, 1.): 2 for row in range(1, 17)}
    assert len(p.S.reaction_merge_events) == 1 and all(p.S.get(l.name).vol == 2.5 for l in p.ALL)
    fixed = ordinary(raw)[-1][1]
    exit_second = overview['aliquot_row_outlet']['A06']['trials']['dual']['exit_s']
    assert len(fixed) == 16 and all(fixed <= rs for _,rs in ordinary(raw)[exit_second:])
    gate, delivery = [use_native(source[k], f) for k, f in zip(('A07', 'A08'), export(p), strict=True)]
    stages = {'A06': waste, 'A07': gate, 'A08': delivery}
    overlap = transfer(deepcopy(source['A05']), waste, gate, delivery, 78000)
    details = cdna(stages, 22, overlap, schedule_handoff=parked_handoff)

    # Return all sixteen merged rectangles to the original A09 handoff.
    tail = frames(delivery[-1])
    setting, merged = tail[-1]
    assert old[-1] == (setting, frozenset((x+6,y,w,h) for x,y,w,h in merged))
    for dx in range(1,7):
        tail.extend([(setting, frozenset((x+dx,y,w,h) for x,y,w,h in merged))] * 2)
    tail.extend([old[-1]] * 4)
    write_frames(delivery[-1], tail)
    cmds = {c.get('ID'): c for f in stages.values() for c in f}
    text = data[SCRIPT].decode()
    changes = {}
    for key in ('A06', 'A08'):
        flow = deepcopy(current[key])
        for c in flow:
            install_frames(c, cmds[c.get('ID')])
        flow.set('Timeout', str(sum(seconds(c) for c in flow)))
        check_footprints(flow)
        changes[key] = dict(before_s=seconds(current[key]), after_s=seconds(flow))
        overview['stages'][key]['four'] = seconds(flow)
        text = replace_flow(text, flow.get('ID'), xml(flow))
    updated = flows(text)
    overview['checks']['ingress_tracks']['A08'] = ingress_tracks(updated['A08'])
    timeline = ordinary(updated['A06']) + ordinary(updated['A08'])
    assert timeline[0] == old[0] and len(timeline) == 608
    assert all({r for r in a if r[1]>=170} == {r for r in b if r[1]>=170}
               for (_,a),(_,b) in zip(ordinary(current['A06']), ordinary(updated['A06']), strict=True))
    assert details['exit_s'] == 178 and details['first_entry_s'] == 234
    details['synchronous_merge_s'] = details['all_wells_final_frame_s']
    details['all_wells_final_frame_s'] = next(i for i,state in enumerate(timeline) if state == old[-1])
    details['window_s'] = len(timeline)
    overview['amp_row_nine'] = dict(before_s=len(old), **details)
    report = json.loads(data[OVERLAP])
    report['A06'].update(details)
    data[OVERLAP] = json_bytes(report)
    root = ET.fromstring(data[SCRIPT])
    publish(data, overview_name, overview, root, retime(root), text, changes)


def direct_mix_handoff(data, overview_name):
    """Keep both settling periods at the merge point, then continue left into A09."""
    text = data[SCRIPT].decode()
    sections = flows(text)
    pre_mix, mixing = sections['A08'], sections['A09']
    tail, position = frames(pre_mix[-1]), frames(mixing[0])
    resting = tail[-17]
    assert tail[-20:-16] == [resting] * 4 and position[5] == resting
    for dx in range(1, 7):
        translated = frozenset((x+dx, y, w, h) for x,y,w,h in resting[1])
        assert tail[-16+2*(dx-1):-16+2*dx] == [(resting[0], translated)] * 2
    assert tail[-4:] == [tail[-5]] * 4
    write_frames(pre_mix[-1], tail[:-16] + [resting] * 4)
    write_frames(mixing[0], position[6:])
    overview = json.loads(data[overview_name])
    changes = {}
    for key, flow, saved in [('A08', pre_mix, 12), ('A09', mixing, 6)]:
        before = seconds(flow)
        flow.set('Timeout', str(before-saved))
        changes[key] = dict(before_s=before, after_s=seconds(flow))
        overview['stages'][key]['four'] = seconds(flow)
        text = replace_flow(text, flow.get('ID'), xml(flow))
    window = seconds(sections['A06']) + seconds(pre_mix)
    details = dict(all_wells_final_frame_s=588, window_s=window)
    overview['amp_row_nine'].update(details)
    overview['amp_mix_handoff'] = dict(before_s=window+seconds(mixing)+18, after_s=window+seconds(mixing), saved_s=18)
    report = json.loads(data[OVERLAP])
    report['A06'].update(details)
    data[OVERLAP] = json_bytes(report)
    root = ET.fromstring(data[SCRIPT])
    publish(data, overview_name, overview, root, retime(root), text, changes)
