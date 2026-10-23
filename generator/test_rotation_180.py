"""Exact orientation-only regression against the preserved first-water-run tag."""
import csv,hashlib,io,json,subprocess,unittest
from decimal import Decimal as D
from pathlib import Path
import xml.etree.ElementTree as E

from rotate_project_180 import (BOX,BASE_CHIP,ROTATED_CHIP,REPORT,rotate_code,
                                rotate_csv,rotate_script,verify_csv,verify_scripts)
from electrode_address_compensation import address_rotation,layout_coordinate_script

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
TAG='tft-first-on-chip-water-run-20261022'


def baseline(name):
    return subprocess.check_output(['git','show',TAG+':projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT)


class RotationTests(unittest.TestCase):
    def test_rectangle_edges_use_transposed_grid_without_off_by_one(self):
        self.assertEqual(rotate_code('(28,19)(4,8)'),'(160,229)(4,8)')
        self.assertEqual(rotate_code('(0,0)(192,256)'),'(0,0)(192,256)')
        self.assertEqual(rotate_code(''),'')
        with self.assertRaises(AssertionError):rotate_code('(0,255)(1,2)')

    def test_all_frames_and_all_non_geometry_parameters_match_water_run_tag(self):
        count=0
        for name in ('main.elab','PTseq_16_A.elab'):
            old=baseline(name).decode();new=layout_coordinate_script(PROJECT,name)
            self.assertEqual(rotate_script(old),new)
            audit=verify_scripts(old,new);count+=audit['frames']
        report=json.loads((PROJECT/REPORT).read_text())
        self.assertEqual(count,sum(s['frames'] for s in report['scripts']))
        self.assertGreater(count,18000)
        with self.assertRaises(AssertionError):rotate_script((PROJECT/'PTseq_16_A.elab').read_text())

    def test_entire_layout_rotates_without_reordering_tec_magnet_or_holes(self):
        old=baseline(BASE_CHIP+'.csv');new=(PROJECT/(ROTATED_CHIP+'.csv')).read_bytes()
        self.assertEqual(rotate_csv(old),new)
        audit=verify_csv(old,new)
        self.assertEqual(audit['feature_counts'],dict(Tec=6,Magnet=96,ReagentWell=216,Rec=96,WarnLine=2))
        self.assertEqual(audit['tec_regions_row_identity_preserved'][0]['after'],['228','20','18','149'])
        a=list(csv.DictReader(io.StringIO(old.decode())));b=list(csv.DictReader(io.StringIO(rotate_csv(new).decode())))
        for source,target in zip(a,b):
            self.assertEqual(source['Type'],target['Type'])
            for key in ('StartX','StartY','Width','Height'):self.assertEqual(D(source[key]),D(target[key]))

    def test_heater_and_external_magnet_commands_do_not_change_identity_or_timing(self):
        a=E.fromstring(baseline('PTseq_16_A.elab'));b=E.parse(PROJECT/'PTseq_16_A.elab').getroot()
        heater=lambda root:[dict(c.attrib) for c in root.iter('Command') if c.get('Component','').startswith('Heater')]
        self.assertEqual(heater(a),heater(b));self.assertEqual(len(heater(b)),13)
        self.assertEqual({c['Component'] for c in heater(b)},{'Heater1'})
        self.assertFalse(any('Motor' in c.get('Component','') or 'Magnet' in c.get('Component','') for c in b.iter('Command')))
        self.assertEqual(b.get('ActiveTec'),a.get('ActiveTec'))

    def test_all_thermal_hold_masks_stay_inside_the_same_rotated_tec_identity(self):
        with (PROJECT/(ROTATED_CHIP+'.csv')).open() as fh:
            tec=next(r for r in csv.DictReader(fh) if r['Type']=='Tec')
        tx,ty,tw,th=(D(tec[k]) for k in ('StartX','StartY','Width','Height'))
        checked=0
        # Compare in layout coordinates, undoing only an explicitly configured
        # address compensation. This is not a physical hardware-mapping test.
        for flow in E.fromstring(layout_coordinate_script(PROJECT,'PTseq_16_A.elab')):
            if not any(c.get('Component')=='Heater1' for c in flow):continue
            loops=[c for c in flow if c.get('Component')=='HighVoltage' and c.get('Params')=='SCRIPTCYCLE_TRUE']
            self.assertEqual(len(loops),1)
            for frame in loops[0]:
                for y,x,h,w in BOX.findall(frame.get('RelayCode','')):
                    x,y,w,h=map(D,(x,y,w,h))
                    self.assertTrue(tx<=x and x+w<=tx+tw and ty<=y and y+h<=ty+th)
                    checked+=1
        self.assertGreaterEqual(checked,64)

    def test_project_model_flowchart_and_operator_coordinates_are_consistent(self):
        manifest=E.parse(PROJECT/'PTseq_SIRO16_A_10x.elabprj').getroot()
        self.assertEqual(manifest.get('ChipModel'),ROTATED_CHIP)
        for name in ('main.elab.layout','PTseq_16_A.elab.layout'):
            self.assertEqual((PROJECT/name).read_bytes(),baseline(name))
        old=list(csv.reader(io.StringIO(baseline('手工吸液顺序.csv').decode('utf-8-sig'))))
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig',newline='') as fh:new=list(csv.reader(fh))
        self.assertEqual(len(old),len(new))
        for row,actual in zip(old,new):
            expected=[BOX.sub(lambda m:rotate_code(m[0]),field) for field in row]
            if address_rotation(PROJECT)==180:
                expected=[BOX.sub(lambda m:rotate_code(m[0]),field) for field in expected]
            self.assertEqual(expected,actual)

    def test_tag_stays_on_original_commit_and_report_hashes_match(self):
        tagged=subprocess.check_output(['git','rev-parse',TAG+'^{}'],cwd=ROOT,text=True).strip()
        self.assertEqual(tagged,'af98a3858761f6bde4f40ff845cee03b8d4ee9c2')
        report=json.loads((PROJECT/REPORT).read_text())
        for row in report['scripts']:
            self.assertEqual(row['after_sha256'],hashlib.sha256(layout_coordinate_script(PROJECT,row['file']).encode()).hexdigest())
        self.assertEqual((PROJECT/(BASE_CHIP+'.csv')).read_bytes(),baseline(BASE_CHIP+'.csv'))


if __name__=='__main__':unittest.main()
