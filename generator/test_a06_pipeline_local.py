"""A06-only native regression, with unchanged immediate neighbors and F tail."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as E
import geom
from waste_recovery import boxes
from update_a06_pipeline_local import build
from audit_binary_frames import audit

ROOT=Path(__file__).resolve().parents[1]
SOURCE='projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'


class A06PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before=subprocess.check_output(['git','show','50687a9:'+SOURCE],cwd=ROOT,text=True)
        cls.after,cls.delta,cls.count,cls.proof=build(cls.before)
        cls.old=E.fromstring(cls.before);cls.new=E.fromstring(cls.after)
        cls.flow=cls.new.find("Flow[@ID='1019']");cls.cmd=cls.flow[-1]

    def test_only_a06_command_1021_changes(self):
        for a,b in zip(self.old,self.new):
            if a.get('ID')!='1019':self.assertEqual(E.tostring(a),E.tostring(b))
        self.assertEqual(E.tostring(self.old.find("Flow[@ID='1019']")[0]),E.tostring(self.flow[0]))

    def test_shipped_native_output_matches_generator(self):
        self.assertEqual((ROOT/SOURCE).read_text(),self.after)

    def test_original_tail_verbatim_except_row_numbers(self):
        old=self.old.find("Flow[@ID='1019']")[-1]
        for a,b in zip(list(old)[-23:],list(self.cmd)[-23:]):
            self.assertEqual({k:v for k,v in a.attrib.items() if k!='ItemID'},
                             {k:v for k,v in b.attrib.items() if k!='ItemID'})

    def test_actual_time_reduction(self):
        self.assertEqual(self.proof['before_seconds'],897)
        self.assertEqual(self.proof['after_seconds'],796)
        self.assertEqual(self.proof['saved_seconds'],101)
        self.assertEqual(sum(int(r.get('Duration')) for r in self.cmd),740000)

    def test_raw_area_bounds_and_other_column_exclusion(self):
        for r in list(self.cmd)[:-23]:
            rs=boxes(r.get('RelayCode'))
            self.assertEqual(sum(w*h for x,y,w,h in rs),704)
            for x,y,w,h in rs:
                self.assertTrue(0<=x<x+w<=256 and 0<=y<y+h<=192)
                self.assertFalse(y<172 and x<68 and x+w>50)
                if y>=172:self.assertIn((w,h),((4,4),(8,8),(16,16)))

    def test_residents_never_lose_power(self):
        resident=boxes(self.flow[0][-1].get('RelayCode'))[::2]
        # Source code order is not relied on: identify the 0.50-uL 3x4 masks.
        resident=[r for r in boxes(self.flow[0][-1].get('RelayCode')) if r[2:]==(3,4)]
        self.assertEqual(len(resident),16)
        for r in self.cmd:self.assertTrue(set(resident)<=set(boxes(r.get('RelayCode'))))

    def test_volume_and_port_counts_unchanged(self):
        self.assertEqual(self.proof['ports'],['F3','F1'])
        self.assertEqual(self.proof['total_waste_ul'],24.)
        for event in self.proof['collection']:
            self.assertEqual(len(event['blocks']),4)
            self.assertEqual(sum(b['volume_ul'] for b in event['blocks']),12.)
            self.assertTrue(all(b['rectangle'][2:]==[8,8] for b in event['blocks']))

    def test_near_port_work_overlaps_far_port_work(self):
        far,near=self.proof['collection']
        self.assertLess(near['blocks'][0]['frame'],far['end_frame'])
        self.assertLess(near['docks'][0]['frame'],far['end_frame'])

    def test_binary_split_audit_including_neighbors(self):
        flows=list(self.new);i=next(i for i,f in enumerate(flows) if f.get('ID')=='1019')
        context=E.Element('Script')
        for f in flows[i-1:i+2]:context.append(copy.deepcopy(f))
        with tempfile.TemporaryDirectory(prefix='tft-a06-check-') as temp:
            path=Path(temp)/'context.elab';E.ElementTree(context).write(path,encoding='utf-8')
            result=audit(path)
        self.assertFalse(result['non_equal_binary_transitions'],result)


if __name__=='__main__':unittest.main()
