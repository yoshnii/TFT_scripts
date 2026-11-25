"""Read the published files through the project entry point and replay the changed timelines."""
import csv
import io
import unittest
from xml.etree import ElementTree as ET

from six_column_schedule import frames, in_well, native, seconds
from update_six_column_local import FEEDS, PROJECT, SCRIPT, baseline, retime


def replay(flow):
    if not flow.get('SyncEndingCmdList'):
        return native(flow)
    start,cycle,timer,*suffix=flow
    rounds,remainder=divmod(seconds(timer),seconds(cycle))
    assert remainder==0 and timer.get('PreTask')==cycle.get('ID')+',SS'
    if suffix:
        assert len(suffix)==1 and suffix[0].get('PreTask')==timer.get('ID')+',FS'
    result=frames(start)+frames(cycle)*rounds+[s for cmd in suffix for s in frames(cmd)]
    assert len(result)==seconds(flow)
    return result


class PublishedSchedule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest=ET.parse(PROJECT/(PROJECT.name+'.elabprj')).getroot()
        assert 'main' in {e.get('include') for e in manifest.iter('file')}
        entry=ET.parse(PROJECT/'main.elab').getroot()[0]
        assert entry.get('Include')==SCRIPT
        cls.before=ET.fromstring(baseline(SCRIPT))
        cls.after=ET.parse(PROJECT/entry.get('Include')).getroot()
        assert retime(cls.after)==seconds(entry)
        cls.old={f.get('Name').split()[0]:f for f in cls.before}
        cls.new={f.get('Name').split()[0]:f for f in cls.after}

    def test_full_program_and_layout(self):
        self.assertEqual(list(self.old),list(self.new))
        self.assertEqual(len(self.old),61)
        self.assertEqual((retime(self.before),retime(self.after)),(34750,33982))
        touched=set(' '.join(FEEDS).split())|{'R05','R06','C05','C06'}
        for k in self.old.keys()-touched:
            self.assertEqual(ET.tostring(self.old[k]),ET.tostring(self.new[k]),k)
        layout=ET.parse(PROJECT/(SCRIPT+'.layout')).getroot()
        ids=[]
        by_id={f.get('ID'):f for f in layout.findall('Flow')}
        self.assertEqual(set(by_id),{f.get('ID') for f in self.after})
        for f in self.after:
            l=by_id[f.get('ID')]
            for a,b in zip([f,*f],[l,*l],strict=True):
                self.assertEqual((a.get('ID'),a.get('Name')),(b.get('ID'),b.get('Name')))
                ids.append(a.get('ID'))
        self.assertEqual(len(ids),len(set(ids)))

    def test_feed_and_resident_traces(self):
        for names in FEEDS:
            start,work,bridge,delivery=names.split()
            old=[*replay(self.old[bridge]),*replay(self.old[delivery])]
            new=[*replay(self.new[bridge]),*replay(self.new[delivery])]
            host=replay(self.old[work])
            actual=replay(self.new[work])
            n=len(old)-len(new)
            gate=frames(self.new[start][-1])[0]
            initial=frozenset(r for r in gate[1] if not in_well(r))
            self.assertEqual(self.old[bridge][-1].get('Name'),self.new[start][-1].get('Name'))
            self.assertEqual(len(initial),2)
            # Compare each of the original work seconds, including all ten cycles,
            # then recover the reagent-only stream from the written program.
            moved=[]
            for i,((s,rs),(t,combined)) in enumerate(zip(host,actual,strict=True)):
                self.assertEqual(s,t)
                self.assertTrue(rs<=combined)
                incoming=combined-rs
                if i<len(host)-n:self.assertEqual(incoming,initial)
                else:moved.append(incoming)
            for i,incoming in enumerate(moved):
                expected=frozenset(r for r in old[i][1] if not in_well(r)) or initial
                self.assertEqual(incoming,expected)
            for i,(s,rects) in enumerate(new):
                later=old[i+n]
                if (s,rects)==later:continue
                original_hosts=frozenset(r for r in old[i][1] if in_well(r))
                self.assertEqual(s,later[0])
                self.assertEqual(rects,original_hosts|{r for r in later[1] if not in_well(r)})
            self.assertEqual(new[-1],old[-1])

    def test_manual_instructions(self):
        for filename,load in [('手工加液顺序.csv',True),('手工吸液顺序.csv',False)]:
            before=list(csv.reader(io.StringIO(baseline(filename).decode('utf-8-sig'))))[1:]
            after=list(csv.reader(io.StringIO((PROJECT/filename).read_text(encoding='utf-8-sig'))))[1:]
            index={f.get('ID'):f for f in self.after}
            for old,new in zip(before,after,strict=True):
                self.assertEqual(old[4:10] if load else old[5:8],new[4:10] if load else new[5:8])
                flow=index[new[1]]
                self.assertEqual(flow[-1].get('ID'),new[3])
                if not load:self.assertEqual(int(new[4]),len(flow[-1]))


if __name__=='__main__':
    unittest.main()
