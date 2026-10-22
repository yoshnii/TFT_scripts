"""Each half moves one cell in opposite horizontal directions per frame."""
import hashlib
import json
import subprocess
import unittest
from pathlib import Path
import xml.etree.ElementTree as E

import rigid
from native_packets import partition,validate_symmetric_split
from scene import Scene,Drop
from update_symmetric_splits_local import audit_symmetric,TARGETS,REPORT

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'


class SplitPrimitiveTests(unittest.TestCase):
    def test_partition_moves_both_halves_every_frame(self):
        s=Scene();d=s.add(Drop('test',(30,4,8,4),1.6));s.step()
        a,b=partition(s,[d],gap=4)[0]
        self.assertEqual(a.rect,(28,4,4,4));self.assertEqual(b.rect,(36,4,4,4))
        validate_symmetric_split(s.split_events[0])
        self.assertEqual(s.split_events[0]['gaps'],[0,2,4])

    def test_odd_minimum_gap_still_does_not_fix_one_half(self):
        s=Scene();d=s.add(Drop('test',(30,4,8,4),1.6));s.step()
        partition(s,[d],gap=3)
        validate_symmetric_split(s.split_events[0])
        self.assertEqual(s.split_events[0]['gaps'],[0,2,4])

    def test_other_split_primitive_is_symmetric_too(self):
        s=Scene();d=s.add(Drop('test',(30,4,8,4),1.6));s.step()
        finals=rigid.split_x(d,4,4)
        rigid.split_many(s,[(d,finals,(.8,.8),('left','right'))])
        validate_symmetric_split(s.split_events[0])
        self.assertEqual(s.split_events[0]['gaps'],[0,2,4])

    def test_stationary_right_half_is_rejected(self):
        s=Scene();d=s.add(Drop('test',(30,4,8,4),1.6));s.step()
        partition(s,[d],gap=4)
        event=s.split_events[0]
        event['positions'][1][1][0]-=1
        with self.assertRaises(AssertionError):validate_symmetric_split(event)


class PublishedSplitTests(unittest.TestCase):
    def test_every_exported_split_is_equal_and_symmetric(self):
        root=E.parse(PROJECT/'PTseq_16_A.elab').getroot()
        report=json.loads((PROJECT/REPORT).read_text())
        result=audit_symmetric(root,{int(f.get('ID')) for f in root})
        self.assertEqual(result,report['exported_frame_audit'])
        self.assertEqual(result['symmetric_horizontal_splits'],611)
        self.assertEqual(report['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())

    def test_non_split_native_controls_and_layout_remain_unchanged(self):
        baseline=E.fromstring(subprocess.check_output(['git','show','7d84e73:projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT))
        current=E.parse(PROJECT/'PTseq_16_A.elab').getroot()
        self.assertEqual(len(baseline),len(current))
        for a,b in zip(baseline,current):
            if int(a.get('ID'))==75010:
                self.assertEqual(a.get('SyncEndingCmdList'),b.get('SyncEndingCmdList'))
                for ca,cb in zip(a,b):
                    self.assertEqual({k:v for k,v in ca.attrib.items() if k not in ('Timeout','Name')},
                                     {k:v for k,v in cb.attrib.items() if k not in ('Timeout','Name')})
            elif int(a.get('ID')) not in TARGETS:self.assertEqual(E.tostring(a),E.tostring(b))
            else:
                self.assertEqual(a.get('ID'),b.get('ID'));self.assertEqual(a.get('Name'),b.get('Name'))
                for ca,cb in zip(a,b):
                    self.assertEqual({k:v for k,v in ca.attrib.items() if k!='Timeout'},
                                     {k:v for k,v in cb.attrib.items() if k!='Timeout'})
        layout='projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab.layout'
        old=E.fromstring(subprocess.check_output(['git','show','7d84e73:'+layout],cwd=ROOT))
        now=E.parse(ROOT/layout).getroot()
        for root in (old,now):
            for c in root.findall("Flow[@ID='75010']/Command"):c.attrib.pop('Name',None)
        self.assertEqual(E.tostring(old),E.tostring(now))


if __name__=='__main__':unittest.main()
