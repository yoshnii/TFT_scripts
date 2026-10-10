"""紧凑接液回归：只允许7处边缘孔加液指令改变；体积与其他动作保持。"""
import argparse, csv, json, re
from pathlib import Path
import xml.etree.ElementTree as ET
import geom


def check(before, after):
    old=ET.parse(before/'A_cDNA_PCR.elab').getroot()
    main=ET.parse(after/'main.elab').getroot()
    assert len(main)==1
    workflow_file=main[0].get('Include')
    new=ET.parse(after/workflow_file).getroot()
    old_cmds={c.get('ID'):c for c in old.iter('Command')}
    new_cmds={c.get('ID'):c for c in new.iter('Command')}
    assert old_cmds.keys()==new_cmds.keys(), '既有指令编号发生变化'
    for a,b in zip(old,new):
        assert {k:v for k,v in a.attrib.items() if k!='Timeout'}=={
            k:v for k,v in b.attrib.items() if k!='Timeout'}, '流程结构/循环/同步改变'
    assert len(old)==len(new)==59
    allowed={str(i+j) for i in (1003,1022,2001,3003,3022,3036,4001) for j in range(3)}
    changed=[]
    for cid,c in new_cmds.items():
        a=old_cmds[cid];a.tail=c.tail=None
        if ET.tostring(a)!=ET.tostring(c):
            assert cid in allowed and c.get('Component')=='HighVoltage', ('超出接液范围修改',cid)
            for k in c.attrib:
                if k!='Timeout':assert c.get(k)==a.get(k), (cid,k)
            changed.append(int(cid))
    for filename in ('手工加液顺序.csv','手工吸液顺序.csv'):
        def rows(folder):
            with (folder/filename).open(encoding='utf-8-sig',newline='') as fh:
                return list(csv.DictReader(fh))
        a,b=rows(before),rows(after)
        assert len(a)==len(b)
        for x,y in zip(a,b):
            ignore={'末尾帧号','流程文件','下一流程组所在文件'}
            assert {k:v for k,v in x.items() if k not in ignore}=={
                k:v for k,v in y.items() if k not in ignore}, '手工停靠/体积变化'
            assert y['流程文件']==y['下一流程组所在文件']==workflow_file
    roots=[]
    pattern=re.compile(r'\((\d+),(\d+)\)\((\d+),(\d+)\)')
    with (after/'手工加液顺序.csv').open(encoding='utf-8-sig',newline='') as fh:
        rows=list(csv.DictReader(fh))
    for row in rows:
        if not row['加液孔'].startswith('E'):continue
        c=new_cmds[row['最后承接指令']]
        rects=[tuple(map(int,m)) for m in pattern.findall(c[0].get('RelayCode'))]
        receivers=[(x,y,w,h) for y,x,h,w in rects if y==geom.E_TANG_Y]
        ports=row['加液孔'].split('/')
        assert len(receivers)==len(ports)
        port_volume=0
        for (x,y,w,h),port in zip(receivers,ports):
            assert max(w,h)/min(w,h)<=2 and 8<=h<=18, (port,'接液框不紧凑',w,h)
            assert x+w/2==geom.HOLE_X[int(port[1:])], '孔不在接液框边中间'
            ul=round(w*h*geom.CELL_UL,2);port_volume+=ul
            roots.append(dict(command=int(c.get('ID')),port=port,shape_cells=[w,h],ul=ul))
        assert abs(port_volume-float(row['本次µL（S/B孔为每道）']))<1e-6
        nxt=next(f for f in new if f.get('ID')==row['下一个流程组编号'])
        assert c[0].get('RelayCode')==nxt[0][0].get('RelayCode'), '手工接液与续行首帧不连续'
    report=dict(status='compact_loading_pass',sections=59,commands=len(new_cmds),
                relay_items=sum(len(c) for c in new_cmds.values()),
                changed_commands=changed,compact_receivers=roots,
                manual_load_sections=14,manual_aspiration_sections=13,
                temperature_and_cycles_unchanged=True,other_commands_unchanged=True,
                gui_verified=False,physical_run_performed=False)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('before',type=Path);ap.add_argument('after',type=Path)
    ap.add_argument('--report',type=Path);args=ap.parse_args()
    result=check(args.before,args.after)
    if args.report:args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
