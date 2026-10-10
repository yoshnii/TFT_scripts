"""本版形变与多孔分配回归；只验证程序几何，不证明液滴物理行为。"""
import argparse,ast,csv,json,math
from collections import defaultdict,Counter
from pathlib import Path
import xml.etree.ElementTree as ET
import geom,protocol,rigid,ops
from scene import Scene,Drop


def edges(r):return r[0],r[1],r[0]+r[2],r[1]+r[3]


def check(out,before,dry_beads=False):
    if hasattr(Scene,'_record'):
        from check_contour_release import check as contour_check
        return contour_check(out,before)
    # 复现用户指出的4×8→8×4：不能再经过8×8包络框。
    sample=Scene();d=sample.add(Drop('example',(20,10,4,8),1.6))
    frames=[];original_step=Scene.step
    def example_step(S,*a,**kw):
        frames.append(S.get('example').rect);return original_step(S,*a,**kw)
    Scene.step=example_step
    try:rigid.reshape(sample,d,(18,12,8,4))
    finally:Scene.step=original_step
    assert max(w*h for x,y,w,h in frames)<=36,(frames,'临时面积翻倍')
    # 逐孔逐道核算，分到多个孔不改变每道输入量。
    plans=[]
    for ul in (.4,2,2.5,.7,1.4,.2,1.7):
        counts=defaultdict(int);receivers=[]
        for k,(vc,n,rows) in enumerate(rigid.loading_plan(geom.cells(ul)),1):
            w,h=rigid.fixed_cascade_shape(vc,n);W,H=w*(n//2),2*h
            assert W*H==vc*n and max(W,H)/min(W,H)<=2
            assert w<=geom.CH_W and h<=geom.LANE_H
            for row in rows:counts[row]+=vc
            receivers.append(dict(port=f'E{k}',ul=round(vc*n*.05,2),shape=[W,H],lanes=rows))
        assert len(counts)==16 and set(counts.values())=={geom.cells(ul)}
        assert len(receivers)<=4
        plans.append(dict(per_lane_ul=ul,receivers=receivers))
    # 运行整套模型，检查所有真实整形帧的边界连续性，顶部不发生整形。
    morph_count=0;frame_count=0;top_count=0;old_plan=rigid.reshape_plan
    parallel_frames=0;parallel_port_frames=0;previous={}
    old_merge=ops.merge_all;merge_context={};synchronized_merges=[]
    def merge(S,lanes,guest_tag,host_tag='L'):
        lanes=list(lanes)
        assert len(lanes)==16,'反应融合必须包括全列16道'
        merge_context.update(hosts={f'{host_tag}{ln.id}':S.get(f'{host_tag}{ln.id}').vol for ln in lanes},
                             guests={f'{host_tag}{ln.id}':f'{guest_tag}{ln.id}' for ln in lanes},
                             contact={},changed={})
        try:
            result=old_merge(S,lanes,guest_tag,host_tag)
            assert len(merge_context['contact'])==len(merge_context['changed'])==16
            assert len(set(merge_context['contact'].values()))==1,'不同反应区先后接触'
            assert len(set(merge_context['changed'].values()))==1,'不同反应区先后融合'
            synchronized_merges.append(dict(guest_tag=guest_tag,rows=16,
                first_contact_step=next(iter(merge_context['contact'].values())),
                merged_step=next(iter(merge_context['changed'].values()))))
            return result
        finally:merge_context.clear()
    def plan(d,target):
        nonlocal morph_count
        assert not d.name.startswith('D'),('顶部母滴不应整形',d.name,d.rect,target)
        last=d.rect
        for rect in old_plan(d,target):
            assert max(abs(x-y) for x,y in zip(edges(last),edges(rect)))<=1,(d.name,last,rect,'边界跳格')
            assert max(abs(x-y) for x,y in zip(last[2:],rect[2:]))<=1,(d.name,last,rect,'长宽跳格')
            assert rect[2]*rect[3]>=geom.cells(d.vol),(d.name,rect,'整形面积不足')
            morph_count+=1;last=rect;yield rect
    def step(S,*a,**kw):
        nonlocal frame_count,top_count,parallel_frames,parallel_port_frames,previous
        original_step(S,*a,**kw);frame_count+=1
        if merge_context:
            for host,initial in merge_context['hosts'].items():
                guest=merge_context['guests'][host]
                if guest in S.drops and rigid._gap(S.get(guest).rect,S.get(host).rect)<=0:
                    merge_context['contact'].setdefault(host,S.nsteps-1)
                if S.get(host).vol!=initial:
                    merge_context['changed'].setdefault(host,S.nsteps-1)
        current={id(d):(d.rect,d.vol) for d in S.drops.values()}
        if getattr(S,'pipeline_active',False):
            start=next(e for e in reversed(S.pipeline_events) if e['kind']=='start')
            assert all(abs(S.get(ops.Lane(1,r).name).vol-v)<1e-6
                       for r,v in start['initial_volumes'].items()),'配送中提前与产物融合'
            moving=[d for d in S.drops.values() if id(d) in previous
                    and d.rect!=previous[id(d)][0] and d.vol==previous[id(d)][1]]
            if len(moving)>1:parallel_frames+=1
            if len({getattr(d,'port',None) for d in moving})>1:parallel_port_frames+=1
        previous=current
        top=[d for d in S.drops.values() if d.name.startswith('D')]
        if top:
            top_count+=1
            for d in top:assert len(d.rects())==1 and d.rect[1]+d.rect[3]<=23
            for i,d in enumerate(top):
                for q in top[i+1:]:assert rigid._gap(d.rect,q.rect)>=4
        for d in S.drops.values():
            if d.name.startswith('BLOB'):
                assert d.rect[2]<=24 and d.rect[3]==6
    Scene.step=step;rigid.reshape_plan=plan;ops.merge_all=merge
    try:P=protocol.Protocol((1,));P.build()
    finally:Scene.step=original_step;rigid.reshape_plan=old_plan;ops.merge_all=old_merge
    events=P.S.pipeline_events
    cuts=[e for e in events if e['kind']=='cut']
    assert cuts and all(len(e['ports'])>=2 for e in cuts),'没有真正多孔同步拆分'
    assert parallel_frames>0 and parallel_port_frames>0,'没有跨孔同步配送'
    batches=[];batch=None
    for e in events:
        if e['kind']=='start':batch=dict(start=e,entries=[],merges=[],moving=[])
        elif e['kind']=='transport_start':batch['moving'].append(e)
        elif e['kind']=='enter':batch['entries'].append(e)
        elif e['kind']=='reagent_collect':batch['merges'].append(e)
        elif e['kind']=='all_ready':
            assert e['rows']==list(range(1,17))
            assert len({m['row'] for m in batch['merges']})==16
            assert max(m['step'] for m in batch['merges'])<e['step']
            batch['all_ready']=e['step']
        elif e['kind']=='finish':
            incoming=defaultdict(float)
            for m in batch['merges']:incoming[m['row']]+=m['incoming_ul']
            assert len(incoming)==16 and all(abs(v-batch['start']['per_lane_ul'])<1e-6 for v in incoming.values())
            assert all(abs(e['final_volumes'][r]-batch['start']['initial_volumes'][r]-incoming[r])<1e-6 for r in incoming)
            spans={p:(min(x['step'] for x in batch['entries'] if x['port']==p),
                      max(x['step'] for x in batch['merges'] if x['port']==p))
                   for p in {x['port'] for x in batch['entries']}}
            starts={p:min(x['step'] for x in batch['moving'] if x['port']==p) for p in spans}
            # 远孔有更长的路，不要求其已入井时最近孔仍未送完。
            # 要求各孔真正开始运动早于任一孔全部送完，且入井区间至少两孔重叠。
            assert max(starts.values())<min(b for a,b in spans.values()),('仍有整孔启动屏障',starts,spans)
            assert any(max(spans[p][0],spans[q][0])<min(spans[p][1],spans[q][1])
                       for p in spans for q in spans if p!=q),('没有同时配送到多个井',spans)
            batches.append(dict(reagent=e['reagent'],port_delivery_spans=spans,
                                port_movement_start_steps=starts,
                                all_lanes_ready_step=batch['all_ready'],
                                input_ul_per_lane=batch['start']['per_lane_ul'],
                                steps=e['step']-batch['start']['step']))
    root=ET.parse(out/'PTseq_16_A.elab').getroot()
    project=ET.parse(next(out.glob('*.elabprj'))).getroot()
    assert [x.get('include') for x in project]==['main','PTseq_16_A']
    ids=[x.get('ID') for f in root for x in [f,*f]];assert len(ids)==len(set(ids))
    layout=ET.parse(out/'PTseq_16_A.elab.layout').getroot()
    assert [x.get('ID') for x in layout if x.tag=='Flow']==[f.get('ID') for f in root]
    def rows(folder,name):return list(csv.DictReader((folder/name).open(encoding='utf-8-sig')))
    load_before,load_out=rows(before,'手工加液顺序.csv'),rows(out,'手工加液顺序.csv')
    assert len(load_before)==len(load_out)==14
    for x,y in zip(load_before,load_out):
        for key in x:
            assert x[key]==y[key],(key,x,y)
    assert rows(out,'手工吸液顺序.csv')==rows(before,'手工吸液顺序.csv')
    prior=ET.parse(before/'PTseq_16_A.elab').getroot()
    nominal=lambda script:sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in script)
    cmds={c.get('ID'):c for c in root.iter('Command')}
    old_cmds={c.get('ID'):c for c in prior.iter('Command')}
    assert set(cmds)==set(old_cmds),'指令编号改变'
    modified=[];magnetic_modified=[]
    magnetic_ids={c.get('ID') for f in root if f.get('Name','').startswith(('R04','R05','R06','C04','C05','C06')) for c in f}
    for ident,c in cmds.items():
        old=old_cmds[ident]
        if dry_beads and ident in magnetic_ids:
            assert {k:v for k,v in c.attrib.items() if k not in ('Name','Timeout')}=={k:v for k,v in old.attrib.items() if k not in ('Name','Timeout')},('磁控指令非动作属性改变',ident)
            if ET.tostring(c)!=ET.tostring(old):magnetic_modified.append(ident)
            continue
        if '加液已完成 → 顶部拆成液珠' in c.get('Name',''):
            modified.append(ident);continue
        assert c.attrib==old.attrib,('其他指令属性改变',ident)
        assert len(c)==len(old),('其他指令长度改变',ident)
        for a,b in zip(c,old):
            assert {k:v for k,v in a.attrib.items() if k!='RelayCode'}=={k:v for k,v in b.attrib.items() if k!='RelayCode'},('其他指令时间/驱动参数改变',ident)
            assert Counter(a.get('RelayCode','').split(';'))==Counter(b.get('RelayCode','').split(';')),('其他动作改变',ident)
    assert len(modified)==7,modified
    report=dict(status='smooth_routing_pass',scene_frames=frame_count,
                native_frames=sum(len(c) for f in root for c in f),
                sections=len(root),commands=sum(len(f) for f in root),
                deformation_drop_frames=morph_count,top_frames=top_count,
                synchronized_multiport_cut_frames=len(cuts),parallel_delivery_frames=parallel_frames,
                parallel_multiport_frames=parallel_port_frames,delivery_batches=batches,
                delivered_volume_checked_per_lane=True,
                before_nominal_seconds=nominal(prior),after_nominal_seconds=nominal(root),
                scheduling_saved_seconds=nominal(prior)-nominal(root),
                changed_delivery_commands=modified,changed_magnetic_commands=magnetic_modified,
                outside_authorized_magnetic_and_delivery_commands_unchanged=True,
                all_reaction_merges_synchronized=True,reaction_merge_groups=synchronized_merges,
                production_volume_unchanged_until_all_lanes_ready=True,
                top_reshapes=0,shape_edge_step_max_cells=1,shape_dimension_step_max_cells=1,
                deformation_area_covers_planned_volume=True,
                example_deformation_shapes=[list(r[2:]) for r in frames],
                example_max_electrode_cells=max(w*h for x,y,w,h in frames),
                waste_box_max_cells=[24,6],loading_plans=plans,
                manual_load_sections=len(load_out),
                manual_aspiration_sections=len(rows(out,'手工吸液顺序.csv')),
                gui_verified=False,visual_check_performed=False,physical_run_performed=False,
                unverified=['实际整形与液滴追踪','分液体积精度','孔边接液/吸液接触','跨样本残留'])
    print(json.dumps(report,ensure_ascii=False,indent=2));return report


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('out',type=Path);ap.add_argument('before',type=Path)
    ap.add_argument('--report',type=Path);ap.add_argument('--dry-beads',action='store_true');a=ap.parse_args();r=check(a.out,a.before,a.dry_beads)
    if a.report:a.report.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
