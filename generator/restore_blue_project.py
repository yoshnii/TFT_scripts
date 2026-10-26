"""Undo publication rotation only, retaining every current biological revision."""
import argparse,csv,hashlib,io,json,re,subprocess
from pathlib import Path
import xml.etree.ElementTree as E
from decimal import Decimal as D

from rotate_project_180 import (BASE_CHIP,ROTATED_CHIP,BOX,rotate_code,
                                rotate_script,verify_scripts)

REPORT='恢复原始蓝色坐标校验_20261026.json'
BASELINE='5a8f87d3c93cb73ba0026f36d53d4089417ee54e'


def blue_script(text):
    assert E.fromstring(text).get('ChipsetType')==ROTATED_CHIP
    text=re.sub(r'RelayCode="([^"]*)"',lambda m:'RelayCode="'+rotate_code(m[1])+'"',text)
    return text.replace(f'ChipsetType="{ROTATED_CHIP}"',f'ChipsetType="{BASE_CHIP}"')


def restore(folder):
    folder=Path(folder);root=Path(__file__).resolve().parent.parent
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()==BASELINE
    manifest=folder/'PTseq_SIRO16_A_10x.elabprj'
    old_manifest=manifest.read_text();assert E.fromstring(old_manifest).get('ChipModel')==ROTATED_CHIP
    canonical=folder/(BASE_CHIP+'.csv')
    tag='tft-first-on-chip-water-run-20261022'
    original=subprocess.check_output(['git','show',tag+':projects/PTseq_SIRO16_A_10x/'+canonical.name],cwd=root)
    assert canonical.read_bytes()==original
    updates={manifest:old_manifest.replace(f'ChipModel="{ROTATED_CHIP}"',f'ChipModel="{BASE_CHIP}"').encode()}
    rows=list(csv.DictReader(io.StringIO(original.decode('utf-8-sig'))))
    tec=next(r for r in rows if r['Type']=='Tec')
    tx,ty,tw,th=[D(tec[k]) for k in ('StartX','StartY','Width','Height')]
    proofs=[];thermal_masks=0
    for stem in ['main']+[f.get('include') for f in E.fromstring(old_manifest) if f.get('include')!='main']:
        path=folder/(stem+'.elab');old=path.read_text();new=blue_script(old)
        assert rotate_script(new)==old
        proof=verify_scripts(new,old)
        for flow in E.fromstring(new):
            if not any(c.get('Component')=='Heater1' for c in flow):continue
            loops=[c for c in flow if c.get('Component')=='HighVoltage' and c.get('Params')=='SCRIPTCYCLE_TRUE']
            assert len(loops)==1
            for relay in loops[0]:
                for y,x,h,w in BOX.findall(relay.get('RelayCode','')):
                    x,y,w,h=map(D,(x,y,w,h))
                    assert tx<=x and x+w<=tx+tw and ty<=y and y+h<=ty+th
                    thermal_masks+=1
        proofs.append(dict(file=path.name,before_sha256=hashlib.sha256(old.encode()).hexdigest(),
                           after_sha256=hashlib.sha256(new.encode()).hexdigest(),**proof))
        updates[path]=new.encode()
    for filename in ('手工吸液顺序.csv','手工加液运行.html'):
        path=folder/filename;raw=path.read_bytes().decode('utf-8')
        updated=BOX.sub(lambda m:rotate_code(m[0]),raw)
        if filename.endswith('.html'):
            note=('<p>当前工程：已恢复原始蓝色布局与原始电极坐标（未旋转，256×192）。'
                  'E孔在上、F孔在下，A侧孔在左、C侧孔在右；最新1.60＋0.40µL cDNA体系、'
                  '反应后保留0.50µL及其他流程参数均保留。Heater1身份及外部磁控时序不变。'
                  '本次不修改软件ChipRotate显示配置；工程坐标恢复不等于实物电极定位已验证。</p>')
            updated,n=re.subn(r'<p>当前工程：[^<]*</p>',note,updated,count=1);assert n==1
        if updated!=raw:updates[path]=updated.encode('utf-8')
    cdna=folder/'cDNA两微升反应局部校验_20261026.json'
    data=json.loads(cdna.read_text())
    assert [data[k] for k in ('sample_ul_per_lane','g4_ul_per_lane','cdna_ul_per_lane',
                             'retained_cdna_ul_per_lane','target_reaction_ul_per_lane')]==[1.6,.4,2.,.5,2.5]
    data['chip_model']=BASE_CHIP
    data['script_sha256']=hashlib.sha256(updates[folder/'PTseq_16_A.elab']).hexdigest()
    data['orientation_revision']='2026-10-26 restored original blue coordinates; biological checks retained'
    updates[cdna]=(json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode()
    tag_sha=subprocess.check_output(['git','rev-parse',tag+'^{}'],cwd=root,text=True).strip()
    assert tag_sha=='af98a3858761f6bde4f40ff845cee03b8d4ee9c2'
    report=dict(status='passed',date='2026-10-26',baseline_commit=BASELINE,
        source_chip=ROTATED_CHIP,published_chip=BASE_CHIP,rotation_degrees=0,
        operation='inverse existing 180-degree publication; not a biological rollback',
        scripts=proofs,thermal_hold_rectangles_checked=thermal_masks,
        canonical_layout_matches_first_water_run_tag=True,first_water_run_tag_unchanged=True,
        chip_csv_sha256=hashlib.sha256(original).hexdigest(),
        all_non_coordinate_parameters_unchanged=True,
        current_cdna_ul_per_lane=2.,retained_cdna_ul_per_lane=.5,target_reaction_ul_per_lane=2.5,
        flowchart_files_unchanged=True,temperatures_cycles_timing_voltage_frequency_unchanged=True,
        software_ChipRotate_config_changed=False,physical_addressing_verified=False,
        inactive_R180_csv_retained_as_historical_asset=True)
    updates[folder/REPORT]=(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()
    for path,content in updates.items():path.write_bytes(content)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path)
    restore(ap.parse_args().folder)
