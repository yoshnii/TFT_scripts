"""Read-only program scan using the existing bottom-motion audit and thresholds."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as E
from audit_bottom_motion import rectangles,audit


def scan(path):
    root=E.parse(path).getroot()
    labels={f.get('Name').split()[0] for f in root
        if any(r[1]>=172 and r[2]*r[3]>16 and max(r[2:])/min(r[2:])>1.5
               for c in f if c.get('Component')=='HighVoltage' for relay in c for r in rectangles(relay))}
    result=audit(root,labels)
    groups=collections.defaultdict(list)
    for issue in result['violations']:
        if issue['reason']!='long_edge_facing_transport':continue
        groups[(issue['section'],issue['command'],tuple(issue['before'][2:]))].append(issue)
    summary=[dict(section=section,command=cmd,shape=list(shape),matched_transitions=len(items),
                  first_row=items[0]['row'],last_row=items[-1]['row'],first_before=items[0]['before'],
                  first_after=items[0]['after']) for (section,cmd,shape),items in groups.items()]
    plans=[dict(section=label,modified=False,
        proposal='保留4×4小滴原形输送，到各自最终F孔位置再固定汇液；取消4×8及8×16合块横移',
        preserve='现有实际液量、比例、吸液孔与吸液批次不变',
        approval_needed='若完整矩形不能逐块自然拼接，另报固定孔旁台阶轮廓方案；B07例外不自动扩展')
        for label in dict.fromkeys(item['section'] for item in summary)]
    return dict(script_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        added_rule='long edge leads the motion: H>W with X translation, or W>H with Y translation',
        existing_thresholds_unchanged=dict(bottom_y=172,area_greater_than=16,aspect_ratio_greater_than=1.5),
        existing_frame_matching_unchanged=True,summary=summary,unexecuted_plans=plans,audit=result,
        matched_transition_counts_are_not_complete_physical_motion_counts=True,
        other_identified_steps_modified=False,wet_run_verified=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('script',type=Path);parser.add_argument('--out',type=Path)
    args=parser.parse_args();report=scan(args.script)
    if args.out:args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='audit'},ensure_ascii=False))
