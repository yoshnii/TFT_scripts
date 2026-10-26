"""Focused regression for cDNA 1.60+0.40µL and its target-PCR boundary."""
import csv, hashlib, json, subprocess, unittest
from pathlib import Path
import xml.etree.ElementTree as E

from audit_binary_frames import PATTERN
from rotate_project_180 import rotate_code

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
CHANGED={1000,1002,50000,1006,1010,1019,1022,50001}
BASE='9a5e4645b8d45557a0f2c98e074abc8c5634f8a2'


class CdnaTwoMicrolitreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=E.parse(PROJECT/'PTseq_16_A.elab').getroot()
        cls.flows={int(f.get('ID')):f for f in cls.raw}
        cls.proof=json.loads((PROJECT/'cDNA两微升反应局部校验_20261026.json').read_text())

    def test_reaction_and_retained_mass(self):
        p=self.proof
        self.assertEqual(p['sample_ul_per_lane']+p['g4_ul_per_lane'],2.)
        self.assertEqual(p['retained_cdna_ul_per_lane']+p['waste_ul_per_lane'],2.)
        self.assertEqual(p['retained_cdna_ul_per_lane']+p['target_reagent_ul_per_lane'],2.5)
        self.assertEqual(p['retained_sample_equivalent_ul'],.4)
        self.assertEqual(p['retained_g4_equivalent_ul'],.1)
        self.assertEqual(sum(p['waste_port_ul'].values()),16*1.5)
        self.assertNotIn(70000,self.flows)
        order=list(self.flows)
        self.assertLess(order.index(1010),order.index(1019))
        self.assertLess(order.index(1019),order.index(1022))

    def test_thermal_geometry_and_timing(self):
        f=self.flows[1010]
        self.assertEqual([c.get('Params') for c in f if c.get('Component')=='Heater1'],['37,1','85,1','4,1'])
        self.assertEqual([c.get('Timeout') for c in f if c.get('Component')=='Delay'],['600','5','30'])
        self.assertEqual(self.flows[1006].find("Command[@Component='Delay']").get('Timeout'),'1800')
        code=list(f.iter('RelayItem'))[-1].get('RelayCode')
        if self.raw.get('ChipsetType').endswith('_R180'):code=rotate_code(code)
        boxes=[tuple(map(int,m)) for m in PATTERN.findall(code)]
        self.assertEqual(len(boxes),16)
        self.assertTrue(all(h*w==40 and x>=10 and x+w<=28 for y,x,h,w in boxes))

    def test_all_related_binary_splits_are_equal(self):
        self.assertEqual(self.proof['split_event_count'],92)
        self.assertEqual(self.proof['scene']['equal_x_splits'],92)
        self.assertTrue(self.proof['all_splits_equal_and_symmetric'])
        self.assertGreater(self.proof['exported_frame_audit']['geometric_splitting_transitions'],0)
        self.assertEqual(self.proof['exported_frame_audit']['non_equal_binary_transitions'],[])

    def test_operator_volumes_and_load_handoffs(self):
        with (PROJECT/'手工加液顺序.csv').open(encoding='utf-8-sig') as fh:
            rows={int(r['加液流程组编号']):r for r in csv.DictReader(fh)}
        self.assertEqual(float(rows[1000]['本次µL（S/B孔为每道）']),1.6)
        self.assertEqual(float(rows[1002]['本次µL（S/B孔为每道）']),6.4)
        self.assertEqual(rows[1002]['各边缘孔µL'],"{'E1': 3.2, 'E2': 3.2}")
        self.assertEqual(float(rows[1022]['本次µL（S/B孔为每道）']),32.)
        for fid in (1000,1002,1022):
            row=rows[fid];a=self.flows[fid][-1][-1]
            b=self.flows[int(row['下一个流程组编号'])][0][0]
            self.assertEqual(a.get('RelayCode'),b.get('RelayCode'))
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig') as fh:
            rows={int(r['吸液流程组编号']):r for r in csv.DictReader(fh)}
        self.assertNotIn(70000,rows)
        self.assertEqual(float(rows[1019]['本次总液体µL（16道合计；排珠载液不计文库）']),24.)
        self.assertEqual(rows[1019]['待吸液电极'],'')

    def test_all_unrelated_flows_and_chip_files_are_unchanged(self):
        old=E.fromstring(subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT))
        for f in old:
            fid=int(f.get('ID'))
            if fid not in CHANGED|{70000}:
                self.assertEqual(E.tostring(f),E.tostring(self.flows[fid]),fid)
        self.assertEqual(old.find("Flow[@ID='50001']")[-1][-1].get('RelayCode'),self.flows[50001][-1][-1].get('RelayCode'))
        for name in ('ChipArea_192_256_96_SIRO.csv','ChipArea_192_256_96_SIRO_R180.csv','PTseq_SIRO16_A_10x.elabprj'):
            self.assertEqual(subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT),(PROJECT/name).read_bytes())
        self.assertEqual(hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest(),self.proof['script_sha256'])


if __name__=='__main__':unittest.main()
