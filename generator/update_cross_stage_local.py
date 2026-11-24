"""Release resource-aware overlap across host positioning, cuts, delivery and F transport.

Manual loading count, chemistry, real volume, split geometry and per-drop
holds remain fixed. Multi-load variants are analysis only.
"""
import argparse,csv,hashlib,json,re,subprocess
from pathlib import Path
from xml.sax.saxutils import escape
import xml.etree.ElementTree as E
import geom,rigid
from native_packets import bisect_barcode
from project_structure import prepare_scripts
from update_transport_cleanup_local import seed,last_hv,validate_scene
from update_fixed_waste_local import FixedAudit,relay_key
from update_refill_local import FrameTrace
from update_parallel_local import register
from update_ready_parallel_local import check_dwell
from update_workbook_volumes_local import read_csv
ROOT=Path(__file__).resolve().parent.parent
BASE='ce3767ec24a49d2391dd35a84a7df194517e2fed'
REPORT='跨阶段并行局部校验_20261124.json'
CASES=[('G4',.4,1.6,1002,50000,'A'),('AMP',2.,.5,1022,50001,'A'),('G14',2.5,2.5,2000,50002,'R1'),('G8',.7,1.3,3002,50003,'B'),('LIG',1.4,2.6,3026,50005,'B'),('EDTA',.4,4.,3041,50006,'B'),('G16',1.76,4.4,4000,50007,'C')]
WASTE={1019,2013,51000,2068,4048,75020}
TARGET=WASTE|{i for c in CASES for i in c[3:5]}
MULTI_LOAD_ANALYSIS=dict(reagent='G14',total_ul=40.,per_lane_added_ul=2.5,
 final_reaction_ul=5.,leaf_ul=.625,leaf_count=64,all_ready_fusion=True,
 human_loading_time_included=False,deployed=False,
 trials=[dict(ports=4,ul_per_port_per_loading=10.,loadings=1,pipetting_actions=4,machine_seconds=2167),
         dict(ports=4,ul_per_port_per_loading=5.,loadings=2,pipetting_actions=8,machine_seconds=2228),
         dict(ports=2,ul_per_port_per_loading=5.,loadings=4,pipetting_actions=8,machine_seconds=2630)],
 limit='离线调度模型，仅作G14此配置比较；不代表液滴实测或所有试剂的最优加液量。')


class Overlap:
 def __init__(self,p,host_total=None,added=None):
  self.p=p;self.prev={};self.frames=0;self.host_and_reagent=0;self.cut_and_delivery=0;self.two_bottom=0;self.loaded=False
  record=p.S._record
  def capture(*args,**kwargs):
   record(*args,**kwargs);S=p.S;self.frames+=1
   now={id(d):(d.name,d.rect,d.vol) for d in S.drops.values() if d.rects()}
   moved=[n for k,(n,r,v) in now.items() if k in self.prev and self.prev[k][1]!=r]
   self.host_and_reagent+=int(any(n.startswith('LA') for n in moved) and any(n.startswith('D') for n in moved))
   self.cut_and_delivery+=int(any(n.startswith('T') for n in moved) and any(n.startswith('D') for n,r,v in now.values()))
   packets=[d for d in S.drops.values() if getattr(d,'waste_grid_packet',False) and not getattr(d,'fixed_waste_receiver',False)]
   self.two_bottom+=int(any(d.rect[1]==172 for d in packets) and any(d.rect[1]==182 for d in packets))
   if host_total is not None:
    self.loaded|=any(n.startswith('D') for n,r,v in now.values())
    expected=host_total+(added if self.loaded else 0)
    assert abs(sum(d.vol for d in S.drops.values())-expected)<1e-7,'加液期间实际液量不守恒'
   self.prev=now
  p.S._record=capture
 def report(self):return dict(raw_frames=self.frames,host_and_reagent_motion_frames=self.host_and_reagent,delivery_before_all_cuts_finished_frames=self.cut_and_delivery,two_bottom_corridors_occupied_frames=self.two_bottom)


def build(root, changed_collectors=WASTE):
 old={int(f.get('ID')):f for f in root};ordered=list(root);fresh={};tops=[];waste=[]
 for reagent,vol,host,fid,did,seg in CASES:
  p=seed(old[fid][0][0],[('',host)])
  if reagent=='G4':p.S.relays[0].dur=int(old[fid][0][0].get('Duration'))
  if reagent=='AMP':
   for d in p.S.drops.values():d.a06_authorized_padding=True
  trace=FrameTrace(p);overlap=Overlap(p,host*16,vol*16)
  p.add_reagent(seg,old[fid].get('Name').split(' · ')[0],reagent,vol)
  fs=prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows;assert len(fs)==2
  for f,i in zip(fs,(fid,did)):register(f,i,old)
  fs[0].cmds[0].name='承接准备 · 样本定位与后续试剂分液并行'
  receiving=E.fromstring(fs[0].xml());delivery=E.fromstring(fs[1].xml())
  assert receiving[-1].get('ID')==old[fid][-1].get('ID')
  assert len(receiving[-1])==1,'手工承接必须仍是一帧'
  assert relay_key(last_hv(delivery))==relay_key(last_hv(old[did])),('下游末态改变',reagent)
  assert receiving[0][0].get('RelayCode')==old[fid][0][0].get('RelayCode')
  ready=next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
  assert all(e['contact_step']>ready for e in p.S.reaction_merge_events)
  assert len(p.S.reaction_merge_events)==1
  assert all(d.name.startswith('L') and abs(d.vol-host-vol)<1e-7 for d in p.S.drops.values())
  assert len(p.S.drops)==16
  for row in range(1,17):assert abs(sum(e['actual_ul'] for e in p.S.native_leaf_events if e['row']==row)-vol)<1e-7
  simultaneous_final_cut = any(len(e['parents']) > 1 for e in getattr(p.S,'ready_top_events',[]))
  assert overlap.host_and_reagent>0 and (overlap.cut_and_delivery>0 or simultaneous_final_cut), (reagent, overlap.report())
  tops.append(dict(reagent=reagent,ids=[fid,did],actual_added_ul_per_lane=vol,final_ul_per_lane=host+vol,
   geometry=validate_scene(p,reagent+' cross-stage'),cut_dwell=check_dwell(p,trace),
   hold_trace=trace.proofs,overlap=overlap.report(),leaves=p.S.native_leaf_events,
   splits=[{k:v for k,v in e.items() if not k.startswith('_')} for e in p.S.split_events],
   ready_cuts=getattr(p.S,'ready_top_events',[]),pipeline_events=p.S.pipeline_events,
   all_ready_frame=ready,contact_frame=p.S.reaction_merge_events[0]['contact_step'],
   per_two_moves_extra_wait_ms=3000,reshape_minimum_ms=4000))
  fresh[fid]=fs[0];fresh[did]=fs[1]
  print('TOP',reagent,sum(int(old[i].get('Timeout')) for i in (fid,did)),'->',sum(f.timeout for f in fs),flush=True)
 def publish(p,fs,ids,audit,overlap,label,mass,wasted,keep_a06=False):
  unchanged = set(ids).isdisjoint(changed_collectors)
  for f,i in zip(fs,ids):
   if keep_a06:
    f.cmds[0].relays[0].dur=int(old[i][0][0].get('Duration'));f.cmds[0].timeout=int(old[i][0].get('Timeout'))
   out=register(f,i,old)
   if keep_a06:assert E.tostring(out[0])==E.tostring(old[i][0])
   n=22 if i==50004 else 24
   assert [relay_key(r) for r in list(out[-1])[-n:]]==[relay_key(r) for r in list(old[i][-1])[-n:]],('吸液收尾改变',i)
   assert relay_key(last_hv(out))==relay_key(last_hv(old[i]))
   if unchanged:assert E.tostring(out)==E.tostring(old[i]),('单路保留段改变',i)
   else:fresh[i]=f
   print('WASTE',label,i,int(old[i].get('Timeout')),'->',f.timeout,flush=True)
  report=audit.report()
  waste.append(dict(case=label,ids=ids,fixed=report,overlap=overlap.report(),
   geometry=validate_scene(p,label,mass,wasted,r05_channel_events=True,captured_channel_events=True),
   cut_dwell=check_dwell(p,audit.trace),
   splits=[{k:v for k,v in e.items() if not k.startswith('_')} for e in p.S.split_events],
   arrivals=getattr(p.S,'waste_arrival_events',[]),
   gap_events=getattr(p.S,'r05_wide_events',[])+getattr(p.S,'captured_channel_events',[]),unchanged=unchanged))
 p=seed(old[1019][0][0],[('',2.)]);audit=FixedAudit(p,32.);overlap=Overlap(p)
 p.split_waste('A',old[1019].get('Name').split(' · ')[0],.5,'cDNA多余液体')
 publish(p,prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows,[1019],audit,overlap,'A06',32.,24.,True)
 for ids,seg,vol in (((2013,51000),'R1',5.),((4013,51002),'C',6.16)):
  p=seed(last_hv(ordered[ordered.index(old[ids[0]])-1]),[('',vol)]);p.magnet_state='up'
  if vol==6.16:p.S.flush()
  audit=FixedAudit(p,vol*16);overlap=Overlap(p);p.supernatant_off(seg,old[ids[0]].get('Name').split(' · ')[0])
  publish(p,prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows,list(ids),audit,overlap,'R captured' if vol==5. else 'C captured',vol*16,vol*16)
 p=seed(old[50004][0][0],[('',2.),('K',1.2)]);audit=FixedAudit(p,51.2);overlap=Overlap(p)
 bisect_barcode(p.S,p.ALL);c=p.cmd(old[50004][0].get('Name'))
 rigid.egress_pieces(p.S,[(ln,p.S.get('BKWA'+ln.id)) for ln in p.ALL],p.blob)
 p.flow('B',old[50004].get('Name'),[c,p.cmd(old[50004][-1].get('Name'))])
 publish(p,prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows,[50004],audit,overlap,'B07',51.2,9.6)
 for fid,label in ((2068,'R19'),(4048,'C15'),(75020,'C18')):
  p=seed(last_hv(ordered[ordered.index(old[fid])-1]),[('',3.)]);p.magnet_state='down'
  if set(p.S.relays[0].codes)!=set(old[fid][0][0].get('RelayCode').split(';')):p.S.flush()
  audit=FixedAudit(p,48.);overlap=Overlap(p)
  rigid.egress_pieces(p.S,[(ln,p.S.get(ln.name)) for ln in p.ALL],p.blob,final=True,single_stop=label!='R19')
  p.flow('C' if label.startswith('C') else 'R1',old[fid].get('Name'),[p.cmd(old[fid][-1].get('Name'))])
  publish(p,prepare_scripts(p.segs,root.get('ChipsetType'),1)[0][0].flows,[fid],audit,overlap,label,48.,48.)
 assert set(fresh)==changed_collectors|{i for c in CASES for i in c[3:5]}
 return fresh,tops,waste


def update(folder):
 path=folder/'PTseq_16_A.elab';baseline=path.read_text()
 assert baseline==subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
 workbook=ROOT.parent/'片上测试1/TFT片上试剂体积表_单列.xlsx';whash=hashlib.sha256(workbook.read_bytes()).hexdigest()
 root=E.fromstring(baseline);old={int(f.get('ID')):f for f in root};fresh,tops,waste=build(root)
 text=baseline;timings=[]
 for i,f in fresh.items():
  register(f,i,old)
  if i in {c[3] for c in CASES}:f.cmds[0].name='承接准备 · 样本定位与后续试剂分液并行'
  text,n=re.subn(rf'<Flow\b(?=[^>]*\bID="{i}")[^>]*>[\s\S]*?</Flow>',lambda _:f.xml().strip(),text,count=1);assert n==1
  timings.append(dict(id=i,section=old[i].get('Name').split()[0],before_seconds=int(old[i].get('Timeout')),after_seconds=f.timeout,saved_seconds=int(old[i].get('Timeout'))-f.timeout))
 after=E.fromstring(text)
 assert {int(f.get('ID')) for f in after if E.tostring(f)!=E.tostring(old[int(f.get('ID'))])}==TARGET
 for i in TARGET:
  a,b=old[i],after.find(f"Flow[@ID='{i}']")
  assert {k:v for k,v in a.attrib.items() if k!='Timeout'}=={k:v for k,v in b.attrib.items() if k!='Timeout'}
  assert len(a)==len(b)
  for k,(x,y) in enumerate(zip(a,b)):
   excluded={'Timeout','Name'} if i in {c[3] for c in CASES} and k==0 else {'Timeout'}
   assert {q:v for q,v in x.attrib.items() if q not in excluded}=={q:v for q,v in y.attrib.items() if q not in excluded}
 # Names displayed by the editor follow the same IDs; graph positions remain.
 layout=(folder/'PTseq_16_A.elab.layout').read_text();tree=E.fromstring(layout)
 changed_names={f.cmds[0].id:f.cmds[0].name for i,f in fresh.items() if i in {c[3] for c in CASES}}
 for cid,name in changed_names.items():
  layout,n=re.subn(rf'(<Command\b(?=[^>]*\bID="{cid}")[^>]*\bName=")[^"]*(")',lambda m:m[1]+escape(name,{'"':'&quot;'})+m[2],layout);assert n==1
 headers,rows=read_csv(folder/'手工吸液顺序.csv')
 for row in rows:
  i=int(row[headers[1]])
  if i in WASTE:
   c=fresh[i].cmds[-1];assert float(row[headers[6]])==c.manual_aspiration['volume_ul'];row[headers[4]]=str(len(c.relays))
 count=0
 def table(m):
  nonlocal count
  count+=1
  if count==1:return m[0]
  return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(str(row[h]))+'</td>' for h in headers)+'</tr>' for row in rows)+'</table>'
 page=re.sub(r'<table\b[^>]*>[\s\S]*?</table>',table,(folder/'手工加液运行.html').read_text());assert count==2
 note='<p id="cross-stage-20261124">加液后，常驻样本定位、母滴拆分和已就绪小滴运输按空间与真实依赖交错推进；全部16行试剂就绪后再同步融入反应体系。F2–F4对已验证的4×4/0.625或0.75µL废液采用Y172/Y182两条底部路径，分别放行后并入原固定框；F1原直下入口、C05/C06和B07原路径、全部孔旁吸液收尾保留。体积、孔距、切口、3秒/4秒/5秒保持、温磁控及手工加液/吸液次数不变。少量多次加液仅作对照试算，未写入本工程。</p>'
 page=page.replace('<table',note+'<table',1)
 total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in after)
 main,n=re.subn(r'Timeout="\d+"',f'Timeout="{total}"',(folder/'main.elab').read_text(),count=1);assert n==1
 report=dict(status='passed',baseline_commit=BASE,modified_section_ids=sorted(TARGET),timings=timings,
  total_saved_seconds=sum(t['saved_seconds'] for t in timings),top_cases=tops,waste_cases=waste,
  script_sha256=hashlib.sha256(text.encode()).hexdigest(),workbook_sha256=whash,
  biochemical_durations_temperatures_magnet_and_manual_gate_counts_unchanged=True,
  actual_volume_ratio_carrier_shapes_and_suction_tails_unchanged=True,
  multi_load_analysis_only=True,multi_load_analysis=MULTI_LOAD_ANALYSIS,
  overall_timeout_before_seconds=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in root),
  overall_timeout_after_seconds=total,GUI_check=False,physical_validation=False)
 assert hashlib.sha256(workbook.read_bytes()).hexdigest()==whash
 path.write_text(text);(folder/'main.elab').write_text(main);(folder/'PTseq_16_A.elab.layout').write_text(layout);(folder/'手工加液运行.html').write_text(page)
 with (folder/'手工吸液顺序.csv').open('w',encoding='utf-8-sig',newline='') as fh:
  writer=csv.DictWriter(fh,headers);writer.writeheader();writer.writerows(rows)
 (folder/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print('TOTAL_SAVED',report['total_saved_seconds'],flush=True)
 return report
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);update(ap.parse_args().folder)
