"""授权的G14承接余量和就近同步融合；只做离线验证。"""
import contextlib,io,unittest
import geom,ops,protocol,rigid
from scene import Scene,Drop


class CompactTransportTests(unittest.TestCase):
    def test_padding_changes_electrodes_not_volume(self):
        s=Scene();ln=ops.Lane(1,1)
        d=s.add(Drop('TG14test',(32,16,2,5),.5));s.step()
        rigid.compact_g14_transport_packets(s,[(d,ln)])
        self.assertEqual(d.vol,.5)
        self.assertEqual(d.rect,(32,16,3,4))
        self.assertEqual(len(d.target_cells()),12)
        self.assertEqual(s.relays[-1].dur,4000)
        self.assertEqual(s.transport_padding_events[0]['source_cells'],10)

    def test_padding_is_not_applied_to_other_volumes(self):
        s=Scene();ln=ops.Lane(1,1)
        d=s.add(Drop('TG14test',(32,16,3,5),.75));s.step()
        rigid.compact_g14_transport_packets(s,[(d,ln)])
        self.assertEqual(len(d.target_cells()),15)
        self.assertFalse(s.transport_padding_events)

    def test_padded_electrodes_cannot_be_treated_as_added_liquid(self):
        s=Scene();ln=ops.Lane(1,1)
        d=s.add(Drop('TG14test',(32,16,2,5),.5));s.step()
        rigid.compact_g14_transport_packets(s,[(d,ln)])
        l,r=rigid.split_x(d,1,2)
        with self.assertRaisesRegex(AssertionError,'运输承接余量'):
            rigid.split_many(s,[(d,(l,r),(.25,.25),('a','b'))])

    def test_a02_merges_nearby_without_precontact_reshape_or_host_motion(self):
        s=Scene();ln=ops.Lane(1,1)
        h=s.add(Drop(ln.name,(38,28,8,4),1.6))
        g=s.add(Drop('T'+ln.id,(30,28,4,2),.4));s.step()
        old=h.rect;ops.merge_all(s,[ln],'T')
        e=s.reaction_merge_events[-1]
        self.assertEqual(e['mode'],'direct_nearby_fusion')
        self.assertFalse(e['precontact_reshape'])
        self.assertEqual(e['initial_host_rects'][ln.id],list(old))
        self.assertLessEqual(e['contact_step']-e['ready_step'],4)
        self.assertEqual(h.vol,2.)
        self.assertEqual(h.rect[2:],(8,5))
        self.assertEqual(len(h.target_cells()),40)
        self.assertEqual(s.relays[e['merge_step']].dur,4000)

    def test_g14_keeps_mass_tip_count_and_retires_every_padding_cell(self):
        p=protocol.Protocol((1,))
        for ln in p.ALL:p.S.add(Drop(ln.name,geom.lane_target(ln.c,ln.r,2.5),2.5))
        p.S.step()
        with contextlib.redirect_stderr(io.StringIO()):p.add_reagent('R1','R01 test','G14',2.5)
        loads=[c.manual_load for f in p.segs['R1'] for c in f.cmds if hasattr(c,'manual_load')]
        self.assertEqual(len(loads),1)
        self.assertEqual(loads[0]['volume_ul'],40.)
        self.assertEqual(loads[0]['port_volumes'],{'E1':10.,'E2':10.,'E3':10.,'E4':10.})
        events=p.S.transport_padding_events
        adds=[e for e in events if e['kind']=='add'];ends=[e for e in events if e['kind']=='retire']
        self.assertEqual(len(adds),32)
        self.assertEqual(sum(e['target_cells']-e['source_cells'] for e in adds),64)
        self.assertEqual(sum(e['cells'] for e in ends),64)
        self.assertTrue(all(p.S.get(ln.name).vol==5. for ln in p.ALL))
        self.assertTrue(all(len(p.S.get(ln.name).target_cells())==100 for ln in p.ALL))
        self.assertFalse(any(getattr(d,'transport_padding_cells',0) for d in p.S.drops.values()))


if __name__=='__main__':unittest.main()
