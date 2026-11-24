"""Read back the experimental electrode commands, independent of simulator receipts."""
import csv
import json
import unittest
import xml.etree.ElementTree as ET

from bottom_drain_timing import EXPECTED, MAIN, TEST, SCRIPT
from geom import GRID_H
from waste_grid import TRANSPORT_Y
from waste_recovery import boxes


class BottomDrainExport(unittest.TestCase):
    def test_native_last_row_arrivals_and_handoff(self):
        base=ET.parse(MAIN/SCRIPT).getroot()
        test=ET.parse(TEST/SCRIPT).getroot()
        self.assertEqual([f.get('ID') for f in base],[f.get('ID') for f in test])
        for before,after in zip(base,test):
            fid=int(after.get('ID'))
            if fid not in EXPECTED:
                if fid==51001:
                    self.assertEqual([ET.tostring(c) for c in before],[ET.tostring(c) for c in after])
                else:self.assertEqual(ET.tostring(before),ET.tostring(after))
                continue
            count=0
            on_edge=False
            for relay in after[-1]:
                rects=boxes(relay.get('RelayCode',''))
                bottom=[r for r in rects if r[1]>=TRANSPORT_Y]
                self.assertTrue(all(x==46 and (w,h)==((3,4) if fid==50004 else (4,4))
                                    for x,y,w,h in bottom))
                at_edge=any(y+h==GRID_H for x,y,w,h in bottom)
                count+=int(at_edge and not on_edge)
                on_edge=at_edge
            self.assertFalse(on_edge)
            self.assertEqual(count,EXPECTED[fid][0])
            self.assertEqual(set(before[-1][-1].get('RelayCode').split(';')),
                             set(after[-1][-1].get('RelayCode').split(';')))

    def test_project_timing_and_product_recovery(self):
        report=json.loads((TEST/'底边排废计时对照.json').read_text())
        main=ET.parse(TEST/'main.elab').getroot()
        flows=ET.parse(TEST/main[0].get('Include')).getroot()
        duration=sum(int(f.get('Timeout'))*int(f.get('Repeats')) for f in flows)
        self.assertEqual(int(main[0].get('Timeout')),duration)
        self.assertEqual(report['main_seconds']-duration,report['saved_seconds'])
        with (TEST/'手工吸液顺序.csv').open(encoding='utf-8-sig',newline='') as fp:
            recovery=list(csv.reader(fp))[1:]
        self.assertEqual([r[2].split()[0] for r in recovery],['R11','R16','C09','C12'])
