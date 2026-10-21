"""Focused regression for the 2026-10-19 workbook-aligned revision."""
import csv,json,subprocess,unittest
from pathlib import Path
import xml.etree.ElementTree as E
import geom,protocol
from scene import Drop
from update_pcr_block_local import temperature_trace

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'


class WorkbookVolumesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=E.parse(PROJECT/'PTseq_16_A.elab').getroot()
        cls.flows={int(f.get('ID')):f for f in cls.raw}

    def test_small_reagent_has_fifteen_equal_cuts_and_exact_two_cell_leaves(self):
        p=protocol.Protocol((1,))
        for ln in p.ALL:p.S.add(Drop(ln.name,(ln.X0+26,ln.Y0,4,2),.4))
        p.S.step();p.S.flush()
        p.add_reagent('A','A03 G4','G4',.1)
        self.assertEqual(len(p.S.split_events),15)
        self.assertTrue(all(e['volumes'][0]==e['volumes'][1] for e in p.S.split_events))
        self.assertEqual({e['volumes'][0] for e in p.S.split_events},{.8,.4,.2,.1})
        self.assertEqual(len(p.S.native_leaf_events),16)
        self.assertTrue(all(e['cells']==2 and e['actual_ul']==.1 and not e['extra_cells'] for e in p.S.native_leaf_events))
        self.assertTrue(all(d.vol==.5 and len(d.target_cells())==10 for d in p.S.drops.values()))

    def test_sample_mass_and_removed_cdna_discard(self):
        proof=json.loads((PROJECT/'体积表对齐局部校验_20261019.json').read_text())
        self.assertEqual(proof['sample_mother_ul'],proof['sample_retained_ul']+proof['sample_waste_ul_per_lane'])
        self.assertEqual(proof['sample_retained_ul']+proof['reagent_per_lane_ul'],.5)
        self.assertNotIn(1019,self.flows)
        self.assertIn(70000,self.flows)
        self.assertEqual(proof['binary_events_checked'],93)
        self.assertEqual(proof['exported_frame_audit']['non_equal_binary_transitions'],[])

    def test_sample_splitting_stays_in_its_own_reaction_well(self):
        from audit_binary_frames import PATTERN
        relays=list(self.flows[70000][0])
        # Locate the two quarters plus the intact waste-half staging frame, not a fixed
        # frame-count assumption that would miss an inserted movement.
        end=next(i for i,relay in enumerate(relays) if sorted(int(x) for y,x,h,w
                 in PATTERN.findall(relay.get('RelayCode','')) if y=='28' and h=='4' and w in ('2','4'))==[11,17,23])
        for relay in relays[:end+1]:
            for y,x,h,w in PATTERN.findall(relay.get('RelayCode','')):
                x,y,w,h=map(int,(x,y,w,h))
                self.assertTrue(10<=x<x+w<=46,(relay.get('ItemID'),x,w))
                row=(y-27)//9+1;py=27+9*(row-1)
                self.assertTrue(1<=row<=16 and py<=y<y+h<=py+6)
        proof=json.loads((PROJECT/'A02整列并行局部校验.json').read_text())
        self.assertEqual(proof['staged_x'],[11,17,23])
        self.assertEqual(proof['scene']['equal_x_splits'],32)

    def test_thermal_targets_and_pcr_are_retained(self):
        cdna=self.flows[1010]
        self.assertEqual([c.get('Params') for c in cdna if c.get('Component')=='Heater1'],['37,1','85,1','4,1'])
        self.assertEqual([c.get('Timeout') for c in cdna if c.get('Component')=='Delay'],['600','5','30'])
        trace=temperature_trace(self.flows[1030])
        self.assertEqual([c.get('Params') for c in trace if c.get('Component')=='Heater1'],['95,1']+['95,1','60,1','72,1']*30+['72,1','4,1'])
        for fid in (2022,2045):self.assertEqual(self.flows[fid].get('Timeout'),'300')
        for fid in (2032,2055):
            self.assertEqual(self.flows[fid][1].get('Timeout'),'120')
        for fid in (2017,2040):
            self.assertEqual(next(c for c in self.flows[fid] if c.get('Component')=='Delay').get('Timeout'),'288')

    def test_unrelated_native_flows_are_identical_to_base(self):
        old=E.fromstring(subprocess.check_output(['git','show','89d50d0:projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT))
        excluded={1000,1002,50000,1006,1010,1019,1022,50001,2015,2017,2022,2032,2038,2040,2045,2055,3000}
        for flow in old:
            fid=int(flow.get('ID'))
            if fid not in excluded:self.assertEqual(E.tostring(flow).strip(),E.tostring(self.flows[fid]).strip())

    def test_orders_and_loading_handoffs_match_actual_native_masks(self):
        flows=list(self.raw);index={f.get('ID'):i for i,f in enumerate(flows)}
        with (PROJECT/'手工加液顺序.csv').open(encoding='utf-8-sig') as fh:rows=list(csv.DictReader(fh))
        self.assertEqual(len(rows),16)
        for row in rows:
            fid=row['加液流程组编号'];f=flows[index[fid]]
            self.assertEqual(row['下一个流程组编号'],flows[index[fid]+1].get('ID'))
            if int(fid) in (1000,1002,1022,2015,2038):
                a=set(f[-1][-1].get('RelayCode','').split(';'))-{''}
                b=set(flows[index[fid]+1][0][0].get('RelayCode','').split(';'))-{''}
                self.assertEqual(a,b)
        self.assertEqual(float(next(r for r in rows if r['加液流程组编号']=='1002')['本次µL（S/B孔为每道）']),1.6)
        for fid in ('2015','2038'):
            self.assertEqual(float(next(r for r in rows if r['加液流程组编号']==fid)['本次µL（S/B孔为每道）']),3.)
        self.assertEqual(float(next(r for r in rows if r['加液流程组编号']=='3000')['本次µL（S/B孔为每道）']),1.3)


if __name__=='__main__':unittest.main()
