"""纯形变的逐帧面积守恒检查；不把体积变量未变当作电极面积守恒。"""
import argparse,json
from collections import Counter
from pathlib import Path
import protocol,rigid,contour,geom
from scene import Scene

def check():
    if hasattr(Scene,'_record'):
        original=Scene._record;previous={};frames=0;changes=0
        def audited_cells(S,*a,**kw):
            nonlocal previous,frames,changes
            original(S,*a,**kw);frames+=1;current={}
            for d in S.drops.values():
                if d.vol<=0:continue
                cells=d.target_cells();assert len(cells)==geom.cells(d.vol) and contour.connected(cells)
                current[id(d)]=(d,d.vol,cells)
                if id(d) in previous and previous[id(d)][1]==d.vol:
                    assert len(cells)==len(previous[id(d)][2]),'同体积液滴面积改变'
                    if cells!=previous[id(d)][2]:changes+=1
            previous=current
        Scene._record=audited_cells
        try:protocol.Protocol((1,)).build()
        finally:Scene._record=original
        return dict(status='pass',actual_frame_count=frames,changed_liquid_frames=changes,
                    actual_electrode_cells_checked=True,area_changed_frames=0,
                    every_liquid_contour_connected=True,visual_check_performed=False,hardware_verified=False)
    original=rigid.reshape_plan
    groups=Counter();examples={};frames=0;failures=0
    def audited(d,target):
        nonlocal frames,failures
        source=d.rect;area=source[2]*source[3];volume=d.vol
        for rect in original(d,target):
            frames+=1
            assert d.vol==volume,'纯形变修改计划液量'
            if rect[2]*rect[3]!=area:
                failures+=1
                key=(source[2:],tuple(target[2:]));groups[key]+=1
                examples.setdefault(key,dict(drop=d.name,planned_ul=volume,
                    source_cells=area,first_changed_cells=rect[2]*rect[3]))
            yield rect
    rigid.reshape_plan=audited
    try:protocol.Protocol((1,)).build()
    finally:rigid.reshape_plan=original
    return dict(status='fail' if failures else 'pass',reshape_frames=frames,
        area_changed_frames=failures,rule='every pure reshape frame must equal its starting electrode area',
        violations=[dict(source=list(a),target=list(b),failed_frames=n,**examples[(a,b)])
                    for (a,b),n in sorted(groups.items())],
        production_files_modified=False,visual_check_performed=False,
        physical_liquid_volume_verified=False)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--report',type=Path);a=ap.parse_args()
    result=check();payload=json.dumps(result,ensure_ascii=False,indent=2)
    print(payload)
    if a.report:a.report.write_text(payload+'\n',encoding='utf-8')
    raise SystemExit(1 if result['status']=='fail' else 0)
