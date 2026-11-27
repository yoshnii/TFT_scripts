"""Publish or check well-exit scheduling against the released 16-sample project."""
import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
from tempfile import TemporaryDirectory
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

from overlap_waste_inlet import barcode, cdna, ordinary
from six_column_schedule import seconds
from update_parallel_mix_local import replace_flow
from update_six_column_local import SCRIPT, retime, update_layout, xml

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = {6: ('PTseq_SIRO16_A_10x', '839e0f825e9a21fe04491be0675dd39ecf5c3899'),
            4: ('POCS_4col_16_10x', 'b9e15e6f053af4258b00035f481084f342e063f9')}
MANUAL = ('手工加液顺序.csv', '手工吸液顺序.csv')
REPORT = '排废离区与进液并行校验.json'
INPUTS = (SCRIPT, SCRIPT+'.layout', 'main.elab', *MANUAL, '手工加液运行.html')


def refresh_manual(files, stages):
    index = {flow.get('ID'): i for i, flow in enumerate(stages)}
    output = {}
    tables = []
    for name in MANUAL:
        rows = list(csv.reader(io.StringIO(files[name].decode('utf-8-sig'))))
        for row in rows[1:]:
            if name == MANUAL[0] and row[5] == 'LIG':
                assert row[1] == '51001'
                row[1] = '3017'
            i = index[row[1]]
            flow = stages[i]
            row[2:4] = [flow.get('Name'),flow[-1].get('ID')]
            if name == MANUAL[1]:
                row[4] = str(len(flow[-1]))
            if row[-2]:
                nxt = stages[i+1]
                row[-2:] = [nxt.get('ID'),nxt.get('Name')]
        stream = io.StringIO(newline='')
        csv.writer(stream).writerows(rows)
        output[name] = stream.getvalue().encode('utf-8-sig')
        html_rows = []
        for i,row in enumerate(rows):
            tag = 'th' if i == 0 else 'td'
            html_rows.append('<tr>'+''.join(f'<{tag}>{escape(cell)}</{tag}>' for cell in row)+'</tr>')
        tables.append('<table>'+''.join(html_rows)+'</table>')
    table_iter = iter(tables)
    page, count = re.subn(r'<table>.*?</table>', lambda _:next(table_iter),
                          files['手工加液运行.html'].decode(), flags=re.S)
    assert count == 2
    output['手工加液运行.html'] = page.encode()
    return output


def check_footprints(flow):
    for cmd in flow:
        if cmd.get('Component') != 'HighVoltage':
            continue
        for _, rects in set(ordinary([cmd])):
            occupied = {}
            for x,y,w,h in rects:
                assert x>=0 and y>=0 and w>0 and h>0 and x+w<=256 and y+h<=192
                mask = ((1<<w)-1)<<x
                for line in range(y,y+h):
                    assert not occupied.get(line,0)&mask, (cmd.get('ID'),x,y)
                    occupied[line] = occupied.get(line,0)|mask


def transform(files, columns, old_report):
    text = files[SCRIPT].decode()
    layout = files[SCRIPT+'.layout'].decode()
    root = ET.fromstring(text)
    before = retime(root)
    stages = {f.get('Name').split()[0]:f for f in root}
    ui = {f.get('ID'):f for f in ET.fromstring(layout).findall('Flow')}
    old_xml = {k:ET.tostring(f) for k,f in stages.items()}
    old_times = {k:seconds(f) for k,f in stages.items()}
    left = {6:10,4:22}[columns]
    overlap = old_report['saved_s' if columns==6 else 'overlap_s']
    a = cdna(stages,left,overlap['A05 A06 A07 A08'])
    b = barcode(stages,left,overlap['B08 B09 B10 B11'])
    for k,f in stages.items():
        if old_xml[k] == ET.tostring(f):
            continue
        assert k in 'A06 A07 A08 B06 B07 B08 B09 B10 B11'.split()
        check_footprints(f)
        fid = f.get('ID')
        text = replace_flow(text,fid,xml(f))
        diagram = ui[fid]
        original = ET.tostring(diagram)
        update_layout(f,diagram)
        if ET.tostring(diagram) != original:
            layout = replace_flow(layout,fid,xml(diagram))
    serialized = ET.fromstring(text)
    after = retime(serialized)
    assert after < before
    gui = {f.get('ID'):f for f in ET.fromstring(layout).findall('Flow')}
    for flow in serialized:
        item = gui[flow.get('ID')]
        assert flow.get('Name') == item.get('Name')
        assert [(c.get('ID'),c.get('Name')) for c in flow]==[(c.get('ID'),c.get('Name')) for c in item]
    main,count = re.subn(f'Timeout="{before}"', f'Timeout="{after}"', files['main.elab'].decode())
    assert count==1 and ET.fromstring(main)[0].get('Include')==SCRIPT
    output = {SCRIPT:text.encode(), SCRIPT+'.layout':layout.encode(),'main.elab':main.encode(),
              **refresh_manual(files,list(serialized))}
    report = dict(source_revision=PROJECTS[columns][1], columns=columns, samples=16,
                  before_s=before, after_s=after, saved_s=before-after,
                  stage_savings_s={k:v-seconds(stages[k]) for k,v in old_times.items() if v!=seconds(stages[k])},
                  A06=a,B07=b, hardware_run=False,
                  native_sha256=hashlib.sha256(output[SCRIPT]).hexdigest())
    output[REPORT] = (json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()
    return output


def historic_name(columns):
    return '六列排废与跨步骤并行校验.json' if columns==6 else '四列排程校验.json'


def current_overview(columns, report, result, folder):
    """Update the project-wide timing index using the serialized delivery."""
    detail=json.loads(result[REPORT])
    current=ET.fromstring(result[SCRIPT])
    if columns==6:
        report['full_after_s']=detail['after_s']
        report['net_s']=report['full_before_s']-detail['after_s']
        report['saved_s']['A05 A06 A07 A08']+=detail['stage_savings_s']['A08']
        report['saved_s']['B08 B09 B10 B11']+=detail['saved_s']-detail['stage_savings_s']['A08']
        assert sum(report['saved_s'].values())==report['net_s']
        report['native_sha256']=detail['native_sha256']
    else:
        from build_four_column import board, verify, BASELINE, MODEL, source_blob
        board.configure()
        release=ET.fromstring(source_blob(BASELINE,SCRIPT))
        with TemporaryDirectory() as scratch:
            target=Path(scratch)
            for name in (MODEL+'.csv', PROJECTS[4][0]+'.elabprj'):
                (target/name).write_bytes((folder/name).read_bytes())
            (target/'main.elab').write_bytes(result['main.elab'])
            report['checks']=verify(target,release,current)
        report['full_s']=detail['after_s']
        subtotal=0
        for flow in current:
            step=flow.get('Name').split()[0]
            report['stages'][step]['four']=seconds(flow)
            subtotal+=seconds(flow)
            if step=='C12':report['through_recovery_s']=subtotal
        assert subtotal==report['full_s']
    report['additional_well_exit_overlap_s']=detail['saved_s']
    return (json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('columns', type=int, choices=PROJECTS)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    name, revision = PROJECTS[args.columns]
    folder = ROOT/'projects'/name
    def source(filename):
        return subprocess.check_output(['git','show',f'{revision}:projects/{name}/{filename}'],cwd=ROOT)
    files={key:source(key) for key in INPUTS}
    overview=historic_name(args.columns)
    files[overview]=source(overview)
    original_report=json.loads(files[overview])
    result=transform(files,args.columns,original_report)
    result[overview]=current_overview(args.columns,original_report,result,folder)
    from loading_sections import consolidate
    prior=dict(result)
    consolidate(result,args.columns,overview)
    folded=dict(result)
    if args.columns==4:
        from dual_outlet import update as update_outlet
        update_outlet(result,overview)
    for key,data in result.items():
        p=folder/key
        if args.write:
            if p.exists() and p.read_bytes() not in (data,prior.get(key),folded.get(key)) and (key not in files or p.read_bytes()!=files[key]):
                raise ValueError('工程文件有其他改动：'+str(p))
        elif p.read_bytes()!=data:
            raise ValueError('工程文件与校验结果不一致：'+str(p))
    if args.write:
        for key,data in result.items():
            (folder/key).write_bytes(data)
    print(result[REPORT].decode())


if __name__=='__main__':
    main()
