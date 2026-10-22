"""Focused R05 scheduling and per-cycle C17 channel coverage regressions."""
import csv,hashlib,json,unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as E

from scene import Scene
from update_r05_c17_local import build,REPORT
from update_transport_cleanup_local import boxes,codes

PROJECT=Path(__file__).resolve().parent.parent/'projects/PTseq_SIRO16_A_10x'


class R05C17Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        original=Scene._record
        def record(scene,*args,**kwargs):
            original(scene,*args,**kwargs)
            for d in scene.drops.values():
                if getattr(d,'native_packet',False) and d.vol==.625:
                    assert d.rect[2:]==(4,4) and len(d.target_cells())==16
        with patch.object(Scene,'_record',record):cls.files,cls.proof=build(PROJECT)
        cls.root=E.parse(PROJECT/'PTseq_16_A.elab').getroot()

    def test_only_requested_sections_regenerate_and_r06_is_identical(self):
        self.assertEqual(self.files[PROJECT/'PTseq_16_A.elab'],(PROJECT/'PTseq_16_A.elab').read_text())
        self.assertTrue(self.proof['r06_unchanged'])
        self.assertTrue(self.proof['other_sections_unchanged'])
        self.assertEqual(self.proof['r05_scene']['equal_x_splits'],112)
        self.assertEqual(self.proof['r05_exported_frame_audit']['symmetric_horizontal_splits'],64)
        self.assertEqual(self.proof['r05_scene']['aspirated_ul'],80.)

    def test_c17_loop_is_closed_and_covers_both_channels_every_iteration(self):
        flow=self.root.find("Flow[@ID='75010']");loop=flow[1]
        self.assertEqual(loop.get('Params'),'SCRIPTCYCLE_TRUE')
        self.assertEqual(codes(loop[0]),codes(loop[-1]))
        positions=[]
        for frame in loop:
            rects=boxes(frame.get('RelayCode'))
            self.assertEqual(len(rects),16)
            self.assertTrue(all(r[2:]==(12,5) for r in rects))
            xs={r[0] for r in rects};self.assertEqual(len(xs),1)
            positions.append(next(iter(xs)))
        self.assertEqual(positions[0],10);self.assertEqual(positions[-1],10)
        self.assertEqual(min(positions),6);self.assertEqual(max(positions),38)
        self.assertTrue(all(abs(a-b)<=1 for a,b in zip(positions,positions[1:])))
        duration=sum(int(r.get('Duration')) for r in loop)
        self.assertEqual(duration,65000)
        self.assertEqual(int(flow[2].get('Timeout'))*1000,2*duration)
        self.assertEqual(flow[2].get('PreTask'),'75012,SS')
        self.assertEqual(flow.get('SyncEndingCmdList'),'75013,75012')

    def test_delivery_timing_and_operator_row_match(self):
        report=json.loads((PROJECT/REPORT).read_text())
        self.assertEqual(report['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
        self.assertEqual([(r['before_seconds'],r['after_seconds'],r['saved_seconds']) for r in report['changes']],
                         [(1319,1309,10),(172,139,33)])
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig',newline='') as fh:
            reader=csv.DictReader(fh);headers=reader.fieldnames
            row=next(r for r in reader if r[headers[1]]=='2013')
        self.assertEqual(int(row[headers[4]]),len(self.root.find("Flow[@ID='2013']")[0]))
        self.assertEqual(float(row[headers[6]]),40.)


if __name__=='__main__':unittest.main()
