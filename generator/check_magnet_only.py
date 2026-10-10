"""参考排液证据和无计划液相磁珠回溶回归；不验证实物残液。"""
import argparse,json,re,zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
import geom,protocol,rigid,ops

def check(out,reference):
    evidence=[]
    with zipfile.ZipFile(reference) as z:
        for filename,separation,rewetting in (
            ('260828_5and8.elab','2255','2262'),
            ('260902_96sampleV2_1.elab','1335','1397')):
            name=next(n for n in z.namelist() if n.endswith('/'+filename))
            root=ET.fromstring(z.read(name));flows=list(root)
            f=next(f for f in root if f.get('ID')==separation)
            last=f[-1];assert last.get('Name')=='排废液'
            rects=[tuple(map(int,re.fullmatch(r'\((\d+),(\d+)\)\((\d+),(\d+)\)',s).groups()))
                   for s in last[-1].get('RelayCode').split(';') if s]
            assert len(rects)==4 and all(x==250 and w==6 for y,x,h,w in rects)
            next_flow=flows[flows.index(f)+1]
            assert next_flow.get('ID')==rewetting and '回溶' in next_flow.get('Name')
            evidence.append(dict(script=name,separation_flow=separation,last_waste_command=last.get('ID'),
                                 last_frame=last[-1].get('RelayCode'),next_flow=rewetting,next_flow_name=next_flow.get('Name')))
    separations=[];elutions=[];old_sep=rigid.supernatant_bites;old_merge=ops.merge_all
    def separate(S,lanes,blob,pellet_vol,final=True):
        initial=round(sum(S.get(ln.name).vol for ln in lanes),2)
        gates_before=len(getattr(S,'manual_aspirations',[]))
        result=old_sep(S,lanes,blob,pellet_vol,final)
        assert pellet_vol==0 and len(S.drops)==16
        assert all(d.vol==0 and getattr(d,'magnet_only',False) and not d.rects() for d in S.drops.values())
        assert not S.relays[-1].codes
        assert all(S.get(ln.name).rect==ln.rect(ln.mag-1,3) for ln in lanes)
        events=S.manual_aspirations[gates_before:]
        removed=round(sum(info['volume_ul'] for relay,info in events),2)
        assert removed==initial,(initial,removed)
        separations.append(dict(initial_liquid_ul=initial,removed_liquid_ul=removed,
                                remaining_planned_liquid_ul=0,bead_locations=16,
                                liquid_electrode_blocks_after_aspiration=0))
        return result
    def merge(S,lanes,guest_tag,host_tag='L'):
        lanes=list(lanes)
        if not all(getattr(S.get(f'{host_tag}{ln.id}'),'magnet_only',False) for ln in lanes):
            return old_merge(S,lanes,guest_tag,host_tag)
        before={ln.id:(S.get(f'{guest_tag}{ln.id}').rect,S.get(f'{guest_tag}{ln.id}').vol,
                       S.get(f'{host_tag}{ln.id}').rect) for ln in lanes}
        expected_blocks=sum(len(S.get(f'{guest_tag}{ln.id}').rects()) for ln in lanes)
        assert len(S.relays[-1].codes)==expected_blocks,'S孔承接混入磁珠占位电极'
        start=len(S.relays);result=old_merge(S,lanes,guest_tag,host_tag)
        for ln in lanes:
            r,v,p=before[ln.id];d=S.get(f'{host_tag}{ln.id}')
            assert d.vol==v and d.rect[2:]==r[2:] and d.rect[0]<=p[0] and d.rect[0]+d.rect[2]>=p[0]+p[2]
            assert not getattr(d,'magnet_only',False) and d.rects()
        for relay in S.relays[start:]:assert len(relay.codes)==expected_blocks
        elutions.append(dict(per_lane_ul=before[lanes[0].id][1],rows=16,shape_fixed_during_rewetting=True,
                             no_zero_volume_liquid_union=True))
        return result
    rigid.supernatant_bites=separate;ops.merge_all=merge
    try:P=protocol.Protocol((1,));P.build()
    finally:rigid.supernatant_bites=old_sep;ops.merge_all=old_merge
    assert [e['initial_liquid_ul'] for e in separations]==[80,94.4]
    assert [e['per_lane_ul'] for e in elutions]==[5,5,3,3]
    native=ET.parse(out/'PTseq_16_A.elab').getroot()
    cmds={c.get('ID'):c for c in native.iter('Command')}
    zero_ids=['2022','4021']+[f[-1].get('ID') for f in native
        if f.get('Name','').startswith(('R04','C04')) and f.get('Name','').endswith('吸液完成后的续行')]
    for ident in zero_ids:
        assert all(not r.get('RelayCode') for r in cmds[ident])
    for ident in ('2015','2026','4014','4025'):
        assert '磁铁保持升起' in cmds[ident].get('Name') and '覆盖磁珠后再降下' in cmds[ident].get('Name')
    report=dict(status='magnet_only_pass',reference_evidence=evidence,separations=separations,
                elutions=elutions,zero_liquid_native_commands=zero_ids,
                external_magnet_instruction_keeps_beads_until_new_liquid_covers=True,
                chemistry_and_pcr_changed=False,visual_check_performed=False,physical_run_performed=False,
                physical_residual_liquid_verified=False)
    print(json.dumps(report,ensure_ascii=False,indent=2));return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('out',type=Path);ap.add_argument('reference',type=Path)
    ap.add_argument('--report',type=Path);a=ap.parse_args();r=check(a.out,a.reference)
    if a.report:a.report.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
