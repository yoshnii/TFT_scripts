"""R05-only two-channel expansion, unchanged transport and released boundaries."""
import hashlib,json,subprocess,unittest
from pathlib import Path
import xml.etree.ElementTree as E
import ops
from scene import Scene,Drop
from waste_recovery import boxes
from wide_r05 import station
from native_packets import validate_symmetric_split
from update_r05_channels_local import BASE,REPORT
from update_transport_cleanup_local import last_hv
from update_fixed_waste_local import relay_key
ROOT=Path(__file__).resolve().parent.parent;PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
def source(n):return subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+n],cwd=ROOT)
class GeometryTests(unittest.TestCase):
 def test_all_rows_maximal_symmetric_clearances_with_waiting_liquid(self):
  cases=[((24,20,5),[],(18,24)),((26,16,4),[(6,10,5)],(26,16)),((42,8,4),[(6,10,5),(18,6,5)],(34,16)),((18,8,4),[(6,10,5)],(30,24))]
  for row in range(1,17):
   ln=ops.Lane(1,row)
   for (x,w,h),wait,expected in cases:
    s=Scene();s.r05_channel_expansion=True;d=s.add(Drop('LIQactive',(x,ln.Y0,w,h),2.5))
    for i,(a,b,c) in enumerate(wait):s.add(Drop('LIQheld'+str(i),(a,ln.Y0,b,c),1.25))
    self.assertEqual(station(s,ln,d),expected)
    maximum=44-w-sum(b for a,b,c in wait)-2*len(wait)
    self.assertEqual(expected[1],maximum//2*2)
    self.assertNotEqual(expected[0]+w//2,ln.mag)
 def test_unchanged_32_cell_waiting_carrier_caps_bottleneck_at_fourteen(self):
  s=Scene();s.r05_channel_expansion=True;ln=ops.Lane(1,1)
  s.add(Drop('LIQheld',(6,ln.Y0,10,5),2.5));s.add(Drop('LIQwait',(18,ln.Y0,8,4),1.25))
  d=s.add(Drop('LIQcut',(42,ln.Y0,8,4),1.25))
  self.assertEqual(station(s,ln,d)[1],14)
 def test_channel_staging_gap_is_scoped_and_transport_is_not_relaxed(self):
  from transport_spacing import required
  s=Scene();s.strict_transport=True;s.r05_channel_expansion=True
  a=s.add(Drop('LIQA1_0',(6,28,10,5),2.5));b=s.add(Drop('LIQA1_1',(18,28,6,5),1.25))
  a.r05_prep_row=b.r05_prep_row=(1,1)
  self.assertEqual(required(s,a,a.rect,b,b.rect),2)
  s.r05_channel_expansion=False;self.assertEqual(required(s,a,a.rect,b,b.rect),3)
  s.r05_channel_expansion=True;b.native_packet=True
  self.assertEqual(required(s,a,a.rect,b,b.rect),2)
  a.native_packet=True
  self.assertEqual(required(s,a,a.rect,b,b.rect),6)
 def test_inflight_channel_packet_limits_its_reserved_opening(self):
  s=Scene();s.r05_channel_expansion=True;ln=ops.Lane(1,1)
  d=s.add(Drop('LIQA1',(24,28,20,5),5.))
  p=s.add(Drop('TRANSIT',(46,28,4,4),.625));p.native_packet=True
  self.assertEqual(station(s,ln,d),(13,14))
class ReleaseTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.old={int(f.get('ID')):f for f in E.fromstring(source('PTseq_16_A.elab'))}
  cls.new={int(f.get('ID')):f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
  cls.p=json.loads((PROJECT/REPORT).read_text())
 def test_only_r05_changes_r04_r06_and_other_sections_identical(self):
  self.assertEqual(self.old.keys(),self.new.keys())
  self.assertEqual({i for i in self.old if E.tostring(self.old[i])!=E.tostring(self.new[i])},{2013})
  self.assertEqual(relay_key(last_hv(self.old[2013])),relay_key(last_hv(self.new[2013])))
 def test_largest_openings_and_symmetric_binary_tree(self):
  self.assertEqual(self.p['gaps'],[24,16,16,24])
  self.assertEqual(len(self.p['splits']),112)
  self.assertEqual(sum(bool(e.get('r05_channel_expansion')) for e in self.p['splits']),64)
  for e in self.p['splits']:
   lo,hi=(6,50) if e.get('r05_channel_expansion') else (10,46)
   self.assertTrue(all(lo<=x and x+w<=hi for frame in e['positions'] for x,y,w,h in frame))
  for e in self.p['splits']:validate_symmetric_split(e)
  self.assertTrue(self.p['cut_dwell']['per_child_gap_and_final_holds_passed'])
  for e in self.p['opening_events'][1:]:
   self.assertEqual(e['frame_ms'][1:3],[3000,3000]);self.assertEqual(e['frame_ms'][-1],5000)
 def test_exported_native_cut_frames_retain_requested_holds(self):
  frames=list(self.new[2013].iter('RelayItem'))
  masks=[set(boxes(r.get('RelayCode',''))) for r in frames]
  examples=[e for e in self.p['splits'] if e['parent'] in ('LIQA1','LIQA1_1','LIQA1_1_1','LIQA1_1_0')]
  self.assertEqual(len(examples),4)
  for e,opening in zip(examples,self.p['opening_events']):
   for guess,positions,hold in zip(e['separation_frames'],e['positions'],opening['frame_ms']):
    wanted={tuple(rc) for rc in positions}
    matches=[i for i in range(max(0,guess-12),min(len(frames),guess+4)) if wanted<=masks[i]]
    self.assertTrue(matches,(e['parent'],positions))
    i=min(matches,key=lambda i:abs(i-guess))
    self.assertGreaterEqual(int(frames[i].get('Duration')),hold)
 def test_temporary_carrier_exception_and_native_transport(self):
  self.assertEqual(len(self.p['pending_carrier_shape_proofs']),16)
  self.assertTrue(all(v==[[8,4],[6,5],[8,4]] for v in self.p['pending_carrier_shape_proofs'].values()))
  c=self.p['temporary_waiting_carrier'];self.assertEqual(c['actual_ul'],1.25)
  self.assertEqual((c['before_cells'],c['waiting_cells'],c['restored_cells']),(32,30,32))
  self.assertEqual(c['transport_w_h'],[4,4]);self.assertEqual(c['transport_ul'],.625)
  for e in self.p['fixed']['admission_events']:self.assertEqual(e['packet_rect'][2:],[4,4]);self.assertEqual(e['packet_ul'],.625)
  self.assertTrue(all(h['all_reshape_holds_at_least_4000ms'] for h in self.p['fixed']['hold_trace']))
 def test_mass_geometry_coverage_and_fixed_receiver_suction(self):
  f=self.p['fixed'];self.assertEqual(f['actual_mass_ul'],80.);self.assertEqual(f['admitted_packets'],128);self.assertEqual(f['receiver_count'],8)
  for k in ('mass_conserved_each_scene_frame','full_coverage_and_no_duplicate_codes','receiver_footprints_unchanged'):self.assertTrue(f[k])
  self.assertEqual([relay_key(r) for r in list(self.old[2013][-1])[-24:]],[relay_key(r) for r in list(self.new[2013][-1])[-24:]])
  self.assertTrue(self.p['geometry']['magnet_cut_lines_avoid_black_center'])
 def test_layout_loading_workbook_and_time(self):
  for n in ('PTseq_SIRO16_A_10x.elabprj','PTseq_16_A.elab.layout','ChipArea_192_256_96_SIRO.csv','手工加液顺序.csv'):self.assertEqual((PROJECT/n).read_bytes(),source(n))
  self.assertEqual(hashlib.sha256((ROOT.parent/'片上测试1/TFT片上试剂体积表_单列.xlsx').read_bytes()).hexdigest(),'3079c26c5c1608f9a944fdda9b0dd7ed8f618cc0b9b48d0a7235951bbef7d21c')
  self.assertEqual(self.p['before_seconds'],1291)
  self.assertEqual(self.p['additional_seconds'],self.p['after_seconds']-1291)
  self.assertEqual(self.p['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
  self.assertEqual(sum(int(r.get('Duration')) for r in self.new[2013].iter('RelayItem')),self.p['after_seconds']*1000)
  self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in self.new.values()))
  self.assertFalse(self.p['GUI_check']);self.assertFalse(self.p['physical_validation'])
if __name__=='__main__':unittest.main()
