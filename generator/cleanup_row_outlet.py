"""Row-ready transport for the four-column 16-sample cleanup withdrawals."""
from build_four_column import audit_waste, board, export, rigid, use_native
from dual_outlet import descent, withdrawal
from six_column_schedule import frames, seconds
from update_ready_parallel_local import FrameTrace, check_dwell
from update_transport_cleanup_local import seed, last_hv

# Four successive equal-volume leaves from each source. Each value is the last
# row using the outer rail that round. Zero selects single-rail transport.
OUTER_ROWS = (8, 8, 7, 7)
PREDECESSORS = {'R19': 'R18', 'C15': 'C14', 'C18': 'C17'}


def simulate(flows, stage, outer_rows=None, row_ready=False):
    board.configure()
    original = flows[stage]
    p = seed(last_hv(flows[PREDECESSORS[stage]]), [('', 3.)])
    if set(p.S.relays[0].codes) != set(original[0][0].get('RelayCode').split(';')):
        p.S.flush()
    if outer_rows is not None:
        p.S.cleanup_outer_rows = outer_rows
    p.S.cleanup_row_dispatch = row_ready
    trace = FrameTrace(p)
    items = [(ln, p.S.get(ln.name)) for ln in p.ALL]
    rigid.egress_pieces(p.S, items, p.blob, final=True, single_stop=stage!='R19')
    p.flow('R1' if stage=='R19' else 'C', stage, [p.cmd('F')])
    audit = audit_waste(p,48.)
    assert audit['withdrawals']==1 and audit['packets']==64
    assert not p.S.drops
    assert p.S.manual_aspirations[0][1]['port_volumes']=={f'F{i}':12. for i in range(1,5)}
    audit.update(check_dwell(p,trace))
    assert audit['splits']==48
    flow = use_native(original,export(p)[0])
    assert withdrawal(flow)==withdrawal(original)
    assert frames(flow[0])[0]==frames(original[0])[0]
    assert frames(flow[0])[-1]==frames(original[0])[-1]
    signature = sorted((e['parent'],e['volumes'],e['positions'],e['gaps'],e['requested_frame_ms'])
                       for e in p.S.split_events)
    early=[]
    for batch,members in p.S.batch_members.items():
        ready=[e['frame'] for e in p.S.row_ready_events if e['round']==batch]
        assert len(ready)==len(members)==16
        assert all(d.vol==.75 and d.rect[2:]==(4,4) for d in members)
        already_out=set()
        for i,(_,state) in zip(trace.steps,trace.rows):
            if i>=max(ready):break
            already_out.update(id(d) for d in members if id(d) in state and state[id(d)][1][0]>=58)
        early.append(len(already_out))
    audit['exits_before_last_row_ready']=early
    return flow,audit,signature


def reroute_cleanup(flows,stage):
    original=flows[stage]
    reference,_,cuts=simulate(flows,stage)
    assert frames(reference[0])==frames(original[0]),stage
    new,audit,signature=simulate(flows,stage,OUTER_ROWS,True)
    assert cuts==signature,stage
    result=dict(before_s=seconds(original),after_s=seconds(new),**descent(new))
    assert result['after_s']<result['before_s'] and result['simultaneous_descent_frames']>0
    assert sum(audit['exits_before_last_row_ready'])>0
    if stage=='R19':
        single,_,single_cuts=simulate(flows,stage,(0,0,0,0),True)
        grouped,_,grouped_cuts=simulate(flows,stage,OUTER_ROWS,False)
        assert single_cuts==grouped_cuts==cuts
        result.update(single_row_ready_s=seconds(single),dual_grouped_s=seconds(grouped))
    return new,result,audit
