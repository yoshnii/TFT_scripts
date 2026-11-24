"""Replay a top-loading pair from a native project and audit actual parent trajectories."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from project_structure import prepare_scripts
from update_cross_stage_local import CASES, Overlap
from update_parallel_local import register
from update_ready_parallel_local import check_dwell
from update_refill_local import FrameTrace
from update_transport_cleanup_local import last_hv, seed, validate_scene


def cut_travel(events, trace):
    opened = {key:e['separation_frames'][-1] for e in events for key in e['_child_keys']}
    result = []
    for event in events:
        key = event['_parent_key']
        positions = [row[key][1] for frame,(_,row) in zip(trace.steps,trace.rows)
                     if opened.get(key,-1) <= frame < event['partition_frame'] and key in row]
        assert positions
        x_steps = y_steps = 0
        for a,b in zip(positions,positions[1:]):
            if a[2:] != b[2:]:
                assert a[:2] == b[:2], 'reshape includes a translation'
            x_steps += abs(b[0]-a[0])
            y_steps += abs(b[1]-a[1])
        assert list(positions[-1][:2]) == event['positions'][0][0][:2]
        result.append(dict(parent=event['parent'],start=positions[0][:2],cut=positions[-1][:2],
                           x_steps=x_steps,y_steps=y_steps))
    return result


def replay(project,case):
    reagent,vol,host,fid,did,segment = case
    root = ET.parse(project/'PTseq_16_A.elab').getroot()
    old = {int(f.get('ID')):f for f in root}
    p = seed(old[fid][0][0],[('',host)])
    if reagent=='G4':
        p.S.relays[0].dur = int(old[fid][0][0].get('Duration'))
    if reagent=='AMP':
        for d in p.S.drops.values():
            d.a06_authorized_padding = True
    trace = FrameTrace(p)
    overlap = Overlap(p,host*16,vol*16)
    p.add_reagent(segment,old[fid].get('Name').split(' · ')[0],reagent,vol)
    gate,delivery = prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows
    assert ET.tostring(register(gate,fid,old)) == ET.tostring(old[fid])
    out = register(delivery,did,old)
    assert [c.attrib | {'Timeout':''} for c in out] == [c.attrib | {'Timeout':''} for c in old[did]]
    assert last_hv(out).get('RelayCode')==last_hv(old[did]).get('RelayCode')
    assert len(p.S.drops)==16 and all(abs(d.vol-host-vol)<1e-7 for d in p.S.drops.values())
    leaves = p.S.native_leaf_events
    for row in range(1,17):
        assert abs(sum(l['actual_ul'] for l in leaves if l['row']==row)-vol)<1e-7
    ready = next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
    assert len(p.S.reaction_merge_events)==1 and p.S.reaction_merge_events[0]['contact_step']>ready
    events = p.S.split_events
    assert all(e['gaps']==[0,2,4,6] for e in events)
    # Include child volumes, carrier shapes, dwell requests and exact port/row assignments.
    signature = [sorted((e['parent'],e['volumes'],e['positions'][0][0][2:],e['gaps'],
                         e.get('requested_frame_ms',[1000]*4)) for e in events),
                 sorted((l['name'],l['row'],l['actual_ul'],l['shape']) for l in leaves)]
    proof = dict(section=delivery.name.split()[0],id=did,gate_seconds=gate.timeout,
                 delivery_seconds=delivery.timeout,pair_seconds=gate.timeout+delivery.timeout,
                 native_matches=ET.tostring(out)==ET.tostring(old[did]),cut_travel=cut_travel(events,trace),
                 signature=hashlib.sha256(json.dumps(signature).encode()).hexdigest(),
                 scene=validate_scene(p,reagent),dwell=check_dwell(p,trace),frames=overlap.frames,
                 all_ready_frame=ready,merge_frame=p.S.reaction_merge_events[0]['contact_step'])
    return delivery,proof


if __name__=='__main__':
    import argparse
    arg=argparse.ArgumentParser()
    arg.add_argument('project',type=Path)
    arg.add_argument('reagent',choices=[c[0] for c in CASES])
    arg.add_argument('output',type=Path)
    args=arg.parse_args()
    flow,proof=replay(args.project,next(c for c in CASES if c[0]==args.reagent))
    args.output.with_suffix('.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    args.output.with_suffix('.xml').write_text(flow.xml().strip())
    print(args.reagent,proof['pair_seconds'],proof['native_matches'])
