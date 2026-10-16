"""Combine timed elution blocks using native semicolon-separated end groups."""
from model import Flow


def combine_incubation(flows, tail, title):
    """Preserve both loop/timer pairs and attach the remaining static incubation."""
    assert len(flows) == 2 and tail and tail.comp == 'HighVoltage'
    expected = sum(f.timeout for f in flows) + tail.timeout
    commands=[];groups=[];previous=None
    for flow in flows:
        assert flow.repeats == 1 and len(flow.sync_groups) == 1
        group=flow.sync_groups[0]
        timer=next(c for c in group if c.comp == 'Delay')
        hv=next(c for c in group if c.comp == 'HighVoltage')
        assert hv.params == 'SCRIPTCYCLE_TRUE' and timer.pre == (hv,'SS')
        assert flow.cmds[0].pre is None
        if previous:
            flow.cmds[0].pre=(previous,'FS')
        commands.extend(flow.cmds);groups.append(group);previous=timer
    tail.pre=(previous,'FS');commands.append(tail)
    combined=Flow(title,commands,sync_groups=groups,chain_commands=False)
    assert combined.timeout == expected == 300
    return combined
