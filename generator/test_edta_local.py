"""Release checks for the approved EDTA dose, bead ratio and local exception."""
import csv, hashlib, json, subprocess, unittest
from pathlib import Path
import xml.etree.ElementTree as E

import geom
from scene import Scene, Drop
from transport_spacing import required, local_cut_gap
from waste_recovery import boxes
from update_edta_local import IDS, REPORT

ROOT=Path(__file__).resolve().parent.parent
FOLDER=ROOT/'projects/PTseq_SIRO16_A_10x'
BASE='c6ef71d2a385d3fed7bc1650822e7168f7aba04e'


class EdtaReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=E.parse(FOLDER/'PTseq_16_A.elab').getroot()
        cls.flows={int(f.get('ID')):f for f in cls.root}
        cls.proof=json.loads((FOLDER/REPORT).read_text())
        cls.baseline=E.fromstring(subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT))

    def test_scope_and_original_blue_coordinates(self):
        for f in self.baseline:
            if int(f.get('ID')) not in IDS:self.assertEqual(E.tostring(f),E.tostring(self.flows[int(f.get('ID'))]))
        self.assertEqual(self.root.get('ChipsetType'),'ChipArea_192_256_96_SIRO')
        for name in ('ChipArea_192_256_96_SIRO.csv','PTseq_SIRO16_A_10x.elabprj'):
            self.assertEqual(subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT),(FOLDER/name).read_bytes())

    def test_edta_never_exports_point_two_drop(self):
        relays=list(self.flows[50006].iter('RelayItem'))
        # Hosts are 80/88 cells and all reagent boxes are at least eight cells.
        self.assertTrue(relays)
        self.assertTrue(all(w*h>=8 for r in relays for x,y,w,h in boxes(r.get('RelayCode'))))
        leaves=boxes(relays[-1].get('RelayCode'))
        self.assertEqual(len(leaves),16)
        self.assertTrue(all(w*h==88 for x,y,w,h in leaves))
        p=self.proof
        self.assertEqual((p['edta_ul_per_lane'],p['edta_leaf_ul']),(.4,.4))
        self.assertEqual((p['scene']['equal_x_splits'],p['waste_leaf_ul']),(157,.77))

    def test_loading_and_aspiration_amounts(self):
        with (FOLDER/'手工加液顺序.csv').open(encoding='utf-8-sig') as fh:lr={int(r['加液流程组编号']):r for r in csv.DictReader(fh)}
        self.assertAlmostEqual(float(lr[3041]['本次µL（S/B孔为每道）'])/16,.4)
        self.assertAlmostEqual(float(lr[4000]['本次µL（S/B孔为每道）'])/16/4.4,.4)
        with (FOLDER/'手工吸液顺序.csv').open(encoding='utf-8-sig') as fh:ar={int(r['吸液流程组编号']):r for r in csv.DictReader(fh)}
        self.assertAlmostEqual(sum(float(ar[f]['本次总液体µL（16道合计；排珠载液不计文库）']) for f in (4013,51002)),6.16*16)
        for fid in (4013,51002):
            self.assertEqual(ar[fid]['待吸液电极'],'')
            end=list(self.flows[fid].iter('RelayItem'))[-1]
            self.assertTrue(all(y<172 for x,y,w,h in boxes(end.get('RelayCode',''))))

    def test_local_two_cell_gap_does_not_escape_scope(self):
        s=Scene();s.strict_transport=True;s.c05_cut_gap=2
        a=Drop('LIQA1_0',(10,28,13,5),3.08)
        b=Drop('LIQA1_1',(25,28,7,5),1.54)
        self.assertEqual(required(s,a,a.rect,b,b.rect),2)
        self.assertIsNone(local_cut_gap(s,a,a.rect,b,(25,37,7,5)))
        self.assertIsNone(local_cut_gap(s,a,a.rect,Drop('LG16',(25,28,7,5),1.54),b.rect))
        a.native_packet=b.native_packet=True
        a.batch_round=b.batch_round=0;s.batch_channel_x=46
        self.assertEqual(required(s,a,(46,50,4,4),b,(46,60,4,4)),6)
        s.c05_cut_gap=None
        self.assertEqual(required(s,a,a.rect,b,b.rect),3)

    def test_capture_carrier_bounds_and_hole_clearance(self):
        capture=self.flows[4009].find("Command[@ID='4012']")
        self.assertEqual(capture.get('Timeout'),'120')
        for x,y,w,h in boxes(capture[0].get('RelayCode')):
            row=(y-28)//9+1;x0,y0=geom.physical_zone(1,row)
            self.assertTrue(x0<=x and x+w<=x0+36 and y0<=y and y+h<=y0+6)
            self.assertEqual((w,h),(32,4));self.assertTrue(geom.reaction_holes_ok((x,y,w,h),1))
        self.assertEqual(self.proof['script_sha256'],hashlib.sha256((FOLDER/'PTseq_16_A.elab').read_bytes()).hexdigest())

    def test_existing_timers_and_cycle_end_groups(self):
        old={int(f.get('ID')):f for f in self.baseline}
        for fid in (3045,4004):
            self.assertEqual(self.flows[fid].get('SyncEndingCmdList'),old[fid].get('SyncEndingCmdList'))
            self.assertEqual(self.flows[fid].find("Command[@Component='Delay']").attrib,old[fid].find("Command[@Component='Delay']").attrib)
            loops=[c for c in self.flows[fid] if c.get('Params')=='SCRIPTCYCLE_TRUE']
            self.assertEqual(len(loops),1)
            self.assertEqual(loops[0][0].get('RelayCode'),loops[0][-1].get('RelayCode'))


if __name__=='__main__':unittest.main()
