"""Publish only the approved 1.60+0.40µL cDNA prefix and its operator metadata.

Preserve the current main's chip orientation and all A09-and-later native flows.
No workbook changes, physical device commands, or local backup copies.
"""
import argparse, copy, csv, hashlib, io, json, re, tempfile
from pathlib import Path
import xml.etree.ElementTree as E
from xml.sax.saxutils import escape

import geom, protocol
from project_structure import prepare_scripts
from manual_sections import write_loading_order, write_aspiration_order
from model import Script
from update_workbook_volumes_local import remap, replace_flow, read_csv
from update_transport_cleanup_local import validate_scene
from rotate_project_180 import rotate_code
from audit_binary_frames import audit

REPORT='cDNA两微升反应局部校验_20261026.json'
PREFIX=[1000,1002,50000,1006,1010,1019,1022,50001]
COMMANDS=[[1001],[1003,1004],[1005],[1007,1008,1009],
          list(range(1011,1019)),[1020,1021],[1023,1024],[1025]]


def generated():
    p=protocol.Protocol((1,))
    p.cdna_and_target_add()
    scripts,_,_=prepare_scripts(p.segs,'ChipArea_192_256_96_SIRO',1)
    flows=scripts[0].flows
    assert len(flows)==len(PREFIX)
    for f,fid,cids in zip(flows,PREFIX,COMMANDS):remap(f,fid,cids)
    assert all(d.vol==2.5 and d.rect==(31,28+9*i,10,5)
               for i,d in enumerate(p.S.drops.values()))
    assert len(p.S.manual_aspirations)==1
    gate=p.S.manual_aspirations[0][1]
    assert gate['volume_ul']==24. and gate['port_volumes']=={'F3':12.,'F1':12.}
    assert gate['progressive_recovery'] and not gate['rects']
    scene=validate_scene(p,'2µL cDNA -> .5µL retained -> 2.5µL target PCR',64.,24.)
    g4=[e for e in p.S.native_leaf_events if e['reagent']=='G4']
    assert len(g4)==16 and all(e['actual_ul']==.4 and e['shape']==[3,4] for e in g4)
    for e in p.S.split_events:
        assert e['axis']=='x' and e['volumes'][0]==e['volumes'][1]
    return p,flows,scene


def table(rows,headers):
    return ''.join('<tr>'+''.join('<td>'+escape(str(r.get(h,'')))+'</td>' for h in headers)+'</tr>' for r in rows)


def update(folder):
    folder=Path(folder);script=folder/'PTseq_16_A.elab'
    original=script.read_text();before=E.fromstring(original)
    old={int(f.get('ID')):f for f in before}
    assert 70000 in old and 1019 not in old,'Expected current small-cDNA main baseline'
    rotated=before.get('ChipsetType').endswith('_R180')
    p,flows,scene=generated()
    fresh={f.id:f for f in flows}
    xmls={fid:f.xml() for fid,f in fresh.items()}
    if rotated:
        xmls={fid:re.sub(r'RelayCode="([^"]*)"',lambda m:'RelayCode="'+rotate_code(m[1])+'"',text)
              for fid,text in xmls.items()}
    text=replace_flow(original,70000,'')
    for fid in PREFIX:
        if fid!=1019:text=replace_flow(text,fid,xmls[fid])
    anchor=xmls[1010].strip()
    assert text.count(anchor)==1
    text=text.replace(anchor,anchor+'\n'+xmls[1019].strip(),1)
    after=E.fromstring(text);newflows=list(after)
    for f in after:
        fid=int(f.get('ID'))
        if fid not in PREFIX:assert E.tostring(f)==E.tostring(old[fid]),('unrelated flow',fid)
    assert E.tostring(after.find("Flow[@ID='1030']"))==E.tostring(old[1030])
    expected=old[50001][-1][-1].get('RelayCode')
    assert after.find("Flow[@ID='50001']")[-1][-1].get('RelayCode')==expected
    assert [c.get('Params') for c in after.find("Flow[@ID='1010']") if c.get('Component')=='Heater1']==['37,1','85,1','4,1']
    assert [c.get('Timeout') for c in after.find("Flow[@ID='1010']") if c.get('Component')=='Delay']==['600','5','30']
    ids=[n.get('ID') for f in after for n in (f,*f)]
    assert len(ids)==len(set(ids))
    assert len({f.get('Name').split()[0] for f in after})==len(after)
    for f in after:
        for c in f:
            pre=c.get('PreTask','-1,UnTask').split(',')[0]
            assert pre=='-1' or pre in ids
    # Native-frame audit includes the changed prefix plus adjacent A09.
    context=E.Element('Script')
    for f in after:
        if int(f.get('ID')) in PREFIX+[1026]:context.append(copy.deepcopy(f))
    with tempfile.TemporaryDirectory(prefix='tft-cdna-context-') as tmp:
        path=Path(tmp)/'context.elab';E.ElementTree(context).write(path)
        audit_result=audit(path)
    assert not audit_result['non_equal_binary_transitions'],audit_result

    # Keep non-A operator rows; refresh only changed rows and their links.
    with tempfile.TemporaryDirectory(prefix='tft-cdna-operator-') as tmp:
        holder=Script.__new__(Script);holder.name='PTseq_16_A';holder.flows=flows
        write_aspiration_order(tmp,[holder])
        ah,fresh_asp=read_csv(Path(tmp)/'手工吸液顺序.csv')
        loads=[]
        for i,f in enumerate(flows):
            gate=getattr(f.cmds[-1],'manual_load',None)
            if gate:
                loads.append(dict(script='PTseq_16_A',section_id=f.id,section_name=f.name,
                    command_id=f.cmds[-1].id,next_script='PTseq_16_A',
                    next_section_id=flows[i+1].id,next_section_name=flows[i+1].name,**gate))
        write_loading_order(tmp,loads)
        lh,fresh_load=read_csv(Path(tmp)/'手工加液顺序.csv')
    assert len(fresh_load)==3
    assert float(fresh_load[0][lh[6]])==1.6
    assert float(fresh_load[1][lh[6]])==6.4
    assert fresh_load[1][lh[7]]=="{'E1': 3.2, 'E2': 3.2}"
    assert float(fresh_load[2][lh[6]])==32.
    # The metadata writer emits canonical codes; transform only new aspiration codes.
    if rotated:
        for r in fresh_asp:r[ah[7]]=rotate_code(r[ah[7]])
    idx={int(f.get('ID')):i for i,f in enumerate(after)}
    tables=[]
    for filename,h,fr in [('手工加液顺序.csv',lh,fresh_load),('手工吸液顺序.csv',ah,fresh_asp)]:
        oldh,rs=read_csv(folder/filename);assert oldh==h
        rs=[r for r in rs if int(r[h[1]]) not in set(PREFIX)|{70000}]+fr
        rs.sort(key=lambda r:idx[int(r[h[1]])])
        for r in rs:
            f=newflows[idx[int(r[h[1]])]];j=idx[int(r[h[1]])]+1
            nxt=newflows[j] if j<len(newflows) else None
            r[h[2]]=f.get('Name')
            r[h[-3]]='PTseq_16_A.elab' if nxt is not None else ''
            r[h[-2]]=nxt.get('ID') if nxt is not None else ''
            r[h[-1]]=nxt.get('Name') if nxt is not None else '工程结束'
        tables.append((filename,h,rs))
    # Gate continuity: loaded liquid remains powered into actual motion.
    for row in fresh_load:
        f=after.find(f"Flow[@ID='{row[lh[1]]}']")
        nxt=newflows[idx[int(f.get('ID'))]+1]
        assert set(f[-1][-1].get('RelayCode').split(';'))==set(nxt[0][0].get('RelayCode').split(';'))

    lt=(folder/'PTseq_16_A.elab.layout').read_text()
    lt=replace_flow(lt,70000,'')
    for i,f in enumerate(after):
        fid=int(f.get('ID'))
        if fid in fresh:
            node=fresh[fid].layout(i)
            if fid==1019:
                mark='</Script>';lt=lt.replace(mark,node+'\n'+mark)
            else:lt=replace_flow(lt,fid,node)
    lt=re.sub(r'\s*<Arrow\b[^>]*/>','',lt)
    arrows='\n'.join(f' <Arrow EndID="{b.get("ID")}" StartID="{a.get("ID")}"/>' for a,b in zip(newflows,newflows[1:]))
    lt=lt.replace('</Script>',arrows+'\n</Script>')
    assert {f.get('ID') for f in E.fromstring(lt) if f.tag=='Flow'}=={f.get('ID') for f in after}
    page=(folder/'手工加液运行.html').read_text()
    replacements=iter('<table><tr>'+''.join('<th>'+escape(hh)+'</th>' for hh in h)
                      +'</tr>'+table(rs,h)+'</table>' for _,h,rs in tables)
    page,n=re.subn(r'<table\b[^>]*>[\s\S]*?</table>',lambda m:next(replacements),page)
    assert n==2
    note=('<p>2026-10-26 cDNA修订：每道S孔加入样本1.60µL，不再预先弃样；G4经E1/E2各加3.20µL，'
          '左右逐级对半后每道加入0.40µL，16道就绪后同时融合。每道以2.00µL完成原混匀和37℃10min→85℃5s→4℃。'
          'A06在cDNA结束后2.00→1.00→0.50µL逐级对半，每道保留0.50µL、弃1.50µL；'
          '16道共24.00µL废液在F3/F1各12.00µL，孔旁按原方式配合手工吸取、逐步撤电。'
          '之后每道加入原2.00µL靶向扩增预混液，总体系2.50µL。其他阶段和芯片方向不变。</p>')
    page=page.replace('<table',note+'<table',1)
    assert '70000</td>' not in page
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
    mapping=[dict(id=int(f.get('ID')),name=f.get('Name'),
                  previous=old.get(int(f.get('ID')),f).get('Name')) for f in after]
    report=dict(status='passed',scope='A01–A08 plus unchanged A09/PCR boundary',
        sample_ul_per_lane=1.6,g4_ul_per_lane=.4,cdna_ul_per_lane=2.,
        retained_cdna_ul_per_lane=.5,waste_ul_per_lane=1.5,
        g4_port_ul={'E1':3.2,'E2':3.2},waste_port_ul={'F3':12.,'F1':12.},
        target_reagent_ul_per_lane=2.,target_reaction_ul_per_lane=2.5,
        cdna_sample_fraction=.8,retained_sample_equivalent_ul=.4,retained_g4_equivalent_ul=.1,
        chip_model=before.get('ChipsetType'),scene=scene,exported_frame_audit=audit_result,
        split_event_count=len(p.S.split_events),all_splits_equal_and_symmetric=True,
        unchanged_from_A09=True,temperatures_and_holds_unchanged=True,
        layout_csv_unchanged=True,workbook_unchanged=True,physical_validation=False,
        baseline_sha256=hashlib.sha256(original.encode()).hexdigest(),
        script_sha256=hashlib.sha256(text.encode()).hexdigest())
    script.write_text(text)
    (folder/'PTseq_16_A.elab.layout').write_text(lt)
    (folder/'main.elab').write_text(mt)
    for filename,h,rs in tables:
        with (folder/filename).open('w',encoding='utf-8-sig',newline='') as fh:
            writer=csv.DictWriter(fh,h);writer.writeheader();writer.writerows(rs)
    (folder/'手工加液运行.html').write_text(page)
    (folder/'步骤编号映射_20261016.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+'\n')
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path)
    update(ap.parse_args().folder)
