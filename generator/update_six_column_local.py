"""Publish and verify the scoped six-column scheduling change against its native main baseline."""
import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from project_structure import prepare_scripts
from six_column_schedule import frames, seconds, transfer, waste
from update_fixed_waste_local import FixedAudit
from update_parallel_local import register
from update_parallel_mix_local import replace_flow
from update_pcr_block_local import model_flow
from update_ready_parallel_local import check_dwell
from update_transport_cleanup_local import last_hv, seed

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
BASE='d20d41d1eebc349bef67bec993979004e4e4f8a9'
SCRIPT='PTseq_16_A.elab'
FEEDS=('A05 A06 A07 A08','B08 B09 B10 B11','B15 B16 C01 C02')
REPORT='六列排废与跨步骤并行校验.json'


def baseline(name):
    return subprocess.check_output(['git','show',f'{BASE}:projects/{PROJECT.name}/{name}'],cwd=ROOT)


def retime(root):
    total=0
    for flow in root:
        # model_flow predates multiple sync groups. Their memberships do not
        # change the dependency graph used for nominal wall time.
        timing_node=ET.Element(flow.tag,flow.attrib)
        timing_node.extend(flow)
        timing_node.set('SyncEndingCmdList',flow.get('SyncEndingCmdList','').replace(';',','))
        actual=model_flow(timing_node).timeout
        assert actual==seconds(flow),(flow.get('Name'),actual,seconds(flow))
        for cmd in flow:
            if cmd.get('Component')=='HighVoltage':
                ms=sum(int(r.get('Duration')) for r in cmd)
                assert (ms+999)//1000==seconds(cmd),(cmd.get('ID'),ms)
        total+=actual*int(flow.get('Repeats'))
    return total


def metered_c(flows):
    old={int(f.get('ID')):f for f in flows}
    start=old[4013]
    p=seed(last_hv(flows[flows.index(start)-1]),[('',6.16)])
    p.S.flush()
    p.magnet_state='up'
    audit=FixedAudit(p,98.56)
    p.supernatant_off('C',start.get('Name').split(' · ')[0])
    proof=audit.report()
    assert proof['admitted_packets']==128 and proof['mass_conserved_each_scene_frame']
    cuts=check_dwell(p,audit.trace)
    generated=prepare_scripts(p.segs,'ChipArea_192_256_96_SIRO',1)[0][0].flows
    assert len(generated)==2
    flows_out=[register(f,i,old) for f,i in zip(generated,(4013,51002))]
    for f in flows_out:
        # Native final manual suction/withdrawal motion has identical dwell and geometry.
        assert frames(f[-1])[-24:]==frames(old[int(f.get('ID'))][-1])[-24:]
    return flows_out,cuts


def xml(node):
    node=deepcopy(node)
    node.tail=None
    ET.indent(node,space='  ')
    return ET.tostring(node,encoding='unicode')


def update_layout(flow,layout_flow):
    references={c.get('ID'):c for c in layout_flow}
    hv_template=next(c for c in layout_flow if c.get('SequenceTypeId')=='0')
    layout_flow.set('Name',flow.get('Name'))
    for i,c in enumerate(flow):
        cid=c.get('ID')
        if cid not in references:
            assert c.get('Component')=='HighVoltage'
            item=deepcopy(hv_template)
            item.set('ID',cid)
            item.set('Position',f'0,{30*i}')
            layout_flow.append(item)
        else:
            item=references[cid]
        item.set('Name',c.get('Name'))
    assert [c.get('ID') for c in flow]==[c.get('ID') for c in layout_flow]


def refresh_manual(flows,load_moves):
    byid={f.get('ID'):f for f in flows}
    output={}
    tables=[]
    for filename in ('手工加液顺序.csv','手工吸液顺序.csv'):
        data=list(csv.reader(io.StringIO(baseline(filename).decode('utf-8-sig'))))
        loading=filename.startswith('手工加')
        for row in data[1:]:
            fid=load_moves.get(row[1],row[1]) if loading else row[1]
            f=byid[fid]
            row[1:4]=[fid,f.get('Name'),f[-1].get('ID')]
            if not loading:
                row[4]=str(len(f[-1]))
            if row[-2]:
                nxt=flows[flows.index(f)+1]
                row[-2:]=[nxt.get('ID'),nxt.get('Name')]
        buf=io.StringIO(newline='')
        csv.writer(buf).writerows(data)
        output[filename]=buf.getvalue().encode('utf-8-sig')
        rows=['<tr>'+''.join(f'<{tag}>{escape(value)}</{tag}>' for value in row)+'</tr>'
              for i,row in enumerate(data) for tag in [('th' if i==0 else 'td')]]
        tables.append('<table>'+''.join(rows)+'</table>')
    html=baseline('手工加液运行.html').decode()
    table_iter=iter(tables)
    assert html.count('<table>')==2
    html=re.sub(r'<table>.*?</table>',lambda _:next(table_iter),html,flags=re.S)
    output['手工加液运行.html']=html.encode()
    return output


def build():
    text=baseline(SCRIPT).decode()
    layout=baseline(SCRIPT+'.layout').decode()
    original=ET.fromstring(text)
    before=retime(original)
    flows={f.get('Name').split()[0]:deepcopy(f) for f in original}
    old_flows={f.get('ID'):f for f in original}
    layout_flows={f.get('ID'):f for f in ET.fromstring(layout)}
    used={e.get('ID') for e in original.iter() if e.get('ID')}
    assert not used.intersection(str(i) for i in range(78000,78022))
    load_moves={}
    saved={}
    for i,names in enumerate(FEEDS):
        load,work,bridge,delivery=[flows[k] for k in names.split()]
        saved[names]=transfer(load,work,bridge,delivery,78000+10*i)
        load_moves[bridge.get('ID')]=load.get('ID')
    metered,cuts=metered_c(list(original))
    for f in metered:
        flows[f.get('Name').split()[0]]=f
    for a,b in (('R05','R06'),('C05','C06')):
        waste(flows[a],flows[b])
        saved[a+' '+b]=sum(seconds(old_flows[f.get('ID')])-seconds(f) for f in (flows[a],flows[b]))
    for f in flows.values():
        fid=f.get('ID')
        if ET.tostring(f)==ET.tostring(old_flows[fid]):continue
        text=replace_flow(text,fid,xml(f))
        layout_flow=layout_flows[fid]
        prev=ET.tostring(layout_flow)
        update_layout(f,layout_flow)
        if ET.tostring(layout_flow)!=prev:layout=replace_flow(layout,fid,xml(layout_flow))
    result=ET.fromstring(text)
    after=retime(result)
    assert before-after==sum(saved.values())
    assert [f.get('ID') for f in original]==[f.get('ID') for f in result]
    main=baseline('main.elab').decode()
    assert ET.fromstring(main)[0].get('Include')==SCRIPT and str(before)==ET.fromstring(main)[0].get('Timeout')
    main=main.replace(f'Timeout="{before}"',f'Timeout="{after}"')
    report=dict(baseline=BASE,sections=len(result),full_before_s=before,full_after_s=after,
                saved_s=saved,net_s=before-after,predicted_s=706,captured_c_cuts=cuts,
                native_sha256=hashlib.sha256(text.encode()).hexdigest(),instrument_run=False)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return {SCRIPT:text.encode(),SCRIPT+'.layout':layout.encode(),'main.elab':main.encode(),
            REPORT:(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode(),
            **refresh_manual(list(result),load_moves)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    opt=parser.parse_args()
    out=build()
    for name,data in out.items():
        path=PROJECT/name
        if opt.write and name!=REPORT:
            assert path.read_bytes() in (baseline(name),data),'人工修改冲突：'+name
        elif not opt.write:
            assert path.read_bytes()==data,name
    if opt.write:
        for name,data in out.items():(PROJECT/name).write_bytes(data)
