"""Replay the two physical handoffs in the saved project against its previous release."""
import csv
import io
import json
import subprocess
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from loading_sections import GROUPS
from overlap_waste_inlet import ordinary, mix_trace
from update_waste_inlet_local import ROOT, PROJECTS, SCRIPT, retime


def intersect(r,x,y):
    rx,ry,w,h=r
    return rx<x+36 and x<rx+w and ry<y+6 and y<ry+h


class TestHandoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.col=4 if (ROOT/'projects'/PROJECTS[4][0]).is_dir() else 6
        name,ref=PROJECTS[cls.col]
        cls.folder=ROOT/'projects'/name
        cls.left=22 if cls.col==4 else 10
        def load(filename):
            return subprocess.check_output(['git','show',f'{ref}:projects/{name}/{filename}'],cwd=ROOT)
        cls.previous=load
        before=ET.fromstring(load(SCRIPT))
        manifest=ET.parse(cls.folder/(name+'.elabprj')).getroot()
        main=ET.parse(cls.folder/'main.elab').getroot()
        assert main.get('ChipsetType')==manifest.get('ChipModel')
        after=ET.parse(cls.folder/main[0].get('Include')).getroot()
        assert retime(after)==int(main[0].get('Timeout'))
        cls.old,cls.new=({f.get('Name').split()[0]:f for f in root} for root in (before,after))

    def test_waste_and_reaction_timing(self):
        for stage,retained in [('A06',1),('B07',2)]:
            before=ordinary(self.old[stage])
            after=ordinary(list(self.new[stage])[1:]) if stage=="B07" else ordinary(self.new[stage])
            def counts(rects):
                return [sum(intersect(r,self.left,27+i*9) for r in rects) for i in range(16)]
            if stage=='A06' and self.col==4:
                from aliquot_outlet import fifo_tail
                self.assertEqual(fifo_tail(before),fifo_tail(after))
                window=after+ordinary(self.new['A08'])
                def present(rects,shape):
                    return any(r[2:]==shape and any(intersect(r,self.left,27+i*9) for i in range(16)) for r in rects)
                clear=1+max(i for i,(_,rs) in enumerate(window) if present(rs,(4,4)))
                first=next(i for i,(_,rs) in enumerate(window) if present(rs,(4,5)))
                self.assertEqual((clear,first,len(window)),(178,234,596))
                old_final=ordinary(self.old['A08'])[-1][1]
                final=frozenset((x-6,y,w,h) for x,y,w,h in old_final)
                self.assertEqual((len(final),window[-1][1]),(16,final))
                merged=next(i for i,(_,rs) in enumerate(window) if final<=rs)
                self.assertTrue(all(not (rs&final) for _,rs in window[:merged]))
                self.assertEqual(merged,588)
                self.assertEqual([i+1 for i in range(16) if any(r[2:]==(4,5) and
                    intersect(r,self.left,27+i*9) for r in window[first][1])], [9])
                self.assertEqual(len(window)+int(self.new['A09'].get('Timeout')),1142)
                self.assertEqual(ordinary([self.new['A09'][0]]),ordinary([self.old['A09'][0]])[6:])
                self.assertEqual(ordinary([self.old['A09'][1]]),ordinary([self.new['A09'][1]]))
                self.assertTrue(all(state==window[-1] for state in window[-4:]))
                continue
            clear=1+max(i for i,(_,rects) in enumerate(before) if max(counts(rects))>retained)
            self.assertEqual(clear,14 if stage=='B07' else (466 if self.col==4 else 478))
            for i,((s,old),(t,new)) in enumerate(zip(before,after,strict=True)):
                self.assertEqual(s,t)
                self.assertEqual({r for r in old if r[1]>=170},{r for r in new if r[1]>=170})
                if i<clear:
                    self.assertEqual(counts(old),counts(new))
            if stage=='A06':
                def new_inlet_in_well(rects):
                    return any(r[2:]==(4,5) and any(intersect(r,self.left,27+i*9) for i in range(16)) for r in rects)
                first=next(i for i,(_,rs) in enumerate(after) if new_inlet_in_well(rs))
                self.assertEqual(clear,first)
                self.assertLess(first,len(before))
                outlets=after+ordinary(self.new['A08'])
                old_final=ordinary(self.old['A08'])[-1][1]
                final=frozenset((x-6,y,w,h) for x,y,w,h in old_final)
            else:
                outlets=after
                final=frozenset(r for r in ordinary(self.old['B08'])[-1][1] if intersect(r,self.left,r[1]) and r[1]>=27)
            self.assertEqual(len(final),16)
            completion=next(i for i,(_,rs) in enumerate(outlets) if final<=rs)
            self.assertGreaterEqual(completion,clear)
            self.assertTrue(all(not (rs&final) for _,rs in outlets[:completion]))

    def test_four_column_bead_refill(self):
        if self.col != 4:
            self.skipTest("Four-column G16 inlet")
        from bead_inlet import mixing_states
        from build_four_column import ingress_tracks
        mix=self.new['B16']
        self.assertEqual(int(mix[2].get('Timeout'))+int(mix[3].get('Timeout')),
                         10*int(mix[1].get('Timeout')))
        window=mixing_states(mix)+ordinary(self.new['C02'])
        self.assertEqual((len(mixing_states(mix)),len(window)),(305,802))
        self.assertEqual(next(i for i,state in enumerate(window) if state==window[-1]),801)
        rails=ingress_tracks(self.new['C02'])
        self.assertTrue({5,16}<=set(rails['descending_x']))
        self.assertGreater(rails['simultaneous_dual_descent_frames'],0)

    def test_parallel_waste_descent(self):
        if self.col!=4:
            self.skipTest("Four-column waste outlet")
        from dual_outlet import descent, withdrawal
        from six_column_schedule import seconds
        for stage in ("R05","R06","C05","C06","R19","C15","C18"):
            with self.subTest(stage=stage):
                old,new=self.old[stage],self.new[stage]
                self.assertEqual(descent(old)["simultaneous_descent_frames"],0)
                self.assertGreater(descent(new)["simultaneous_descent_frames"],0)
                self.assertLess(seconds(new),seconds(old))
                self.assertEqual(withdrawal(new),withdrawal(old))

    def test_cleanup_admission_and_withdrawal(self):
        if self.col!=4:
            self.skipTest("Four-column waste outlet")
        from build_four_column import board, geom, MODEL, boxes
        board.configure()
        with (self.folder/(MODEL+'.csv')).open() as fh:
            holes=[tuple(float(row[k]) for k in ('StartX','StartY','Width','Height'))
                   for row in csv.DictReader(fh) if row['Type']=='ReagentWell' and float(row['StartY'])!=189.5]
        report=json.loads((self.folder/'四列排程校验.json').read_bytes())
        checked=set()
        for tag in ('R19','C15','C18'):
            flow=self.new[tag]
            proof=report['route_checks'][tag]
            self.assertEqual((proof['waste_ul'],proof['packets'],proof['withdrawals']),(48.,64,1))
            self.assertGreater(min(proof['exits_before_last_row_ready'][1:]),0)
            for relay in flow.iter('RelayItem'):
                occupancy={}
                for r in boxes(relay.get('RelayCode')):
                    x,y,w,h=r
                    mask=((1<<w)-1)<<x
                    for i in range(y,y+h):
                        self.assertFalse(occupancy.get(i,0)&mask)
                        occupancy[i]=occupancy.get(i,0)|mask
                    if r in checked:continue
                    self.assertTrue(0<=x<x+w<=256 and 0<=y<y+h<=192)
                    self.assertFalse(geom.between_lanes(r))
                    self.assertFalse(any(x<c+36 and c<x+w and y<172 and y+h>23 for c in board.COLUMNS[1:]))
                    self.assertTrue(all(geom.well_gap(r,port)>=1 for port in holes))
                    checked.add(r)

    def test_mixing_and_loading(self):
        def inside(trace):
            return [(s,frozenset(r for r in rs if any(intersect(r,self.left,27+i*9) for i in range(16)))) for s,rs in trace]
        self.assertEqual(inside(ordinary(self.new['B08'])+mix_trace(self.new['B09'])),
                         inside(mix_trace(self.old['B09'])))
        def table(data):
            return list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))[1:]
        for name,part in [('手工加液顺序.csv',slice(4,10)),('手工吸液顺序.csv',slice(5,8))]:
            old=table(type(self).previous(name))
            new=table((self.folder/name).read_bytes())
            self.assertEqual([r[part] for r in old],[r[part] for r in new])
            if name.startswith('手工加'):
                self.assertEqual([r[1] for r in new if r[5]=='LIG'],[self.new['B07'].get('ID')])
        affected=set('A06 A07 A08 B06 B07 B08 B09 B10 B11'.split())
        if self.col==4:
            affected.update(("B16","A09","R05","R06","C05","C06","R19","C15","C18"))
        folded={s for group,target in GROUPS for s in group.split() if s!=target}
        affected.update(s for group,target in GROUPS for s in group.split())
        self.assertEqual([s for s in self.old if s not in folded],list(self.new))
        self.assertEqual(len(self.new),44)
        self.assertEqual(ordinary([self.new['A02'][0]])[0],ordinary([self.old['A02'][0]])[0])
        flows=list(self.new.values())
        ui=ET.parse(self.folder/(SCRIPT+'.layout')).getroot()
        ids=[f.get('ID') for f in flows]
        self.assertEqual([(a.get('StartID'),a.get('EndID')) for a in ui.findall('Arrow')],list(zip(ids,ids[1:])))
        for r in table((self.folder/'手工加液顺序.csv').read_bytes()):
            flow=flows[ids.index(r[1])]
            n=next(i for i,c in enumerate(flow) if c.get('ID')==r[3])
            nxt=flow if n+1<len(flow) else flows[ids.index(r[1])+1]
            self.assertEqual((r[-3],r[-1]),(nxt.get('ID'),nxt[n+1 if nxt is flow else 0].get('ID')))

        for step in self.old.keys()-affected:
            self.old[step].tail=self.new[step].tail=None
            self.assertEqual(ET.tostring(self.old[step]),ET.tostring(self.new[step]),step)


if __name__=='__main__':
    unittest.main()
