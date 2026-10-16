"""Current release: approved B01/G8/LIG/G16 paths and physical-volume ledger."""
import contextlib
import io
import unittest

import geom
import ops
import protocol
from scene import Drop,Scene
from native_packets import _configuration


class CompactCascadeTests(unittest.TestCase):
    def test_b01_full_rectangle_all_96_positions_actual_volume_unchanged(self):
        s=Scene();lanes=ops.lanes_all(tuple(range(1,7)))
        ops.load_holes(s,lanes,'S',1.3,'L',manual=True,receiving_shape=(6,5))
        for ln in lanes:
            d=s.get(ln.name)
            self.assertEqual(d.vol,1.3)
            self.assertEqual(d.rect[2:],(6,5))
            self.assertEqual(len(d.target_cells()),30)
            self.assertEqual(d.margin_budget,4)
            self.assertTrue(geom.reaction_holes_ok(d.rect,1))

    def run_reagent(self,reagent,host,volume,paths):
        p=protocol.Protocol((1,))
        if reagent=='G8':
            p.load_samples('B','B01 test','S',1.3,'L','normalized',receiving_shape=(6,5))
        else:
            for ln in p.ALL:
                shape=(13,4) if reagent=='LIG' else (21,4)
                p.S.add(Drop(ln.name,(ln.X0+36-shape[0],ln.Y0,*shape),host))
            p.S.step();p.S.flush()
        with contextlib.redirect_stderr(io.StringIO()):
            p.add_reagent('C' if reagent=='G16' else 'B',reagent,reagent,volume)
        for e in p.S.split_events:
            self.assertEqual(e['volumes'][0],e['volumes'][1])
            self.assertEqual(e['positions'][0][0][2:],paths[e['area']])
            self.assertTrue(all(0<=b-a<=1 for a,b in zip(e['gaps'],e['gaps'][1:])))
        for d in p.S.drops.values():
            self.assertAlmostEqual(d.vol,host+volume)
            self.assertEqual(len(d.target_cells()),geom.electrode_cells(host+volume))
        self.assertEqual(p.S.reaction_merge_events[-1]['rows'],list(range(1,17)))
        ready=next(e['step'] for e in p.S.pipeline_events if e['kind']=='all_ready')
        self.assertGreater(p.S.reaction_merge_events[-1]['contact_step'],ready)
        return p

    def test_g8_exact_approved_mother_path_and_single_port(self):
        p=self.run_reagent('G8',1.3,.7,{240:[10,12],120:[6,10],60:[6,5],32:[4,4]})
        leaves=p.S.native_leaf_events
        self.assertEqual(len(leaves),16)
        self.assertTrue(all(e['shape']==[4,4] and e['extra_cells']==2 and e['actual_ul']==.7 for e in leaves))
        self.assertEqual([c.manual_load['port_volumes'] for f in p.segs['B'] for c in f.cmds
                          if hasattr(c,'manual_load') and 'port_volumes' in c.manual_load],[{'E1':11.2}])
        self.assertEqual(sum(e['after_cells']-e['before_cells'] for e in p.S.approved_mother_shapes),16)

    def test_lig_two_compact_packets_per_well_and_unchanged_dose(self):
        p=self.run_reagent('LIG',2.6,1.4,{240:[10,12],120:[6,10],60:[6,5],32:[4,4]})
        leaves=p.S.native_leaf_events
        self.assertEqual(len(leaves),32)
        self.assertTrue(all(e['shape']==[4,4] and e['extra_cells']==2 for e in leaves))
        self.assertTrue(all(sum(e['row']==r for e in leaves)==2 for r in range(1,17)))
        self.assertEqual([c.manual_load['port_volumes'] for f in p.segs['B'] for c in f.cmds if hasattr(c,'manual_load')],[{'E1':11.2,'E2':11.2}])

    def test_g16_rotates_only_mothers_and_keeps_actual_590(self):
        p=self.run_reagent('G16',4.2,1.7,{320:[10,16],160:[10,8],80:[5,8],40:[4,5]})
        self.assertEqual(len(p.S.native_leaf_events),32)
        self.assertTrue(all(e['actual_ul']==.85 and e['shape']==[4,5] and e['extra_cells']==3 for e in p.S.native_leaf_events))
        self.assertTrue(all(e['before'][2:]==[5,8] and e['after'][2:]==[8,5]
                            and e['before_cells']==e['after_cells']==40
                            for e in p.S.approved_mother_shapes))
        self.assertTrue(all(d.vol==5.9 and len(d.target_cells())==120 for d in p.S.drops.values()))

    def test_all_final_packets_fit_channel_and_have_at_most_five_extra_cells(self):
        for reagent in ('G4','AMP','G14','G8','LIG','EDTA','G16'):
            ports,n,vol,w,h,per_lane=_configuration(reagent)
            self.assertLessEqual(w,4)
            self.assertLessEqual(h,5)
            self.assertGreaterEqual(w*h,geom.cells(vol))
            self.assertLessEqual(w*h-geom.cells(vol),5)

    def test_unmodified_sample_and_barcode_load_shapes(self):
        self.assertEqual(geom.s_block(1,1,1.6)[2:],(8,4))
        self.assertEqual(geom.b_block(1,1,1.2)[2:],(6,4))


if __name__=='__main__':unittest.main()
