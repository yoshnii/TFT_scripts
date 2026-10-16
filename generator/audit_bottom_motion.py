"""Audit exported electrode components, independent of droplet object names."""
from audit_binary_frames import PATTERN,components,inspect_transition


def rectangles(relay):
    result=[]
    for group in components([(int(x),int(y),int(w),int(h))
                             for y,x,h,w in PATTERN.findall(relay.get('RelayCode',''))]):
        x=min(r[0] for r in group);y=min(r[1] for r in group)
        w=max(r[0]+r[2] for r in group)-x;h=max(r[1]+r[3] for r in group)-y
        if sum(r[2]*r[3] for r in group)==w*h:
            result.append((x,y,w,h))
    return result


def violations(previous,current,allow_long_axis=False):
    bad=[]
    for a in previous:
        if a[1]<172 or a[2]*a[3]<=16 or max(a[2:])/min(a[2:])<=1.5:continue
        for b in current:
            if a[2:]!=b[2:]:continue
            dx=b[0]-a[0];dy=b[1]-a[1]
            # An overlap links a moving rectangle between consecutive frames.
            if not (abs(dx)<a[2] and abs(dy)<a[3]) or not (dx or dy):continue
            if (a[2]>a[3] and dx) or (a[3]>a[2] and dy):
                if not allow_long_axis:
                    bad.append(dict(before=a,after=b,reason='long_axis_transport'))
                elif (a[2]>a[3] and dy) or (a[3]>a[2] and dx):
                    bad.append(dict(before=a,after=b,reason='long_edge_facing_transport'))
                elif abs(dx)+abs(dy)>1:
                    bad.append(dict(before=a,after=b,reason='multi_cell_translation'))
            elif (a[3]>a[2] and dx) or (a[2]>a[3] and dy):
                # A long vertical edge moving horizontally (and vice versa):
                # the whole long edge is leading the motion, also forbidden.
                bad.append(dict(before=a,after=b,reason='long_edge_facing_transport'))
            elif abs(dx)+abs(dy)>1:
                bad.append(dict(before=a,after=b,reason='multi_cell_translation'))
    return bad


def audit(root,labels,allow_long_axis_labels=()):
    frames=0;failures=[]
    for flow in root:
        if flow.get('Name').split()[0] not in labels:continue
        previous=[];previous_groups=[]
        for command in flow:
            if command.get('Component')!='HighVoltage':continue
            for row,relay in enumerate(command,1):
                assert float(relay.get('Amp','0'))>0,'本次排废不得全场断电'
                current=rectangles(relay);frames+=1
                for failure in violations(previous,current,
                        allow_long_axis=flow.get('Name').split()[0] in allow_long_axis_labels):
                    failures.append(dict(section=flow.get('Name').split()[0],
                                         command=command.get('ID'),row=row,**failure))
                groups=components([(int(x),int(y),int(w),int(h))
                                   for y,x,h,w in PATTERN.findall(relay.get('RelayCode',''))])
                groups=[g for g in groups if min(r[1] for r in g)>=172]
                for area,parts in inspect_transition(previous_groups,groups):
                    failures.append(dict(section=flow.get('Name').split()[0],command=command.get('ID'),
                        row=row,reason='unexpected_bottom_separation',parent_cells=area,child_cells=parts))
                previous_groups=groups
                previous=current
    return dict(energized_frames=frames,violations=failures,
                named_collection_blocks_excluded=False,wet_liquid_verified=False)
