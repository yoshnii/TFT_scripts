"""Keep orange layout coordinates, compensate only exported electrode addresses.

This implements an assumed 180-degree device-address mapping. It is not proof
that the connected device actually has that mapping. No controller interception
or GUI/display patch is performed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as E

from rotate_project_180 import BOX,BASE_CHIP,ROTATED_CHIP,rotate_code

CONFIG='chip_orientation.json'
REPORT='电极寻址180度补偿校验_20261023.json'


def address_rotation(folder):
    path=Path(folder)/CONFIG
    return int(json.loads(path.read_text()).get('electrode_address_compensation_degrees',0)) if path.exists() else 0


def transform_script(text):
    """Do not change ChipsetType, channel IDs, names, timing or dependencies."""
    assert E.fromstring(text).get('ChipsetType')==ROTATED_CHIP
    return re.sub(r'RelayCode="([^"]*)"',lambda m:'RelayCode="'+rotate_code(m[1])+'"',text)


def layout_coordinate_script(folder,name):
    """Read a compensated file in its orange layout coordinates for offline QA."""
    text=(Path(folder)/name).read_text()
    return transform_script(text) if address_rotation(folder)==180 else text


def verify(before,after):
    a,b=E.fromstring(before),E.fromstring(after)
    frames=rectangles=0
    for source,target in zip(a.iter('RelayItem'),b.iter('RelayItem')):
        code=source.get('RelayCode','')
        assert target.get('RelayCode','')==rotate_code(code)
        assert rotate_code(target.get('RelayCode',''))==code
        target.set('RelayCode',code)
        frames+=1;rectangles+=len(BOX.findall(code))
    assert E.tostring(a)==E.tostring(b),'A non-address parameter changed'
    return dict(frames=frames,rectangles=rectangles,
                all_non_address_parameters_unchanged=True,
                inverse_transform_recovers_every_original_frame=True)


def publication_files(folder,fresh_layout_output=False):
    folder=Path(folder)
    if address_rotation(folder)==180 and not fresh_layout_output:
        # Idempotent offline publishing: do not rotate a second time.
        return {},json.loads((folder/REPORT).read_text())
    assert address_rotation(folder) in (0,180)
    manifests=list(folder.glob('*.elabprj'));assert len(manifests)==1
    manifest=E.parse(manifests[0]).getroot()
    assert manifest.get('ChipModel')==ROTATED_CHIP
    files={};scripts=[]
    for item in manifest:
        stem=item.get('include')
        if not stem:continue
        path=folder/(stem+'.elab');before=path.read_text();after=transform_script(before)
        scripts.append(dict(file=path.name,before_sha256=hashlib.sha256(before.encode()).hexdigest(),
            after_sha256=hashlib.sha256(after.encode()).hexdigest(),**verify(before,after)))
        if after!=before:files[path]=after.encode()
    for name in ('手工吸液顺序.csv','手工加液运行.html'):
        path=folder/name
        if not path.exists():continue
        before=path.read_bytes().decode('utf-8-sig')
        after=BOX.sub(lambda m:rotate_code(m[0]),before)
        if name.endswith('.html'):
            note=('<p>当前工程：保留橙色R180芯片布局，全部电极指令另作180°寻址补偿；'
                  '本页明确列出的电极坐标为补偿后的设备地址，不是橙色布局上的预期物理位置。'
                  'IDE直接按指令坐标高亮，因此可能不与橙色孔位重合。'
                  'E/F/S/B及A1–A16保留原身份。温控指令仍为Heater1，磁铁仍由外部控制。'
                  '本版基于设备寻址反转180°的假设，真实上电、温控与磁铁对位均待实机确认。</p>')
            after,n=re.subn(r'<p>当前工程：芯片布局及全部步骤电极已整体旋转180°[^<]*</p>',
                            lambda _:note,after,count=1)
            assert n==1
        files[path]=after.encode()
    config=dict(layout_model=ROTATED_CHIP,layout_rotation_degrees=180,
        electrode_address_compensation_degrees=180,
        assumed_device_address_rotation_degrees=180,hardware_mapping_verified=False)
    files[folder/CONFIG]=(json.dumps(config,ensure_ascii=False,indent=2)+'\n').encode()
    proof=dict(status='software_checks_passed_physical_mapping_pending',date='2026-10-23',
        scope='All electrode addresses only; retain orange chip layout',scripts=scripts,
        layout_model=ROTATED_CHIP,layout_csv_unchanged=True,
        layout_csv_sha256=hashlib.sha256((folder/(ROTATED_CHIP+'.csv')).read_bytes()).hexdigest(),
        manifest_flowchart_heater_delay_cycles_waveforms_amp_freq_unchanged=True,
        actual_volume_area_and_reagent_ratios_unchanged=True,
        address_transform='(Y,X)(H,W) -> (192-Y-H,256-X-W)(H,W)',
        predicted_device_transform_degrees=180,physical_device_transform_verified=False,
        software_highlight_is_device_address_not_predicted_physical_position=True,
        heater_identity='Heater1 unchanged; actual thermal-zone wiring unverified',
        magnetic_control='External control unchanged; actual magnet alignment unverified',
        runtime_loops_pauses_or_confirmation_dialogs_added=False,
        previous_reports_use_pre_compensation_layout_coordinates=True,
        GUI_checked=False,hardware_verified=False)
    files[folder/REPORT]=(json.dumps(proof,ensure_ascii=False,indent=2)+'\n').encode()
    return files,proof


def write_publication(folder,fresh_layout_output=False):
    files,proof=publication_files(folder,fresh_layout_output)
    for path,data in files.items():path.write_bytes(data)
    return proof


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--write',action='store_true')
    args=ap.parse_args();files,proof=publication_files(args.folder)
    if args.write:
        for path,data in files.items():path.write_bytes(data)
    print(json.dumps({k:proof[k] for k in ('status','scope','scripts')},ensure_ascii=False,indent=2))
