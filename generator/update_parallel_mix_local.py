"""Publish the active 16-well project's split/dock paths and ten-round mixing timers."""
import csv
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from protocol import STANDARD_MIX_ROUNDS
from update_cross_stage_local import CASES, build
from update_parallel_local import register
from update_workbook_volumes_local import read_csv

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'projects/PTseq_SIRO16_A_10x'
BASE = '6035d416004cf4fd93d10d72209dbce942431797'
REPORT = '就近并行分液与十轮混匀局部校验_20261124.json'
WASTE = {2013,51000,4013,51002,2068,4048,75020}
EXCEPTIONS = {'R09','R14','C17'}
MIX = {'A04','A09','R03','R08','R13','R18','B04','B09','B12','B16','C03','C08','C11','C14'}


def baseline(name):
    return subprocess.check_output(['git','show',f'{BASE}:projects/{PROJECT.name}/{name}'],cwd=ROOT)


def replace_flow(text, fid, xml):
    text, count = re.subn(rf'<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>',lambda m:xml,text,count=1)
    assert count == 1
    return text


def publish(generated):
    script = 'PTseq_16_A.elab'
    text = baseline(script).decode()
    old = {int(f.get('ID')):f for f in ET.fromstring(text)}
    layout = baseline(script+'.layout').decode()
    fresh, tops, waste = generated
    timings, mixes = {}, {}
    for fid, flow in fresh.items():
        out = register(flow,fid,old)
        if fid not in WASTE and fid not in {c[4] for c in CASES}:
            assert ET.tostring(out)==ET.tostring(old[fid])
            continue
        assert flow.timeout < int(old[fid].get('Timeout'))
        assert flow.name == old[fid].get('Name')
        assert [c.attrib | {'Timeout':'*'} for c in out] == [c.attrib | {'Timeout':'*'} for c in old[fid]]
        timings[flow.name.split()[0]] = [int(old[fid].get('Timeout')),flow.timeout]
        text = replace_flow(text,fid,flow.xml().strip())
    previous=json.loads(baseline('跨阶段并行局部校验_20261124.json'))
    def leaf_map(case):
        return sorted((v['name'],v['row'],v['actual_ul'],v['shape'],v['cells']) for v in case['leaves'])
    for prior,current in zip(previous['top_cases'],tops):
        assert prior['reagent']==current['reagent'] and leaf_map(prior)==leaf_map(current)
    for prior,current in zip(previous['waste_cases'],waste):
        assert prior['ids']==current['ids']
        def receipts(case):
            return sorted((e['port'],e['packet_ul']) for e in case['fixed']['admission_events'])
        assert receipts(prior)==receipts(current)
    for c in tops:
        assert all(t['all_reshape_holds_at_least_4000ms'] for t in c['hold_trace'])
        assert c['contact_frame'] > c['all_ready_frame']
    for c in waste:
        assert c['fixed']['full_coverage_and_no_duplicate_codes']
        assert c['fixed']['mass_conserved_each_scene_frame']
    assert sum(c['fixed']['admitted_packets'] for c in waste)==496
    assert sum(c['fixed']['receiver_count'] for c in waste)==31
    assert abs(sum(e['packet_ul'] for c in waste for e in c['fixed']['admission_events'])-356.16)<1e-6
    for fid, original in old.items():
        name = original.get('Name').split()[0]
        timers = [c for c in original if c.get('Component')=='Delay' and c.get('Name').startswith('混匀计时')]
        if not timers or name in EXCEPTIONS:
            continue
        assert name in MIX and len(timers)==1
        timer = timers[0]
        tid = timer.get('ID')
        parent, relation = timer.get('PreTask').split(',')
        hv = original.find(f"Command[@ID='{parent}']")
        assert relation=='SS' and hv.get('Params')=='SCRIPTCYCLE_TRUE'
        assert original.get('SyncEndingCmdList')==f'{tid},{parent}'
        assert hv[0].get('RelayCode')==hv[-1].get('RelayCode')
        ms = sum(int(frame.get('Duration')) for frame in hv)
        assert ms%1000==0 and ms//1000==int(hv.get('Timeout'))
        duration = STANDARD_MIX_ROUNDS * ms//1000
        previous = int(timer.get('Timeout'))
        new_name = f'混匀{STANDARD_MIX_ROUNDS}次 · 计时 {duration}s（同步结束）'
        xml = re.search(rf'<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>',text)[0]
        xml = re.sub(r'Timeout="\d+"',f'Timeout="{int(original.get("Timeout"))+duration-previous}"',xml,count=1)
        cmd_pattern = rf'<Command\b(?=[^>]*\bID="{tid}")[^>]*>'
        def rename(m):return m[0].replace(timer.get('Name'),new_name)
        layout,n = re.subn(cmd_pattern,rename,layout)
        assert n==1
        xml,n = re.subn(cmd_pattern,lambda m:rename(m).replace(f'Timeout="{previous}"',f'Timeout="{duration}"'),xml)
        assert n==1
        text = replace_flow(text,fid,xml)
        mixes[name]=dict(single_round_seconds=ms//1000,old_timer_seconds=previous,new_timer_seconds=duration)
    assert mixes.keys()==MIX
    new = {int(f.get('ID')):f for f in ET.fromstring(text)}
    assert old.keys()==new.keys()
    changed = {f.get('Name').split()[0] for i,f in new.items() if ET.tostring(f)!=ET.tostring(old[i])}
    assert changed==timings.keys()|MIX
    def total(flows):return sum(int(f.get('Timeout'))*int(f.get('Repeats')) for f in flows.values())
    main = re.sub(r'Timeout="\d+"',f'Timeout="{total(new)}"',baseline('main.elab').decode(),count=1)
    transport_saving = sum(before-after for before,after in timings.values())
    mix_saving = sum(v['old_timer_seconds']-v['new_timer_seconds'] for v in mixes.values())
    assert total(old)-total(new)==transport_saving+mix_saving

    headers,rows = read_csv(PROJECT/'手工吸液顺序.csv')
    for row in rows:
        fid=int(row[headers[1]])
        if fid in WASTE:row[headers[4]]=str(len(new[fid][-1]))
    buffer=io.StringIO(newline='')
    writer=csv.DictWriter(buffer,headers)
    writer.writeheader()
    writer.writerows(rows)
    page = baseline('手工加液运行.html').decode()
    matches=list(re.finditer(r'<table\b[^>]*>[\s\S]*?</table>',page))
    assert len(matches)==2
    table='<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr>'
    table+=''.join('<tr>'+''.join('<td>'+escape(row[h])+'</td>' for h in headers)+'</tr>' for row in rows)+'</table>'
    page=page[:matches[1].start()]+table+page[matches[1].end():]
    page=page.replace('<table','<p>当前程序按附近空位并行拆分，前往多个废液孔的液滴可在远端仍汇液时提前接近各自孔位。常规混匀及磁珠重悬均设为10次完整往返。R09、R14保留孵育轻晃，C17清洗两轮。</p><table',1)
    report=dict(baseline=BASE,transport_seconds_saved=transport_saving,mix_seconds_saved=mix_saving,
                old_seconds=total(old),new_seconds=total(new),transport=timings,mixing=mixes,
                exceptions=sorted(EXCEPTIONS),script_sha256=hashlib.sha256(text.encode()).hexdigest(),
                top=[{k:c[k] for k in ('reagent','overlap','cut_dwell','geometry','all_ready_frame','contact_frame')} for c in tops],
                waste=[dict(ids=c['ids'],cuts=c['cut_dwell'],admissions=c['fixed']['admitted_packets'],
                            static_geometry=c['geometry'],cut_gaps=[g['gap'] for g in c['gap_events']]) for c in waste],
                wet_run=False)
    output={script:text.encode(),script+'.layout':layout.encode(),'main.elab':main.encode(),
            '手工加液运行.html':page.encode(),'手工吸液顺序.csv':buffer.getvalue().encode('utf-8-sig'),
            REPORT:(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()}
    for name, data in output.items():
        dest=PROJECT/name
        if name!=REPORT:assert dest.read_bytes()==baseline(name),'本地工程已变化'
        else:assert not dest.exists() or dest.read_bytes()==data
    for name,data in output.items():(PROJECT/name).write_bytes(data)
    print(report['transport'],report['transport_seconds_saved'],report['mix_seconds_saved'],report['new_seconds'])


if __name__=='__main__':
    publish(build(ET.fromstring(baseline('PTseq_16_A.elab')),WASTE))
