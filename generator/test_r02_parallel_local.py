"""R02 paired binary preparation; no other native section changes."""
import hashlib,json,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as E

from scene import Scene
from update_r02_parallel_local import build,REPORT

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'


class R02ParallelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        original=Scene._record
        def record(scene,*args,**kwargs):
            original(scene,*args,**kwargs)
            for d in scene.drops.values():
                if getattr(d,'native_packet',False) and d.vol==.625:
                    assert d.rect[2:]==(3,5) and len(d.target_cells())==15
        with patch.object(Scene,'_record',record):cls.files,cls.proof=build(PROJECT)

    def test_two_families_are_split_simultaneously(self):
        self.assertEqual(self.proof['parallel_mothers_by_level'],[2,4,8,16]*2)
        self.assertEqual(self.proof['exported_frame_audit']['symmetric_horizontal_splits'],60)
        self.assertEqual(self.proof['leaf_count'],64)
        self.assertEqual(self.proof['leaf_shape'],[3,5])
        self.assertEqual(self.proof['actual_leaf_ul'],.625)
        self.assertTrue(self.proof['synchronous_reaction_merge'])

    def test_published_r02_reproduces_and_neighbors_are_unchanged(self):
        generated=E.fromstring(self.files[PROJECT/'PTseq_16_A.elab'])
        current=E.parse(PROJECT/'PTseq_16_A.elab').getroot()
        for a,b in zip(current,generated):self.assertEqual(E.tostring(a),E.tostring(b))
        old=E.fromstring(subprocess.check_output(['git','show','7d84e73:projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'],cwd=ROOT))
        for fid in (2000,2004):
            self.assertEqual(E.tostring(current.find(f"Flow[@ID='{fid}']")),E.tostring(old.find(f"Flow[@ID='{fid}']")))

    def test_saved_time_and_script_hash(self):
        report=json.loads((PROJECT/REPORT).read_text())
        self.assertEqual(report['saved_seconds'],526)
        self.assertEqual(report['after_seconds'],self.proof['after_seconds'])
        self.assertEqual(report['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
        for name in ('A02整列并行局部校验.json','双侧同步分液局部校验_20261022.json'):
            self.assertEqual(json.loads((PROJECT/name).read_text())['script_sha256'],report['script_sha256'])


if __name__=='__main__':unittest.main()
