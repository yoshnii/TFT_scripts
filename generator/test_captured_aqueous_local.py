"""Local release checks for captured-water cuts and their immediate boundaries."""
import csv,hashlib,io,json,subprocess,unittest
from pathlib import Path
import xml.etree.ElementTree as E

import geom
from scene import Scene,Drop
from native_packets import partition,validate_symmetric_split
from transport_spacing import required
from waste_recovery import boxes
from update_captured_aqueous_local import BASE,REPORT,native_key,bottom_key

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
TARGET={2013,51000,4013,51002}


def source(name):return subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT)


class CutTimingTests(unittest.TestCase):
    def test_new_gap_cadence_and_default_are_separate(self):
        for gap,ms in ((4,None),(6,(1000,3000,3000,5000))):
            s=Scene();d=s.add(Drop('TEST',(40,8,16,4),3.2))
            children=partition(s,[d],2,'x',gap,frame_ms=ms)[0]
            e=s.split_events[0];validate_symmetric_split(e)
            self.assertEqual(e['gaps'],[0,2,4] if ms is None else [0,2,4,6])
            self.assertEqual([r.dur for r in s.relays],[1000]*3 if ms is None else list(ms))
            self.assertEqual(sum(d.vol for d in children),3.2)

    def test_waiting_exception_stays_inside_well(self):
        s=Scene();s.strict_transport=True;s.aqueous_wait_gap=2;s.batch_channel_x=46
        a=Drop('LIQA1',(10,28,10,5),2.5);b=Drop('LIQA1_1',(22,28,8,4),1.25)
        self.assertEqual(required(s,a,a.rect,b,b.rect),2)
        a.native_packet=b.native_packet=True;a.batch_round=b.batch_round=1
        self.assertEqual(required(s,a,(46,50,4,4),b,(46,59,4,4)),5)
        s.c05_cut_gap=2
        self.assertEqual(required(s,a,(46,50,4,4),b,(46,59,4,4)),6)
        s.c05_cut_gap=s.aqueous_wait_gap=None
        self.assertEqual(required(s,a,a.rect,b,b.rect),3)


class CapturedWaterReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old={int(f.get('ID')):f for f in E.fromstring(source('PTseq_16_A.elab'))}
        cls.new={int(f.get('ID')):f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
        cls.proof=json.loads((PROJECT/REPORT).read_text())

    def test_only_four_sections_changed(self):
        self.assertEqual(self.old.keys(),self.new.keys())
        changed={fid for fid in self.old if E.tostring(self.old[fid])!=E.tostring(self.new[fid])}
        self.assertEqual(changed,TARGET)
        for name in ('PTseq_SIRO16_A_10x.elabprj','PTseq_16_A.elab.layout','ChipArea_192_256_96_SIRO.csv','手工加液顺序.csv'):
            self.assertEqual((PROJECT/name).read_bytes(),source(name))

    def test_first_cut_preserved_and_later_cuts_settle(self):
        self.assertEqual(self.proof['symmetric_splits_checked'],224)
        self.assertEqual(self.proof['first_cut_events_unchanged'],32)
        self.assertEqual(self.proof['widened_cut_events'],192)
        for case,fid in zip(self.proof['cases'],(2013,4013)):
            n=case['unchanged_first_cut_frames']
            self.assertEqual([native_key(r) for r in list(self.old[fid][0])[:n]],
                             [native_key(r) for r in list(self.new[fid][0])[:n]])
            self.assertEqual(case['original_prefix_hash'],case['first_cut_prefix_sha256'])
            for i,group in enumerate(case['cut_groups']):
                self.assertEqual(group['parents'],16)
                self.assertEqual(group['gaps'],[0,2,4] if i==0 else [0,2,4,6])
                self.assertEqual(group['frame_ms'],[1000]*3 if i==0 else [1000,3000,3000,5000])
            self.assertTrue(case['scene']['magnet_cut_lines_avoid_black_center'])
            self.assertTrue(case['scene']['static_geometry_passed'])

    def test_water_volumes_and_f_recovery_pattern(self):
        for fid in TARGET:
            self.assertEqual([bottom_key(r) for r in list(self.old[fid][-1])[-24:]],
                             [bottom_key(r) for r in list(self.new[fid][-1])[-24:]])
        self.assertEqual(self.proof['cases'][0]['batch_aspirations_ul'],[40.,40.])
        self.assertEqual(self.proof['cases'][1]['batch_aspirations_ul'],[49.28,49.28])
        for fid in (51000,51002):self.assertEqual(self.new[fid][-1][-1].get('RelayCode',''),'')
        for a,b in ((2013,51000),(4013,51002)):
            self.assertEqual(self.new[a][-1][-1].get('RelayCode'),self.new[b][0][0].get('RelayCode'))

    def test_temporary_c_half_frame_is_66_cells_with_hole_gap(self):
        rects=boxes(self.new[4013][-1][-1].get('RelayCode'))
        self.assertEqual(len(rects),16)
        for rc in rects:
            x,y,w,h=rc;self.assertEqual((w,h),(11,6))
            row=(y-27)//9+1;X,Y=geom.physical_zone(1,row)
            self.assertEqual((x,y),(X,Y));self.assertTrue(geom.reaction_holes_ok(rc,1))
        self.assertEqual(self.proof['cases'][1]['temporary_carrier_cell_increase'],1)

    def test_operator_csv_changes_only_target_end_frame_counts(self):
        old=list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv').decode('utf-8-sig'))))
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig') as fh:new=list(csv.DictReader(fh))
        self.assertEqual(len(old),len(new))
        for a,b in zip(old,new):
            fid=int(a['吸液流程组编号'])
            if fid in TARGET:
                self.assertEqual(int(b['末尾帧号']),len(self.new[fid][-1]))
                a=dict(a);b=dict(b);a.pop('末尾帧号');b.pop('末尾帧号')
            self.assertEqual(a,b)

    def test_time_accounting_and_current_report_hash(self):
        total=0
        for fid,f in self.new.items():
            if fid in TARGET:self.assertEqual(int(f.get('Timeout')),sum(int(r.get('Duration')) for r in f.iter('RelayItem'))//1000)
            total+=int(f.get('Timeout'))*int(f.get('Repeats','1'))
        self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),total)
        self.assertEqual(self.proof['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
        self.assertEqual(self.proof['total_added_seconds'],sum(int(self.new[i].get('Timeout'))-int(self.old[i].get('Timeout')) for i in TARGET))


if __name__=='__main__':unittest.main()
