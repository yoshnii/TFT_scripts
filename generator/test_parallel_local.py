"""Concurrency release checks: individual cooldowns, native scope and physical results."""
import csv,hashlib,io,json,subprocess,unittest
from pathlib import Path
import xml.etree.ElementTree as E
import rigid
from scene import Scene,Drop
import top_mother_timing as T
from update_parallel_local import BASE,REPORT,CHANGED,CASES
from update_transport_cleanup_local import last_hv
from update_captured_aqueous_local import bottom_key

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'

def source(name):return subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT)

class IndependentClockTests(unittest.TestCase):
    def test_one_mother_can_move_while_other_finishes_three_second_rest(self):
        s=Scene();a=s.add(Drop('DTEST',(30,8,10,8),4.));b=s.add(Drop('DOTHER',(100,8,10,8),4.))
        s.step();T.start(s,'AMP',[a,b]);s.top_parallel_moves=True
        trace=[];record=s._record
        def capture(*args,**kwargs):
            record(*args,**kwargs);trace.append((a.rect,b.rect,s.relays[-1].dur))
        s._record=capture
        def later():
            yield 'wait'
            yield from rigid.go(b,[('x',106)])
        rigid.run(s,[(a,rigid.go(a,[('x',36)])),(b,later())])
        elapsed=0;prev=[(30,8,10,8),(100,8,10,8)];count=[0,0];ready=[0,0];overlap=False
        for ar,br,ms in trace:
            moved=[ar!=prev[0],br!=prev[1]]
            if moved[1] and not moved[0] and elapsed<ready[0]:overlap=True
            for i,rc in enumerate((ar,br)):
                if moved[i]:
                    self.assertGreaterEqual(elapsed,ready[i])
                    self.assertEqual(abs(rc[0]-prev[i][0])+abs(rc[1]-prev[i][1]),1)
                    count[i]+=1
                    if count[i]==2:ready[i]=elapsed+ms+3000;count[i]=0
                prev[i]=rc
            elapsed+=ms
        self.assertTrue(overlap,'Independent movement must use time while another mother waits')
        self.assertGreaterEqual(elapsed,max(ready))
        self.assertEqual((a.rect[0],b.rect[0]),(36,106))
        self.assertFalse(s.top_mother_clock.parallel_run)

    def test_odd_last_move_rests_before_subsequent_reshape(self):
        s=Scene();d=s.add(Drop('DTEST',(30,8,10,8),4.));s.step()
        T.start(s,'G14',[d]);s.top_parallel_moves=True
        rigid.run(s,[(d,rigid.go(d,[('x',31)]))])
        self.assertGreaterEqual(sum(r.dur for r in s.relays[1:]),4000)
        self.assertEqual(s.top_independent_rest_events[-1]['moved_cells'],1)
        rigid.reshape_many(s,[(d,(31,8,16,5))],dur=4000)
        self.assertEqual(s.relays[-1].dur,4000)

class ParallelReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old={int(f.get('ID')):f for f in E.fromstring(source('PTseq_16_A.elab'))}
        cls.new={int(f.get('ID')):f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
        cls.proof=json.loads((PROJECT/REPORT).read_text())

    def test_only_ten_scheduled_sections_change(self):
        self.assertEqual(self.old.keys(),self.new.keys())
        self.assertEqual({i for i in self.old if E.tostring(self.old[i])!=E.tostring(self.new[i])},CHANGED)
        for name in ('PTseq_SIRO16_A_10x.elabprj','PTseq_16_A.elab.layout','ChipArea_192_256_96_SIRO.csv','手工加液顺序.csv'):
            self.assertEqual((PROJECT/name).read_bytes(),source(name))
        for _,_,_,fid,did,_ in CASES:
            self.assertEqual(E.tostring(self.old[fid]),E.tostring(self.new[fid]))
            self.assertEqual(last_hv(self.old[did]).get('RelayCode'),last_hv(self.new[did]).get('RelayCode'))
        for fid in (1002,50000,1019,2013,4013,51002):
            self.assertEqual(E.tostring(self.old[fid]),E.tostring(self.new[fid]))

    def test_mass_leaf_counts_and_synchronized_fusion(self):
        tops=[r for r in self.proof['cases'] if r['kind']=='top']
        self.assertEqual(len(tops),6)
        self.assertEqual(sum(r['split_events'] for r in tops),180)
        for r in tops:
            self.assertAlmostEqual(r['total_leaves']*r['leaf_actual_ul'],16*r['per_lane_added_ul'])
            self.assertEqual(r['total_leaves'],r['split_events']+r['total_leaves']//16)
            self.assertGreater(r['fusion_step'],r['all_ready_step'])
            self.assertEqual(r['rest_ms'],3000);self.assertEqual(r['shape_hold_ms'],4000)
            self.assertTrue(r['scene']['static_geometry_passed'])
        for fid in (51000,2068,4048,75020):
            self.assertEqual([bottom_key(r) for r in list(self.old[fid][-1])[-24:]],
                             [bottom_key(r) for r in list(self.new[fid][-1])[-24:]])
        for a,b in ((2013,51000),(4013,51002)):
            self.assertEqual(last_hv(self.new[a]).get('RelayCode'),self.new[b][0][0].get('RelayCode'))

    def test_graph_hardware_and_fixed_timing_unchanged(self):
        for fid in CHANGED:
            old,new=self.old[fid],self.new[fid]
            self.assertEqual({k:v for k,v in old.attrib.items() if k!='Timeout'},
                             {k:v for k,v in new.attrib.items() if k!='Timeout'})
            self.assertEqual(len(old),len(new))
            for a,b in zip(old,new):
                self.assertEqual({k:v for k,v in a.attrib.items() if k!='Timeout'},
                                 {k:v for k,v in b.attrib.items() if k!='Timeout'})
                if a.get('Component')!='HighVoltage':self.assertEqual(E.tostring(a),E.tostring(b))
                else:self.assertEqual(int(b.get('Timeout')),sum(int(r.get('Duration')) for r in b)//1000)

    def test_csv_updates_only_four_waste_frame_counts(self):
        old=list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv').decode('utf-8-sig'))))
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig') as fh:new=list(csv.DictReader(fh))
        self.assertEqual(len(old),len(new))
        for a,b in zip(old,new):
            fid=int(a['吸液流程组编号'])
            if fid in (51000,2068,4048,75020):
                self.assertEqual(int(b['末尾帧号']),len(self.new[fid][-1]))
                a.pop('末尾帧号');b.pop('末尾帧号')
            self.assertEqual(a,b)

    def test_time_savings_and_report_current_hash(self):
        self.assertEqual(self.proof['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
        self.assertEqual(self.proof['total_saved_seconds'],sum(int(self.old[i].get('Timeout'))-int(self.new[i].get('Timeout')) for i in CHANGED))
        self.assertEqual(self.proof['total_saved_seconds'],1821)
        self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),
                         sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in self.new.values()))

if __name__=='__main__':unittest.main()
