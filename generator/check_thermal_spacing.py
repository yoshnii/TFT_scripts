"""本次温控居中和3/4格间距更新的只读回归检查。"""
import argparse
import csv
import re
from pathlib import Path
import xml.etree.ElementTree as ET
import geom


def rects(relay):
    return [(int(x), int(y), int(w), int(h)) for y,x,h,w in
            re.findall(r'\((\d+),(\d+)\)\((\d+),(\d+)\)', relay.get('RelayCode', ''))]


def check(before, after):
    thermal = 0
    for name in ('A_cDNA_PCR', 'R1_purify', 'B_ligation', 'C_purify2'):
        old = ET.parse(before/(name+'.elab')).getroot()
        new = ET.parse(after/(name+'.elab')).getroot()
        assert [c.get('ID') for c in old.findall('.//Command')] == [c.get('ID') for c in new.findall('.//Command')]
        for f in new:
            on = [c for c in f if c.get('Action') == 'CMD_TEMP_ON']
            if not on: continue
            thermal += 1
            hv = [c for c in f if c.get('Component') == 'HighVoltage']
            assert len(hv) == 1, f.get('Name')
            hv = hv[0]
            assert hv.get('Params') == 'SCRIPTCYCLE_TRUE'
            required = sum(int(c.get('Timeout')) for c in f if c.get('Component') != 'HighVoltage')
            assert int(hv.get('Timeout')) == required, (f.get('Name'), required)
            assert sum(int(r.get('Duration')) for r in hv) == required*1000
            states = [rects(r) for r in hv]
            assert all(s == states[0] for s in states), '温控保持循环禁止夹带移动'
            assert len(states[0]) == 16
            for x,y,w,h in states[0]:
                assert h == 6 and y in range(27,163,9)
                assert 10 <= x and x+w <= 28
                assert abs((x+w/2)-19) <= 0.5, (f.get('Name'),x,w)
            print(f"PASS: {f.get('Name')}：温度 " + '/'.join(c.get('Params').split(',')[0] for c in on)
                  + f"；循环保持 {required}s；16道居中。")
    assert thermal == 6, thermal
    # 加液量和顺序不改变；只允许增大接液/运动电极间距。
    with (before/'手工加液顺序.csv').open(encoding='utf-8-sig') as a, (after/'手工加液顺序.csv').open(encoding='utf-8-sig') as b:
        assert list(csv.DictReader(a)) == list(csv.DictReader(b))
    assert geom.MIN_GAP == 3 and geom.PREFERRED_GAP == 4 and geom.CUT_GAP == 4
    print('PASS: 18个手工加液入口的体积、顺序和原流程组编号不变；反应区3格、顶部切分4格。')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('before', type=Path)
    ap.add_argument('after', type=Path)
    a = ap.parse_args()
    check(a.before, a.after)
