"""Focused A02 scheduling and native-boundary regressions."""
import csv
import hashlib
import json
import subprocess
import unittest
from pathlib import Path
import xml.etree.ElementTree as E

from update_a02_batch_local import build, REPORT

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
BASE='7d84e73'


class A02BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before=subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
        cls.new,cls.total,cls.report,cls.frame=build(cls.before)
        cls.root=E.fromstring(cls.new)

    def test_two_batches_stream_into_vacated_channel_space(self):
        r=self.report
        self.assertEqual(r['batch_drop_counts'],[16,16])
        self.assertEqual(r['packet_areas_cells'],[16,8])
        self.assertGreater(r['second_batch_packets_entering_before_first_clears'],0)
        self.assertEqual(r['packet_shapes'],[[4,4],[2,4]])
        self.assertEqual(r['packet_volumes_ul'],[.8,.4])
        self.assertEqual(r['transport_reshapes'],0)
        self.assertTrue(r['right_half_enters_channel_before_left_half_split'])
        self.assertTrue(r['symmetric_left_right_splits'])
        self.assertEqual(r['scene']['equal_x_splits'],32)
        self.assertEqual(r['waste_ul_per_port'],{'F3':9.6,'F1':9.6})
        self.assertGreater(r['saved_seconds'],0)
        self.assertFalse(r['exported_frame_audit']['non_equal_binary_transitions'])

    def test_only_a02_changes_and_recovery_tail_is_preserved(self):
        before=E.fromstring(self.before)
        self.assertEqual(len(before),len(self.root))
        for a,b in zip(before,self.root):
            if a.get('ID')!='70000':self.assertEqual(E.tostring(a),E.tostring(b))
        self.assertGreaterEqual(self.report['unchanged_recovery_suffix_frames'],21)
        published=E.parse(PROJECT/'PTseq_16_A.elab').getroot().find("Flow[@ID='70000']")
        self.assertEqual(E.tostring(published).strip(),E.tostring(self.root.find("Flow[@ID='70000']")).strip())

    def test_operator_metadata_and_total_timing_match_new_flow(self):
        stored=json.loads((PROJECT/REPORT).read_text())
        self.assertEqual(stored['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
        for key in ('after_frames','after_seconds','batch_drop_counts',
                    'second_batch_packets_entering_before_first_clears',
                    'waste_ul_per_port','exported_frame_audit'):
            self.assertEqual(stored[key],self.report[key])
        self.assertEqual(stored['saved_seconds'],stored['before_seconds']-stored['after_seconds'])
        current=E.parse(PROJECT/'PTseq_16_A.elab').getroot()
        total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in current)
        self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),total)
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig',newline='') as fh:
            reader=csv.DictReader(fh);headers=reader.fieldnames
            row=next(r for r in reader if r[headers[1]]=='70000')
        self.assertEqual(int(row[headers[4]]),self.frame)
        self.assertEqual(float(row[headers[6]]),19.2)


if __name__=='__main__':unittest.main()
