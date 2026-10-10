"""只读回归检查：旧动作顺序不变，整秒调度无空段，顶部手工加液状态连续。"""
import argparse
import csv
from pathlib import Path
import xml.etree.ElementTree as ET


def state(r):
    return (tuple(filter(None, r.get('RelayCode', '').split(';'))), r.get('Amp'),
            r.get('Freq'), r.get('WaveForm'))


def check(before, after, allow_layout_change=False):
    count, padding = 0, 0
    for file in ('A_cDNA_PCR', 'R1_purify', 'B_ligation', 'C_purify2'):
        old = ET.parse(before / (file + '.elab')).getroot()
        new = ET.parse(after / (file + '.elab')).getroot()
        assert [(f.get('ID'), f.get('Repeats'), [c.get('ID') for c in f]) for f in old] == [
            (f.get('ID'), f.get('Repeats'), [c.get('ID') for c in f]) for f in new], file
        oc = {c.get('ID'): c for c in old.findall('.//Command')}
        for c in new.findall('.//Command'):
            prior = oc[c.get('ID')]
            if c.get('Component') != 'HighVoltage':
                assert ET.tostring(prior) == ET.tostring(c), c.get('ID')
                continue
            relays = list(c)
            total = sum(int(r.get('Duration')) for r in relays)
            assert total == int(c.get('Timeout')) * 1000, c.get('ID')
            # 允许物理上无液体的空场景；禁止有电极时出现零电压。
            assert all(not r.get('RelayCode') or float(r.get('Amp')) > 0 for r in relays)
            if not allow_layout_change:
                states = iter(map(state, relays))
                for r in prior:
                    assert any(s == state(r) for s in states), (file, c.get('ID'), r.get('ItemID'))
            count += 1
            padding += (-sum(int(r.get('Duration')) for r in prior)) % 1000
    gates = list(csv.DictReader((after / '手工加液顺序.csv').open(encoding='utf-8-sig')))
    tops = 0
    for row in gates:
        if not row['加液孔'].startswith('E'):
            continue
        root = ET.parse(after / row['流程文件']).getroot()
        load = next(f for f in root if f.get('ID') == row['加液流程组编号'])
        nxt = next(f for f in root if f.get('ID') == row['下一个流程组编号'])
        assert state(load[-1][-1]) == state(nxt[0][0]), row
        if len(load) > 1:
            assert state(load[-2][-1]) == state(load[-1][0]), row
        tops += 1
    print(f'PASS: {count} 高压指令全时段覆盖；{tops} 顶孔手工加液衔接；'
          f'{len(gates)} 加液组及所有原编号/温控目标和时间保持不变；'
          + ('电极布局按本次授权更新。' if allow_layout_change else '旧电极状态顺序保持不变。'))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('before', type=Path)
    ap.add_argument('after', type=Path)
    ap.add_argument('--allow-layout-change', action='store_true', help='允许已授权的电极布局变化；仍检查编号、温控和加液衔接')
    a = ap.parse_args()
    check(a.before, a.after, a.allow_layout_change)
