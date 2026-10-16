"""逐帧离线审计：物料守恒、等分、原形运输、编号、温控及手工边界。

不操作GUI、不下发仪器；生成通过不等于真实液滴/磁控/到温放行。
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import sys
import xml.etree.ElementTree as ET
import contour
import geom
from scene import Scene


def validate(out):
    original=Scene._record;previous={};receivers={};loaded=0.
    stats=dict(frames=0,translation_frames=0,reshape_frames=0,native_packet_frames=0,approved_padding_expansions=0)
    def record(S,dur=geom.STEP_MS,*a,**kw):
        nonlocal loaded,previous
        original(S,dur,*a,**kw)
        stats['frames']+=1;current={}
        for d in S.drops.values():
            if getattr(d,'receiving',False) and id(d) not in receivers:
                receivers[id(d)]=d;loaded+=d.vol
            if not d.rects():continue
            cells=d.target_cells();rc=contour.bounds(cells)
            assert len(contour.rectangles(cells))==1
            assert len(cells)==geom.cells(d.vol)+d.margin_budget
            x,y,w,h=rc
            for c in range(2,7):
                tx=10+40*(c-1)
                assert not(x<tx+18 and tx<x+w and y<172 and 23<y+h),('跨邻列TEC',d.name,rc)
            old=previous.get(id(d))
            if old and old[0] is d and abs(old[1]-d.vol)<1e-9:
                if len(cells)!=len(old[2]):
                    policies={('G4',1.6,36,40),('G4',.8,20,24),('A06',1.,20,24),
                              ('G8',1.4,30,32),('LIG',1.4,30,32),('R04',5.,100,120)}
                    matched=[e for e in getattr(S,'approved_mother_shapes',[])
                             if e['frame']==S.nsteps-1 and e['name']==d.name]
                    assert len(matched)==1,('未授权电极面积变化',d.name,len(old[2]),len(cells))
                    e=matched[0]
                    assert (e['reagent'],d.vol,len(old[2]),len(cells)) in policies
                    assert e['before']==list(old[3]) and e['after']==list(rc)
                    assert dur>=4000 and not getattr(d,'native_packet',False)
                    stats['approved_padding_expansions']+=1
                if cells!=old[2]:
                    before=old[3];dx,dy=x-before[0],y-before[1]
                    if rc[2:]==before[2:]:
                        assert cells==frozenset((xx+dx,yy+dy) for xx,yy in old[2])
                        assert abs(dx)+abs(dy)<=1,('跳格平移',d.name,before,rc)
                        stats['translation_frames']+=1
                    else:
                        assert not getattr(d,'native_packet',False),('运输小滴逐个形变',d.name,before,rc)
                        assert dur>=4000,('形变未保持4秒',d.name,before,rc)
                        if rc[2:]!=tuple(reversed(before[2:])):
                            assert max(w/before[2],before[2]/w,h/before[3],before[3]/h)<=2.5
                        stats['reshape_frames']+=1
            if getattr(d,'native_packet',False):stats['native_packet_frames']+=1
            current[id(d)]=(d,d.vol,cells,rc)
        aspirated=sum(info['volume_ul'] for relay,info in (getattr(S,'manual_aspirations',[]) or []))
        onchip=sum(d.vol for d in S.drops.values())
        assert abs(onchip+aspirated-loaded)<1e-7,('逐帧实际液量不守恒',S.nsteps,onchip,aspirated,loaded)
        previous=current
    Scene._record=record;saved=sys.argv
    try:
        sys.argv=['build.py','--cols','1','--out',str(out)]
        result=runpy.run_path(str(Path(__file__).with_name('build.py')),run_name='__main__')
    finally:Scene._record=original;sys.argv=saved
    P=result['P'];scripts=result['scripts'];S=P.S
    assert not S.drops
    root=ET.parse(out/'PTseq_16_A.elab').getroot()
    ids=[e.get('ID') for e in root.iter() if e.tag in ('Flow','Command')]
    assert len(ids)==len(set(ids))
    labels=[f.get('Name').split()[0] for f in root]
    assert len(labels)==len(set(labels))
    for letter in 'ARBC':
        numbers=[int(n[1:]) for n in labels if n[0]==letter]
        assert numbers==list(range(1,len(numbers)+1))
    for f in root:
        local={c.get('ID') for c in f}
        assert set(filter(None,f.get('SyncEndingCmdList','').split(',')))<=local
        for c in f:
            pre=c.get('PreTask','').split(',')[0];assert pre=='-1' or pre in local
            if c.get('Component')=='HighVoltage':
                assert len(c)>0 and all(r.get('RelayCode') for r in c)
                assert sum(int(r.get('Duration')) for r in c)==1000*int(c.get('Timeout'))
                assert all(int(r.get('Duration'))!=750 for r in c)
    byid={int(f.get('ID')):f for f in root}
    flows=[f for script in scripts for f in script.flows]
    def original_flow(prefix):return next(f for f in flows if f.original_name.startswith(prefix+' '))
    thermal={'A04':[(37,600),(85,5),(4,30)],'A08':[(95,180)],
        'A09':[(95,30),(60,60),(72,45)],'A10':[(72,60),(4,30)],
        'B04':[(37,240),(4,30)],'B09':[(25,600),(4,30)]}
    for old,expected in thermal.items():
        f=byid[original_flow(old).id];cs=list(f)
        actual=[(int(c.get('Params').split(',')[0]),int(cs[i+1].get('Timeout')))
                for i,c in enumerate(cs) if c.get('Component')=='Heater1']
        assert actual==expected,(old,actual,expected)
    pcr=byid[original_flow('A09').id];assert pcr.get('Repeats')=='30'
    pulse=next(c for c in pcr if c.get('Component')=='HighVoltage')
    assert [(int(r.get('Duration')),int(r.get('Amp'))) for r in pulse]==[(1000,0),(1000,50)]
    for f in root:
        for c in f:
            if c.get('Component')=='HighVoltage' and any(float(r.get('Amp'))==0 for r in c):assert f is pcr
    expected={'G4':.4,'AMP':2.,'G14':2.5,'G8':.7,'LIG':1.4,'EDTA':.2,'G16':1.7}
    before={'G4':1.6,'AMP':.5,'G14':2.5,'G8':1.3,'LIG':2.6,'EDTA':4.,'G16':4.2}
    finishes=[e for e in S.pipeline_events if e['kind']=='finish']
    assert {e['reagent'] for e in finishes}==set(expected)
    for e in finishes:assert all(abs(v-before[e['reagent']]-expected[e['reagent']])<1e-8 for v in e['final_volumes'].values())
    leaf_specs={'G4':(16,.4,[3,4],4),'AMP':(32,1.,[4,5],0),'G14':(64,.625,[3,5],3),
                'G8':(16,.7,[4,4],2),'LIG':(32,.7,[4,4],2),'EDTA':(16,.2,[2,2],0),
                'G16':(32,.85,[4,5],3)}
    for reagent,(count,vol,shape,extra) in leaf_specs.items():
        leaves=[e for e in S.native_leaf_events if e['reagent']==reagent]
        assert len(leaves)==count
        assert all(e['actual_ul']==vol and e['shape']==shape and e['extra_cells']==extra for e in leaves)
    b01=byid[original_flow('B01').id]
    b01_codes=next(c for c in b01 if c.get('Component')=='HighVoltage')[0].get('RelayCode')
    assert b01_codes.count('(5,6)')==16,'B01 must receive 1.30µL in full 6×5 rectangles'
    approved_paths={
        'G8':{240:[10,12],120:[6,10],60:[6,5],32:[4,4]},
        'LIG':{240:[10,12],120:[6,10],60:[6,5],32:[4,4]},
        'G16':{320:[10,16],160:[10,8],80:[5,8],40:[4,5]}}
    for e in S.split_events:
        assert len(e['volumes'])==2,('非二分动作',e['parent'],e['volumes'])
        for reagent,path in approved_paths.items():
            if e['parent'].startswith('D'+reagent+'E'):
                assert e['area'] in path and path[e['area']] is not None
                assert e['positions'][0][0][2:]==path[e['area']]
    loads=result['rows'];assert len(loads)==16
    positions={f.id:i for i,f in enumerate(flows)}
    for row in loads:
        i=positions[row['section_id']]
        assert set(flows[i].cmds[-1].relays[-1].codes)==set(flows[i+1].cmds[0].relays[0].codes),('加液前后电极不衔接',row['section_id'])
    assert [r['per_lane_ul'] for r in loads if r['hole'].startswith('S')]==[1.6,5.,5.,3.,1.3,3.,3.,3.]
    assert next(r for r in loads if r['hole'].startswith('B'))['per_lane_ul']==1.2
    assert {r['reagent']:r['port_volumes'] for r in loads if r['hole'].startswith('E')}=={
        'G4':{'E1':3.2,'E2':3.2},'AMP':{'E1':16.,'E2':16.},
        'G14':{'E1':10.,'E2':10.,'E3':10.,'E4':10.},'G8':{'E1':11.2},
        'LIG':{'E1':11.2,'E2':11.2},'EDTA':{'E1':3.2},'G16':{'E1':13.6,'E2':13.6}}
    for e in S.split_events:
        assert max(e['volumes'])-min(e['volumes'])<1e-8,('不等量分液',e['parent'],e['volumes'])
        assert all(0<=b-a<=1 for a,b in zip(e['gaps'],e['gaps'][1:]))
    schedules={e['kind']:e for e in S.waste_schedule_events}
    assert abs(schedules['C04']['actual_packet_ul']-.7375)<1e-9
    assert schedules['C04']['packet_areas']==[15]*8
    assert schedules['R04']['packet_areas']==[15]*8
    assert schedules['R04']['actual_packet_ul']==.625
    from audit_binary_frames import audit
    binary_frames=audit(out/'PTseq_16_A.elab')
    assert not binary_frames['non_equal_binary_transitions'],binary_frames['non_equal_binary_transitions']
    assert all(e['name'].startswith('WROW') for e in getattr(S,'waste_morph_events',[]))
    mix=[c for f in flows for c in f.cmds if hasattr(c,'mix_timing')]
    assert all(c.params=='SCRIPTCYCLE_TRUE' and c.relays[0].codes==c.relays[-1].codes for c in mix)
    assert [(e['stage'],e['mix_cycles']) for e in P.bead_cleanup_events]==[('R1',12),('C',2)]
    assert S.manual_aspirations[-1][1]['volume_ul']==48.
    assert len(S.manual_aspirations[-1][1]['port_volumes'])==4
    assert all(not e['hardware_control'] for e in P.magnet_events)
    asp=sum(e['volume_ul'] for relay,e in S.manual_aspirations)
    assert abs(loaded-asp)<1e-7
    workbook=Path('/Users/shan/Documents/BGI/SIRO解决方案中心/SIRO-Plus/实验/TFT片上测试/片上测试1/TFT片上试剂体积表_单列.xlsx')
    prior=Path(__file__).parent.parent/'projects/PTseq_SIRO16_A_10x/修订校验_20261015.json'
    workbook_sha=hashlib.sha256(workbook.read_bytes()).hexdigest()
    assert json.loads(prior.read_text())['workbook_sha256']==workbook_sha,'体积表已变，不能沿用原审核常量'
    mapping=[dict(id=f.id,name=f.name,previous=f.original_name) for f in flows]
    report=dict(status='passed',evidence='offline_only',visual_check=False,hardware_verified=False,
        script_sha256=hashlib.sha256((out/'PTseq_16_A.elab').read_bytes()).hexdigest(),
        workbook_sha256=workbook_sha,approved_per_lane_reagent_ul=expected,
        frames=stats,flows=len(flows),commands=sum(len(f.cmds) for f in flows),
        equal_volume_splits=True,binary_only=True,split_events=len(S.split_events),
        independent_exported_frame_audit=binary_frames,
        authorized_fractional_packets_ul={'G14':.625,'R04':.625,'C04':.7375},
        r04_carrier=dict(actual_ul=5.,electrode_cells=120,extra_cells=20,extra_liquid_ul=0),
        no_per_packet_reshape=True,no_foreign_TEC=True,translation_max_cells=1,
        cumulative_actual_load_ul=loaded,cumulative_actual_aspirate_ul=asp,
        c04=dict(actual_per_lane_ul=5.9,electrode_cells=120,packet_count=8,packet_actual_ul=.7375,
                 packet_shape=[3,5],volume_quantization_exception=True),
        waste_schedules=S.waste_schedule_events,whole_row_morphs=getattr(S,'waste_morph_events',[]),
        native_padding=S.native_padding_events,thermal=thermal,
        approved_mother_shapes=S.approved_mother_shapes,native_leaf_specs=leaf_specs,
        native_leaf_events=S.native_leaf_events,b01=dict(actual_ul=1.3,shape=[6,5],cells=30,extra_cells=4),
        generator_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(__file__).parent.glob('*.py') if not p.name.startswith('test_')},
        magnet_sequence=P.magnet_events,elutions=P.elution_events,
        elution_manual_aspiration_groups=len(S.manual_aspirations),
        manual_loading_groups=len(loads),section_mapping=mapping,
        remaining_physical_validation=['液滴真正等分与电极承接余量','外部磁铁升降到位及捕获',
            'Heater到温反馈与真实液温','ScriptCycle共同结束能否在整轮末停机'])
    def serializable(v):
        if isinstance(v,dict):return {':'.join(map(str,k)) if isinstance(k,tuple) else str(k):serializable(a) for k,a in v.items()}
        if isinstance(v,(list,tuple)):return [serializable(a) for a in v]
        return v
    report=serializable(report)
    (out/'交付代码校验_20261016.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (out/'步骤编号映射_20261016.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','script_sha256','frames','flows','commands','cumulative_actual_load_ul')},ensure_ascii=False))
    return report


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('out',type=Path);validate(ap.parse_args().out)
