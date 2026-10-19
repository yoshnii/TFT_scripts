"""Apply six approved transport/cleanup rules to released native sections only.

No workbook edits, hardware actions, local backups or implicit extra pauses.
All publication happens after geometry, mass and native-boundary checks.
"""
import argparse,copy,csv,hashlib,json,pickle,re,tempfile
from pathlib import Path
import xml.etree.ElementTree as E
import geom,ops,protocol,rigid
from scene import Drop,Relay
from model import Script,Cmd
from project_structure import prepare_scripts
from update_workbook_volumes_local import remap,replace_flow,read_csv
from audit_binary_frames import audit
from waste_recovery import boxes

CHIP='ChipArea_192_256_96_SIRO'
REPORT='运输间距与末尾清洗局部校验_20261019.json'


def codes(relay):return set(filter(None,relay.get('RelayCode','').split(';')))


def last_hv(flow):return [c for c in flow if c.get('Component')=='HighVoltage'][-1][-1]


def seed(relay,volumes):
    p=protocol.Protocol((1,));rects=boxes(relay.get('RelayCode',''))
    for ln in p.ALL:
        row=sorted((rc for rc in rects if rc[1]==ln.Y0),key=lambda q:q[0])
        assert len(row)==len(volumes),(ln.id,row,volumes)
        for rc,(tag,vol) in zip(row,volumes):
            name=ln.name if not tag else tag+ln.id
            d=p.S.add(Drop(name,rc,vol));d.electrode_area=rc[2]*rc[3]
            d.margin_budget=d.electrode_area-geom.cells(vol)
            assert d.margin_budget>=0
            d.native_authorized_padding=d.margin_budget>0
    p.S.step(geom.HOLD_MS,changed=[])
    return p


def validate_scene(p,title,initial_mass=None,expected_waste=None):
    events=getattr(p.S,'split_events',[])
    for event in events:
        assert event['axis']=='x' and len(event['volumes'])==2
        assert event['volumes'][0]==event['volumes'][1]
        assert event['partition_preserves_mother_cells']
        assert all(0<=b-a<=1 for a,b in zip(event['gaps'],event['gaps'][1:]))
        if event['parent'].startswith('LIQ'):
            row=int(re.match(r'LIQA(\d+)',event['parent'])[1]);ln=p.ALL[row-1]
            assert event['positions'][0][1][0]!=ln.mag,('切口碰磁珠中心',event)
            for frame in event['positions']:
                for x,y,w,h in frame:
                    assert ln.X0<=x and x+w<=ln.X0+36
                    assert ln.Y0-1<=y and y+h<=ln.Y0+5
        if event['parent'].startswith('D'):
            a,b=event['positions'][0]
            assert a[2]*2>=a[3],('顶部分液前母框必须横向或方形',event['parent'],a,b)
    removed=sum(info['volume_ul'] for _,info in p.S.manual_aspirations)
    if initial_mass is not None:
        assert abs(sum(d.vol for d in p.S.drops.values())+removed-initial_mass)<1e-7
    if expected_waste is not None:assert abs(removed-expected_waste)<1e-7
    for relay,info in p.S.manual_aspirations:
        if info.get('progressive_recovery'):
            assert not info['rects'] and not info['final_recovery_electrodes']
            assert all(rc[1]<172 for code in relay.codes for rc in boxes(code))
    return dict(case=title,frames=p.S.nsteps,equal_x_splits=len(events),
                aspirated_ul=removed,static_geometry_passed=True,
                magnet_cut_lines_avoid_black_center=True)


def generate(before,old):
    replacements={};models=[];proof=[]

    def register(flows,ids,p,case,rename=False):
        assert len(flows)==len(ids),(case,len(flows),ids)
        for flow,fid in zip(flows,ids):
            existing=old.get(fid)
            command_ids=([int(c.get('ID')) for c in existing] if existing is not None
                         else list(range(fid+1,fid+1+len(flow.cmds))))
            remap(flow,fid,command_ids)
            if existing is not None and not rename:flow.name=existing.get('Name')
            replacements[fid]=flow;models.append(flow)
        print('CHECKED',case,flush=True)

    def prepared(p):return prepare_scripts(p.segs,CHIP,1)[0][0].flows

    # Native A01 is retained byte-for-byte. Only equal aliquoting/transport A02 changes.
    from cdna_aliquot import load_and_aliquot
    p=protocol.Protocol((1,));load_and_aliquot(p)
    fs=prepared(p);register([fs[1]],[70000],p,'sample waste')
    proof.append(validate_scene(p,'sample 1.6 -> .8 -> .4',25.6,19.2))

    # Seven original top-dispense pairs; preserve every downstream entry mask.
    cases=[('G4',.1,.4,1002,50000,'A'),('AMP',2.,.5,1022,50001,'A'),
           ('G14',2.5,2.5,2000,50002,'R1'),('G8',.7,1.3,3002,50003,'B'),
           ('LIG',1.4,2.6,3026,50005,'B'),('EDTA',.2,4.,3041,50006,'B'),
           ('G16',1.7,4.2,4000,50007,'C')]
    for reagent,vol,host,fid,did,seg in cases:
        p=seed(old[fid][0][0],[('',host)])
        p.add_reagent(seg,old[fid].get('Name').split(' · ')[0],reagent,vol)
        # Match the released downstream position without touching its chemistry,
        # temperature, mix-loop, pulse or timing commands.
        target=boxes(last_hv(old[did]).get('RelayCode'))
        byrow={rc[1]:rc for rc in target};assert len(byrow)==16
        for ln in p.ALL:
            d=p.S.get(ln.name);assert len(d.target_cells())==byrow[ln.Y0][2]*byrow[ln.Y0][3]
        rigid.reshape_many(p.S,[(p.S.get(ln.name),(*p.S.get(ln.name).rect[:2],*byrow[ln.Y0][2:])) for ln in p.ALL])
        ops.move_rects(p.S,{ln.name:byrow[ln.Y0][0] for ln in p.ALL})
        tail=p.cmd('衔接原下游反应区位置')
        if tail:p.segs[seg][-1].cmds[-1].relays.extend(tail.relays)
        fs=prepared(p);register(fs,[fid,did],p,reagent)
        assert set(fs[-1].cmds[-1].relays[-1].codes)==codes(last_hv(old[did]))
        assert set(fs[0].cmds[-1].relays[-1].codes)==set(fs[1].cmds[0].relays[0].codes)
        assert set(fs[0].cmds[0].relays[0].codes)==codes(old[fid][0][0])
        assert all(abs(d.vol-host-vol)<1e-8 for d in p.S.drops.values())
        proof.append(validate_scene(p,reagent))

    # All captured aqueous liquid is discarded; dry beads remain on the magnet.
    ordered=list(before)
    for fid,other,vol,seg in ((2013,51000,5.,'R1'),(4013,51002,5.9,'C')):
        prev=ordered[ordered.index(old[fid])-1]
        p=seed(last_hv(prev),[('',vol)]);p.magnet_state='up'
        p.supernatant_off(seg,old[fid].get('Name'))
        fs=prepared(p);register(fs,[fid,other],p,'captured aqueous waste')
        assert set(fs[0].cmds[0].relays[0].codes)==codes(last_hv(prev))
        assert all(d.vol==0 and d.magnet_only for d in p.S.drops.values())
        proof.append(validate_scene(p,f'{vol} aqueous waste',vol*16,vol*16))

    # Keep barcode aliquoting command 3019 byte-identical; only waste route changes.
    raw=old[50004][0];p=seed(raw[-1],[('',2.),('K',.6),('BKWA',.6)])
    initial=51.2;p.S.step();p.S.flush()
    rigid.egress_pieces(p.S,[(ln,p.S.get('BKWA'+ln.id)) for ln in p.ALL],p.blob)
    original=Cmd(raw.get('Name'),raw.get('Component'),raw.get('Action'),int(raw.get('Timeout')),raw.get('Params'),
                 [Relay(list(codes(r)),int(r.get('Duration')),int(r.get('Amp'))) for r in raw])
    p.flow('B',old[50004].get('Name'),[original,p.cmd('接头余液保持原形排废，孔旁收尾后撤电')])
    fs=prepared(p);register(fs,[50004],p,'barcode waste')
    proof.append(validate_scene(p,'barcode waste',initial,9.6))

    # First purification: remove beads without washing. Final purification:
    # bead carrier is discarded first; fresh liquid subsequently washes channels.
    for seg,ids in (('R1',[2061,2063,2068]),('C',[4041,4043,4048])):
        p=protocol.Protocol((1,));p.magnet_state='up'
        for ln in p.ALL:
            d=p.S.add(Drop(ln.name,(ln.mag-1,ln.Y0,3,5),0));d.magnet_only=True
        p.cleanup_beads(seg,'排珠','G15' if seg=='R1' else 'G18')
        assert not getattr(p.S,'cleanup_sweep_events',[])
        assert not p.S.drops
        fs=prepared(p);register(fs,ids,p,'bead carrier '+seg,rename=True)
        proof.append(validate_scene(p,'bead carrier '+seg,48.,48.))
        if seg=='C':
            # A separate scene makes this input explicitly bead-free.
            q=protocol.Protocol((1,));q.final_wash('C','G18')
            fs=prepared(q);register(fs,[75000,75010,75020],q,'fresh final wash',rename=True)
            for number,flow in zip((16,17,18),fs):flow.name=f'C{number:02d} '+flow.name.split(' ',1)[1]
            assert len(q.S.cleanup_sweep_events)==1
            proof.append(validate_scene(q,'fresh final wash',48.,48.))

    return replacements,models,proof


def update(folder):
    path=folder/'PTseq_16_A.elab';before_text=path.read_text();before=E.fromstring(before_text)
    old={int(f.get('ID')):f for f in before}
    assert 75000 not in old,'Revision already applied'
    # Transient generated-model cache only; never a backup or published project.
    # Invalidated by any action-layer source change or different baseline XML.
    h=hashlib.sha256(before_text.encode());base=Path(__file__).parent
    for name in ('geom','ops','protocol','top_horizontal','scene','rigid','native_packets',
                 'native_waste','square_waste','waste_recovery','compact_collect','cdna_aliquot',
                 'fixed_barcode_collect','manual_sections','model','project_structure','transport_spacing'):
        h.update((base/(name+'.py')).read_bytes())
    cache=Path(tempfile.gettempdir())/('tft-six-rules-generated-'+h.hexdigest()+'.pickle')
    if cache.exists():
        replacements,models,proof=pickle.loads(cache.read_bytes())
    else:
        replacements,models,proof=generate(before,old)
        cache.write_bytes(pickle.dumps((replacements,models,proof)))

    # Preserve existing labels, with three genuinely new operations at the end.
    for fid in (2061,2063,2068,4041,4043,4048):
        f=replacements[fid];f.name=old[fid].get('Name').split()[0]+' '+f.name.split(' ',1)[1]
    newflows=[E.fromstring(replacements[int(f.get('ID'))].xml())
              if int(f.get('ID')) in replacements else copy.deepcopy(f) for f in before]
    newflows.extend(E.fromstring(replacements[fid].xml()) for fid in (75000,75010,75020))
    for flow in newflows:
        if int(flow.get('ID')) in replacements:
            assert '清理载液扫过' not in E.tostring(flow,encoding='unicode') or flow.get('ID')=='75010'
    root=copy.deepcopy(before);root[:]=newflows
    text=before_text
    for fid,f in replacements.items():
        if fid in old:text=replace_flow(text,fid,f.xml())
    text=text.replace('</Script>', '\n'.join(replacements[fid].xml() for fid in (75000,75010,75020))+'\n</Script>')
    after=E.fromstring(text)
    ids=[n.get('ID') for f in after for n in (f,*f)]
    assert len(ids)==len(set(ids))
    labels=[f.get('Name').split()[0] for f in after];assert len(labels)==len(set(labels))
    for f in after:
        if int(f.get('ID')) not in replacements:
            assert E.tostring(f).strip()==E.tostring(old[int(f.get('ID'))]).strip()
        local={c.get('ID') for c in f}
        for c in f:
            assert c.get('PreTask').split(',')[0] in local|{'-1'}
            if c.get('Component')=='HighVoltage' and c.get('Params')=='SCRIPTCYCLE_FALSE':
                assert int(c.get('Timeout'))*1000==sum(int(r.get('Duration')) for r in c)
    assert E.tostring(after.find("Flow[@ID='1030']")).strip()==E.tostring(old[1030]).strip()
    # Explicit S-hole recovery terminal OFF already exists in the released four flows.
    for fid in (2032,2055,4022,4035):assert not codes(last_hv(old[fid]))

    # Only changed native sections plus immediate neighboring endpoint frames.
    context=E.Element('Script')
    for i,f in enumerate(after):
        if int(f.get('ID')) in replacements:context.append(copy.deepcopy(f))
        elif ((i and int(after[i-1].get('ID')) in replacements)
              or (i+1<len(after) and int(after[i+1].get('ID')) in replacements)):
            stub=E.SubElement(context,'Flow',dict(f.attrib));hv=[c for c in f if c.get('Component')=='HighVoltage']
            if hv:
                for c,r in ((hv[0],hv[0][0]),(hv[-1],hv[-1][-1])):
                    node=E.SubElement(stub,'Command',dict(c.attrib));node.append(copy.deepcopy(r))
    with tempfile.TemporaryDirectory(prefix='tft-six-rules-') as td:
        scope=Path(td)/'scope.elab';E.ElementTree(context).write(scope);result=audit(scope)
    assert not result['non_equal_binary_transitions'],result

    # Layout: replace only affected flow nodes, then rebuild sequential arrows.
    lp=folder/'PTseq_16_A.elab.layout';lt=lp.read_text()
    for i,f in enumerate(after):
        fid=int(f.get('ID'))
        if fid not in replacements:continue
        node=replacements[fid].layout(i)
        if fid in old:lt=replace_flow(lt,fid,node)
        else:lt=lt.replace('</Script>',node+'\n</Script>')
    lt=re.sub(r'\s*<Arrow\b[^>]*/>','',lt)
    lt=lt.replace('</Script>','\n'.join(f' <Arrow StartID="{a.get("ID")}" EndID="{b.get("ID")}"/>' for a,b in zip(after,list(after)[1:]))+'\n</Script>')
    lr=E.fromstring(lt)
    assert {f.get('ID') for f in lr if f.tag=='Flow'}=={f.get('ID') for f in after}
    main=folder/'main.elab';mt,n=re.subn(r'Timeout="\d+"',f'Timeout="{sum(int(f.get("Timeout")) for f in after)}"',main.read_text(),count=1);assert n==1

    # Operator tables: update affected records, retain every unrelated record.
    lh,loads=read_csv(folder/'手工加液顺序.csv');ah,asp=read_csv(folder/'手工吸液顺序.csv')
    loads=[r for r in loads if int(r[lh[1]]) not in replacements]
    asp=[r for r in asp if int(r[ah[1]]) not in replacements]
    for f in models:
        c=f.cmds[-1];info=getattr(c,'manual_load',None)
        if info:
            values=['PTseq_16_A.elab',f.id,f.name,c.id,info['hole'],info['reagent'],info['volume_ul'],
                    info.get('port_volumes',''),info['lanes'],f"{info.get('batch',1)}/{info.get('batches',1)}",'','','']
            loads.append(dict(zip(lh,map(str,values))))
        info=getattr(c,'manual_aspiration',None)
        if info:
            values=['PTseq_16_A.elab',f.id,f.name,c.id,len(c.relays),info['hole'],info['volume_ul'],'','','','']
            asp.append(dict(zip(ah,map(str,values))))
    positions={int(f.get('ID')):i for i,f in enumerate(after)}
    for headers,rows in ((lh,loads),(ah,asp)):
        rows.sort(key=lambda r:positions[int(r[headers[1]])])
        for row in rows:
            i=positions[int(row[headers[1]])];f=after[i];nxt=after[i+1] if i+1<len(after) else None
            row[headers[2]]=f.get('Name');row[headers[-3]]='PTseq_16_A.elab' if nxt is not None else ''
            row[headers[-2]]=nxt.get('ID') if nxt is not None else '';row[headers[-1]]=nxt.get('Name') if nxt is not None else '工程结束'
    mapping_path=folder/'步骤编号映射_20261016.json';mapping=json.loads(mapping_path.read_text())
    names={int(f.get('ID')):f.get('Name') for f in after}
    for row in mapping:row['name']=names[row['id']]
    mapping.extend(dict(id=fid,name=names[fid],previous='新增：排珠完成后独立新液清洗') for fid in (75000,75010,75020))
    report=dict(status='passed',baseline_sha256=hashlib.sha256(before_text.encode()).hexdigest(),
        script_sha256=hashlib.sha256(text.encode()).hexdigest(),cases=proof,
        approved_spacing=dict(top_and_transport=6,reaction='existing 3-4',outlet_staging=4,
            outlet_entry='last-row mouth to bottom collector',C02_entry='existing 3-4 only while entering reaction well'),
        scope=sorted(replacements),exported_frame_audit=result,unrelated_native_sections_unchanged=True,
        temperature_PCR_cycles_unchanged=True,workbook_edited=False,
        fresh_wash_extra_ul_per_lane=3.,fresh_wash_extra_ul_total=48.,
        physical_liquid_validation=False,hardware_verified=False,GUI_reload_verified=False)
    # All assertions precede publication. Generated files, not backups.
    path.write_text(text);lp.write_text(lt);main.write_text(mt)
    for filename,headers,rows in (('手工加液顺序.csv',lh,loads),('手工吸液顺序.csv',ah,asp)):
        with (folder/filename).open('w',encoding='utf-8-sig',newline='') as fh:
            w=csv.DictWriter(fh,headers);w.writeheader();w.writerows(rows)
    # The old operator HTML contains obsolete geometry prose. Replace that
    # existing companion page with the actual current orders, not a new guide.
    from html import escape
    def table(headers,rows):
        return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr>'+''.join(
            '<tr>'+''.join('<td>'+escape(str(row[h]))+'</td>' for h in headers)+'</tr>' for row in rows)+'</table>'
    page=('<!doctype html><meta charset="utf-8"><title>单列手工加液与吸液顺序</title>'
          '<style>body{font:15px/1.7 system-ui;color:#000;background:#fff;padding:24px}table{border-collapse:collapse;margin:20px 0}td,th{border:1px solid #999;padding:5px}</style>'
          '<h1>单列10× · 当前加液与吸液顺序</h1>'
          '<p>承接组结束后手工加液，再运行下一组。顶部左右对半分液；顶部和独立运输间距6格，反应区沿用原间距，孔旁汇液准备区4格，C02仅进门过渡沿用原3–4格。所有行就绪后同步融合。</p>'
          '<p>孔旁回收配合手工吸取逐步撤电，末帧撤除已回收液体的孔口电极；仍在片的其他液滴继续保持上电。不要把末帧撤电当作自动吸液。</p>'
          '<p>纯化1只另加3.00µL/道载液携珠排废，不清洗通道。纯化2排珠完成后，再另加新液3.00µL/道清洗反应区及两侧通道；含磁珠载液不承担通道清洗。</p>'
          '<p>表中体积为实际液量，承接余量不增加试剂。温控、PCR局部30轮和原电极脉冲不变。静态检查不代替水跑、手工回收或芯片验证。</p>'
          '<h2>手工加液</h2>'+table(lh,loads)+'<h2>手工吸液</h2>'+table(ah,asp))
    (folder/'手工加液运行.html').write_text(page)
    mapping_path.write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+'\n')
    (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);update(ap.parse_args().folder)
