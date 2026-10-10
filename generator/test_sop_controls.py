"""POCS片上修改的离线时序回归，不把模拟磁铁状态当作硬件反馈。"""
import unittest
from unittest.mock import patch
import ops,protocol,rigid
from scene import Drop

class SopControlsTests(unittest.TestCase):
    def test_source_capture_intervals_and_increased_cycles(self):
        self.assertEqual(protocol.BEAD_CAPTURE_SECONDS,{'R1':180,'C':120})
        self.assertEqual(protocol.BEAD_ELUTE_CAPTURE_SECONDS,{'R1':120,'C':30})
        self.assertEqual(protocol.BEAD_BIND_MIX,{'R1':20,'C':12})
        self.assertEqual(protocol.BEAD_ELUTE_MIX,12)

    def test_supernatant_requires_raised_magnet(self):
        P=protocol.Protocol((1,))
        with self.assertRaisesRegex(AssertionError,'保持升起'):
            P.supernatant_off('R1','排废')

    def test_elution_requires_raised_magnet_before_wetting_beads(self):
        P=protocol.Protocol((1,))
        with self.assertRaisesRegex(AssertionError,'先保持磁铁升起'):
            P.elute_offchip('R1','洗脱','G15',5,5,1)

    def test_zero_liquid_beads_cannot_be_transported(self):
        P=protocol.Protocol((1,));ln=P.ALL[0]
        d=P.S.add(Drop(ln.name,ln.rect(ln.mag-1,3),0));d.magnet_only=True
        with self.assertRaisesRegex(AssertionError,'真实载液'):
            rigid.egress_pieces(P.S,[(ln,d)],P.blob)

    def test_cleanup_wets_before_lowering_magnet_and_discards_48ul(self):
        P=protocol.Protocol((1,));P.magnet_state='up';calls=[]
        for ln in P.ALL:
            d=P.S.add(Drop(ln.name,ln.rect(ln.mag-1,3),0));d.magnet_only=True
        original=ops.merge_all
        def wet(S,lanes,*a,**kw):
            self.assertEqual(P.magnet_state,'up')
            self.assertTrue(all(S.get('E'+ln.id).vol==3 for ln in lanes))
            result=original(S,lanes,*a,**kw);calls.append('wet');return result
        def mix(S,lanes,cycles):
            self.assertEqual(P.magnet_state,'down');self.assertEqual(cycles,12)
            self.assertTrue(all(S.get(ln.name).vol==3 for ln in lanes));calls.append('mix')
        with patch.object(ops,'merge_all',wet),patch.object(ops,'resuspend_all',mix):
            P.cleanup_beads('R1','R06清理','G15')
        self.assertEqual(calls,['wet','mix'])
        self.assertEqual(sum(info['volume_ul'] for r,info in P.S.manual_aspirations),48)
        self.assertEqual(P.bead_cleanup_events[0]['per_lane_ul'],3)
        self.assertFalse(P.S.drops)

    def test_capture_mix_occurs_down_and_capture_occurs_after_arrival(self):
        P=protocol.Protocol((1,));calls=[]
        for ln in P.ALL:P.S.add(Drop(ln.name,(ln.X0,ln.Y0,20,5),5))
        def mix(S,lanes,cycles,span,**kw):
            self.assertTrue(kw['full_region'])
            self.assertEqual(P.magnet_state,'down');calls.append(('mix',cycles))
        def park(S,lanes,**kw):
            self.assertEqual(P.magnet_state,'down');calls.append(('arrive',None))
        original=ops.hold_all
        def hold(S,secs,*args,**kw):
            calls.append((P.magnet_state,secs));original(S,secs,*args,**kw)
        with patch.object(ops,'mix_all',mix),patch.object(ops,'to_magnet_all',park),patch.object(ops,'hold_all',hold):
            P.bead_capture('R1','结合',20,19)
        self.assertEqual(calls,[('mix',20),('down',300),('arrive',None),('up',180)])

if __name__=='__main__':unittest.main()
