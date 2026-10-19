"""Apply the approved workbook volumes without regenerating unrelated native flows."""
import argparse,copy,csv,hashlib,json,re,tempfile
from pathlib import Path
import xml.etree.ElementTree as E

import geom,ops,protocol
from cdna_aliquot import load_and_aliquot
from scene import Drop
from project_structure import prepare_scripts
from manual_sections import write_loading_order,write_aspiration_order
from audit_binary_frames import audit

CHIP='ChipArea_192_256_96_SIRO'


def remap(flow, fid, command_ids):
    assert len(flow.cmds)==len(command_ids),(flow.name,len(flow.cmds),command_ids)
    flow.id=fid
    for cmd,cid in zip(flow.cmds,command_ids):cmd.id=cid


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as fh:
        reader=csv.DictReader(fh);return reader.fieldnames,list(reader)


def replace_flow(text, fid, xml):
    text,n=re.subn(rf'\s*<Flow\b(?=[^>]*\bID="{fid}")[^>]*>[\s\S]*?</Flow>',
                   lambda _: '\n'+xml.strip() if xml else '',text,count=1)
    assert n==1,('missing flow',fid)
    return text


def update(folder):
    path=folder/'PTseq_16_A.elab';text=path.read_text();before=E.fromstring(text)
    old={int(f.get('ID')):f for f in before};baseline=hashlib.sha256(path.read_bytes()).hexdigest()
    assert 70000 not in old,'This volume revision is already applied'
    p=protocol.Protocol((1,));load_and_aliquot(p)
    p.add_reagent('A','A03 G4 cDNA反应液0.10µL/道：母液1.60µL四级对半','G4',.10)
    p.mix('A','A04 往复混匀',seconds=protocol.A03_MIX_SECONDS)
    p.thermal('A','A05 cDNA合成37℃10min→85℃5s→4℃；0.50µL全量保留',[(37,600),(85,5)],lid=105)
    assert all(d.vol==.5 and len(d.target_cells())==10 for d in p.S.drops.values())
    p.add_reagent('A','A06 加靶向扩增反应液2.00µL（台面预混）','AMP',2.)
    # Keep A09 and the native PCR block byte-identical, including their input mask.
    ops.move_rects(p.S,{ln.name:31 for ln in p.ALL})
    tail=p.cmd('接续已发布A09混匀起点')
    if tail:p.segs['A'][-1].cmds[-1].relays.extend(tail.relays)
    scripts,_,_=prepare_scripts(p.segs,CHIP,1);aflows=scripts[0].flows
    assert len(aflows)==8
    amap=[(1000,[1001]),(70000,[70001]),(1002,[1003,1004]),(50000,[1005]),
          (1006,[1007,1008,1009]),(1010,list(range(1011,1019))),
          (1022,[1023,1024]),(50001,[1025])]
    for f,(fid,cids) in zip(aflows,amap):remap(f,fid,cids)
    assert set(aflows[-1].cmds[-1].relays[-1].codes)==set(old[50001][-1][-1].get('RelayCode').split(';'))
    assert len(p.S.split_events)==93 # sample 48; G4 15; AMP 30
    # Explicit event checks are independent of the command naming/numbering.
    for event in p.S.split_events:
        assert len(event['volumes'])==2 and event['volumes'][0]==event['volumes'][1]
        assert event['partition_preserves_mother_cells']
        assert all(0<=b-a<=1 for a,b in zip(event['gaps'],event['gaps'][1:]))
    assert all(abs(d.vol-2.5)<1e-8 for d in p.S.drops.values())
    assert sum(info['volume_ul'] for _,info in p.S.manual_aspirations)==19.2

    q=protocol.Protocol((1,));q.magnet_state='up'
    for ln in q.ALL:
        d=q.S.add(Drop(ln.name,(ln.mag-1,ln.Y0,3,5),0));d.magnet_only=True
    for n in (1,2):
        q.elute_offchip('R1',f'R0{n} 纯化1第{n}次洗脱：溶解液3.00µL → 产物离片',
                       'G15',3.,3.,n,final=False)
    rscript,_,_=prepare_scripts(q.segs,CHIP,1);rflows=rscript[0].flows
    rids=[2015,2017,2022,2032,2038,2040,2045,2055]
    assert len(rflows)==len(rids)
    for flow,fid in zip(rflows,rids):
        remap(flow,fid,[int(c.get('ID')) for c in old[fid]])
        flow.name=old[fid].get('Name').replace('5.00µL','3.00µL')
    assert [e['incubation_seconds'] for e in q.elution_events]==[300,300]
    assert all(info['volume_ul']==48. for _,info in q.S.manual_aspirations)
    assert all(q.S.get(ln.name).vol==0 and q.S.get(ln.name).magnet_only for ln in q.ALL)
    replacements={f.id:f for f in [*aflows,*rflows]}
    # Native B01 only documents the manually prepared concentration; no branch added.
    bflow=copy.deepcopy(old[3000]);bflow.set('Name','B01 均一化产物50ng/µL，回片1.30µL/道=65ng · 加液承接（上电后手工加液）')
    bflow[0].set('Name',bflow[0].get('Name').replace('均一化产物','均一化产物50ng/µL（1.30µL=65ng）'))
    for fid,f in replacements.items():
        if fid!=70000:text=replace_flow(text,fid,f.xml())
    text=replace_flow(text,1019,'')
    text=replace_flow(text,3000,E.tostring(bflow,encoding='unicode'))
    anchor=aflows[0].xml()
    assert text.count(anchor)==1
    text=text.replace(anchor,anchor+'\n'+aflows[1].xml(),1)
    after=E.fromstring(text);changed=set(replacements)|{1019,3000}
    for f in after:
        fid=int(f.get('ID'))
        if fid not in changed:assert E.tostring(f).strip()==E.tostring(old[fid]).strip(),('unrelated flow',fid)
    assert E.tostring(next(f for f in after if f.get('ID')=='1030'))==E.tostring(old[1030])
    ids=[x.get('ID') for f in after for x in (f,*f)]
    assert len(ids)==len(set(ids))
    labels=[f.get('Name').split()[0] for f in after];assert len(labels)==len(set(labels))
    for f in after:
        for c in f:
            pre=c.get('PreTask','-1,UnTask').split(',')[0]
            assert pre=='-1' or pre in ids,(f.get('Name'),c.get('ID'),pre)
    # Loading gate masks must be identical to the following section's first frame.
    newflows=list(after)
    for flow in [*aflows,*rflows]:
        if not hasattr(flow.cmds[-1],'manual_load'):continue
        i=next(i for i,f in enumerate(newflows) if f.get('ID')==str(flow.id))
        nxt=newflows[i+1]
        assert set(flow.cmds[-1].relays[-1].codes)==set(nxt[0][0].get('RelayCode','').split(';'))-{''}
    # Only changed moves and neighboring input/output masks enter this audit.
    context=E.Element('Script')
    for f in after:
        if int(f.get('ID')) in replacements:context.append(copy.deepcopy(f))
    with tempfile.TemporaryDirectory(prefix='tft-volume-audit-') as tmp:
        check=Path(tmp)/'context.elab';E.ElementTree(context).write(check)
        result=audit(check)
    assert not result['non_equal_binary_transitions'],result

    layout=folder/'PTseq_16_A.elab.layout';lt=layout.read_text()
    for i,f in enumerate(after):
        fid=int(f.get('ID'))
        if fid in replacements:
            native=replacements[fid].layout(i)
            if fid!=70000:lt=replace_flow(lt,fid,native)
            else:
                marker='\n <Flow';start=lt.index(marker,lt.index('</Flow>'))
                lt=lt[:start]+'\n'+native+lt[start:]
        elif fid==3000:
            node=copy.deepcopy(next(x for x in E.fromstring(lt) if x.tag=='Flow' and x.get('ID')=='3000'))
            node.set('Name',bflow.get('Name'));node[0].set('Name',bflow[0].get('Name'))
            lt=replace_flow(lt,3000,E.tostring(node,encoding='unicode'))
    lt=replace_flow(lt,1019,'')
    lt=re.sub(r'\s*<Arrow\b[^>]*/>','',lt)
    arrows='\n'.join(f' <Arrow EndID="{b.get("ID")}" StartID="{a.get("ID")}"/>' for a,b in zip(newflows,newflows[1:]))
    lt=lt.replace('</Script>',arrows+'\n</Script>')
    lr=E.fromstring(lt)
    assert {x.get('ID') for x in lr if x.tag=='Flow'}=={x.get('ID') for x in after}

    # Regenerate only affected CSV rows, retain all other operator metadata.
    with tempfile.TemporaryDirectory(prefix='tft-volume-orders-') as tmp:
        from model import Script
        holder=Script.__new__(Script);holder.name='PTseq_16_A';holder.flows=[*aflows,*rflows]
        write_aspiration_order(tmp,[holder])
        _,fresh_asp=read_csv(Path(tmp)/'手工吸液顺序.csv')
    ah,arows=read_csv(folder/'手工吸液顺序.csv')
    arows=[r for r in arows if int(r[ah[1]]) not in changed]
    arows.extend(fresh_asp)
    lh,lrows=read_csv(folder/'手工加液顺序.csv')
    lrows=[r for r in lrows if int(r[lh[1]]) not in replacements and r[lh[1]]!='1019']
    for flow in [*aflows,*rflows]:
        cmd=flow.cmds[-1];info=getattr(cmd,'manual_load',None)
        if info is None:continue
        values=['PTseq_16_A.elab',flow.id,flow.name,cmd.id,info['hole'],info['reagent'],
                info['volume_ul'],info.get('port_volumes',''),info['lanes'],'1/1','','','']
        lrows.append(dict(zip(lh,map(str,values))))
    positions={int(f.get('ID')):i for i,f in enumerate(after)}
    for headers,rows in ((ah,arows),(lh,lrows)):
        rows.sort(key=lambda r:positions[int(r[headers[1]])])
        for row in rows:
            idx=positions[int(row[headers[1]])];current=newflows[idx]
            nxt=newflows[idx+1] if idx+1<len(newflows) else None
            if int(current.get('ID')) not in changed and (nxt is None or int(nxt.get('ID')) not in changed):continue
            row[headers[2]]=current.get('Name')
            row[headers[-3]]='PTseq_16_A.elab' if nxt is not None else ''
            row[headers[-2]]=nxt.get('ID') if nxt is not None else ''
            row[headers[-1]]=nxt.get('Name') if nxt is not None else '工程结束'
    # B01's existing loading row keeps every numeric value and only changes wording.
    for row in lrows:
        if row[lh[1]]=='3000':row[lh[5]]='均一化产物50ng/µL（1.30µL=65ng）'
    for asp in fresh_asp:
        assert int(asp[ah[1]]) in replacements
    total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
    main=folder/'main.elab';mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',main.read_text(),count=1);assert n==1
    # All checks above precede publication, avoiding partially updated native files.
    path.write_text(text);layout.write_text(lt);main.write_text(mt)
    for filename,headers,rows in [('手工加液顺序.csv',lh,lrows),('手工吸液顺序.csv',ah,arows)]:
        with (folder/filename).open('w',encoding='utf-8-sig',newline='') as fh:
            writer=csv.DictWriter(fh,headers);writer.writeheader();writer.writerows(rows)
    mapping=folder/'步骤编号映射_20261016.json'
    prior_mapping={row['id']:row for row in json.loads(mapping.read_text())}
    mapping.write_text(json.dumps([dict(id=int(f.get('ID')),name=f.get('Name'),previous=old.get(int(f.get('ID')),f).get('Name'))
        if int(f.get('ID')) in changed else prior_mapping[int(f.get('ID'))] for f in after],ensure_ascii=False,indent=2)+'\n')
    proof=dict(status='passed',scope='cDNA aliquots/AMP boundary, purification1 elutions and manual concentration metadata',
        baseline_sha256=baseline,script_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        sample_mother_ul=1.6,sample_retained_ul=.4,sample_waste_ul_per_lane=1.2,
        reagent_mother_ul=1.6,reagent_per_lane_ul=.1,cdna_total_ul=.5,cdna_all_retained=True,
        amplifier_total_ul=2.5,normalization_ng_ul=50,return_ul=1.3,return_ng=65,
        purification1_elutions_ul=[3,3],purification1_mix_timer_seconds=288,
        elution_incubation_seconds=300,magnetic_capture_seconds=120,periodic_mix_timer_seconds=49,
        binary_events_checked=len(p.S.split_events),exported_frame_audit=result,
        pcr_native_block_unchanged=True,other_native_flows_unchanged=True,
        hardware_verified=False,vm_verified=False)
    (folder/'体积表对齐局部校验_20261019.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    update(parser.parse_args().folder)
