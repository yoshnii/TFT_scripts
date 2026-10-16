"""Local section grouping regression; no full protocol or instrument execution."""
import unittest
import xml.etree.ElementTree as E
import contextlib
import io
import geom,protocol
from scene import Drop
from model import Script
from elution_sections import combine_incubation


class ElutionSectionTests(unittest.TestCase):
    def test_actual_two_elutions_each_emit_one_incubation_section(self):
        p=protocol.Protocol((1,));p.magnet_state='up'
        for lane in p.ALL:
            d=p.S.add(Drop(lane.name,lane.rect(lane.mag-1,3),0));d.magnet_only=True
        with contextlib.redirect_stderr(io.StringIO()):
            for round_no in (1,2):
                p.elute_offchip('R1',f'R0{round_no+4} elution {round_no}','G15',5.,5.,round_no,final=False)
        merged=[f for f in p.segs['R1'] if len(f.sync_groups)==2]
        self.assertEqual(len(merged),2)
        self.assertTrue(all(f.timeout==300 for f in merged))
        self.assertFalse(any('孵育第' in f.name for f in p.segs['R1']))
        self.assertTrue(all([e['start_seconds'] for e in event['periodic_mixing']]==[120,240]
                            for event in p.elution_events))
        self.assertEqual([i['volume_ul'] for _,i in p.S.manual_aspirations],[80.,80.])

    def test_both_incubations_keep_their_native_trace_and_timer(self):
        for round_no in (1,2):
            p=protocol.Protocol((1,))
            for lane in p.ALL:
                p.S.add(Drop(lane.name,(lane.X0,lane.Y0,20,5),5.))
            p.S.step();p.S.flush()
            elapsed=0
            for at in (120,240):
                wait=p.hold('wait',at-elapsed)
                elapsed+=p.mix('R1','light movement',cycles=2,initial=[wait])
            flows=p.segs['R1'];tail=p.hold('remaining',300-elapsed)
            originals=[c for f in flows for c in f.cmds]+[tail]
            frames=[[(tuple(r.codes),r.dur,r.amp) for r in c.relays] for c in originals]
            combined=combine_incubation(flows,tail,f'elution {round_no}')
            self.assertEqual(combined.cmds,originals)
            self.assertEqual(combined.timeout,300)
            self.assertEqual([[(tuple(r.codes),r.dur,r.amp) for r in c.relays] for c in combined.cmds],frames)
            self.assertEqual([g[0].timeout for g in combined.sync_groups],[48,48])
            self.assertEqual([g[1].timeout for g in combined.sync_groups],[33,33])
            self.assertEqual([c.timeout for c in (combined.cmds[0],combined.cmds[4],combined.cmds[-1])],[120,71,11])
            self.assertEqual(combined.cmds[4].pre,(combined.cmds[3],'FS'))
            self.assertEqual(tail.pre,(combined.cmds[7],'FS'))
            script=Script('test',[combined],'chip',100)
            root=E.fromstring(script.xml());groups=root[0].get('SyncEndingCmdList').split(';')
            self.assertEqual(len(groups),2)
            self.assertEqual(groups,[','.join(str(c.id) for c in g) for g in combined.sync_groups])
            self.assertTrue(all(r.amp>0 and len(r.codes)==16 for c in combined.cmds for r in c.relays))


if __name__=='__main__':unittest.main()
