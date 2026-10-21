"""Focused A02 scheduling and native-boundary regressions."""
import csv
import json
import subprocess
import unittest
from pathlib import Path
import xml.etree.ElementTree as E

from update_a02_batch_local import build, REPORT

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
BASE='ed5cb66'


class A02BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before=subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT).decode()
        cls.new,cls.total,cls.report,cls.frame=build(cls.before)
        cls.root=E.fromstring(cls.new)

    def test_two_batches_stream_into_vacated_channel_space(self):
        r=self.report
        self.assertEqual(r['batch_sizes'],[16,32])
        self.assertGreater(r['second_batch_packets_entering_before_first_clears'],0)
        self.assertEqual(r['packet_shape'],[2,4])
        self.assertEqual(r['scene']['equal_x_splits'],48)
        self.assertEqual(r['waste_ul_per_port'],{'F3':9.6,'F1':9.6})
        self.assertGreater(r['saved_seconds'],0)
        self.assertFalse(r['exported_frame_audit']['non_equal_binary_transitions'])

    def test_only_a02_changes_and_recovery_tail_is_preserved(self):
        before=E.fromstring(self.before)
        self.assertEqual(len(before),len(self.root))
        for a,b in zip(before,self.root):
            if a.get('ID')!='70000':self.assertEqual(E.tostring(a),E.tostring(b))
        self.assertGreaterEqual(self.report['unchanged_recovery_suffix_frames'],21)
        self.assertEqual((PROJECT/'PTseq_16_A.elab').read_text(),self.new)

    def test_operator_metadata_and_total_timing_match_new_flow(self):
        stored=json.loads((PROJECT/REPORT).read_text())
        for key in ('script_sha256','after_frames','after_seconds','batch_sizes',
                    'second_batch_packets_entering_before_first_clears',
                    'waste_ul_per_port','exported_frame_audit'):
            self.assertEqual(stored[key],self.report[key])
        self.assertEqual(stored['saved_seconds'],stored['before_seconds']-stored['after_seconds'])
        self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),self.total)
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig',newline='') as fh:
            reader=csv.DictReader(fh);headers=reader.fieldnames
            row=next(r for r in reader if r[headers[1]]=='70000')
        self.assertEqual(int(row[headers[4]]),self.frame)
        self.assertEqual(float(row[headers[6]]),19.2)


if __name__=='__main__':unittest.main()
