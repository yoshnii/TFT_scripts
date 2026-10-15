"""20261015授权边界回归：仅离线验证，不下发仪器、不操作GUI。"""
import contextlib,io,unittest
import geom
from scene import Scene,Drop


class ApprovedGeometryTests(unittest.TestCase):
    def test_approved_shapes_all_96_positions(self):
        for col in range(1,7):
            for row in range(1,17):
                for volume,side,shape in ((4.,'left',(16,5)),(5.,'right',(20,5)),(5.9,'right',(24,5))):
                    rc=geom.lane_target(col,row,volume,side)
                    self.assertEqual(rc[2:],shape)
                    self.assertTrue(geom.reaction_holes_ok(rc,1))
                    x,y=geom.physical_zone(col,row)
                    self.assertTrue(x<=rc[0] and rc[0]+rc[2]<=x+36)
                    self.assertTrue(y<=rc[1] and rc[1]+rc[3]<=y+6)
                    if volume==4:self.assertLessEqual(rc[0]+rc[2],x+18)

    def test_no_implicit_volume_or_margin_change(self):
        self.assertEqual(geom.cells(5.9),118)
        self.assertEqual(geom.electrode_cells(5.9),120)
        for volume in (.2,.4,.5,.6,.7,1.3,1.4,1.6,1.7,2.,2.5,2.6,3.,4.,4.2,5.):
            self.assertEqual(geom.electrode_cells(volume),geom.cells(volume))
        self.assertEqual(geom.running_gap(4.2),2)
        self.assertEqual(geom.running_gap(2.5),2)
        self.assertEqual(geom.running_gap(4.),1)

    def test_other_targets_retain_two_grid_clearance(self):
        for volume in (.2,.4,.5,.6,.7,1.3,1.4,1.6,1.7,2.,2.5,2.6,3.,4.2):
            rc=geom.lane_target(1,1,volume)
            self.assertTrue(geom.reaction_holes_ok(rc,2))
            self.assertEqual(rc[2]*rc[3],geom.cells(volume))

    def test_extra_area_rejected_without_authorized_lineage(self):
        s=Scene();d=s.add(Drop('test',(10,29,5,2),.4));d.electrode_area=10
        with self.assertRaises(AssertionError):s.step()

    def test_bulk_physical_volume_and_rectangular_electrode_separate(self):
        s=Scene();d=s.add(Drop('C',geom.lane_target(1,1,5.9,'right'),5.9));s.step()
        self.assertEqual(d.vol,5.9)
        self.assertEqual(len(d.target_cells()),120)
        self.assertEqual(len(d.rects()),1)
        self.assertEqual(geom.STEP_MS,1000)

    def test_b02_every_native_cut_is_on_long_dimension(self):
        import protocol
        p=protocol.Protocol((1,))
        for ln in p.ALL:p.S.add(Drop(ln.name,(ln.X0+23,ln.Y0,13,2),1.3))
        p.S.step()
        with contextlib.redirect_stderr(io.StringIO()):p.add_reagent('B','B02 test','G8',.7)
        for e in p.S.split_events:
            a,b=e['positions'][0]
            w=max(a[0]+a[2],b[0]+b[2])-min(a[0],b[0])
            h=max(a[1]+a[3],b[1]+b[3])-min(a[1],b[1])
            self.assertGreaterEqual(w if e['axis']=='x' else h,h if e['axis']=='x' else w)

    def test_other_active_cascades_cut_long_dimension(self):
        import rigid
        for vc,n in ((8,8),(40,8),(25,8),(28,8),(36,8),(4,16)):
            w,h=(6,6) if vc==36 else (2,2) if vc==4 else rigid.fixed_cascade_shape(vc,n)
            nx=n//2;W=w*nx;H=h*2
            while nx>1:self.assertGreaterEqual(W,H);W//=2;nx//=2
            self.assertGreaterEqual(H,W)


if __name__=='__main__':unittest.main()
