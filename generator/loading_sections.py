"""Fold loading-only sections into the operation they prepare in the native TFT project."""
import csv
import hashlib
import io
import json
import re
from copy import deepcopy
from html import escape
from xml.etree import ElementTree as ET

from six_column_schedule import frames, seconds
from update_six_column_local import SCRIPT, retime, xml
from update_parallel_mix_local import replace_flow

# Keep the original A02/B02 labels at the two product-loading entry points.
GROUPS = (
    ('A01 A02 A03','A02'), ('A07 A08','A08'), ('R01 R02','R02'),
    ('R07 R08','R08'), ('R12 R13','R13'), ('R17 R18','R18'),
    ('B01 B02 B03','B02'), ('B06 B07','B07'), ('B10 B11','B11'),
    ('B14 B15','B15'), ('C01 C02','C02'), ('C07 C08','C08'),
    ('C10 C11','C11'), ('C13 C14','C14'), ('C16 C17','C17'),
)
DUPLICATES = {'A01','B01'}
LOAD_TABLE = '手工加液顺序.csv'
SUCTION_TABLE = '手工吸液顺序.csv'
PAGE = '手工加液运行.html'
OVERLAP = '排废离区与进液并行校验.json'


def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()


def manual_tables(data,flows,aliases):
    tables=[]
    by_command={c.get('ID'):(f,i) for f in flows for i,c in enumerate(f)}
    for name in (LOAD_TABLE,SUCTION_TABLE):
        rows=list(csv.reader(io.StringIO(data[name].decode('utf-8-sig'))))
        loading=name==LOAD_TABLE
        if loading:
            rows[0][3]='承接指令'
            rows[0][-3:]=['继续所在文件','继续所在流程组编号','继续所在流程组名称']
            rows[0].append('加好后继续指令')
        for r in rows[1:]:
            cmd=aliases.get(r[3],r[3]) if loading else r[3]
            flow,i=by_command[cmd]
            r[1:4]=[flow.get('ID'),flow.get('Name'),cmd]
            if loading and i+1<len(flow):
                nxt=flow
                continuation=flow[i+1]
            elif flows.index(flow)+1<len(flows):
                nxt=flows[flows.index(flow)+1]
                continuation=nxt[0]
            else:
                assert not loading and not r[-2]
                continue
            r[-3:]=[SCRIPT,nxt.get('ID'),nxt.get('Name')]
            if loading:r.append(continuation.get('ID'))
        buf=io.StringIO(newline='')
        csv.writer(buf).writerows(rows)
        data[name]=buf.getvalue().encode('utf-8-sig')
        html_rows=[]
        for i,r in enumerate(rows):
            tag='td' if i else 'th'
            html_rows.append('<tr>'+''.join(f'<{tag}>{escape(v)}</{tag}>' for v in r)+'</tr>')
        tables.append('<table>'+''.join(html_rows)+'</table>')
    it=iter(tables)
    page,n=re.subn(r'<table>.*?</table>',lambda _:next(it),data[PAGE].decode(),flags=re.S)
    assert n==2
    page=page.replace('承接组结束后手工加液，再运行下一组。','')
    note=('<p>开头在 A02 给 16 个 S 孔加样，并给 E1、E2 加 G4。B02 回片同理。'
          '按组内指令调试时，先执行至表列承接指令，加完该指令对应的各孔后，再执行表列继续指令。'
          '首次进行 R08、R13、R18、C08、C11、C14、C17 的组内加液时，可单独执行首条承接指令，加好后从组首运行整个流程组，以执行原有同步循环。'
          '整组连续运行时会按排程继续，手工加液需由操作员配合。</p>')
    data[PAGE]=page.replace('<table>',note+'<table>',1).encode()


def consolidate(data,columns,overview_name):
    """Transform an already validated waste/inlet schedule; retain native relay payloads."""
    text=data[SCRIPT].decode()
    old=ET.fromstring(text)
    size=retime(old)
    index={f.get('Name').split()[0]:f for f in old}
    diagram=ET.fromstring(data[SCRIPT+'.layout'])
    ui={f.get('ID'):f for f in diagram.findall('Flow')}
    arrows=diagram.findall('Arrow')
    ids=[f.get('ID') for f in old]
    assert [(e.get('StartID'),e.get('EndID')) for e in arrows]==list(zip(ids,ids[1:]))
    for e in arrows:diagram.remove(e)
    aliases={}
    replacements={}
    removed_seconds=0
    for names,key in GROUPS:
        keys=names.split()
        inputs=[index[k] for k in keys]
        start=list(old).index(inputs[0])
        assert list(old)[start:start+len(inputs)]==inputs
        active=[f for k,f in zip(keys,inputs) if k not in DUPLICATES]
        if len(inputs)!=len(active):
            duplicate=inputs[0]
            first=duplicate[0]
            assert len(duplicate)==1 and seconds(duplicate)==1
            assert frames(first)[0]==frames(active[0][0])[0]
            s,initial=frames(first)[0]
            assert len(initial)==16
            assert all(setting==s and initial<=boxes for cmd in active[0] for setting,boxes in frames(cmd))
            # Both receipts can be loaded at the same stationary sample mask.
            aliases[first.get('ID')]=active[0][-1].get('ID')
            removed_seconds+=seconds(duplicate)
        actual=inputs[-1]
        merged=deepcopy(actual)
        merged[:]=[]
        fid=index[key].get('ID')
        merged.set('ID',fid)
        name=actual.get('Name').split(' ',1)[1].replace('加液后分液','加液、分液')
        if key=='A02':name='样本与 G4 承接、分液及融合'
        if key=='B02':name='均一化产物回片、末端修复液承接及融合'
        merged.set('Name',key+' '+name)
        # All prefix sections are ordinary finite electrode commands; loops and
        # synchronisation belong to the final operation and keep their IDs.
        for f in active:
            if f is not actual:
                assert all(c.get('Component')=='HighVoltage' and c.get('Params')=='SCRIPTCYCLE_FALSE' for c in f)
                assert not f.get('UnBrokenBlockList') and not f.get('SyncEndingCmdList')
            for c in f:
                cmd=deepcopy(c)
                if c is f[0] and len(merged):
                    assert cmd.get('PreTask')=='-1,UnTask'
                    cmd.set('PreTask',merged[-1].get('ID')+',FS')
                if f is not actual and '【手工加液・流程组结束】' in cmd.get('Name'):
                    title=cmd.get('Name').replace('【手工加液・流程组结束】','【手工加液】').replace('加好后运行下一流程组','加好后继续本组')
                    if key in ('A02','B02'):title+='；同时承接16道样本' if key=='A02' else '；同时承接16道均一化产物'
                    cmd.set('Name',title)
                merged.append(cmd)
        merged.set('Timeout',str(sum(seconds(f) for f in active)))
        row=deepcopy(ui[fid])
        row.set('Name',merged.get('Name'))
        row.set('Position',ui[inputs[0].get('ID')].get('Position'))
        layout_commands={c.get('ID'):c for f in active for c in ui[f.get('ID')]}
        row[:]=[]
        for i,c in enumerate(merged):
            item=deepcopy(layout_commands[c.get('ID')])
            item.set('Position',f'0,{30*i}')
            item.set('Name',c.get('Name'))
            row.append(item)
        diagram.insert(list(diagram).index(ui[inputs[0].get('ID')]),row)
        for f in reversed(inputs):
            diagram.remove(ui[f.get('ID')])
            text=replace_flow(text,f.get('ID'),xml(merged) if f is inputs[0] else '')
        replacements[key]=keys
    root=ET.fromstring(text)
    ids=[f.get('ID') for f in root]
    nodes={f.get('ID'):f for f in diagram.findall('Flow')}
    assert set(ids)==nodes.keys()
    diagram[:]=[nodes[fid] for fid in ids]
    for a,b in zip(ids,ids[1:]):ET.SubElement(diagram,'Arrow',{'StartID':a,'EndID':b})
    # Prove that flattening every section reproduces every electrode, heater
    # and delay command, excluding exactly the two redundant one-second holds.
    old_cmds=[c for f in old for c in f if c.get('ID') not in aliases]
    new_cmds=[c for f in root for c in f]
    assert len({c.get('ID') for c in new_cmds})==len(new_cmds)
    for a,b in zip(old_cmds,new_cmds,strict=True):
        for k in a.attrib.keys()-{'Name','PreTask'}:assert a.get(k)==b.get(k)
        if a.get('PreTask')!='-1,UnTask':assert a.get('PreTask')==b.get('PreTask')
        assert [r.attrib for r in a]==[r.attrib for r in b]
    after=retime(root)
    assert size-after==removed_seconds==2
    assert frames(root[0][0])[0]==frames(index['A02'][0])[0]
    manual_tables(data,list(root),aliases)
    data[SCRIPT]=text.encode()
    ET.indent(diagram,space='  ')
    data[SCRIPT+'.layout']=ET.tostring(diagram,encoding='utf-8',xml_declaration=True)
    main=data['main.elab'].decode()
    assert main.count(f'Timeout="{size}"')==1
    data['main.elab']=main.replace(f'Timeout="{size}"',f'Timeout="{after}"').encode()
    report=json.loads(data[overview_name])
    detail=json.loads(data[OVERLAP])
    # Preserve the independently measured waste-transport timing and its old
    # within-section clock; B07 now has a one-second manual loading prefix.
    detail['timing_origin']='A06起点；B07按原排废指令3019起点（组内第2秒）'
    detail['before_loading_consolidation_s']=size
    detail['loading_consolidation_saved_s']=2
    detail['published_full_s']=after
    digest=hashlib.sha256(data[SCRIPT]).hexdigest()
    detail['native_sha256']=digest
    data[OVERLAP]=json_bytes(detail)
    if columns==6:
        report.update(full_after_s=after,net_s=report['full_before_s']-after,sections=len(root),native_sha256=digest)
        report['saved_s']['合并重复承接首帧']=2
    else:
        stages=report['stages']
        # Six-column comparator here is the pinned six-column baseline.
        for key,keys in replacements.items():
            sums={k:sum(stages[x][k] for x in keys) for k in ('six','four')}
            for x in keys:del stages[x]
            stages[key]=sums
        report['stages']={f.get('Name').split()[0]:dict(six=stages[f.get('Name').split()[0]]['six'],four=seconds(f)) for f in root}
        report.update(full_s=after,through_recovery_s=report['through_recovery_s']-2)
        report['checks'].update(native_flows=len(root),native_relay_frames=sum(1 for _ in root.iter('RelayItem')))
        for key,previous in [('A02','A03'),('B02','B03')]:
            rails=report['checks']['ingress_tracks']
            rails[key]=rails.pop(previous)
    report['merged_loading_sections']=replacements
    data[overview_name]=json_bytes(report)
    return data
