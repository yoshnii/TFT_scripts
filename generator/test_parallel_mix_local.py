"""Compare final native files, the generated timer policy and the approved exemptions."""
import hashlib
import json
import unittest
import xml.etree.ElementTree as ET

from update_parallel_mix_local import PROJECT, MIX, REPORT, baseline
from update_transport_cleanup_local import seed

class ParallelMixRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = ET.fromstring(baseline('PTseq_16_A.elab'))
        cls.new = ET.parse(PROJECT/'PTseq_16_A.elab').getroot()
        cls.proof = json.loads((PROJECT/REPORT).read_text())

    def test_ten_complete_rounds_preserve_native_actions_and_sync(self):
        checked=set()
        for a,b in zip(self.old,self.new):
            section=a.get('Name').split()[0]
            if section not in MIX:
                continue
            checked.add(section)
            self.assertEqual(len(a),len(b))
            timers=0
            for c,d in zip(a,b):
                if not c.get('Name').startswith('混匀计时'):
                    self.assertEqual(ET.tostring(c),ET.tostring(d))
                    continue
                timers+=1
                parent,relation=d.get('PreTask').split(',')
                hv=b.find(f"Command[@ID='{parent}']")
                self.assertEqual((relation,hv.get('Params')),('SS','SCRIPTCYCLE_TRUE'))
                self.assertEqual(b.get('SyncEndingCmdList'),d.get('ID')+','+parent)
                self.assertEqual(hv[0].get('RelayCode'),hv[-1].get('RelayCode'))
                duration=sum(int(r.get('Duration')) for r in hv)
                self.assertEqual(int(d.get('Timeout'))*1000,10*duration)
                self.assertEqual(int(b.get('Timeout'))-int(a.get('Timeout')),
                                 int(d.get('Timeout'))-int(c.get('Timeout')))
            self.assertEqual(timers,1)
        self.assertEqual(checked,MIX)

    def test_scope_and_timings(self):
        expected=MIX|set(self.proof['transport'])
        self.assertEqual(len(self.old),len(self.new))
        self.assertEqual([f.get('ID') for f in self.old],[f.get('ID') for f in self.new])
        for a,b in zip(self.old,self.new):
            section=a.get('Name').split()[0]
            self.assertEqual(ET.tostring(a)!=ET.tostring(b),section in expected)
            if section in self.proof['transport']:
                self.assertLess(int(b.get('Timeout')),int(a.get('Timeout')))
                self.assertEqual([c.attrib|{'Timeout':''} for c in a],[c.attrib|{'Timeout':''} for c in b])
        self.assertTrue({'R09','R14','C17','A10'}.isdisjoint(expected))
        self.assertEqual(self.proof['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
        self.assertEqual(int(ET.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),
                         sum(int(f.get('Timeout'))*int(f.get('Repeats')) for f in self.new))

    def test_native_generation_uses_actual_round_length(self):
        initial=self.old.find("Flow[@ID='1006']")[0][0]
        p=seed(initial,[('',2.)])
        p.mix('A','A04 往复混匀',seconds=1800)
        circuit,timer=p.segs['A'][-1].cmds[-2:]
        self.assertEqual(timer.timeout,circuit.timeout*10)
        self.assertEqual(timer.timeout,570)

    def test_all_captured_cut_widths_and_collection_checks(self):
        gaps={tuple(c['ids']):c['cut_gaps'] for c in self.proof['waste']}
        self.assertEqual(gaps[2013,51000],[24,16,16,24,28,26,36])
        self.assertEqual(gaps[4013,51002],[12,14,14,22,28,26,36])
        for c in self.proof['waste']+self.proof['top']:
            self.assertTrue(c.get('cut_dwell',c.get('cuts'))['per_child_gap_and_final_holds_passed'])
        for c in self.proof['top']:
            self.assertGreater(c['contact_frame'],c['all_ready_frame'])
