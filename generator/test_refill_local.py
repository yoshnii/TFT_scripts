"""R02 refill: one shared frame, unchanged physical endpoints and native scope."""
import hashlib,json,subprocess,unittest
from pathlib import Path
import xml.etree.ElementTree as E
import rigid
from scene import Scene,Drop
from top_horizontal import DeliveryPool
from update_refill_local import BASE,REPORT
from update_transport_cleanup_local import last_hv
ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
def source(name):return subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT)

class BackgroundDeliveryTests(unittest.TestCase):
    def setup_pool(self):
        s=Scene();d=s.add(Drop('PACKET',(100,8,4,4),.8))
        d.native_packet=True;d.port=1;d.row=1;s.pipeline_events=[]
        pool=s.top_delivery_pool=DeliveryPool(s)
        pool.items.append((d,rigid.go(d,[('x',104)])))
        return s,d,pool
    def test_single_step_and_cleanup(self):
        s,d,p=self.setup_pool()
        for i in range(4):
            s.step();self.assertEqual((d.rect[0],s.nsteps),(101+i,i+1))
        p.finish();self.assertIsNone(s.background);self.assertIsNone(d.pending)
    def test_mother_hold_stays_four_seconds(self):
        s,d,p=self.setup_pool();m=s.add(Drop('MOTHER',(160,8,10,8),4.));fixed=m.rect
        s.step(4000);self.assertEqual(s.relays[-1].dur,4000)
        self.assertEqual(m.rect,fixed);self.assertEqual(d.rect[0],101);p.finish()
    def test_no_extra_frame_from_background_advance(self):
        s,d,p=self.setup_pool();moved,touch,ms=p.advance(s)
        self.assertEqual(s.nsteps,0);self.assertEqual(d.rect[0],101)
        self.assertIn(d.name,moved);self.assertEqual(ms,1000)
    def test_foreground_and_background_share_frame(self):
        s,d,p=self.setup_pool();m=s.add(Drop('MOTHER',(160,8,10,8),4.))
        rigid.tick(s,[(m,rigid.go(m,[('x',161)]))])
        self.assertEqual((s.nsteps,m.rect[0],d.rect[0]),(1,161,101))
    def test_restore_preceding_empty_waste_scheduler(self):
        import waste_grid as W
        s=Scene();W._pool(s);prior=s.background
        p=DeliveryPool(s);p.finish()
        self.assertEqual(s.background,prior)

class RefillReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old={int(f.get('ID')):f for f in E.fromstring(source('PTseq_16_A.elab'))}
        cls.new={int(f.get('ID')):f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
        cls.report=json.loads((PROJECT/REPORT).read_text())
    def test_only_R02_changes(self):
        self.assertEqual(self.old.keys(),self.new.keys())
        self.assertEqual({i for i in self.old if E.tostring(self.old[i])!=E.tostring(self.new[i])},{50002})
        for name in ('PTseq_SIRO16_A_10x.elabprj','PTseq_16_A.elab.layout','ChipArea_192_256_96_SIRO.csv','手工加液顺序.csv','手工吸液顺序.csv'):
            self.assertEqual((PROJECT/name).read_bytes(),source(name))
        a,b=self.old[50002],self.new[50002]
        self.assertEqual({k:v for k,v in a.attrib.items() if k!='Timeout'},{k:v for k,v in b.attrib.items() if k!='Timeout'})
        self.assertEqual(last_hv(a).get('RelayCode'),last_hv(b).get('RelayCode'))
        self.assertEqual(a[0][0].get('RelayCode'),b[0][0].get('RelayCode'))
        for x,y in zip(a,b):self.assertEqual({k:v for k,v in x.attrib.items() if k!='Timeout'},{k:v for k,v in y.attrib.items() if k!='Timeout'})
    def test_mass_and_synchronized_fusion(self):
        e=self.report['evidence']
        self.assertEqual((e['leaf_count'],e['leaf_ul'],e['leaf_shape_w_h'],e['symmetric_splits']),(64,.625,[3,5],60))
        self.assertEqual(e['leaf_count']*e['leaf_ul'],16*e['true_ul_per_lane_added'])
        self.assertEqual((e['true_ul_per_port'],e['true_ul_per_lane_final']),(10.,5.))
        self.assertGreater(e['first_reaction_contact_step'],e['all_ready_step'])
        self.assertTrue(e['scene']['static_geometry_passed'])
    def test_hold_and_overlap_evidence(self):
        e=self.report['evidence'];self.assertGreater(e['overlap']['mother_and_packet_moving_frames'],0)
        self.assertEqual(len({r['at_ms'] for r in e['initial_root_reshapes']}),1)
        self.assertEqual({r['name'] for r in e['initial_root_reshapes']},{f'DG14E{i}' for i in range(1,5)})
        self.assertTrue(all(r['all_reshape_holds_at_least_4000ms'] for r in e['raw_trace']))
        self.assertEqual((e['per_mother_rest_ms'],e['ordinary_transport_gap']),(3000,6))
    def test_time_and_current_hash(self):
        f=self.new[50002];p=self.report
        self.assertEqual(p['total_saved_seconds'],520);self.assertEqual(p['before_seconds']-p['after_seconds'],520)
        self.assertEqual(p['cumulative_saved_since_5445be1_seconds'],2341)
        self.assertEqual(int(f.get('Timeout')),sum(int(r.get('Duration')) for r in f.iter('RelayItem'))//1000)
        self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),sum(int(e.get('Timeout'))*int(e.get('Repeats','1')) for e in self.new.values()))
        self.assertEqual(p['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
if __name__=='__main__':unittest.main()
