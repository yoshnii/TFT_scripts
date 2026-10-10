"""层级调整回归：原有流程 XML 完全相同，只改变所属文件及工程引用。"""
import argparse, csv, json
from pathlib import Path
import xml.etree.ElementTree as ET


def ordered_flows(folder):
    main = ET.parse(folder/'main.elab').getroot()
    return [flow for include in main
            for flow in ET.parse(folder/include.get('Include')).getroot()]


def check(before, after):
    old, new = ordered_flows(before), ordered_flows(after)
    assert len(old) == len(new)
    for a, b in zip(old, new):
        # 文件末尾与文件中间的缩进可能不同，不属于 Flow 内容。
        a.tail = b.tail = None
        assert ET.tostring(a) == ET.tostring(b), (a.get('ID'), '流程内容发生改变')
    ids = [x.get('ID') for f in new for x in [f, *f]]
    assert len(ids) == len(set(ids))
    main = ET.parse(after/'main.elab').getroot()
    assert len(main) == 1
    workflow_file=main[0].get('Include')
    script_name=workflow_file.removesuffix('.elab')
    project = ET.parse(next(after.glob('*.elabprj'))).getroot()
    assert [f.get('include') for f in project] == ['main', script_name]
    assert {p.name for p in after.glob('*.elab')} == {'main.elab', workflow_file}
    for filename in ['手工加液顺序.csv', '手工吸液顺序.csv']:
        def rows(folder):
            with (folder/filename).open(encoding='utf-8-sig', newline='') as fh:
                return list(csv.DictReader(fh))
        old_rows, new_rows = rows(before), rows(after)
        assert len(old_rows) == len(new_rows)
        for a, b in zip(old_rows, new_rows):
            for key in a:
                if key in ('流程文件', '下一流程组所在文件'):
                    assert b[key] == workflow_file
                else:
                    assert a[key] == b[key], (filename, key)
    layout = ET.parse(after/(workflow_file+'.layout')).getroot()
    assert [f.get('ID') for f in layout if f.tag=='Flow'] == [f.get('ID') for f in new]
    assert [(a.get('StartID'), a.get('EndID')) for a in layout if a.tag=='Arrow'] == [
        (a.get('ID'), b.get('ID')) for a, b in zip(new, new[1:])]
    report = dict(status='hierarchy_only_pass',
                  scripts=['main', script_name], sections=len(new),
                  commands=sum(len(f) for f in new),
                  relay_items=sum(len(c) for f in new for c in f),
                  all_flow_xml_identical=True, manual_handoffs_unchanged=True,
                  runtime_verified=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('before', type=Path); ap.add_argument('after', type=Path)
    ap.add_argument('--report', type=Path)
    args = ap.parse_args()
    report = check(args.before, args.after)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
