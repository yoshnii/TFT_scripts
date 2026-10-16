"""Independent exported-electrode audit; does not trust logical droplet IDs.

Electrode components are a geometric proxy, not a measurement of wet droplets.
"""
import collections
import re
import xml.etree.ElementTree as ET

PATTERN=re.compile(r'\((\d+),(\d+)\)\((\d+),(\d+)\)')


def overlap(a,b):
    return (min(a[0]+a[2],b[0]+b[2])>max(a[0],b[0])
            and min(a[1]+a[3],b[1]+b[3])>max(a[1],b[1]))


def components(rectangles):
    parents=list(range(len(rectangles)))
    def find(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    for i,a in enumerate(rectangles):
        for j,b in enumerate(rectangles[:i]):
            x=min(a[0]+a[2],b[0]+b[2])-max(a[0],b[0])
            y=min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1])
            if x>=0 and y>=0 and (x>0 or y>0):parents[find(i)]=find(j)
    groups=collections.defaultdict(list)
    for i,rc in enumerate(rectangles):groups[find(i)].append(rc)
    return list(groups.values())


def inspect_transition(previous,current):
    links={i:[] for i in range(len(previous))}
    for j,group in enumerate(current):
        for i,old in enumerate(previous):
            if any(overlap(a,b) for a in group for b in old):links[i].append(j)
    splits=[]
    for i,targets in links.items():
        if len(targets)<=1:continue
        if any(sum(j in peers for peers in links.values())!=1 for j in targets):continue
        before=sum(a[2]*a[3] for a in previous[i])
        areas=[sum(a[2]*a[3] for a in current[j]) for j in targets]
        if sum(areas)==before:splits.append((before,sorted(areas)))
    return splits


def audit(path):
    previous=[];failures=[];frames=0;transitions=0
    for flow in ET.parse(path).getroot():
        for command in flow:
            if command.get('Component')!='HighVoltage':continue
            for row,relay in enumerate(command,1):
                if float(relay.get('Amp','0'))==0:continue
                rectangles=[(int(x),int(y),int(w),int(h))
                            for y,x,h,w in PATTERN.findall(relay.get('RelayCode',''))]
                current=components(rectangles);frames+=1
                for area,parts in inspect_transition(previous,current):
                    transitions+=1
                    if len(parts)!=2 or parts[0]!=parts[1]:
                        failures.append(dict(section=flow.get('Name'),command=command.get('ID'),
                                             row=row,parent_cells=area,child_component_cells=parts))
                previous=current
    return dict(energized_frames=frames,geometric_splitting_transitions=transitions,
                non_equal_binary_transitions=failures,physical_liquid_verified=False)
