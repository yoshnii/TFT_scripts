"""Local checks for wider cuts, settled leaves and continuous waste traffic."""
import csv,hashlib,io,json,subprocess,unittest
from pathlib import Path
from collections import defaultdict
import xml.etree.ElementTree as E
import ops,rigid
from transport_spacing import queue_exit
from scene import Scene,Drop
from native_packets import bisect_barcode,validate_symmetric_split
from update_split_refill_local import BASE,REPORT,TARGET,WASTE,TOP
from update_fixed_waste_local import relay_key
from update_transport_cleanup_local import last_hv
ROOT=Path(__file__).resolve().parent.parent;PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
def source(n):return subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+n],cwd=ROOT)
class SplitRefillUnitTests(unittest.TestCase):
    def test_barcode_twelve_cell_symmetric_gap_retained_endpoint(self):
        s=Scene();s.strict_transport=True;ln=ops.Lane(1,1)
        s.add(Drop(ln.name,(15,28,8,5),2.));s.add(Drop('KA1',(30,28,6,4),1.2));s.step()
        a,b=bisect_barcode(s,[ln])[0];event=s.split_events[-1];validate_symmetric_split(event)
        self.assertEqual((a.rect,b.rect,a.vol,b.vol),((28,28,3,4),(43,28,3,4),.6,.6))
        self.assertEqual(event['gaps'],[0,2,4,6,8,10,12])
        self.assertTrue(all(10<=x and x+w<=46 for pair in event['positions'] for x,y,w,h in pair))
    def test_waiting_packet_does_not_nose_into_approaching_channel_traffic(self):
        s=Scene();s.strict_transport=True;s.batch_channel_x=46
        d=s.add(Drop('NEW',(36,28,4,4),.75));q=s.add(Drop('OLD',(46,20,4,4),.75))
        for packet,batch in ((d,1),(q,0)):
            packet.continuous_waste_exit=True;packet.waste_grid_packet=True;packet.batch_round=batch
        g=queue_exit(s,d,46,rigid.go(d,[('x',46)]))
        self.assertEqual(next(g),'wait');self.assertEqual(d.rect[0],36)
        q.rect=(46,40,4,4)
        self.assertEqual(next(g),'+X')

class SplitRefillReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old={int(f.get('ID')):f for f in E.fromstring(source('PTseq_16_A.elab'))}
        cls.new={int(f.get('ID')):f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
        cls.p=json.loads((PROJECT/REPORT).read_text())
    def test_only_approved_sections_control_graph_unchanged(self):
        self.assertEqual(self.old.keys(),self.new.keys())
        self.assertEqual({i for i in self.old if E.tostring(self.old[i])!=E.tostring(self.new[i])},TARGET)
        for i in TARGET:
            a,b=self.old[i],self.new[i]
            self.assertEqual({k:v for k,v in a.attrib.items() if k!='Timeout'},{k:v for k,v in b.attrib.items() if k!='Timeout'})
            self.assertEqual(len(a),len(b))
            for x,y in zip(a,b):self.assertEqual({k:v for k,v in x.attrib.items() if k!='Timeout'},{k:v for k,v in y.attrib.items() if k!='Timeout'})
    def test_a06_and_barcode_wider_gaps(self):
        for name in ('A06','B07'):
            c=next(c for c in self.p['cases'] if c['case']==name);e=c['split_samples'][-1];validate_symmetric_split(e)
            self.assertEqual(e['gaps'],[0,2,4,6,8,10,12])
            self.assertTrue(all(10<=x and x+w<=46 and y==28 for pair in e['positions'] for x,y,w,h in pair))
        self.assertEqual(E.tostring(self.old[1019][0]),E.tostring(self.new[1019][0]))
    def test_three_second_leaf_hold_real_volumes_and_synchronized_fusion(self):
        self.assertEqual({c['section_id'] for c in self.p['top_holds']},TOP)
        expected={'G14':(2.5,5.,64,.625,[3,5]),'LIG':(1.4,4.,32,.7,[4,4]),'EDTA':(.4,4.4,16,.4,[2,4])}
        for c in self.p['top_holds']:
            add,final,n,ul,shape=expected[c['reagent']]
            self.assertEqual((c['added_ul'],c['final_ul'],len(c['leaves'])),(add,final,n))
            self.assertTrue(all(e['actual_ul']==ul and e['shape']==shape for e in c['leaves']))
            self.assertEqual((c['final_gap'],c['split_hold_ms']),(6,3000));self.assertGreater(c['first_contact_step'],c['all_ready_step'])
    def test_refills_while_previous_round_still_in_vertical_channel(self):
        for c in self.p['cases']:
            if c['case']=='B07':continue
            self.assertGreater(c['fixed']['multi_batch_channel_frames'],0,c['case'])
            self.assertGreater(c['fixed']['refill_before_previous_bottom_frames'],0,c['case'])
    def test_per_hole_volumes_fixed_coverage_and_shape_waits(self):
        old=json.loads(source('固定废液孔逐滴汇入局部校验_20261028.json'))
        for a,b in zip(old['cases'],self.p['cases']):
            self.assertEqual(a['case'],b['case'])
            for key in ('admitted_packets','receiver_count','actual_mass_ul'):self.assertEqual(a['fixed'][key],b['fixed'][key])
            totals=[]
            for c in (a,b):
                ports=defaultdict(list)
                for e in c['fixed']['admission_events']:ports[e['port']].append(e['packet_ul'])
                totals.append({k:(len(v),round(sum(v),8)) for k,v in ports.items()})
            self.assertEqual(*totals)
            for key in ('mass_conserved_each_scene_frame','receiver_footprints_unchanged','full_coverage_and_no_duplicate_codes','no_intermediate_collection_blocks'):self.assertTrue(b['fixed'][key])
            self.assertTrue(all(h['all_reshape_holds_at_least_4000ms'] for h in b['fixed']['hold_trace']))
    def test_suction_tails_and_magnetic_cut_holds_unchanged(self):
        for i in WASTE:
            n=22 if i==50004 else 24
            self.assertEqual([relay_key(r) for r in list(self.old[i][-1])[-n:]],[relay_key(r) for r in list(self.new[i][-1])[-n:]])
            self.assertEqual(relay_key(last_hv(self.old[i])),relay_key(last_hv(self.new[i])))
        for c in self.p['captured_cut_checks']:
            self.assertTrue(c['first_split_unchanged'])
            for j,g in enumerate(c['cut_groups']):self.assertEqual((g['gaps'],g['frame_ms']),([0,2,4],[1000]*3) if j==0 else ([0,2,4,6],[1000,3000,3000,5000]))
    def test_layout_loading_and_aspiration_totals_unchanged(self):
        for n in ('PTseq_SIRO16_A_10x.elabprj','PTseq_16_A.elab.layout','ChipArea_192_256_96_SIRO.csv','手工加液顺序.csv'):self.assertEqual((PROJECT/n).read_bytes(),source(n))
        old=list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv').decode('utf-8-sig'))))
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig') as fh:new=list(csv.DictReader(fh))
        for a,b in zip(old,new):
            if int(a['吸液流程组编号']) in WASTE:
                self.assertEqual(int(b['末尾帧号']),len(self.new[int(b['吸液流程组编号'])][-1]));a.pop('末尾帧号');b.pop('末尾帧号')
            self.assertEqual(a,b)
    def test_times_and_current_hash(self):
        p=self.p
        self.assertEqual(p['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
        self.assertEqual(p['total_saved_seconds'],sum(int(self.old[i].get('Timeout'))-int(self.new[i].get('Timeout')) for i in TARGET))
        for i in TARGET:self.assertEqual(int(self.new[i].get('Timeout')),sum(int(r.get('Duration')) for r in self.new[i].iter('RelayItem'))//1000)
        self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in self.new.values()))
        self.assertFalse(p['GUI_check']);self.assertFalse(p['physical_validation'])
if __name__=='__main__':unittest.main()
