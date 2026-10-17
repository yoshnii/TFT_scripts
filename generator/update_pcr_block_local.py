"""Merge native A10/A11/A12 only, preserving all unrelated native XML."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as E
from model import Cmd, CommandBlock, Flow

FLOW_IDS = (1030,1035,1043)
BLOCK_IDS = list(range(1036,1042))
REMOVED_COMMANDS = {1034,1044,1049}
TITLE = 'A10 预变性95℃3min→PCR三段×30→终延伸72℃1min→4℃30s [热盖105℃]'
PULSE_TITLE = '全段温控：OFF1000ms/ON1000ms（循环至4℃保持结束；周期末帧上电）'


def flow_text(f):
    f=copy.deepcopy(f);f.text='\n    ';f.tail=None
    for i,c in enumerate(f):c.tail='\n  ' if i==len(f)-1 else '\n    '
    return '  '+E.tostring(f,encoding='unicode',short_empty_elements=True)


def replace_flow(text,fid,f):
    text,n=re.subn(rf'(?m)^[ \t]*<Flow\b[^>]*\bID="{fid}"[\s\S]*?^[ \t]*</Flow>',
                   lambda _:flow_text(f) if f is not None else '',text,count=1)
    assert n==1,(fid,n)
    return text


def model_flow(raw):
    cmds={c.get('ID'):Cmd(c.get('Name'),c.get('Component'),c.get('Action'),
                         int(c.get('Timeout')),c.get('Params','')) for c in raw}
    for c in raw:
        item=cmds[c.get('ID')];item.id=int(c.get('ID'))
        parent,relation=c.get('PreTask').split(',')
        if parent!='-1':item.pre=(cmds[parent],relation)
    blocks=[CommandBlock(b['Name'],[cmds[str(i)] for i in b['IDList']],b['Cycle'])
            for b in json.loads(raw.get('UnBrokenBlockList','[]'))]
    sync=[cmds[i] for i in raw.get('SyncEndingCmdList','').split(',') if i]
    f=Flow(raw.get('Name'),list(cmds.values()),repeats=int(raw.get('Repeats','1')),
           sync=sync,chain_commands=False,blocks=blocks)
    f.id=int(raw.get('ID'))
    return f


def temperature_trace(raw):
    """Independent explicit iteration count; only used for this local check."""
    controls=[c for c in raw if c.get('Component','').startswith('Heater')
              or c.get('Component')=='Delay']
    blocks=json.loads(raw.get('UnBrokenBlockList','[]'))
    if blocks:
        assert len(blocks)==1
        b=blocks[0];ids=set(map(str,b['IDList']));first=str(b['IDList'][0]);trace=[]
        for c in controls:
            if c.get('ID')==first:
                trace.extend([copy.deepcopy(x) for _ in range(b['Cycle'])
                              for x in controls if x.get('ID') in ids])
            elif c.get('ID') not in ids:trace.append(copy.deepcopy(c))
        return trace
    return [copy.deepcopy(c) for _ in range(int(raw.get('Repeats','1'))) for c in controls]


def control_signature(c):
    return tuple(c.get(k) for k in ('ID','Name','Component','Action','Params','Timeout'))


def update(folder):
    path=folder/'PTseq_16_A.elab';text=path.read_text();before=E.fromstring(text)
    byid={int(f.get('ID')):f for f in before};old=[byid[i] for i in FLOW_IDS]
    assert [f.get('Repeats') for f in old]==['1','30','1']
    commands={int(c.get('ID')):c for f in old for c in f}
    merged=copy.deepcopy(old[0])
    for c in list(merged):merged.remove(c)
    merged.set('Name',TITLE);merged.set('Repeats','1')
    merged.set('SyncEndingCmdList','1048,1042')
    block=dict(Cycle=30,Enable=True,IDList=BLOCK_IDS,Name='PCR三段温控')
    merged.set('UnBrokenBlockList',json.dumps([block],ensure_ascii=False,separators=(',',':')))
    order=[1031,1032,1033,*BLOCK_IDS,1045,1046,1047,1048,1042]
    for cid in order:merged.append(copy.deepcopy(commands[cid]))
    new_cmds={int(c.get('ID')):c for c in merged}
    new_cmds[1036].set('PreTask','1033,FS')
    new_cmds[1045].set('PreTask','1041,FS')
    new_cmds[1042].set('PreTask','1032,SS');new_cmds[1042].set('Name',PULSE_TITLE)
    duration=model_flow(merged).timeout;merged.set('Timeout',str(duration))
    assert duration==9904
    old_trace=[c for f in old for c in temperature_trace(f)]
    new_trace=temperature_trace(merged)
    assert list(map(control_signature,old_trace))==list(map(control_signature,new_trace))
    assert len(new_trace)==186
    assert len([c for c in new_trace if c.get('ID')=='1036'])==30
    assert new_trace[-1].get('ID')=='1048'
    assert not set(BLOCK_IDS)&{1042,1032,1033,1045,1046,1047,1048}
    assert new_cmds[1045].get('PreTask')=='1041,FS'
    pulse=new_cmds[1042]
    assert pulse.get('Params')=='SCRIPTCYCLE_TRUE' and pulse.get('Timeout')=='2'
    assert [(r.get('Duration'),r.get('Amp')) for r in pulse]==[('1000','0'),('1000','50')]
    assert pulse[0].get('RelayCode')==pulse[1].get('RelayCode')
    assert pulse[1].get('RelayCode')==commands[1031][-1].get('RelayCode')
    assert pulse[1].get('RelayCode')==commands[1049][-1].get('RelayCode')
    # Non-temperature positioning is byte-identical; only pulse scope changes.
    assert E.tostring(new_cmds[1031])==E.tostring(commands[1031])
    for cid in BLOCK_IDS+[1032,1033,1045,1046,1047,1048]:
        assert control_signature(new_cmds[cid])==control_signature(commands[cid])
    text=replace_flow(text,1030,merged)
    for fid in FLOW_IDS[1:]:text=replace_flow(text,fid,None)
    after=E.fromstring(text)
    assert len(after)==len(before)-2
    for f in after:
        if f.get('ID')!='1030':assert E.tostring(f)==E.tostring(byid[int(f.get('ID'))])
    all_ids=[int(x.get('ID')) for f in after for x in (f,*list(f))]
    assert len(all_ids)==len(set(all_ids))
    old_nominal=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in old)
    delta=duration-old_nominal;assert delta==-1
    # Immediate neighbors: keep original positioning entrance and powered exit mask.
    idx=list(before).index(old[0]);previous=before[idx-1];following=before[idx+3]
    previous_last=[c for c in previous if c.get('Component')=='HighVoltage'][-1][-1]
    following_first=next(c for c in following if c.get('Component')=='HighVoltage')[0]
    assert previous_last.get('Amp')!='0' and following_first.get('Amp')!='0'
    assert E.tostring(previous)==E.tostring(after[idx-1])
    assert E.tostring(following)==E.tostring(after[idx+1])
    # Preserve native layout coordinates everywhere except the merged section.
    layout=folder/'PTseq_16_A.elab.layout';lt=layout.read_text();lr=E.fromstring(lt)
    nodes={c.get('ID'):c for f in lr if f.tag=='Flow' for c in f}
    lf=copy.deepcopy(next(f for f in lr if f.tag=='Flow' and f.get('ID')=='1030'))
    lf.set('Name',TITLE)
    for c in list(lf):lf.remove(c)
    for i,c in enumerate(merged):
        node=copy.deepcopy(nodes[c.get('ID')]);node.set('Name',c.get('Name'));node.set('Position',f'0,{30*i}')
        lf.append(node)
    lt=replace_flow(lt,1030,lf)
    for fid in FLOW_IDS[1:]:lt=replace_flow(lt,fid,None)
    for start,end,replacement in ((1030,1035,2000),(1035,1043,None),(1043,2000,None)):
        pattern=rf'\s*<Arrow\b[^>]*\bEndID="{end}"[^>]*\bStartID="{start}"[^>]*/>'
        lt,n=re.subn(pattern,lambda _:f'\n <Arrow EndID="{replacement}" StartID="{start}"/>'
                     if replacement else '',lt,count=1);assert n==1
    la=E.fromstring(lt)
    assert {f.get('ID') for f in la if f.tag=='Flow'}=={f.get('ID') for f in after}
    assert [(a.get('StartID'),a.get('EndID')) for a in la if a.tag=='Arrow']==[
        (a.get('ID'),b.get('ID')) for a,b in zip(after,list(after)[1:])]
    for f in la:
        if f.tag=='Flow' and f.get('ID')!='1030':
            original=next(x for x in lr if x.tag=='Flow' and x.get('ID')==f.get('ID'))
            assert f.attrib==original.attrib and [(c.tag,c.attrib) for c in f]==[(c.tag,c.attrib) for c in original]
    mapping_path=folder/'步骤编号映射_20261016.json';mapping=json.loads(mapping_path.read_text())
    mapping=[row for row in mapping if row['id'] not in FLOW_IDS[1:]]
    next(row for row in mapping if row['id']==1030)['name']=TITLE
    main=folder/'main.elab';mt=main.read_text()
    mt,n=re.subn(r'Timeout="(\d+)"',lambda m:f'Timeout="{int(m[1])+delta}"',mt,count=1);assert n==1
    proof=dict(status='passed',scope=['A10/A11/A12','A09 last frame','R01 first frame'],
        base_script_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(text.encode()).hexdigest(),
        merged_flow_id=1030,removed_flow_ids=list(FLOW_IDS[1:]),removed_command_ids=sorted(REMOVED_COMMANDS),
        native_block=block,outer_repeats=1,temperature_trace_commands=len(new_trace),
        temperatures_and_hold_times_identical=True,expanded_planned_PCR_rounds=30,
        final_4C_hold_seconds=30,pulse_frame_ms=[1000,1000],pulse_frame_volts=[0,50],
        pulse_covers='pre-denaturation, all PCR rounds, final extension, 4C hold',
        pulse_outside_PCR_block=True,shared_end_ids=[1048,1042],
        old_nominal_seconds=old_nominal,new_nominal_seconds=duration,
        one_second_removed='旧A12开头恢复持续上电帧不再需要；保温时长未减少',
        all_other_sections_identical=True,manual_boundaries_unchanged=True,
        unrelated_layout_coordinates_identical=True,other_labels_not_renumbered=True,
        reference='Ptseq_WY251017-PTseq-final/GR_WY251017/main.elab Flow1132; UnBrokenBlockList',
        device_execution_verified=False,temperature_reached_verified=False,full_workflow_rerun=False)
    path.write_text(text);layout.write_text(lt);main.write_text(mt)
    mapping_path.write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+'\n')
    (folder/'PCR块循环合并局部校验_20261017.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    update(parser.parse_args().folder)
