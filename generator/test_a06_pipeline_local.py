"""Current rectangle transport: approved waste commands and their boundaries."""
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as E
from waste_recovery import boxes
from update_waste_timing_local import TARGETS,REPORT
from audit_binary_frames import audit

ROOT=Path(__file__).resolve().parents[1]
SOURCE='projects/PTseq_SIRO16_A_10x/PTseq_16_A.elab'


class WasteTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old=E.fromstring(subprocess.check_output(['git','show','4f9bcef:'+SOURCE],cwd=ROOT,text=True))
        cls.new=E.parse(ROOT/SOURCE).getroot()
        cls.proof=json.loads((ROOT/SOURCE).with_name(REPORT).read_text())
        cls.oldmap={f.get('Name').split()[0]:f for f in cls.old}
        cls.newmap={f.get('Name').split()[0]:f for f in cls.new}

    def test_only_named_waste_commands_change(self):
        for a,b in zip(self.old,self.new):
            if a.get('Name').split()[0] not in TARGETS:self.assertEqual(E.tostring(a),E.tostring(b))
        self.assertEqual(E.tostring(self.oldmap['A06'][0]),E.tostring(self.newmap['A06'][0]))
        for label in TARGETS:
            a,b=self.oldmap[label][-1],self.newmap[label][-1]
            self.assertEqual({k:v for k,v in a.attrib.items() if k!='Timeout'},
                             {k:v for k,v in b.attrib.items() if k!='Timeout'})

    def test_original_tail_verbatim_except_row_numbers(self):
        for label in TARGETS:
            for a,b in zip(list(self.oldmap[label][-1])[-23:],list(self.newmap[label][-1])[-23:]):
                self.assertEqual({k:v for k,v in a.attrib.items() if k!='ItemID'},
                                 {k:v for k,v in b.attrib.items() if k!='ItemID'})

    def test_nominal_time_and_explicit_revert_cost(self):
        cases={c['section']:c for c in self.proof['cases']}
        self.assertEqual(cases['A06']['after_seconds'],869)
        self.assertEqual(cases['A06']['before_seconds'],796)
        self.assertEqual(897-cases['A06']['after_seconds'],28)
        self.assertEqual(self.proof['saved_seconds'],191)
        for label in TARGETS-{'A06'}:self.assertEqual(cases[label]['saved_seconds'],44)
        for label in TARGETS:
            cmd=self.newmap[label][-1]
            self.assertEqual(sum(int(r.get('Duration')) for r in cmd),1000*int(cmd.get('Timeout')))

    def test_area_levels_unchanged_outside_aspiration(self):
        # No new carrier padding; only rectangle positions and timing differ.
        for label in TARGETS:
            def levels(cmd):
                return {sum(w*h for x,y,w,h in boxes(r.get('RelayCode'))) for r in list(cmd)[:-23]}
            self.assertEqual(levels(self.oldmap[label][-1]),levels(self.newmap[label][-1]),label)

    def test_energized_frames_bounds_and_other_column_exclusion(self):
        for label in TARGETS:
            for r in self.newmap[label][-1]:
                self.assertGreater(float(r.get('Amp')),0)
                rs=boxes(r.get('RelayCode'));self.assertTrue(rs)
                for x,y,w,h in rs:
                    self.assertTrue(0<=x<x+w<=256 and 0<=y<y+h<=192)
                    self.assertFalse(y<172 and x<68 and x+w>50)

    def test_a06_residents_never_lose_power(self):
        f=self.newmap['A06'];res=[r for r in boxes(f[0][-1].get('RelayCode')) if r[2:]==(3,4)]
        self.assertEqual(len(res),16)
        for r in f[-1]:self.assertTrue(set(res)<=set(boxes(r.get('RelayCode'))))

    def test_aspiration_csv_unchanged_except_row_count(self):
        path=(ROOT/SOURCE).with_name('手工吸液顺序.csv')
        before=subprocess.check_output(['git','show','4f9bcef:'+str(path.relative_to(ROOT))],cwd=ROOT).decode('utf-8-sig')
        a=list(csv.DictReader(io.StringIO(before)))
        b=list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
        self.assertEqual(len(a),len(b))
        for old,new in zip(a,b):
            if old['吸液流程组名称'].split()[0] in TARGETS:
                self.assertEqual({k:v for k,v in old.items() if k!='末尾帧号'},
                                 {k:v for k,v in new.items() if k!='末尾帧号'})
            else:self.assertEqual(old,new)

    def test_binary_split_audit_with_immediate_neighbors(self):
        fs=list(self.new);indices={i for i,f in enumerate(fs) if f.get('Name').split()[0] in TARGETS}
        included={j for i in indices for j in (i-1,i,i+1) if 0<=j<len(fs)}
        context=E.Element('Script')
        for i in sorted(included):context.append(copy.deepcopy(fs[i]))
        with tempfile.TemporaryDirectory(prefix='tft-waste-local-') as temp:
            path=Path(temp)/'context.elab';E.ElementTree(context).write(path,encoding='utf-8')
            result=audit(path)
        self.assertFalse(result['non_equal_binary_transitions'],result)

    def test_hash_and_main_timeout(self):
        self.assertEqual(hashlib.sha256((ROOT/SOURCE).read_bytes()).hexdigest(),self.proof['source_sha256'])
        total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in self.new)
        main=E.parse((ROOT/SOURCE).with_name('main.elab')).getroot()
        self.assertEqual(int(main[0].get('Timeout')),total)


if __name__=='__main__':unittest.main()
