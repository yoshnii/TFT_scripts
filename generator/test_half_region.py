"""半区约束离线回归；不打开GUI，不向仪器下发命令。"""
import unittest
import geom, ops, protocol
from scene import Scene, Drop

class HalfRegionTests(unittest.TestCase):
    def test_complete_shapes_and_permitted_corner(self):
        self.assertEqual(geom.half_shape(2.00), (8, 5))
        self.assertEqual(geom.half_shape(2.50), (10, 5))
        self.assertEqual(geom.half_shape(4.00), (16, 5))
        self.assertEqual(geom.half_shape(5.00), (17, 6))
        self.assertEqual(geom.half_shape(5.40), (18, 6))

    def test_capacity_is_fail_closed(self):
        for v in (5.45, 5.90):
            with self.assertRaises(geom.HalfRegionCapacityError):
                geom.half_shape(v)

    def test_actual_footprint_not_center(self):
        ln = ops.Lane(1, 1)
        S = Scene()
        S.add(Drop(ln.name, (ln.X0+16, ln.Y0, 20, 6), 5.90))
        with self.assertRaises(geom.HalfRegionCapacityError):
            ops.assert_half(S, [ln], 'right')
        d = S.get(ln.name)
        d.vol = 5.00
        d.rect = (ln.X0+17, ln.Y0, 17, 6)
        with self.assertRaises(ValueError):
            ops.assert_half(S, [ln], 'right')

    def test_centered_thermal_and_magnetic_holds(self):
        for side, vol, shape in [('left', 2.00, (7, 6)), ('left', 2.50, (9, 6)),
                                  ('left', 4.00, (14, 6)), ('right', 5.00, (17, 6))]:
            with self.subTest(side=side, volume=vol):
                lanes = ops.lanes_all((1,))
                S = Scene()
                for ln in lanes:
                    S.add(Drop(ln.name, (ln.X0, ln.Y0, *shape), vol))
                S.step(changed=[])
                ops.park_half(S, lanes, side)
                ops.assert_half(S, lanes, side)
                for ln in lanes:
                    d = S.get(ln.name)
                    self.assertEqual(len(d.target_cells()), geom.cells(vol))
                    self.assertEqual(d.rect[2:], geom.half_shape(vol))
                self.assertTrue(all(r.codes and r.amp > 0 for r in S.relays))

    def test_only_590_magnetic_exception_is_authorized(self):
        self.assertEqual(geom.magnetic_shape(5.90,True),(20,6))
        with self.assertRaises(geom.HalfRegionCapacityError):
            geom.magnetic_shape(5.45,True)
        ln=ops.Lane(1,1);S=Scene()
        S.add(Drop(ln.name,(ln.X0+16,ln.Y0,20,6),5.90))
        ops.assert_half(S,[ln],'right',allow_590_cross=True)
        with self.assertRaises(geom.HalfRegionCapacityError):
            ops.assert_half(S,[ln],'left',allow_590_cross=True)
        S.get(ln.name).rect=(ln.X0+15,ln.Y0,20,6)
        with self.assertRaises(ValueError):
            ops.assert_half(S,[ln],'right',allow_590_cross=True)

    def test_column_capacity_preflight_has_no_partial_moves(self):
        lanes = ops.lanes_all((1,))
        S = Scene()
        for ln in lanes:
            v = 5.90 if ln.r == 16 else 5.00
            S.add(Drop(ln.name, (ln.X0, ln.Y0, 20, 6), v))
        old = {n: d.rect for n, d in S.drops.items()}
        with self.assertRaises(geom.HalfRegionCapacityError):
            ops.park_half(S, lanes, 'right')
        self.assertEqual(old, {n: d.rect for n, d in S.drops.items()})
        self.assertEqual(S.relays, [])

if __name__ == '__main__':
    unittest.main()
