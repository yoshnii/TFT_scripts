"""单列轮廓版发布检查：逐格面积、连通、同步融合、手工边界和温控。无GUI/硬件验证。"""
import argparse,csv,json,re
from collections import Counter,defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET
import contour,geom,ops,protocol
from scene import Scene, _overlap

def codes(r):return Counter(filter(None,r.get('RelayCode','').split(';')))
def native_rect(s):
    y,x,h,w=map(int,re.fullmatch(r'\((\d+),(\d+)\)\((\d+),(\d+)\)',s).groups());return x,y,w,h

def check(out,before):
    old_record=Scene._record;old_merge=ops.merge_all;previous={};previous_relay=None
    frames=0;morphs=0;moves=0;empty=0;losses=[];merge_ctx={};merges=[];partitions=0;waste_rigid_frames=0
    def merge(S,lanes,guest_tag,host_tag='L'):
        lanes=list(lanes);assert len(lanes)==16
        merge_ctx.update(hosts={f'{host_tag}{ln.id}':S.get(f'{host_tag}{ln.id}').vol for ln in lanes},changes={})
        try:
            result=old_merge(S,lanes,guest_tag,host_tag)
            assert len(merge_ctx['changes'])==16 and len(set(merge_ctx['changes'].values()))==1,'各孔未同步融合'
            merges.append(dict(guest_tag=guest_tag,rows=16,merge_frame=next(iter(merge_ctx['changes'].values()))))
            return result
        finally:merge_ctx.clear()
    def record(S,*args,**kwargs):
        nonlocal previous,previous_relay,frames,morphs,moves,empty,partitions,waste_rigid_frames
        old_record(S,*args,**kwargs);frames+=1
        current={id(d):(d,d.vol,d.target_cells(),d.rect) for d in S.drops.values() if d.rects()}
        powered=Counter(geom.emit(r) for d,_,_,_ in current.values() for r in d.rects())
        assert Counter(S.relays[-1].codes)==powered,'漏掉驻留液滴电极'
        positive=[cells for _,v,cells,_ in current.values() if v>0]
        assert len(frozenset().union(*positive))==sum(map(len,positive)),'液滴实际电极重叠'
        for e in getattr(S,'split_events',[]):
            if e['partition_frame']!=S.nsteps-1:continue
            parent=previous[e['_parent_key']]
            children=[current[k] for k in e['_child_keys']]
            assert frozenset().union(*(d[2] for d in children))==parent[2],'切分第一帧子滴跳离母滴'
            assert abs(sum(d[1] for d in children)-parent[1])<1e-7,'切分改变总体积'
            partitions+=1
        if getattr(S,'pipeline_active',False):
            initial=next(e for e in reversed(S.pipeline_events) if e['kind']=='start')['initial_volumes']
            assert all(abs(S.get(ops.Lane(1,r).name).vol-v)<1e-6 for r,v in initial.items()),'试剂备齐前已与样本融合'
        for key,(d,v,cells,rect) in current.items():
            assert v>0,('禁止没有载液的磁珠块移动/上电',d.name,rect)
            assert contour.connected(cells)
            if v>0:assert len(cells)==geom.cells(v),(d.name,v,len(cells))
            if key in previous and v>0 and v==previous[key][1]:
                _,_,old_cells,old_rect=previous[key]
                if getattr(d,'waste_grid_packet',False):
                    assert contour.bounds(cells)[2:]==contour.bounds(old_cells)[2:],'废液转运中改变长宽/旋转'
                    waste_rigid_frames+=1
                assert len(cells)==len(old_cells),'同液量形变改变面积'
                dx,dy=rect[0]-old_rect[0],rect[1]-old_rect[1]
                translated=frozenset((x+dx,y+dy) for x,y in old_cells)
                if rect[2:]==old_rect[2:] and rect[:2]!=old_rect[:2] and cells==translated:
                    assert abs(dx)+abs(dy)<=1,('移动跳格',d.name,old_rect,rect)
                    moves+=1
                elif cells!=old_cells:
                    assert contour.edge_change(old_cells,cells)<=1,('形变外边界跳格',d.name)
                    assert len(cells^old_cells)<=max(2,len(cells)//2),('形变变化过大',d.name)
                    assert contour.near_rectangle(cells) or len(cells^old_cells)<=2,('形变出现细枝',d.name)
                    morphs+=1
        total=sum(v for _,v,_,_ in current.values());prior=sum(v for _,v,_,_ in previous.values())
        if prior-total>1e-6:
            assert any(r is previous_relay for r,info in S.manual_aspirations),('非手工边界液量减少',frames,prior-total)
            losses.append(round(prior-total,2))
        if not powered:assert total==0;empty+=1
        if merge_ctx:
            for n,v in merge_ctx['hosts'].items():
                if S.get(n).vol!=v:merge_ctx['changes'].setdefault(n,frames)
        previous=current;previous_relay=S.relays[-1]
    Scene._record=record;ops.merge_all=merge
    try:P=protocol.Protocol((1,));P.build()
    finally:Scene._record=old_record;ops.merge_all=old_merge
    root=ET.parse(out/'PTseq_16_A.elab').getroot();prior=ET.parse(before/'PTseq_16_A.elab').getroot()
    cmds={c.get('ID'):c for c in root.iter('Command')}
    # 新增非温控动作可能改变编号，只比较温控内容和顺序；依赖另由SOP发布检查验证。
    def thermal(r):return [{k:v for k,v in c.attrib.items() if k not in ('ID','PreTask')}
                          for c in r.iter('Command') if c.get('Component')!='HighVoltage']
    assert thermal(root)==thermal(prior),'温控改变'
    def non_r04(r):
        # 组拆分减少会平移新增编号段；动作、电极、持续时间、其余组结构不能跟着变。
        return [ET.tostring(strip_ids(ET.fromstring(ET.tostring(f))),encoding='unicode')
                for f in r if not f.get('Name','').startswith('R04')]
    def strip_ids(element):
        for item in element.iter():
            for key in ('ID','PreTask'):item.attrib.pop(key,None)
        return element
    # 当前授权范围还包括全区mix、A05、B01/B02/B05/B10及空帧清理；
    # B07配送保持与上一交付版逐帧相同，而非用整段放宽替代保护。
    def lig_delivery(r):
        return [ET.tostring(strip_ids(ET.fromstring(ET.tostring(c))),encoding='unicode')
                for c in r.iter('Command') if c.get('Name','').startswith('LIG 加液已完成')]
    assert lig_delivery(root)==lig_delivery(prior),'未经允许改变B07配送'
    assert next(f for f in root if f.get('Name','').startswith('A09')).get('Repeats')=='30'
    ids=[x.get('ID') for f in root for x in [f,*f]];assert len(ids)==len(set(ids))
    assert [x.get('include') for x in ET.parse(next(out.glob('*.elabprj'))).getroot()]==['main','PTseq_16_A']
    tecs=[(float(row['StartX']),float(row['StartY']),float(row['Width']),float(row['Height']))
          for row in csv.DictReader((before/'ChipArea_192_256_96_SIRO.csv').open(encoding='utf-8-sig'))
          if row['Type']=='Tec' and float(row['StartX'])!=10]
    assert len(tecs)==5
    for c in cmds.values():
        if c.get('Component')=='HighVoltage':
            assert sum(int(r.get('Duration')) for r in c)==int(c.get('Timeout'))*1000
            assert all(float(r.get('Amp'))>0 for r in c)
            assert all(codes(r) for r in c),'导出空电极帧'
            for relay in c:
                for code in codes(relay):
                    rect=native_rect(code)
                    assert all(not _overlap(rect,tec) for tec in tecs),('穿过其他列TEC',c.get('ID'),relay.get('ItemID'),rect)
    half_holds=[]
    for f in root:
        for c in f:
            thermal_hold='循环至温控结束' in c.get('Name','')
            magnetic_hold='磁铁升起' in c.get('Name','') and c.get('Component')=='HighVoltage'
            if not (thermal_hold or magnetic_hold):continue
            counts=set();crossing=False
            for relay in c:
                occupied=set()
                for code in codes(relay):
                    x,y,w,h=native_rect(code)
                    occupied.update((xx,yy) for xx in range(x,x+w) for yy in range(y,y+h))
                rows=[]
                for row in range(1,17):
                    x0,y0=geom.zone(1,row)
                    actual={(x,y) for x,y in occupied if y0<=y<y0+geom.LANE_H}
                    n=len(actual);counts.add(n);rows.append(actual)
                    exception=magnetic_hold and n==118 and f.get('Name','').startswith('C02-C03')
                    left=x0 if thermal_hold else x0+16 if exception else x0+geom.TEC_L
                    right=x0+geom.TEC_L if thermal_hold else x0+geom.LANE_L
                    assert actual and all(left<=x<right for x,y in actual),('半区越界',f.get('Name'),row,n)
                    crossing=crossing or exception
                assert set().union(*rows)==occupied,'保持帧存在反应区外电极'
            half_holds.append(dict(section=f.get('Name'),command=c.get('ID'),side='left' if thermal_hold else 'right',
                                   per_lane_cells=sorted(counts),authorized_590_crossing=crossing))
    assert len(half_holds)==12 and sum(x['authorized_590_crossing'] for x in half_holds)==1
    load=list(csv.DictReader((out/'手工加液顺序.csv').open(encoding='utf-8-sig')))
    old_load=list(csv.DictReader((before/'手工加液顺序.csv').open(encoding='utf-8-sig')))
    assert len(load)==16 and len(old_load)==14
    original_load=[r for r in load if '排珠载液承接' not in r['加液流程组名称']]
    cleanup_load=[r for r in load if '排珠载液承接' in r['加液流程组名称']]
    assert len(cleanup_load)==2 and all(r['加液孔']=='S孔×16' and float(r['本次µL（S/B孔为每道）'])==3 for r in cleanup_load)
    flows={f.get('ID'):f for f in root}
    for row,old in zip(original_load,old_load):
        authorized=row['加液流程组名称'].startswith(('B02','B05','B10'))
        if not authorized:
            for k in ('试剂','本次µL（S/B孔为每道）','各边缘孔µL','泳道','批次','加液孔'):assert row[k]==old[k]
        f,nxt=flows[row['加液流程组编号']],flows[row['下一个流程组编号']]
        assert codes(f[-1][-1])==codes(nxt[0][0])
        if row['加液孔']=='B孔×16':
            assert float(row['本次µL（S/B孔为每道）'])==1.0
            blocks=[native_rect(c) for c in codes(f[-1][-1]) if native_rect(c)[0]==31]
            assert len(blocks)==16 and all((w,h)==(5,4) for x,y,w,h in blocks)
    asp=list(csv.DictReader((out/'手工吸液顺序.csv').open(encoding='utf-8-sig')));totals=defaultdict(float);dock_shapes=[]
    for row in asp:
        f=flows[row['吸液流程组编号']];nxt=flows.get(row['下一个流程组编号'])
        dock=Counter(row['待吸液电极'].split(';'));held=codes(f[-1][-1])
        assert not dock-held
        remaining=held-dock
        if nxt is not None:
            if remaining:
                assert codes(nxt[0][0])==remaining,'手工吸出后驻留电极变化'
            else:
                # 同一道S孔第一次取液后，第二次洗脱在原位重新接液是正常新操作。
                receiving_ids={r['加液流程组编号'] for r in load}
                if row['吸液孔']=='S孔×16' and nxt.get('ID') in receiving_ids:
                    assert '加液承接' in nxt.get('Name',''),'同孔重新上电必须是真实加液组'
                else:
                    assert not dock & codes(nxt[0][0]),'吸液后仍在同一废液孔开始无动作续行'
        else:
            assert f is list(root)[-1] and not remaining,'只有最后取液才可直接结束工程'
        v=float(row['本次总液体µL（16道合计；排珠载液不计文库）'])
        assert v>0,'0µL磁珠清理仍被输出为吸液动作'
        prefix=f.get('Name').split(' · ')[0];totals[prefix]+=v
        if row['吸液孔'].startswith('F'):
            ports=row['吸液孔'].split('/');rs=[native_rect(c) for c in dock]
            assert len(ports)==len(rs)
            for p,(x,y,w,h) in zip(ports,rs):
                assert max(w,h)/min(w,h)<=2 and y+h==189 and abs(x+w/2-54*int(p[1:]))<=.5
                dock_shapes.append([w,h])
            if v:assert sum(w*h for x,y,w,h in rs)==geom.cells(v),'吸液框面积与实际总液量不符'
        elif row['吸液孔']=='S孔×16':
            rs=[native_rect(code) for code in dock]
            assert len(rs)==16 and sum(w*h for x,y,w,h in rs)==geom.cells(v),'离开磁区后的洗脱产物未恢复完整矩形'
    for stage,v in totals.items():
        expected=24 if stage.startswith('A05') else 128 if stage.startswith('R06') else 80 if stage.startswith(('R04','R05')) else 94.4 if stage.startswith('C04') else 96 if stage.startswith('C06') else 48 if stage.startswith('C05') else None
        if expected is not None:assert abs(v-expected)<1e-6,(stage,v,expected)
    assert not any(f.get('Name','').endswith('吸液完成后的续行') and
                   all(not codes(r) for c in f for r in c) for f in root),'多余的空续行组'
    cuts=[e for e in P.S.pipeline_events if e['kind']=='cut']
    splits=P.S.split_events
    assert partitions==len(splits)>0
    from native_packets import validate_symmetric_split
    for e in splits:
        validate_symmetric_split(e)
    assert len([e for e in P.S.pipeline_events if e['kind']=='all_ready'])==7
    assert all(e['ports'] for e in cuts)
    assert all(e.get('row_ready') and e['actual_area']==e['rect'][2]*e['rect'][3]
               for e in P.S.waste_grid_events),'底部汇液没有形成完整矩形排'
    import waste_grid
    for b in P.S.waste_batch_events:
        expected_plans=waste_grid.plan_batch(*b['tile_shape'],b['count'])
        if b.get('retained_ports') is not None:
            assert b['collection_group'] in (1,2,3,'A05')
            assert b['plans']==[dict(p,port=k) for p,k in zip(expected_plans,b['retained_ports'])]
        else:
            assert b['plans']==expected_plans
        if waste_grid.layout(*b['tile_shape'],b['count']) is not None:
            assert b['minimum_ports']==1,'整批能放一孔却使用多个孔'
    r04=[r for r in asp if r['吸液流程组名称'].startswith('R04')]
    assert [r['吸液孔'] for r in r04]==['F3/F2/F1','F3/F2/F1','F1']
    assert [float(r['本次总液体µL（16道合计；排珠载液不计文库）']) for r in r04]==[35.2,32.0,12.8]
    a05=[r for r in asp if r['吸液流程组名称'].startswith('A05')]
    assert len(a05)==1 and a05[0]['吸液孔']=='F3/F2/F1'
    assert all(native_rect(s)[2:]==(16,10) for s in a05[0]['待吸液电极'].split(';'))
    retained=[b for b in P.S.waste_batch_events if b.get('retained_port') is not None]
    assert [b['retained_port'] for b in retained]==[3,2,1,3,2,1,1]
    assert [b['collection_group'] for b in retained]==[1,1,1,2,2,2,3]
    assert all(e['scheduled_drops']>=8 for e in P.S.waste_parallel_events)
    assert all(e['entries'][4]['frame']<e['row_merges'][0] for e in P.S.waste_parallel_events)
    for reagent,dose,total in [('G8',.7,11.2),('EDTA',.2,3.2)]:
        rows=[r for r in load if r['试剂']==reagent]
        assert len(rows)==1 and rows[0]['加液孔']=='E1'
        assert float(rows[0]['本次µL（S/B孔为每道）'])==total
        entries=[e for e in P.S.pipeline_events if e['kind']=='start' and e['reagent']==reagent]
        assert len(entries)==1 and entries[0]['per_lane_ul']==dose
    for r in asp:
        if r['吸液流程组名称'].startswith(('C05','C06')) and '磁珠清理' not in r['吸液流程组名称']:
            assert r['吸液孔']=='S孔×16','纯化2洗脱产物不得走废液孔'
    assert len(P.bead_cleanup_events)==2
    for event in P.bead_cleanup_events:
        assert event['per_lane_ul']==3 and event['total_ul']==48 and event['magnet_state']=='down'
        rows=[r for r in asp if r['吸液流程组名称'].startswith('R06' if event['stage']=='R1' else 'C06') and '磁珠清理' in r['吸液流程组名称']]
        assert rows and abs(sum(float(r['本次总液体µL（16道合计；排珠载液不计文库）']) for r in rows)-48)<1e-6
    report=dict(status='contour_release_pass',model_frames=frames,native_frames=sum(len(c) for c in cmds.values()),
                sections=len(root),commands=len(cmds),actual_electrode_quantum_ul=.05,
                all_liquid_frames_match_exact_volume=True,all_liquid_contours_connected=True,
                morphology_max_boundary_motion_cells_per_frame=1,morphology_drop_frames=morphs,
                morphology_method='coordinated rectangular edge bands; constant actual area',
                fixed_contour_one_cell_moves=moves,manual_load_sections=len(load),manual_aspiration_sections=len(asp),
                previous_aspiration_sections=len(list(csv.DictReader((before/'手工吸液顺序.csv').open(encoding='utf-8-sig')))),compact_waste_dock_shapes=dock_shapes,
                gradual_split_groups=len(splits),split_partition_preserves_mother_cells=True,
                split_max_gap_growth_cells_per_frame=1,
                split_gap_sequences=[dict(gaps=list(gaps),count=count) for gaps,count in Counter(tuple(e['gaps']) for e in splits).most_common()],
                barcode_receiving_cells=[5,4],barcode_loaded_ul=1.0,barcode_retained_ul=.6,barcode_discarded_ul=.4,
                all_merges_synchronized=merges,
                thermal_commands_unchanged=True,pcr_cycles=30,liquid_removed_only_after_manual_gate=True,
                external_magnet_sequence=P.magnet_events,magnet_hardware_controlled=False,
                bead_cleanup_events=P.bead_cleanup_events,no_zero_liquid_bead_transport=True,
                bead_binding_mix_cycles=protocol.BEAD_BIND_MIX,bead_elution_mix_cycles=protocol.BEAD_ELUTE_MIX,
                bead_elution_incubation_events=P.elution_events,
                half_region_holds=half_holds,only_590_magnetic_crossing_authorized=True,
                waste_rows_merge_only_after_whole_row_ready=True,waste_row_terminal_shapes_complete=True,
                concurrent_multiport_cut_frames=len(cuts),full_column_ready_before_fusion=True,
                waste_grid_tiles=len(P.S.waste_grid_events),
                waste_collection_columns=sorted(set(p['columns'] for b in P.S.waste_batch_events for p in b['plans'])),
                waste_collection_max_rows=max(p['rows'] for b in P.S.waste_batch_events for p in b['plans']),
                waste_collection_minimum_ports_per_batch=True,waste_batches=P.S.waste_batch_events,
                r04_aspiration_groups=3,r04_group_volumes_ul=[35.2,32.0,12.8],
                r04_completed_docks_retained_until_group_aspiration=True,
                b07_delivery_identical_to_baseline=True,no_exported_empty_electrode_frames=True,
                waste_whole_port_pipeline_events=P.S.waste_parallel_events,
                all_frames_avoid_other_column_tec=True,waste_transport_y_min=172,
                waste_transport_preserves_tile_dimensions=True,waste_rigid_drop_frames=waste_rigid_frames,
                waste_ports_may_have_different_rectangle_sizes=True,
                waste_tile_shapes=sorted(set(tuple(e['tile_shape']) for e in P.S.waste_grid_events)),
                waste_tile_areas=sorted(set(e['tile_area'] for e in P.S.waste_grid_events)),
                gui_verified=False,visual_check_performed=False,hardware_verified=False,
                before_nominal_seconds=sum(int(f.get('Timeout'))*int(f.get('Repeats')) for f in prior),
                after_nominal_seconds=sum(int(f.get('Timeout'))*int(f.get('Repeats')) for f in root),
                unverified=['液滴追踪/形变响应','分液体积精度','捕珠回收率','孔边溢出风险'])
    print(json.dumps(report,ensure_ascii=False,indent=2));return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('out',type=Path);ap.add_argument('before',type=Path);ap.add_argument('--report',type=Path)
    a=ap.parse_args();r=check(a.out,a.before)
    if a.report:a.report.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
