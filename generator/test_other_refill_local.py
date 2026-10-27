"""B07 streaming regressions: area, unchanged split, recovery and downstream state."""
import csv,hashlib,io,json,re,subprocess,unittest
from pathlib import Path
import xml.etree.ElementTree as E
from scene import Scene,Drop
from fixed_barcode_collect import join
from update_other_refill_local import BASE,REPORT,relay_key
ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
def source(name):return subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT)

class CollectionGeometryTests(unittest.TestCase):
    def test_adjacent_merge_preserves_area_and_real_volume(self):
        s=Scene();a=s.add(Drop('A',(80,180,3,4),.6));b=s.add(Drop('B',(83,180,3,4),.6))
        d=join(s,[a,b],'C');self.assertEqual((d.vol,d.rect,len(d.target_cells())),(1.2,(80,180,6,4),24))
        self.assertEqual(set(s.drops),{'C'})
    def test_incomplete_rectangle_is_not_silently_filled(self):
        s=Scene();a=s.add(Drop('A',(80,180,3,4),.6));b=s.add(Drop('B',(83,181,3,4),.6))
        with self.assertRaises(AssertionError):join(s,[a,b],'C')

class OtherRefillReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old={int(f.get('ID')):f for f in E.fromstring(source('PTseq_16_A.elab'))}
        cls.new={int(f.get('ID')):f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
        cls.report=json.loads((PROJECT/REPORT).read_text())
    def test_only_B07_changes(self):
        self.assertEqual(self.old.keys(),self.new.keys())
        self.assertEqual({i for i in self.old if E.tostring(self.old[i])!=E.tostring(self.new[i])},{50004})
        for name in ('PTseq_SIRO16_A_10x.elabprj','PTseq_16_A.elab.layout','ChipArea_192_256_96_SIRO.csv','手工加液顺序.csv'):
            self.assertEqual((PROJECT/name).read_bytes(),source(name))
    def test_original_split_and_recovery_are_identical(self):
        a,b=self.old[50004],self.new[50004]
        self.assertEqual(E.tostring(a[0]),E.tostring(b[0]))
        self.assertEqual([relay_key(r) for r in list(a[-1])[-22:]],[relay_key(r) for r in list(b[-1])[-22:]])
        self.assertEqual(a[-1][-1].get('RelayCode'),b[-1][-1].get('RelayCode'))
        for x,y in zip(a,b):self.assertEqual({k:v for k,v in x.attrib.items() if k!='Timeout'},{k:v for k,v in y.attrib.items() if k!='Timeout'})
        self.assertEqual({k:v for k,v in a.attrib.items() if k!='Timeout'},{k:v for k,v in b.attrib.items() if k!='Timeout'})
    def test_volume_shapes_and_actual_overlap(self):
        e=self.report['evidence']
        self.assertEqual((e['total_waste_ul'],e['batch_actual_ul'],e['leaf_ul']),(9.6,4.8,.6))
        self.assertEqual((e['leaf_shape_w_h'],e['transport_shape_w_h'],e['final_pool_w_h']),([3,4],[12,8],[12,16]))
        self.assertEqual(len(e['batches']),2)
        self.assertEqual([x['actual_volume_ul'] for x in e['batches']],[4.8,4.8])
        self.assertGreater(e['motion']['simultaneous_moves'],0)
        self.assertTrue(all(x['far_quarter_cells_moved_during_near_preparation']>0 for x in e['quarter_overlap']))
        self.assertLess(len(e['pipeline_events'][0]['staged_tickets']),16)
        self.assertTrue(e['scene']['static_geometry_passed'])
    def test_holds_and_existing_spacing_limits(self):
        e=self.report['evidence']
        self.assertTrue(all(r['all_reshape_holds_at_least_4000ms'] for r in e['hold_trace']))
        self.assertTrue(any(r['reshape_events'] for r in e['hold_trace']))
        self.assertEqual((e['reshape_hold_ms'],e['independent_transport_gap'],e['bottom_staging_exception_gap']),(4000,6,4))
        self.assertGreaterEqual(next(x for x in e['pipeline_events'] if x['kind']=='mouth_cleared')['remaining_drop_min_x'],60)
    def test_only_operator_frame_count_changes(self):
        old=list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv').decode('utf-8-sig'))))
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig') as fh:new=list(csv.DictReader(fh))
        self.assertEqual(len(old),len(new))
        for a,b in zip(old,new):
            if int(a['吸液流程组编号'])==50004:
                self.assertEqual(int(b['末尾帧号']),len(self.new[50004][-1]));a.pop('末尾帧号');b.pop('末尾帧号')
            self.assertEqual(a,b)
        page=(PROJECT/'手工加液运行.html').read_text()
        self.assertEqual(re.findall(r'<table\b[^>]*>[\s\S]*?</table>',page)[0],re.findall(r'<table\b[^>]*>[\s\S]*?</table>',source('手工加液运行.html').decode())[0])
    def test_timing_and_report_hash(self):
        p=self.report;f=self.new[50004]
        self.assertEqual((p['before_seconds'],p['after_seconds'],p['total_saved_seconds']),(708,643,65))
        self.assertEqual(p['cumulative_saved_since_5445be1_seconds'],2406)
        self.assertEqual(int(f.get('Timeout')),sum(int(r.get('Duration')) for r in f.iter('RelayItem'))//1000)
        self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),sum(int(e.get('Timeout'))*int(e.get('Repeats','1')) for e in self.new.values()))
        self.assertEqual(p['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
if __name__=='__main__':unittest.main()
