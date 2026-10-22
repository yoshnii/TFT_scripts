"""Publish symmetric split trajectories without rebuilding native chemistry controls."""
import argparse
import copy
import csv
import hashlib
import io
import json
import pickle
import re
from pathlib import Path
import xml.etree.ElementTree as E

from audit_binary_frames import PATTERN, components, overlap

TARGETS={70000,1002,50000,1022,50001,2000,50002,3002,50003,
         3026,50005,3041,50006,4000,50007,2013,51000,4013,51002,
         50004,2068,4048,75020}
REPORT='双侧同步分液局部校验_20261022.json'


def mask(row):return set(filter(None,row.get('RelayCode','').split(';')))


def bounds(group):
    x=min(q[0] for q in group);y=min(q[1] for q in group)
    return x,y,max(q[0]+q[2] for q in group)-x,max(q[1]+q[3] for q in group)-y


def audit_symmetric(root,ids):
    count=0;frames=0;one_cell_halves=0
    for flow in root:
        if int(flow.get('ID')) not in ids:continue
        previous=[]
        for cmd in flow:
            if cmd.get('Component')!='HighVoltage':continue
            for index,row in enumerate(cmd,1):
                if not row.get('RelayCode'):previous=[];continue
                current=components([(int(x),int(y),int(w),int(h))
                                    for y,x,h,w in PATTERN.findall(row.get('RelayCode'))])
                links=[[j for j,g in enumerate(current)
                        if any(overlap(a,b) for a in group for b in g)] for group in previous]
                # A one-cell-wide child shifted by one cell has no positive
                # overlap with its mother. Match its two exact projected
                # rectangles, rather than silently omitting those G4 cuts.
                current_bounds={bounds(g):sum(q[2]*q[3] for q in g) for g in current}
                for group in previous:
                    x,y,w,h=bounds(group)
                    if w!=2 or sum(q[2]*q[3] for q in group)!=2*h:continue
                    if current_bounds.get((x-1,y,1,h))==h and current_bounds.get((x+2,y,1,h))==h:
                        count+=1;one_cell_halves+=1
                for group,indices in zip(previous,links):
                    if len(indices)!=2 or any(sum(j in q for q in links)!=1 for j in indices):continue
                    area=sum(q[2]*q[3] for q in group)
                    children=[current[j] for j in indices]
                    areas=[sum(q[2]*q[3] for q in g) for g in children]
                    if sum(areas)!=area:continue
                    assert areas[0]==areas[1],('unequal native split',flow.get('Name'),index,areas)
                    x,y,w,h=bounds(group);a,b=sorted(map(bounds,children))
                    assert w*h==area and w%2==0
                    assert a==(x-1,y,w//2,h) and b==(x+w//2+1,y,w//2,h),(
                        'split is not simultaneous left/right',flow.get('Name'),index,(x,y,w,h),a,b)
                    count+=1
                previous=current;frames+=1
    return dict(frames=frames,symmetric_horizontal_splits=count,
                one_cell_wide_half_splits=one_cell_halves,
                unequal_or_one_sided_splits=0,physical_liquid_verified=False)


def build(folder,cache):
    path=folder/'PTseq_16_A.elab';text=path.read_text();before=E.fromstring(text)
    baseline,(models,_,cases)=pickle.loads(cache.read_bytes())
    assert baseline==hashlib.sha256(path.read_bytes()).hexdigest(),'Generated model baseline changed'
    old={int(f.get('ID')):f for f in before};newtext=text;changes=[]
    for fid in sorted(TARGETS):
        native=old[fid];fresh=E.fromstring(models[fid].xml())
        assert len(native)==len(fresh)
        replacement=copy.deepcopy(native)
        for a,b,node in zip(native,fresh,replacement):
            assert a.get('ID')==b.get('ID') and a.get('Component')==b.get('Component')=='HighVoltage'
            assert mask(a[0])==mask(b[0]) and mask(a[-1])==mask(b[-1]),('boundary changed',fid,a.get('ID'))
            node[:]=[copy.deepcopy(r) for r in b]
            node.set('Timeout',b.get('Timeout'))
            assert int(node.get('Timeout'))*1000==sum(int(r.get('Duration')) for r in node)
        replacement.set('Timeout',str(sum(int(c.get('Timeout')) for c in replacement)))
        pattern=rf'<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>'
        newtext,n=re.subn(pattern,lambda _:E.tostring(replacement,encoding='unicode').strip(),newtext,count=1)
        assert n==1
        if E.tostring(native)!=E.tostring(replacement):
            changes.append(dict(id=fid,section=native.get('Name').split()[0],
                before_seconds=int(native.get('Timeout')),after_seconds=int(replacement.get('Timeout'))))
    after=E.fromstring(newtext)
    assert len(before)==len(after)
    for a,b in zip(before,after):
        if int(a.get('ID')) not in TARGETS:assert E.tostring(a)==E.tostring(b)
    result=audit_symmetric(after,{int(f.get('ID')) for f in after})
    assert result['symmetric_horizontal_splits']==sum(c['equal_x_splits'] for c in cases)
    # Heater/Delay parameters, block cycles and all native non-motion sections
    # are excluded from TARGETS, so they retain their original bytes/content.
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1)
    assert n==1
    reader=csv.DictReader(io.StringIO((folder/'手工吸液顺序.csv').read_text(encoding='utf-8-sig')))
    headers=reader.fieldnames;rows=list(reader);page=(folder/'手工加液运行.html').read_text()
    mapping={int(f.get('ID')):f for f in after}
    for row in rows:
        fid=int(row[headers[1]])
        if fid not in TARGETS:continue
        cmd=next(c for c in mapping[fid] if c.get('ID')==row[headers[3]])
        row[headers[4]]=str(len(cmd))
        pattern=rf'(<tr><td>PTseq_16_A\.elab</td><td>{fid}</td><td>[^<]*</td><td>{cmd.get("ID")}</td><td>)\d+(</td>)'
        page,n=re.subn(pattern,lambda m:m[1]+str(len(cmd))+m[2],page)
        assert n==1,('missing operator HTML row',fid)
    stream=io.StringIO(newline='');w=csv.DictWriter(stream,headers);w.writeheader();w.writerows(rows)
    report=dict(status='passed',baseline_sha256=baseline,
        script_sha256=hashlib.sha256(newtext.encode()).hexdigest(),changes=changes,cases=cases,
        exported_frame_audit=result,unrelated_sections_unchanged=True,
        temperature_mix_timers_and_PCR_unchanged=True,actual_volumes_unchanged=True,
        recovery_endpoints_unchanged=True,GUI_checked=False,hardware_verified=False)
    return {path:newtext,folder/'main.elab':mt,
            folder/'手工吸液顺序.csv':stream.getvalue(),folder/'手工加液运行.html':page,
            folder/REPORT:json.dumps(report,ensure_ascii=False,indent=2)+'\n'},report


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path)
    ap.add_argument('cache',type=Path);ap.add_argument('--write',action='store_true');args=ap.parse_args()
    files,report=build(args.folder,args.cache)
    if args.write:
        for path,text in files.items():
            path.write_text(text,encoding='utf-8-sig' if path.suffix=='.csv' else 'utf-8',newline='')
    print(json.dumps(report,ensure_ascii=False,indent=2))
