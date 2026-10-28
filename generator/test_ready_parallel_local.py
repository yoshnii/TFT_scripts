"""Narrow release checks for ready-position preparation and parallel scheduling."""
import csv,hashlib,io,json,subprocess,unittest
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as E
from native_packets import validate_symmetric_split
from update_ready_parallel_local import BASE,REPORT,TARGET,WASTE
from update_fixed_waste_local import relay_key
from update_transport_cleanup_local import last_hv
from waste_recovery import boxes
ROOT=Path(__file__).resolve().parent.parent;PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
def source(n):return subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+n],cwd=ROOT)
class ReleaseTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.old={int(f.get('ID')):f for f in E.fromstring(source('PTseq_16_A.elab'))}
  cls.new={int(f.get('ID')):f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
  cls.p=json.loads((PROJECT/REPORT).read_text())
 def test_exact_scope_and_control_graph(self):
  self.assertEqual(self.old.keys(),self.new.keys())
  self.assertEqual({i for i in self.old if E.tostring(self.old[i])!=E.tostring(self.new[i])},TARGET)
  for i in TARGET:
   a,b=self.old[i],self.new[i]
   self.assertEqual({k:v for k,v in a.attrib.items() if k!='Timeout'},{k:v for k,v in b.attrib.items() if k!='Timeout'})
   self.assertEqual(len(a),len(b))
   for x,y in zip(a,b):self.assertEqual({k:v for k,v in x.attrib.items() if k!='Timeout'},{k:v for k,v in y.attrib.items() if k!='Timeout'})
 def test_mass_per_hole_totals_coverage(self):
  old=json.loads(source('分液增距停留与排废续流局部校验_20261028.json'))
  def totals(c):
   vals=defaultdict(list)
   for e in c['fixed']['admission_events']:vals[e['port']].append(e['packet_ul'])
   return {k:(len(v),round(sum(v),8)) for k,v in vals.items()}
  for a,b in zip(old['cases'],self.p['cases']):
   self.assertEqual(a['case'],b['case']);self.assertEqual(totals(a),totals(b))
   for k in ('admitted_packets','receiver_count','actual_mass_ul'):self.assertEqual(a['fixed'][k],b['fixed'][k])
   for k in ('mass_conserved_each_scene_frame','receiver_footprints_unchanged','full_coverage_and_no_duplicate_codes','no_intermediate_collection_blocks'):self.assertTrue(b['fixed'][k])
  self.assertEqual(sum(c['fixed']['admitted_packets'] for c in self.p['cases']),496)
  self.assertEqual(sum(c['fixed']['receiver_count'] for c in self.p['cases']),31)
 def test_symmetric_cuts_shapes_and_dwell(self):
  for c in self.p['cases']+self.p['tops']:
   for e in c['splits']:validate_symmetric_split(e)
   self.assertTrue(c['cut_dwell']['per_child_gap_and_final_holds_passed'])
   proofs=c['fixed']['hold_trace'] if 'fixed' in c else c['hold_trace']
   self.assertTrue(all(e['all_reshape_holds_at_least_4000ms'] for e in proofs))
  for c in self.p['cases']:
   if not c['case'].startswith('captured'):continue
   self.assertEqual(len(c['splits']),112)
   self.assertTrue(all(e['gaps']==[0,2,4] for e in c['splits'][:16]))
   self.assertTrue(all(e['gaps']==[0,2,4,6] and e['requested_frame_ms']==[1000,3000,3000,5000] for e in c['splits'][16:]))
 def test_fixed_aspiration_tails_and_a06_first_command(self):
  self.assertEqual(E.tostring(self.old[1019][0]),E.tostring(self.new[1019][0]))
  for i in WASTE:
   n=22 if i==50004 else 24;a=list(self.old[i][-1])[-n:];b=list(self.new[i][-1])[-n:]
   if i in (1019,50004):
    def key(r):return sorted(rc for rc in boxes(r.get('RelayCode','')) if rc[1]>=172),r.get('Duration'),r.get('Amp')
    self.assertEqual([key(r) for r in a],[key(r) for r in b])
   else:self.assertEqual([relay_key(r) for r in a],[relay_key(r) for r in b])
 def test_retained_preposition_and_no_early_fusion(self):
  for label in ('A06','B07'):
   c=next(c for c in self.p['cases'] if c['case']==label)
   self.assertTrue(c['retained_ready_events']);self.assertTrue(all(e['waste_in_flight'] for e in c['retained_ready_events']))
  self.assertEqual({rc[0] for rc in boxes(last_hv(self.new[1019]).get('RelayCode'))},{37})
  retained=[rc for rc in boxes(last_hv(self.new[50004]).get('RelayCode')) if rc[2:]==(3,4)]
  self.assertEqual({rc[0] for rc in retained},{26})
  self.assertEqual(relay_key(last_hv(self.old[51001])),relay_key(last_hv(self.new[51001])))
 def test_top_delivery_volume_and_all_ready_boundary(self):
  for c in self.p['tops']:
   self.assertGreater(c['first_contact_step'],c['all_ready_step'])
   self.assertTrue(all(abs(sum(e['actual_ul'] for e in c['leaves'] if e['row']==r)-c['added_ul'])<1e-8 for r in range(1,17)))
  c=next(c for c in self.p['tops'] if c['reagent']=='G14')
  self.assertTrue(any(e['others_travelling'] for e in c['ready_cut_events']))
  self.assertEqual(len(c['leaves']),64);self.assertTrue(all(e['shape']==[3,5] and e['actual_ul']==.625 for e in c['leaves']))
 def test_layout_loading_workbook_and_aspiration_table(self):
  for n in ('PTseq_SIRO16_A_10x.elabprj','PTseq_16_A.elab.layout','ChipArea_192_256_96_SIRO.csv','手工加液顺序.csv'):self.assertEqual((PROJECT/n).read_bytes(),source(n))
  self.assertEqual(hashlib.sha256((ROOT.parent/'片上测试1/TFT片上试剂体积表_单列.xlsx').read_bytes()).hexdigest(),'3079c26c5c1608f9a944fdda9b0dd7ed8f618cc0b9b48d0a7235951bbef7d21c')
  old=list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv').decode('utf-8-sig'))))
  with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig') as fh:new=list(csv.DictReader(fh))
  for a,b in zip(old,new):
   if int(a['吸液流程组编号']) in WASTE:a.pop('末尾帧号');b.pop('末尾帧号')
   self.assertEqual(a,b)
 def test_native_timings_current_hash_and_evidence_limits(self):
  self.assertEqual(self.p['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
  self.assertEqual(self.p['total_saved_seconds'],133)
  for i in TARGET:self.assertEqual(int(self.new[i].get('Timeout')),sum(int(r.get('Duration')) for r in self.new[i].iter('RelayItem'))//1000)
  self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in self.new.values()))
  self.assertFalse(self.p['GUI_check']);self.assertFalse(self.p['physical_validation'])
if __name__=='__main__':unittest.main()
