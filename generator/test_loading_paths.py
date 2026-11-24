"""Regression checks for the seven native loading paths and their immediate boundaries."""
import hashlib
import json
import subprocess
import unittest
import xml.etree.ElementTree as ET

from update_parallel_mix_local import PROJECT


class LoadingPaths(unittest.TestCase):
    def test_native_scope_timing_and_root_cuts(self):
        report=json.loads((PROJECT/'顶部分液原位与错排校验_20261124.json').read_text())
        script='PTseq_16_A.elab'
        baseline=subprocess.check_output(['git','show',report['baseline']+':projects/'+PROJECT.name+'/'+script],cwd=PROJECT)
        before=ET.fromstring(baseline)
        data=(PROJECT/script).read_bytes()
        self.assertEqual(report['script_sha256'],hashlib.sha256(data).hexdigest())
        after=ET.fromstring(data)
        self.assertEqual([x.get('ID') for x in before],[x.get('ID') for x in after])
        changed={int(x.get('ID')) for x,y in zip(before,after) if ET.tostring(x)!=ET.tostring(y)}
        self.assertEqual(changed,{50000,50001,50002,50003,50005,50006,50007})
        saved=0
        for case in report['cases']:
            key=f"Flow[@ID='{case['id']}']"
            old,new=before.find(key),after.find(key)
            self.assertEqual([x.attrib|{'Timeout':''} for x in old],[x.attrib|{'Timeout':''} for x in new])
            self.assertEqual(old[-1][-1].get('RelayCode'),new[-1][-1].get('RelayCode'))
            delta=int(old.get('Timeout'))-int(new.get('Timeout'))
            self.assertEqual(delta,case['before']['pair_seconds']-case['after']['pair_seconds'])
            self.assertGreater(delta,0)
            saved+=delta
            for cut in case['after']['cuts']:
                if '_' not in cut['parent']:
                    self.assertEqual((cut['x_steps'],cut['y_steps']),(0,0))
            self.assertLess(case['all_ready_frame'],case['first_merge_frame'])
        self.assertEqual(saved,report['saved_seconds'])
        total=sum(int(x.get('Timeout'))*int(x.get('Repeats')) for x in after)
        self.assertEqual(total,int(ET.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')))
