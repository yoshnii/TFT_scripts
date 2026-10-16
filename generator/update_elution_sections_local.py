"""Merge only the two R1 incubation blocks; preserve native commands verbatim."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as E

PAIRS=((2022,2027,2032),(2045,2050,2055))


def signature(c):
    attrs=dict(c.attrib);attrs.pop('PreTask',None)
    return attrs,[dict(r.attrib) for r in c]


def schedule(flows):
    """Local nominal command starts; does not simulate instrument cycle stopping."""
    result={};offset=0
    for f in flows:
        byid={c.get('ID'):c for c in f};starts={};active=set()
        def start(cid):
            if cid in starts:return starts[cid]
            assert cid not in active
            active.add(cid);c=byid[cid];dep,relation=c.get('PreTask').split(',')
            if dep=='-1':value=0
            else:
                assert dep in byid and relation in ('SS','FS')
                value=start(dep)+(int(byid[dep].get('Timeout')) if relation=='FS' else 0)
            active.remove(cid);starts[cid]=value;return value
        for cid in byid:result[cid]=offset+start(cid)
        duration=max((starts[cid]+int(c.get('Timeout')) for cid,c in byid.items()),default=0)
        assert duration==int(f.get('Timeout'))
        offset+=duration
    return result,offset


def flow_text(flow):
    flow=copy.deepcopy(flow)
    flow.text='\n    '
    for i,c in enumerate(flow):c.tail='\n  ' if i==len(flow)-1 else '\n    '
    flow.tail=None
    return '  '+E.tostring(flow,encoding='unicode',short_empty_elements=True)


def replace_flow(text,fid,flow):
    text,n=re.subn(rf'(?m)^[ \t]*<Flow\b[^>]*\bID="{fid}"[\s\S]*?^[ \t]*</Flow>',
                   lambda _:flow_text(flow) if flow is not None else '',text,count=1)
    assert n==1,(fid,n)
    return text


def update(folder):
    script=folder/'PTseq_16_A.elab';text=script.read_text();before=E.fromstring(text)
    old_sha=hashlib.sha256(script.read_bytes()).hexdigest()
    byid={int(f.get('ID')):f for f in before};changed={};removed=set();proof=[]
    for first_id,second_id,after_id in PAIRS:
        first,second,after=(byid[i] for i in (first_id,second_id,after_id))
        assert len(first)==len(second)==4 and after[0].get('Component')=='HighVoltage'
        assert after[0].get('Timeout')=='11' and len(after[0])==1
        assert all(c.get('Timeout')=='48' for c in (first[-1],second[-1]))
        merged=copy.deepcopy(first)
        merged.set('Name',first.get('Name').replace('孵育第120s轻晃','洗脱孵育300s（第120/240s轻晃）'))
        merged.set('SyncEndingCmdList',first.get('SyncEndingCmdList')+';'+second.get('SyncEndingCmdList'))
        following=[copy.deepcopy(c) for c in second]
        following[0].set('PreTask',first[-1].get('ID')+',FS')
        merged.extend(following)
        tail=copy.deepcopy(after[0]);tail.set('PreTask',second[-1].get('ID')+',FS');merged.append(tail)
        merged.set('Timeout',str(int(first.get('Timeout'))+int(second.get('Timeout'))+int(tail.get('Timeout'))))
        assert merged.get('Timeout')=='300'
        capture=copy.deepcopy(after);capture.remove(capture[0]);capture[0].set('PreTask','-1,UnTask')
        capture.set('Timeout',str(int(after.get('Timeout'))-int(tail.get('Timeout'))))
        old_commands={c.get('ID'):c for f in (first,second,after) for c in f}
        new_commands={c.get('ID'):c for f in (merged,capture) for c in f}
        assert old_commands.keys()==new_commands.keys()
        assert all(signature(c)==signature(new_commands[cid]) for cid,c in old_commands.items())
        old_schedule,old_total=schedule([first,second,after]);new_schedule,new_total=schedule([merged,capture])
        assert old_schedule==new_schedule and old_total==new_total
        for old_flow,new_flow in ((first,merged),(second,None),(after,capture)):
            fid=int(old_flow.get('ID'));text=replace_flow(text,fid,new_flow)
            if new_flow is None:removed.add(fid)
            else:changed[fid]=new_flow
        proof.append(dict(merged_flow_id=first_id,removed_flow_id=second_id,following_flow_id=after_id,
            merged_name=merged.get('Name'),incubation_seconds=300,old_local_seconds=old_total,
            new_local_seconds=new_total,command_ids=list(old_commands),
            command_starts_seconds=old_schedule,all_electrode_frames_and_durations_identical=True,
            loop_parameters_unchanged=True,original_48s_timer_and_33s_trace_issue_not_changed=True,
            sync_groups=merged.get('SyncEndingCmdList').split(';')))
    after=E.fromstring(text)
    assert len(after)==len(before)-2
    for f in after:
        fid=int(f.get('ID'))
        if fid not in changed:assert E.tostring(f)==E.tostring(byid[fid]),fid
    assert sum(int(f.get('Timeout')) for f in before)==sum(int(f.get('Timeout')) for f in after)
    # Layout: keep every unrelated node and coordinate, moving only the affected commands.
    layout=folder/'PTseq_16_A.elab.layout';layout_text=layout.read_text();lr=E.fromstring(layout_text)
    layout_flows={int(f.get('ID')):f for f in lr if f.tag=='Flow'}
    command_nodes={c.get('ID'):c for f in layout_flows.values() for c in f}
    for fid,f in changed.items():
        node=copy.deepcopy(layout_flows[fid]);node.set('Name',f.get('Name'))
        for c in list(node):node.remove(c)
        for i,c in enumerate(f):
            child=copy.deepcopy(command_nodes[c.get('ID')]);child.set('Position',f'0,{30*i}')
            node.append(child)
        layout_text=replace_flow(layout_text,fid,node)
    for fid in removed:layout_text=replace_flow(layout_text,fid,None)
    for first_id,second_id,after_id in PAIRS:
        layout_text,n=re.subn(rf'(<Arrow\b[^>]*\bEndID="){second_id}("[^>]*\bStartID="{first_id}")',
                             rf'\g<1>{after_id}\2',layout_text);assert n==1
        layout_text,n=re.subn(rf'\s*<Arrow\b[^>]*\bEndID="{after_id}"[^>]*\bStartID="{second_id}"[^>]*/>',
                             '',layout_text);assert n==1
    new_layout=E.fromstring(layout_text)
    ids={f.get('ID') for f in after}
    assert {f.get('ID') for f in new_layout if f.tag=='Flow'}==ids
    arrows=[(a.get('StartID'),a.get('EndID')) for a in new_layout if a.tag=='Arrow']
    assert arrows==[(a.get('ID'),b.get('ID')) for a,b in zip(after,list(after)[1:])]
    new_byid={f.get('ID'):f for f in after}
    for f in new_layout:
        if f.tag=='Flow':
            assert [c.get('ID') for c in f]==[c.get('ID') for c in new_byid[f.get('ID')]]
    mapping_path=folder/'步骤编号映射_20261016.json'
    mapping=json.loads(mapping_path.read_text());names={int(f.get('ID')):f.get('Name') for f in after}
    mapping=[row for row in mapping if row['id'] not in removed]
    for row in mapping:row['name']=names[row['id']]
    # Endpoint frames remain identical: only their section membership changes.
    for first_id,second_id,after_id in PAIRS:
        assert signature(changed[first_id][0])==signature(byid[first_id][0])
        assert signature(changed[after_id][-1])==signature(byid[after_id][-1])
    report=dict(status='passed',scope='R1 elution incubation section grouping only',
        old_script_sha256=old_sha,script_sha256=hashlib.sha256(text.encode()).hexdigest(),
        cases=proof,removed_section_names=[byid[i].get('Name') for i in sorted(removed)],
        unrelated_sections_identical=True,other_labels_not_renumbered=True,
        shared_end_group_reference='PF_L1_1209.elab Flow3: 6,7;8,4',
        manual_boundaries_volumes_magnet_temperature_and_main_timeout_unchanged=True,
        full_workflow_rerun=False,wet_run_verified=False)
    script.write_text(text);layout.write_text(layout_text)
    mapping_path.write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+'\n')
    (folder/'洗脱孵育Section合并局部校验_20261017.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    update(parser.parse_args().folder)
