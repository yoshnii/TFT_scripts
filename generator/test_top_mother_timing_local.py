"""Scoped tests for the slow-mother cadence and exported native geometry."""
import hashlib,json,subprocess,unittest
from pathlib import Path
import xml.etree.ElementTree as E

from scene import Scene,Drop
from native_packets import partition
import top_mother_timing as T
from update_top_mother_timing_local import BASE,REPORT,CASES

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'


def scene(area=80,shape=(10,8)):
    s=Scene();d=s.add(Drop('DTEST',(30,8,*shape),area*.05))
    s.step();T.start(s,'G14',[d]);return s,d


class MotherClockTests(unittest.TestCase):
    def test_two_moves_then_three_additional_seconds(self):
        s,d=scene()
        for _ in range(4):
            d.rect=(d.rect[0]+1,*d.rect[1:]);s.step()
        self.assertEqual([r.dur for r in s.relays[1:]],[1000,4000,1000,4000])
        self.assertEqual(len(s.relays),5)

    def test_parallel_mothers_share_one_wait(self):
        s,a=scene();b=s.add(Drop('DOTHER',(100,8,10,8),4.))
        b.top_slow_mother=True;s.step()
        for _ in range(2):
            for d in (a,b):d.rect=(d.rect[0]+1,*d.rect[1:])
            s.step()
        self.assertEqual(s.relays[-1].dur,4000)
        self.assertEqual(sum(e['extra_ms'] for e in s.top_mother_events),3000)

    def test_odd_tail_settles_before_four_second_reshape(self):
        s,d=scene();d.rect=(31,8,10,8);s.step()
        d.rect=(31,8,16,5);s.step(4000)
        self.assertEqual([r.dur for r in s.relays[-2:]],[4000,4000])

    def test_small_stage_is_fast_after_symmetric_cut(self):
        s,d=scene(60,(12,5))
        children=partition(s,[d],2,'x',6)[0]
        self.assertFalse(any(q.top_slow_mother for q in children))
        a=children[0];a.electrode_area=32;a.margin_budget=2
        a.rect=(*a.rect[:2],8,4);s.step(4000)
        for _ in range(2):a.rect=(a.rect[0]-1,*a.rect[1:]);s.step()
        self.assertEqual([r.dur for r in s.relays[-2:]],[1000,1000])

    def test_g4_is_not_enabled(self):
        s=Scene();d=s.add(Drop('DG4',(30,8,12,6),3.6))
        T.start(s,'G4',[d])
        self.assertIsNone(getattr(s,'top_mother_clock',None))


class NativeTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old=E.fromstring(subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT))
        cls.new=E.parse(PROJECT/'PTseq_16_A.elab').getroot()
        cls.report=json.loads((PROJECT/REPORT).read_text())

    def test_only_six_approved_sections_changed(self):
        changed={case[4] for case in CASES};actual=set()
        for a,b in zip(self.old,self.new):
            self.assertEqual(a.get('ID'),b.get('ID'))
            if E.tostring(a)!=E.tostring(b):actual.add(int(a.get('ID')))
            for x,y in zip(a.iter('RelayItem'),b.iter('RelayItem')):
                before=dict(x.attrib);after=dict(y.attrib)
                old_ms=int(before.pop('Duration'));new_ms=int(after.pop('Duration'))
                self.assertEqual(before,after)
                self.assertGreaterEqual(new_ms,old_ms)
                self.assertEqual((new_ms-old_ms)%3000,0)
            self.assertEqual(len(list(a.iter('RelayItem'))),len(list(b.iter('RelayItem'))))
        self.assertEqual(actual,changed)
        for fid in ('1002','50000'):
            self.assertEqual(E.tostring(self.old.find(f"Flow[@ID='{fid}']")),E.tostring(self.new.find(f"Flow[@ID='{fid}']")))

    def test_report_and_native_timing_sums(self):
        p=self.report
        self.assertEqual(p['baseline_commit'],BASE)
        self.assertEqual(p['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
        self.assertEqual(p['symmetric_split_count'],180)
        self.assertEqual(p['total_added_seconds'],sum(e['extra_ms'] for e in p['pauses'])//1000)
        for row in p['cases']:
            f=self.new.find(f"Flow[@ID='{row['section_id']}']")
            self.assertEqual(int(f.get('Timeout')),sum(int(r.get('Duration')) for r in f.iter('RelayItem'))//1000)
            self.assertEqual(row['added_seconds'],row['pause_count']*3)
        main=E.parse(PROJECT/'main.elab').getroot()
        self.assertEqual(int(main[0].get('Timeout')),sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in self.new))

    def test_adjacent_assets_not_rewritten(self):
        for name in ('PTseq_16_A.elab.layout','PTseq_SIRO16_A_10x.elabprj','ChipArea_192_256_96_SIRO.csv','手工加液顺序.csv','手工吸液顺序.csv'):
            self.assertEqual((PROJECT/name).read_bytes(),subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT))


if __name__=='__main__':unittest.main()
