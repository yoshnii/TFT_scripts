"""只读回归：手工排废停靠、孔边对齐、右半区捕获以及原体积/温控保留。"""
import argparse
import csv
from collections import Counter
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def codes(r):
    return Counter(filter(None, r.get('RelayCode', '').split(';')))


def rect(code):
    y,x,h,w = map(int, re.fullmatch(r'\((\d+),(\d+)\)\((\d+),(\d+)\)',code).groups())
    return x,y,w,h


def check(before, after):
    roots = {}; commands = 0
    for name in ('A_cDNA_PCR','R1_purify','B_ligation','C_purify2'):
        old=ET.parse(before/(name+'.elab')).getroot()
        new=ET.parse(after/(name+'.elab')).getroot(); roots[name+'.elab']=new
        old_ids=[c.get('ID') for c in old.findall('.//Command')]
        assert old_ids == [c.get('ID') for c in new.findall('.//Command') if int(c.get('ID'))<60000]
        nflows={f.get('ID'):f for f in new}
        for f in old:
            assert f.get('ID') in nflows and f.get('Repeats')==nflows[f.get('ID')].get('Repeats')
        oc={c.get('ID'):c for c in old.findall('.//Command')}
        for c in new.findall('.//Command'):
            if c.get('Component') != 'HighVoltage':
                assert ET.tostring(c)==ET.tostring(oc[c.get('ID')])
                continue
            commands+=1
            assert sum(int(r.get('Duration')) for r in c)==1000*int(c.get('Timeout'))
            assert all(not r.get('RelayCode') or float(r.get('Amp'))>0 for r in c)
    load_old=list(csv.DictReader((before/'手工加液顺序.csv').open(encoding='utf-8-sig')))
    load_new=list(csv.DictReader((after/'手工加液顺序.csv').open(encoding='utf-8-sig')))
    assert load_old==load_new, '手工加液入口或体积被改变'
    for row in load_new:
        if not row['加液孔'].startswith('E'): continue
        root=roots[row['流程文件']]
        f=next(f for f in root if f.get('ID')==row['加液流程组编号'])
        nxt=next(f for f in root if f.get('ID')==row['下一个流程组编号'])
        assert codes(f[-1][-1])==codes(nxt[0][0])
        if len(f)>1: assert codes(f[-2][-1])==codes(f[-1][0])
    rows=list(csv.DictReader((after/'手工吸液顺序.csv').open(encoding='utf-8-sig')))
    assert len(rows)==9
    totals=Counter(); middle=0
    for row in rows:
        root=roots[row['流程文件']]
        f=next(f for f in root if f.get('ID')==row['吸液流程组编号'])
        nxt=next(f for f in roots[row['下一流程组所在文件']] if f.get('ID')==row['下一个流程组编号'])
        assert f[-1].get('ID')==row['末尾指令'] and len(f[-1])==int(row['末尾帧号'])
        last=f[-1][-1]; dock=row['待吸液电极']; state=codes(last)
        assert state[dock]==1 and float(last.get('Amp'))>0
        after_state=state.copy(); after_state.subtract({dock:1}); after_state=+after_state
        assert codes(nxt[0][0])==after_state, (row['吸液流程组编号'],'除吸走废液外的其他电极不应变化')
        x,y,w,h=rect(dock); hx=54*int(row['吸液孔'][1:])
        assert abs(x+w/2-hx)<=0.5 and x<hx<x+w
        assert y+h==189, '按整格边界停靠F孔上缘，禁止延伸出芯片'
        stage=f.get('Name').split(' · ')[0]
        totals[stage]+=float(row['本次废液µL'])
        if stage.startswith('R04'): middle+=1
        print(f"PASS: {row['流程文件']} 组{f.get('ID')}末帧 → {row['吸液孔']} "
              f"{row['本次废液µL']}µL；吸走后才运行组{nxt.get('ID')}。")
    assert middle==3
    for stage,volume in totals.items():
        expected=24 if stage.startswith('A05') else 75.2 if stage.startswith('R04') else 89.6 if stage.startswith('C04') else 4.8
        assert abs(volume-expected)<0.002, (stage,volume,expected)
    capture=next(f for f in roots['R1_purify.elab'] if f.get('Name').startswith('R02-R03'))
    captured=capture[-1]
    for r in captured:
        rs=[rect(c) for c in codes(r)]
        assert len(rs)==16
        for x,y,w,h in rs:
            assert 28<=x and x+w<=46 and h==6 and y in range(27,163,9)
    print(f'PASS: {commands}高压指令整秒覆盖；原编号、18个加液点、温控/PCR及体积不变；R1全部16道在右半区捕获。')


if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('before',type=Path); ap.add_argument('after',type=Path)
    a=ap.parse_args(); check(a.before,a.after)
