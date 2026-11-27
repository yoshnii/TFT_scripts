"""Publish the row-ready G16 route only when the complete B16/C02 window wins."""
import json
from collections import Counter
from xml.etree import ElementTree as ET

from aliquot_outlet import flows
from build_four_column import BASE, SCRIPT, board, copy_controls, export, ingress_tracks, seed, shifted_relay, source_blob, use_native
from dual_outlet import publish, replace_flow, retime, xml
from loading_sections import OVERLAP, json_bytes
from overlap_waste_inlet import ordinary
from six_column_schedule import frames, in_well, seconds, transfer, write_frames
from update_ready_parallel_local import FrameTrace, check_dwell
from update_waste_inlet_local import check_footprints


def mixing_states(flow):
    lead, cycle, timer, tail = flow
    rounds, remainder = divmod(seconds(timer), seconds(cycle))
    assert not remainder and 0 < rounds < 10
    return frames(lead) + frames(cycle)*rounds + frames(tail)


def update(data, overview_name):
    board.configure()
    source = flows(source_blob(BASE, SCRIPT))
    current = flows(data[SCRIPT])
    mixing, delivery = current['B16'], current['C02']
    initial = mixing_states(mixing)
    baseline = initial + ordinary(delivery)
    first = source['C01'][0][0]
    p = seed(shifted_relay(first), [('',4.4)])
    p.S.relays[0].dur = int(first.get('Duration'))
    p.S.bead_refill_enabled = True
    trace = FrameTrace(p)
    p.add_reagent('C','C01','G16',1.76)
    assert check_dwell(p,trace)['splits']==30
    assert Counter((e['row'],e['actual_ul']) for e in p.S.native_leaf_events)=={
        (row,.88):2 for row in range(1,17)}
    assert len(p.S.reaction_merge_events)==1 and all(abs(p.S.get(l.name).vol-6.16)<1e-8 for l in p.ALL)
    gate, receipt = [use_native(source[k],flow) for k,flow in zip(('C01','C02'),export(p),strict=True)]
    work = copy_controls(source['B16'])
    overlap = transfer(source['B15'], work, gate, receipt, 78020)
    prepared = mixing_states(work)
    assert len(initial) == len(prepared) == seconds(mixing)
    for (s,old),(t,new) in zip(initial,prepared,strict=True):
        assert s==t and {r for r in old if in_well(r)}=={r for r in new if in_well(r)}
    end = ordinary(gate)+ordinary(receipt)
    candidate = prepared+end
    assert candidate[0]==baseline[0] and candidate[-1]==baseline[-1]
    fusion = lambda states: next(i for i,state in enumerate(states) if state==baseline[-1])
    assert len(candidate)<len(baseline) and fusion(candidate)<fusion(baseline)
    # Retain the existing one repeated round and nine unrolled rounds in the UI.
    lead,cycle,timer,tail = mixing
    i = seconds(lead)
    assert seconds(timer)==seconds(cycle) and seconds(tail)==9*seconds(cycle)
    write_frames(lead,prepared[:i])
    write_frames(cycle,prepared[i:i+seconds(cycle)])
    write_frames(tail,prepared[i+seconds(cycle):])
    assert mixing_states(mixing)==prepared
    for dest,src in zip(delivery,[*gate,*receipt],strict=True):
        write_frames(dest,frames(src))
    delivery.set('Timeout',str(len(end)))
    text = data[SCRIPT].decode()
    overview = json.loads(data[overview_name])
    changes = {}
    for key,flow in [('B16',mixing),('C02',delivery)]:
        check_footprints(flow)
        prev = overview['stages'][key]['four']
        changes[key] = dict(before_s=prev,after_s=seconds(flow))
        overview['stages'][key]['four']=seconds(flow)
        text = replace_flow(text,flow.get('ID'),xml(flow))
    rails = ingress_tracks(delivery)
    assert rails['simultaneous_dual_descent_frames']>0
    overview['checks']['ingress_tracks']['C02']=rails
    proof=dict(single_s=len(baseline),dual_s=len(candidate),single_merge_s=fusion(baseline),
               dual_merge_s=fusion(candidate),mix_s=len(initial),saved_s=len(baseline)-len(candidate),
               overlap_s=overlap,packet_ul=.88,per_well_ul=1.76,**rails)
    overview['bead_inlet_refill']=proof
    overview['overlap_s']['B15 B16 C01 C02']=overlap
    report=json.loads(data[OVERLAP])
    report['B16_C02']=proof
    data[OVERLAP]=json_bytes(report)
    root=ET.fromstring(data[SCRIPT])
    publish(data,overview_name,overview,root,retime(root),text,changes)
