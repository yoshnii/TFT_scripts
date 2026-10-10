"""离线回归：单格移动、计划体积、手工吸液边界、洗脱S孔、全区重悬；不验证真实液体或设备。"""
import argparse,csv,json,re,sys
from collections import Counter,defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET
import geom,protocol
from scene import Scene

def rc(code):
    y,x,h,w=map(int,re.fullmatch(r'\((\d+),(\d+)\)\((\d+),(\d+)\)',code).groups())
    return x,y,w,h

def state(relay):return Counter(filter(None,relay.get('RelayCode','').split(';')))
def cells(rect):
    x,y,w,h=rect;return {(a,b) for a in range(x,x+w) for b in range(y,y+h)}

def check(out,before):
    if hasattr(Scene,'_record'):
        from check_contour_release import check as contour_check
        return contour_check(out,before)
    checked=0;losses=[];dry_frames=0; original=Scene.step
    def audit(S,*args,**kwargs):
        nonlocal checked,dry_frames
        original(S,*args,**kwargs)
        for d in S.drops.values():
            if getattr(d,'magnet_only',False):
                assert d.vol==0 and d.rects()==[],('磁珠占位仍输出液滴电极',d.name)
            if d.vol>0:assert d.rects(),('实际液体未上电',d.name)
        if not S.relays[-1].codes:
            assert sum(d.vol for d in S.drops.values())==0,'有液体时全部断电'
            if S.drops:
                assert len(S.drops)==16 and all(getattr(d,'magnet_only',False) for d in S.drops.values())
                dry_frames+=1
        current={n:(d.rect,d.vol) for n,d in S.drops.items()}
        for n,(r,v) in current.items():
            assert v>=0 and abs(v/geom.CELL_UL-round(v/geom.CELL_UL))<1e-6,(n,r,v,'非0.05µL整数倍')
        old=getattr(S,'audit_old',{})
        if old and not current:
            assert any(relay is getattr(S,'audit_previous',None) for relay,info in S.manual_aspirations),'仍有液滴时全部断电'
        for n in current.keys() & old.keys():
            r,v=current[n];q,v0=old[n]
            if abs(v-v0)>1e-6:continue # 切分/融合改变身份的面积，另核对总液量
            if r[2:]==q[2:]:
                assert abs(r[0]-q[0])+abs(r[1]-q[1])<=1,(n,q,r,'固定形状跳格')
            else:
                edges=lambda a:(a[0],a[1],a[0]+a[2],a[1]+a[3])
                if max(abs(a-b) for a,b in zip(edges(r),edges(q)))>1:
                    occupied=set().union(*(cells(z[0]) for z in current.values()))
                    assert cells(q)<=occupied,(n,q,r,'形变突然撤掉旧电极')
        loss=round(sum(v for r,v in old.values())-sum(v for r,v in current.values()),6)
        if loss>1e-6:
            assert any(relay is getattr(S,'audit_previous',None) for relay,info in S.manual_aspirations),('非手工吸液边界液体减少',S.nsteps,loss)
            losses.append(loss)
        S.audit_old=current;S.audit_previous=S.relays[-1];checked+=1
    Scene.step=audit
    try:P=protocol.Protocol((1,));P.build()
    finally:Scene.step=original
    roots={p.name:ET.parse(p).getroot() for p in out.glob('*.elab')}
    def thermal_commands(folder):
        main=ET.parse(folder/'main.elab').getroot()
        result={}
        for flow in main:
            root=ET.parse(folder/flow.get('Include')).getroot()
            for c in root.iter('Command'):
                if c.get('Component')!='HighVoltage':
                    assert c.get('ID') not in result
                    result[c.get('ID')]=tuple(c.get(k) for k in ('Component','Action','Params','Timeout'))
        return result
    # 比较整套方案而非逐文件比较，兼容只调整文件层级的版本。
    assert thermal_commands(out)==thermal_commands(before),'温控/非高压指令改变'
    for name,root in roots.items():
        ids=[c.get('ID') for c in root.iter('Command')];assert len(ids)==len(set(ids))
        for c in root.iter('Command'):
            if c.get('Component')=='HighVoltage':
                assert sum(int(r.get('Duration')) for r in c)==1000*int(c.get('Timeout'))
                assert all(float(r.get('Amp'))>0 for r in c)
    load=list(csv.DictReader((out/'手工加液顺序.csv').open(encoding='utf-8-sig')))
    assert len(load)==14
    for row in load:
        root=roots[row['流程文件']]
        f=next(f for f in root if f.get('ID')==row['加液流程组编号'])
        nxt=next(f for f in roots[row['下一流程组所在文件']] if f.get('ID')==row['下一个流程组编号'])
        assert len(f[-1])==1 and state(f[-1][0])==state(nxt[0][0]),row
        if row['试剂'] in ('G15','G18'):assert row['加液孔']=='S孔×16'
    asp=list(csv.DictReader((out/'手工吸液顺序.csv').open(encoding='utf-8-sig')))
    assert len(asp)>=13  # 多孔小框容量边界按实际产生，不固化旧版的13个停靠点。
    totals=defaultdict(float)
    for row in asp:
        f=next(f for f in roots[row['流程文件']] if f.get('ID')==row['吸液流程组编号'])
        nxt=next(f for f in roots[row['下一流程组所在文件']] if f.get('ID')==row['下一个流程组编号'])
        dock=Counter(row['待吸液电极'].split(';'));s=state(f[-1][-1]);assert not (dock-s)
        assert state(nxt[0][0])==s-dock,('吸液后其他电极变化',row)
        volume=float(row['本次总液体µL（洗脱为16道合计；磁珠清理0不代表无磁珠）'])
        totals[(row['流程文件'],f.get('Name').split(' · ')[0])]+=volume
        if row['吸液孔'].startswith('F'):
            ports=row['吸液孔'].split('/');rectangles=row['待吸液电极'].split(';')
            assert len(ports)==len(rectangles)
            for port,code in zip(ports,rectangles):
                x,y,w,h=rc(code);hx=54*int(port[1:])
                assert y+h==189 and abs(x+w/2-hx)<=0.5
    for (name,stage),volume in totals.items():
        expected=24 if stage.startswith('A05') else 80 if stage.startswith('R04') else 94.4 if stage.startswith('C04') else 80 if stage.startswith(('R05','R06')) else 48 if stage.startswith(('C05','C06')) else None
        if expected is not None:assert abs(volume-expected)<1e-6,(stage,volume,expected)
    sweeps=[]
    for root in roots.values():
        for c in root.iter('Command'):
            if '固定矩形扫过整个反应区' not in c.get('Name',''):continue
            frames=[[rc(z) for z in state(r)] for r in c]
            for y in range(27,163,9):
                rects=[r for frame in frames for r in frame if r[1]==y]
                assert min(r[0] for r in rects)==10 and max(r[0]+r[2] for r in rects)==46
                assert len({r[2:] for r in rects})==1
            sweeps.append(c.get('ID'))
    assert len(sweeps)==4
    for ident in ('60004','60009','2022','4021'):
        c=next(c for root in roots.values() for c in root.iter('Command') if c.get('ID')==ident)
        assert all(not state(r) for r in c),('全液相离片后仍有液滴占位',ident)
    assert dry_frames>=4
    report=dict(status='offline_pass',frames=checked,planned_volume_quantum_ul=.05,
                manual_load_sections=len(load),manual_aspiration_sections=len(asp),
                full_region_resuspension_commands=sweeps,pcr_cycles=30,
                liquid_removed_only_at_manual_gates=True,magnet_only_frames_without_liquid_electrodes=dry_frames,
                zero_liquid_continuation_commands=['60004','60009','2022','4021'],
                all_positive_volume_droplets_energized=True,
                unverified=['hardware_hold','droplet_motion','bead_capture_yield','cross_contamination','microscopic_residual_liquid'])
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('out',type=Path);ap.add_argument('before',type=Path);ap.add_argument('--report',type=Path)
    a=ap.parse_args();report=check(a.out,a.before)
    if a.report:a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
