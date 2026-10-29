"""Approved captured-water channel openings and unchanged release boundaries."""
import hashlib,json,subprocess,unittest
from pathlib import Path
import xml.etree.ElementTree as E
import geom,ops
from scene import Scene,Drop
from captured_channels import station
from transport_spacing import required
from native_packets import validate_symmetric_split
from update_captured_channels_local import BASE,REPORT,TARGET,GAPS
from update_fixed_waste_local import relay_key
from update_transport_cleanup_local import last_hv
ROOT=Path(__file__).resolve().parent.parent;PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
def source(n):return subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+n],cwd=ROOT)


class GeometryTests(unittest.TestCase):
 def test_maximum_even_openings_in_all_sixteen_rows(self):
  cases={'R06':[(20,16,4,[],28),(42,8,4,[(6,8,4)],26),(6,8,4,[],36)],
    'C05':[(14,32,4,[],12),(34,16,4,[(6,11,6)],14),
           (41,8,4,[(6,11,6),(19,7,5)],14),(19,8,4,[(6,11,6)],22)],
    'C06':[(10,16,4,[],28),(42,8,4,[(6,7,5)],26),(6,8,4,[],36)]}
  for stage,stages in cases.items():
   for row in range(1,17):
    ln=ops.Lane(1,row)
    for x,w,h,held,gap in stages:
     s=Scene();s.captured_channel_stage=stage
     for i,(a,b,c) in enumerate(held):
      rc=(a,ln.Y0-int(c==6),b,c);s.add(Drop('WAIT'+str(i),rc,1.))
      self.assertTrue(geom.reaction_holes_ok(rc,1) and geom.keep_out_ok(rc))
     d=s.add(Drop('CUT',(x,ln.Y0,w,h),1.));station_x,actual=station(s,ln,d)
     self.assertEqual(actual,gap,(stage,row))
     self.assertEqual(actual,(44-w-sum(b for a,b,c in held)-2*len(held))//2*2)
     self.assertNotEqual(station_x+w//2,ln.mag)
 def test_c05_compacts_waiting_side_for_next_cut(self):
  ln=ops.Lane(1,1);s=Scene();s.captured_channel_stage='C05'
  s.add(Drop('WAIT',(6,27,11,6),3.08));d=s.add(Drop('CUT',(34,28,16,4),3.08))
  self.assertEqual(station(s,ln,d),(26,14))
 def test_preparation_exception_does_not_follow_transport(self):
  s=Scene();s.strict_transport=True;s.captured_channel_stage='C05'
  a=s.add(Drop('LIQa',(6,28,10,5),2.5));b=s.add(Drop('LIQb',(18,28,6,5),1.25))
  a.captured_prep_row=b.captured_prep_row=(1,1)
  self.assertEqual(required(s,a,a.rect,b,b.rect),2)
  s.captured_channel_stage=None;self.assertEqual(required(s,a,a.rect,b,b.rect),3)
  s.captured_channel_stage='C05';a.native_packet=b.native_packet=True
  self.assertEqual(required(s,a,a.rect,b,b.rect),6)
  a.native_packet=b.native_packet=False;b.captured_prep_row=(1,2)
  self.assertEqual(required(s,a,a.rect,b,b.rect),3)
 def test_occupied_channel_reduces_or_blocks_borrowed_opening(self):
  s=Scene();s.captured_channel_stage='R06';ln=ops.Lane(1,1)
  d=s.add(Drop('CUT',(20,28,16,4),2.5))
  p=s.add(Drop('TRANSIT',(46,28,4,4),.625));p.native_packet=True
  self.assertLess(station(s,ln,d)[1],28)


class ReleaseTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.old={int(f.get('ID')):f for f in E.fromstring(source('PTseq_16_A.elab'))}
  cls.new={int(f.get('ID')):f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
  cls.p=json.loads((PROJECT/REPORT).read_text())
 def test_only_three_approved_sections_change(self):
  self.assertEqual(self.old.keys(),self.new.keys())
  self.assertEqual({i for i in self.old if E.tostring(self.old[i])!=E.tostring(self.new[i])},TARGET)
  for i in TARGET:
   self.assertEqual({k:v for k,v in self.old[i].attrib.items() if k!='Timeout'},
                    {k:v for k,v in self.new[i].attrib.items() if k!='Timeout'})
   self.assertEqual(len(self.old[i]),len(self.new[i]))
   for a,b in zip(self.old[i],self.new[i]):
    self.assertEqual({k:v for k,v in a.attrib.items() if k!='Timeout'},
                     {k:v for k,v in b.attrib.items() if k!='Timeout'})
 def test_exact_openings_and_symmetric_equal_splits(self):
  self.assertEqual(self.p['gaps'],GAPS);total=0
  for c in self.p['cases']:
   for stage in dict.fromkeys(e['stage'] for e in c['opening_events']):
    self.assertEqual([e['gap'] for e in c['opening_events'] if e['stage']==stage],GAPS[stage])
   for e in c['splits']:
    validate_symmetric_split(e);total+=1
    lo,hi=(6,50) if e.get('captured_channel_stage') or e.get('r05_channel_expansion') else (10,46)
    self.assertTrue(all(lo<=x and x+w<=hi for f in e['positions'] for x,y,w,h in f))
  self.assertEqual(total,224)
 def test_holds_and_single_cell_movement(self):
  for c in self.p['cases']:
   self.assertTrue(c['cut_dwell']['per_child_gap_and_final_holds_passed'])
   self.assertTrue(c['native_holds']['all_requested_holds_passed'])
   for e in c['opening_events']:
    if e['first']:self.assertTrue(all(ms==1000 for ms in e['frame_ms']))
    else:
     self.assertEqual(e['frame_ms'][1:3],[3000,3000]);self.assertEqual(e['frame_ms'][-1],5000)
   self.assertTrue(all(h['all_reshape_holds_at_least_4000ms'] for h in c['fixed']['hold_trace']))
 def test_liquid_mass_transport_and_fixed_receivers(self):
  for c in self.p['cases']:
   f=c['fixed'];self.assertEqual(f['actual_mass_ul'],c['per_lane_ul']*16)
   self.assertEqual(f['admitted_packets'],128);self.assertEqual(f['receiver_count'],8)
   for key in ('mass_conserved_each_scene_frame','receiver_footprints_unchanged','full_coverage_and_no_duplicate_codes'):
    self.assertTrue(f[key])
   for e in f['admission_events']:
    self.assertEqual(e['packet_rect'][2:],[4,4]);self.assertEqual(e['packet_ul'],c['per_lane_ul']/8)
   self.assertEqual(c['port_totals_ul'],{str(i):round(c['per_lane_ul']*4,8) for i in range(1,5)})
   self.assertTrue(c['carrier_areas_unchanged'])
 def test_suction_tails_and_published_handoffs(self):
  for fid in TARGET:
   self.assertEqual([relay_key(r) for r in list(self.old[fid][-1])[-24:]],
                    [relay_key(r) for r in list(self.new[fid][-1])[-24:]])
   self.assertEqual(relay_key(last_hv(self.old[fid])),relay_key(last_hv(self.new[fid])))
  self.assertEqual(last_hv(self.new[2013]).get('RelayCode'),self.new[51000][0][0].get('RelayCode'))
  self.assertEqual(last_hv(self.new[4013]).get('RelayCode'),self.new[51002][0][0].get('RelayCode'))
  self.assertEqual(last_hv(self.new[51000]).get('RelayCode'),'')
  self.assertEqual(last_hv(self.new[51002]).get('RelayCode'),'')
 def test_layout_workbook_loading_and_aspiration_volume_unchanged(self):
  for n in ('ChipArea_192_256_96_SIRO.csv','PTseq_16_A.elab.layout','PTseq_SIRO16_A_10x.elabprj','手工加液顺序.csv'):
   self.assertEqual((PROJECT/n).read_bytes(),source(n))
  self.assertEqual(hashlib.sha256((ROOT.parent/'片上测试1/TFT片上试剂体积表_单列.xlsx').read_bytes()).hexdigest(),
                   '3079c26c5c1608f9a944fdda9b0dd7ed8f618cc0b9b48d0a7235951bbef7d21c')
  from update_workbook_volumes_local import read_csv
  import csv,io
  headers,rows=read_csv(PROJECT/'手工吸液顺序.csv');old=list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv').decode('utf-8-sig'))))
  self.assertEqual(len(rows),len(old))
  for a,b in zip(old,rows):
   self.assertEqual({k:v for k,v in a.items() if k!=headers[4]}, {k:v for k,v in b.items() if k!=headers[4]})
 def test_native_time_totals_and_evidence_flags(self):
  self.assertEqual(self.p['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
  for fid in TARGET:
   self.assertEqual(sum(int(r.get('Duration')) for r in self.new[fid].iter('RelayItem')),int(self.new[fid].get('Timeout'))*1000)
  self.assertEqual(self.p['total_added_seconds'],sum(int(self.new[i].get('Timeout'))-int(self.old[i].get('Timeout')) for i in TARGET))
  self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),
                   sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in self.new.values()))
  self.assertFalse(self.p['GUI_check']);self.assertFalse(self.p['physical_validation'])

if __name__=='__main__':unittest.main()
