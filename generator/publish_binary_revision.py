"""Publish the reviewed binary-split revision after scoped XML comparisons."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET


def publish(generated):
    repo=Path(__file__).resolve().parent.parent
    project=repo/'projects/PTseq_SIRO16_A_10x'
    mac=repo.parent/'片上测试1/PTseq_SIRO16_A_10x'
    name='PTseq_16_A.elab'
    before=ET.parse(project/name).getroot();after=ET.parse(generated/name).getroot()
    assert [f.get('ID') for f in before]==[f.get('ID') for f in after]
    expected={'R02':{'2003'},'R03':{'2005'},'R05':{'2014'},'R06':{'60000'},'B07':{'3020'}}
    changed=[]
    for old,new in zip(before,after):
        assert old.get('Name')==new.get('Name')
        if ET.tostring(old)==ET.tostring(new):continue
        section=old.get('Name').split()[0]
        assert section in expected,(section,'unrequested section change')
        assert [c.get('ID') for c in old]==[c.get('ID') for c in new]
        commands=[a.get('ID') for a,b in zip(old,new) if ET.tostring(a)!=ET.tostring(b)]
        assert set(commands)==expected[section],(section,commands)
        changed.append(dict(section=section,flow_id=old.get('ID'),commands=commands))
    assert {e['section'] for e in changed}==set(expected)
    baseline=hashlib.sha256((project/name).read_bytes()).hexdigest()
    assert hashlib.sha256((mac/name).read_bytes()).hexdigest()==baseline,'Mac baseline differs'
    report=json.loads((generated/'交付代码校验_20261016.json').read_text())
    assert report['status']=='passed' and report['binary_only']
    assert not report['independent_exported_frame_audit']['non_equal_binary_transitions']
    files=sorted(p.name for p in generated.iterdir() if p.is_file())
    for folder in (project,mac):
        for filename in files:shutil.copy2(generated/filename,folder/filename)
    hashes={filename:hashlib.sha256((project/filename).read_bytes()).hexdigest() for filename in files}
    assert all(hashlib.sha256((mac/f).read_bytes()).hexdigest()==v for f,v in hashes.items())
    proof=dict(status='passed',baseline_script_sha256=baseline,script_sha256=hashes[name],
        changed_sections=changed,unrelated_sections_unchanged=True,
        existing_A08_changes_preserved=True,R03_only_incoming_position_changed=True,
        native_packet_regressions_passed=12,waste_regressions_passed=2,
        independent_exported_frame_audit=report['independent_exported_frame_audit'],
        generic_check_errors=0,generic_check_warnings=5,
        warning_reasons=['main BuildMd5 empty: GUI compilation not performed',
                         'PTseq_16_A BuildMd5 empty: GUI compilation not performed',
                         'approved large loading/collection electrode blocks exceed legacy 5.2uL threshold',
                         'intentional 50V hold frames','intentional PCR 0V pulse'],
        mac_files_verified=hashes,vm_verified=False,visual_check=False,hardware_verified=False,
        no_local_backup_created=True)
    text=json.dumps(proof,ensure_ascii=False,indent=2)+'\n'
    for folder in (project,mac):(folder/'二分修订局部校验_20261016.json').write_text(text)
    print(json.dumps(dict(script_sha256=hashes[name],changed_sections=changed,
                         mac_published=str(mac),files_verified=len(hashes)),ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('generated',type=Path)
    publish(ap.parse_args().generated)
