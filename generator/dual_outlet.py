"""Two descending waste rails for aqueous separation in the four-column 10x project."""
import csv
import hashlib
import io
import json
import re
from collections import Counter
from html import escape
from itertools import groupby
from xml.etree import ElementTree as ET

from build_four_column import board, audit_waste, export, use_native
from loading_sections import OVERLAP, SUCTION_TABLE, PAGE, json_bytes
from six_column_schedule import frames, seconds, waste
from update_ready_parallel_local import FrameTrace, check_dwell
from update_six_column_local import SCRIPT, xml, replace_flow, retime
from update_transport_cleanup_local import seed, last_hv

# The reaction well ends at x58; the next column begins at x81.
RAILS = (58,68)
SPLIT_HASH = {
    'R05': 'd308532fb93590582148db1e03c28a7a7e6a22c75f938aed4549384e16b5f78a',
    'C05': 'ce9c5a853344f580e074f313c387e2c3a4d2c6c2acb3285806d95872c9b64f8a',
}


def descent(flow):
    previous=frozenset()
    counts=Counter()
    together=0
    for _,rects in frames(flow[0]):
        moving={x for x,y,w,h in rects if x in RAILS and 28<=y<=163
                and (w,h)==(4,4) and (x,y-1,w,h) in previous and (x,y,w,h) not in previous}
        counts.update(moving)
        together+=len(moving)==2
        previous=rects
    return dict(rails=list(RAILS),moving_frames_per_rail=[counts[x] for x in RAILS],
                simultaneous_descent_frames=together)


def withdrawal(flow):
    # Include the fully settled collection dwell, every shift/shrink and power-off.
    band=[frozenset(r for r in boxes if r[1]>=170) for _,boxes in frames(flow[0])]
    runs=[(mask,len(list(group))) for mask,group in groupby(band)]
    touched=next(i for i,(mask,_) in enumerate(runs) if any(y+h>=190 for x,y,w,h in mask))
    suffix=runs[touched-2:]
    assert len(suffix[0][0])==4 and suffix[-1][0]==frozenset()
    return suffix


def reroute(flows,first):
    second=first[0]+'06'
    per_well=5.0 if first=='R05' else 6.16
    p=seed(last_hv(flows[first[0]+'04']),[('',per_well)])
    if first=='C05':p.S.flush()
    trace=FrameTrace(p)
    p.S.dual_waste_outlet=True
    p.magnet_state='up'
    p.supernatant_off('R1' if first=='R05' else 'C',first)
    assert len(p.S.drops)==16 and all(d.magnet_only and d.vol==0 for d in p.S.drops.values())
    info=audit_waste(p,16*per_well)
    assert info['withdrawals']==2 and info['packets']==128
    for _,row in p.S.manual_aspirations:
        assert row['port_volumes']=={f'F{i}':2*per_well for i in range(1,5)}
    info['per_port_per_withdrawal_ul']=2*per_well
    signature=[(e['parent'],e['volumes'],e['gaps'],e['positions'],e.get('requested_frame_ms'))
               for e in p.S.split_events]
    digest=hashlib.sha256(json.dumps(signature).encode()).hexdigest()
    print(first,'split signature',digest,flush=True)
    assert digest==SPLIT_HASH[first],(first,'cut changed',digest)
    info['cut_geometry_and_dwell_sha256']=digest
    info.update(check_dwell(p,trace))
    new=[use_native(flows[k],native) for k,native in zip((first,second),export(p),strict=True)]
    overlap=waste(*new)
    result={}
    for k,flow in zip((first,second),new):
        original=flows[k]
        assert withdrawal(flow)==withdrawal(original),k
        assert seconds(flow)<seconds(original),k
        result[k]=dict(before_s=seconds(original),after_s=seconds(flow),**descent(flow))
        assert result[k]['simultaneous_descent_frames']>0
    assert frames(new[0][0])[0]==frames(flows[first][0])[0]
    assert frames(new[1][0])[-1]==frames(flows[second][0])[-1]
    return new,result,info,overlap


def update(data,overview_name):
    board.configure()
    overview=json.loads(data[overview_name])
    assert 'dual_outlet' not in overview
    text=data[SCRIPT].decode()
    root=ET.fromstring(text)
    before=retime(root)
    assert overview['full_s']==before
    flows={f.get('Name').split()[0]:f for f in root}
    stats={}
    for first,second in [('R05','R06'),('C05','C06')]:
        new,results,audit,overlap=reroute(flows,first)
        stats.update(results)
        overview['route_checks'][first+'+'+second]=audit
        overview['overlap_s'][first+' '+second]=overlap
        for name,flow in zip((first,second),new):
            text=replace_flow(text,flow.get('ID'),xml(flow))
            overview['stages'][name]['four']=seconds(flow)
    overview['dual_outlet']=dict(outer_rows=[1,7],inner_rows=[8,16],stages=stats,
                                 saved_s=sum(v['before_s']-v['after_s'] for v in stats.values()))
    publish(data,overview_name,overview,root,before,text,stats)
    return update_cleanup(data,overview_name)


def update_cleanup(data,overview_name):
    from cleanup_row_outlet import OUTER_ROWS, PREDECESSORS, reroute_cleanup
    overview=json.loads(data[overview_name])
    assert 'dual_outlet' in overview and 'cleanup_row_outlet' not in overview
    text=data[SCRIPT].decode()
    root=ET.fromstring(text)
    flows={f.get('Name').split()[0]:f for f in root}
    before=retime(root)
    assert before==overview['full_s']
    stats={}
    for stage in PREDECESSORS:
        flow,result,audit=reroute_cleanup(flows,stage)
        stats[stage]=result
        text=replace_flow(text,flow.get('ID'),xml(flow))
        overview['route_checks'][stage]=audit
        overview['stages'][stage]['four']=seconds(flow)
    overview['cleanup_row_outlet']=dict(outer_rows_through=list(OUTER_ROWS),stages=stats,
            saved_s=sum(v['before_s']-v['after_s'] for v in stats.values()))
    publish(data,overview_name,overview,root,before,text,stats)
    from aliquot_outlet import update as update_aliquots
    return update_aliquots(data,overview_name)


def publish(data,overview_name,overview,root,before,text,stats):
    rebuilt=ET.fromstring(text)
    after=retime(rebuilt)
    assert [f.get('ID') for f in root]==[f.get('ID') for f in rebuilt]
    for old,new in zip(root,rebuilt):
        if old.get('Name').split()[0] not in stats:
            assert ET.tostring(old)==ET.tostring(new)
    assert before-after==sum(s['before_s']-s['after_s'] for s in stats.values())
    main=data['main.elab'].decode()
    assert main.count(f'Timeout="{before}"')==1
    data['main.elab']=main.replace(f'Timeout="{before}"',f'Timeout="{after}"').encode()
    data[SCRIPT]=text.encode()
    overview['full_s']=after
    end=next(i for i,f in enumerate(rebuilt,1) if f.get('Name').split()[0]=='C12')
    overview['through_recovery_s']=sum(seconds(f)*int(f.get('Repeats')) for f in list(rebuilt)[:end])
    overview['checks']['native_relay_frames']=sum(1 for _ in rebuilt.iter('RelayItem'))
    data[overview_name]=json_bytes(overview)
    detail=json.loads(data[OVERLAP])
    detail.update(published_full_s=after,native_sha256=hashlib.sha256(data[SCRIPT]).hexdigest())
    data[OVERLAP]=json_bytes(detail)
    by_id={f.get('ID'):f for f in rebuilt}
    rows=list(csv.reader(io.StringIO(data[SUCTION_TABLE].decode('utf-8-sig'))))
    for row in rows[1:]:
        flow=by_id[row[1]]
        if flow.get('Name').split()[0] in stats:
            assert flow[-1].get('ID')==row[3]
            row[4]=str(len(flow[-1]))
    output=io.StringIO(newline='')
    csv.writer(output).writerows(rows)
    data[SUCTION_TABLE]=output.getvalue().encode('utf-8-sig')
    page=data[PAGE].decode()
    matches=list(re.finditer(r'<table>.*?</table>',page,re.S))
    assert len(matches)==2
    table='<table>'+''.join('<tr>'+''.join(f'<{tag}>{escape(cell)}</{tag}>' for cell in row)+'</tr>'
          for i,row in enumerate(rows) for tag in [('td' if i else 'th')])+'</table>'
    data[PAGE]=(page[:matches[1].start()]+table+page[matches[1].end():]).encode()
    return stats
