"""Capacity is not liquid; test receiver handoffs and all nine native collectors."""
import csv,hashlib,io,json,subprocess,unittest
from pathlib import Path
import xml.etree.ElementTree as E
import geom
from scene import Scene,Drop
import fixed_waste_collect as F
from update_fixed_waste_local import BASE,REPORT,TARGET,relay_key
from waste_recovery import boxes
ROOT=Path(__file__).resolve().parent.parent;PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
def source(n):return subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+n],cwd=ROOT)
def cells(s):return {(i,j) for c in s.relays[-1].codes for x,y,w,h in boxes(c) for i in range(x,x+w) for j in range(y,y+h)}
class ReceiverUnitTests(unittest.TestCase):
    def setup_receiver(self,w=16):
        s=Scene();s.strict_transport=True;s.outlet_staging_gap=4
        j=F.make_job(s,dict(port=1,W=w,rows=[([4]*4,4)]*4),{});F.open_receiver(s,j)
        return s,j,j['receiver']
    def packet(self,s,rc,vol=.75):
        d=s.add(Drop('P',rc,vol));d.electrode_area=rc[2]*rc[3];d.margin_budget=d.electrode_area-geom.cells(vol)
        d.native_packet=d.waste_grid_packet=True;return d
    def test_preopen_zero_mass_full_capacity(self):
        s,j,h=self.setup_receiver();s.step();self.assertEqual((h.vol,len(cells(s))),(0.,256))
    def test_block_foreign_traffic(self):
        s,j,h=self.setup_receiver();s.remove(h.name);j['receiver']=None;d=self.packet(s,(48,172,4,4))
        self.assertFalse(F.can_open(s,j));d.rect=(100,172,4,4);self.assertTrue(F.can_open(s,j))
    def test_partial_entry_retains_full_coverage_then_conserves_mass(self):
        s,j,h=self.setup_receiver();d=self.packet(s,(46,169,4,4));d.waste_receiver=h.name;d.allow=(h.name,)
        s.step(touching=[(d.name,h.name)]);self.assertEqual(cells(s),set(h.target_cells()|d.target_cells()))
        with self.assertRaises(AssertionError):F.absorb(s,j,d)
        for y in (170,171,172):d.rect=(46,y,4,4);s.step(touching=[(d.name,h.name)])
        F.absorb(s,j,d);s.step();self.assertEqual((h.vol,h.rect,j['received']),(.75,(46,172,16,16),1))
        self.assertEqual(cells(s),set(h.target_cells()))
    def test_corner_overlap_has_no_missing_or_duplicate_electrodes(self):
        s,j,h=self.setup_receiver(12);d=self.packet(s,(46,170,3,4),.6);d.waste_receiver=h.name;d.allow=(h.name,)
        s.step(touching=[(d.name,h.name)]);self.assertEqual(cells(s),set(h.target_cells()|d.target_cells()))
        self.assertEqual(sum(w*hh for c in s.relays[-1].codes for x,y,w,hh in boxes(c)),len(cells(s)))
    def test_wrong_receiver_overlap_rejected(self):
        s,j,h=self.setup_receiver();d=self.packet(s,(46,169,4,4))
        with self.assertRaises(AssertionError):s.step(touching=[(d.name,h.name)])
    def test_unrelated_overlap_still_rejected(self):
        s,j,h=self.setup_receiver();a=s.add(Drop('A',(100,8,4,4),.8));b=s.add(Drop('B',(101,8,4,4),.8))
        with self.assertRaises(AssertionError):s.step(touching=[(a.name,b.name)])
    def test_unsupported_plans_not_intercepted(self):
        d=Drop('P',(10,28,4,4),.75)
        self.assertTrue(F.accepts([(None,d)],dict(packed=True,specs={1:dict(native=True,W=16,rows=[([4]*4,4)]*4)})))
        self.assertFalse(F.accepts([(None,d)],dict(packed=True,specs={1:dict(native=True,W=24,rows=[([3]*8,5)]*2)})))
class FixedWasteReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old={int(f.get('ID')):f for f in E.fromstring(source('PTseq_16_A.elab'))}
        cls.new={int(f.get('ID')):f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
        cls.report=json.loads((PROJECT/REPORT).read_text())
    def test_only_all_nine_collectors_change(self):
        self.assertEqual(self.old.keys(),self.new.keys());self.assertEqual({i for i in self.old if E.tostring(self.old[i])!=E.tostring(self.new[i])},TARGET)
        for n in ('PTseq_SIRO16_A_10x.elabprj','PTseq_16_A.elab.layout','ChipArea_192_256_96_SIRO.csv','手工加液顺序.csv'):self.assertEqual((PROJECT/n).read_bytes(),source(n))
        for i in (1019,50004):self.assertEqual(E.tostring(self.old[i][0]),E.tostring(self.new[i][0]))
    def test_suction_tails_and_control_metadata_identical(self):
        for i in TARGET:
            a,b=self.old[i],self.new[i];n=22 if i==50004 else 24
            self.assertEqual([relay_key(r) for r in list(a[-1])[-n:]],[relay_key(r) for r in list(b[-1])[-n:]])
            self.assertEqual({k:v for k,v in a.attrib.items() if k!='Timeout'},{k:v for k,v in b.attrib.items() if k!='Timeout'})
            for x,y in zip(a,b):self.assertEqual({k:v for k,v in x.attrib.items() if k!='Timeout'},{k:v for k,v in y.attrib.items() if k!='Timeout'})
    def test_receiver_masks_fixed_until_suction(self):
        for i in TARGET:
            rr=list(self.new[i][-1]);n=22 if i==50004 else 24;body=rr[:-n]
            wanted=[r for r in boxes(rr[-n].get('RelayCode')) if r[1]==172 and r[3]==16];self.assertTrue(wanted)
            for rc in wanted:
                first=next(j for j,r in enumerate(body) if rc in boxes(r.get('RelayCode')))
                self.assertTrue(all(rc in boxes(r.get('RelayCode')) for r in body[first:]))
    def test_capacity_and_actual_mass_separately_accounted(self):
        p=self.report;self.assertEqual((p['admitted_packets'],p['fixed_receiver_cycles']),(496,31))
        self.assertAlmostEqual(sum(sum(e['packet_ul'] for e in c['fixed']['admission_events']) for c in p['cases']),356.16)
        for c in p['cases']:
            e=c['fixed']
            for key in ('mass_conserved_each_scene_frame','receiver_footprints_unchanged','full_coverage_and_no_duplicate_codes','no_intermediate_collection_blocks'):self.assertTrue(e[key])
            self.assertTrue(all(x['actual_ul']==0 for x in e['preopen_events']))
            self.assertTrue(all(t['all_reshape_holds_at_least_4000ms'] for t in e['hold_trace']))
    def test_water_cut_geometry_and_waits_preserved(self):
        for c in self.report['captured_cut_checks']:
            self.assertTrue(c['first_split_unchanged'])
            for i,g in enumerate(c['cut_groups']):self.assertEqual((g['gaps'],g['frame_ms']),([0,2,4],[1000]*3) if i==0 else ([0,2,4,6],[1000,3000,3000,5000]))
        for a,b in ((2013,51000),(4013,51002)):self.assertEqual(self.new[a][-1][-1].get('RelayCode'),self.new[b][0][0].get('RelayCode'))
    def test_only_aspiration_frame_counters_change(self):
        old=list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv').decode('utf-8-sig'))))
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig') as f:new=list(csv.DictReader(f))
        self.assertEqual(len(old),len(new))
        for a,b in zip(old,new):
            if int(a['吸液流程组编号']) in TARGET:self.assertEqual(int(b['末尾帧号']),len(self.new[int(b['吸液流程组编号'])][-1]));a.pop('末尾帧号');b.pop('末尾帧号')
            self.assertEqual(a,b)
    def test_time_and_report_hash(self):
        p=self.report;self.assertEqual(p['total_saved_seconds'],sum(int(self.old[i].get('Timeout'))-int(self.new[i].get('Timeout')) for i in TARGET))
        for i in TARGET:self.assertEqual(int(self.new[i].get('Timeout')),sum(int(r.get('Duration')) for r in self.new[i].iter('RelayItem'))//1000)
        self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in self.new.values()))
        self.assertEqual(p['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
if __name__=='__main__':unittest.main()
