"""Cleanup-only revision against the previously released native program."""
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
from audit_binary_frames import audit
from waste_recovery import boxes
from update_cleanup_local import TARGETS,REPORT

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'projects/PTseq_SIRO16_A_10x'


class CleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def old(name):return subprocess.check_output(['git','show','a6d6b72:projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT)
        cls.old_bytes=staticmethod(old)
        cls.old=E.fromstring(old('PTseq_16_A.elab'));cls.new=E.parse(FOLDER/'PTseq_16_A.elab').getroot()
        cls.oldmap={f.get('Name').split()[0]:f for f in cls.old};cls.newmap={f.get('Name').split()[0]:f for f in cls.new}
        cls.report=json.loads((FOLDER/REPORT).read_text())

    def test_scope_ids_and_only_four_action_commands_change(self):
        allowed={'2065','2069','4045','4049'}
        self.assertEqual([f.get('ID') for f in self.old],[f.get('ID') for f in self.new])
        for a,b in zip(self.old,self.new):
            if a.get('Name').split()[0] not in TARGETS:self.assertEqual(E.tostring(a),E.tostring(b))
            else:
                self.assertEqual([c.get('ID') for c in a],[c.get('ID') for c in b])
                for old,new in zip(a,b):
                    if old.get('ID') not in allowed:self.assertEqual(E.tostring(old),E.tostring(new))

    def test_names_are_cleanup_not_elution(self):
        for label in TARGETS:
            name=self.newmap[label].get('Name');self.assertIn('清理',name);self.assertNotIn('洗脱',name)

    def test_carrier_channel_wash_is_one_grid_constant_rectangle(self):
        for label in ('R18','C14'):
            f=self.newmap[label];cmd=f[1];prior=boxes(f[0][-1].get('RelayCode'));xs=[]
            for row in cmd:
                now=boxes(row.get('RelayCode'));self.assertEqual(len(now),16)
                self.assertTrue(all(rc[2:]==(12,5) for rc in now))
                self.assertEqual(sum(w*h for x,y,w,h in now),960)
                for a,b in zip(prior,now):
                    self.assertEqual(a[1:],b[1:]);self.assertLessEqual(abs(a[0]-b[0]),1)
                prior=now;xs.append(now[0][0])
            self.assertEqual((min(xs),max(xs),xs[-1]),(6,38,10))
            self.assertEqual(cmd[-1].get('RelayCode'),self.oldmap[label][1][-1].get('RelayCode'))
            self.assertEqual(int(cmd.get('Timeout'))-int(self.oldmap[label][1].get('Timeout')),64)

    def test_last_real_packet_revisits_outlet_and_well(self):
        for label in ('R19','C15'):
            cmd=self.newmap[label][-1];positions=[]
            for row in cmd:
                rs=boxes(row.get('RelayCode'))
                packet=next((rc for rc in rs if rc[1]==28 and rc[2:]==(4,4)),None)
                if packet:positions.append(packet[0])
            # The final outbound sweep occurs only after all other lane fluid
            # has gone to the bottom band; full rectangular packet remains 16 cells.
            k=max(i for i,x in enumerate(positions) if x==6)
            self.assertIn(46,positions[:k]);self.assertIn(46,positions[k:])
            self.assertTrue(all(abs(b-a)<=1 for a,b in zip(positions[k:],positions[k+1:])))
            self.assertEqual(self.report['cases'][2 if label=='R19' else 3]['events'][0]['extra_electrodes'],0)

    def test_waste_area_levels_and_first_cut_prefix_unchanged(self):
        for label in ('R19','C15'):
            a,b=self.oldmap[label][-1],self.newmap[label][-1]
            def sig(row):return {k:v for k,v in row.attrib.items() if k!='ItemID'}
            prefix=0
            for x,y in zip(a,b):
                if sig(x)!=sig(y):break
                prefix+=1
            self.assertGreater(prefix,900)
            def levels(cmd):return {sum(w*h for x,y,w,h in boxes(r.get('RelayCode'))) for r in list(cmd)[:-23]}
            self.assertEqual(levels(a),levels(b))
            self.assertEqual([sig(r) for r in list(a)[-23:]],[sig(r) for r in list(b)[-23:]])

    def test_loop_timers_and_magnets_unchanged(self):
        for label in ('R18','C14'):
            a,b=self.oldmap[label],self.newmap[label]
            self.assertEqual(a.get('SyncEndingCmdList'),b.get('SyncEndingCmdList'))
            self.assertEqual(E.tostring(a[-2]),E.tostring(b[-2]))
            self.assertEqual(E.tostring(a[-1]),E.tostring(b[-1]))

    def test_layout_names_ids_and_coordinates(self):
        old=E.fromstring(self.old_bytes('PTseq_16_A.elab.layout'));new=E.parse(FOLDER/'PTseq_16_A.elab.layout').getroot()
        for a,b in zip(old.iter(),new.iter()):
            self.assertEqual(a.tag,b.tag)
            self.assertEqual({k:v for k,v in a.attrib.items() if k!='Name'},
                             {k:v for k,v in b.attrib.items() if k!='Name'})
        names={node.get('ID'):node.get('Name') for node in self.new.iter() if node.tag in ('Flow','Command')}
        for f in new:
            if f.tag=='Flow' and f.get('Name').split()[0] in TARGETS:
                for node in (f,*list(f)):self.assertEqual(node.get('Name'),names[node.get('ID')])

    def test_csv_keeps_volumes_ports_and_terminal_masks(self):
        old=list(csv.DictReader(io.StringIO(self.old_bytes('手工吸液顺序.csv').decode('utf-8-sig'))))
        with (FOLDER/'手工吸液顺序.csv').open(encoding='utf-8-sig',newline='') as f:new=list(csv.DictReader(f))
        for a,b in zip(old,new):
            self.assertEqual({k:v for k,v in a.items() if k not in ('吸液流程组名称','下一个流程组名称','末尾帧号')},
                             {k:v for k,v in b.items() if k not in ('吸液流程组名称','下一个流程组名称','末尾帧号')})

    def test_binary_splits_with_immediate_neighbors(self):
        fs=list(self.new);indices={i for i,f in enumerate(fs) if f.get('Name').split()[0] in TARGETS}
        selected={j for i in indices for j in (i-1,i,i+1) if 0<=j<len(fs)}
        context=E.Element('Script')
        for i in sorted(selected):context.append(copy.deepcopy(fs[i]))
        with tempfile.TemporaryDirectory(prefix='tft-cleanup-check-') as temp:
            path=Path(temp)/'context.elab';E.ElementTree(context).write(path,encoding='utf-8');result=audit(path)
        self.assertFalse(result['non_equal_binary_transitions'])

    def test_hash_and_scheduling(self):
        self.assertEqual(self.report['source_sha256'],hashlib.sha256((FOLDER/'PTseq_16_A.elab').read_bytes()).hexdigest())
        self.assertEqual(self.report['added_nominal_seconds'],252)
        total=sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in self.new)
        self.assertEqual(int(E.parse(FOLDER/'main.elab').getroot()[0].get('Timeout')),total)
        for label in TARGETS:
            for c in self.newmap[label]:
                if len(c):self.assertEqual(sum(int(r.get('Duration')) for r in c),1000*int(c.get('Timeout')))


if __name__=='__main__':unittest.main()
