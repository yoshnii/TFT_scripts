"""Regression fixtures for collection direction and group-only reshaping."""
import unittest
from audit_bottom_motion import violations


class BottomMotionTests(unittest.TestCase):
    def test_old_wide_rows_moving_horizontally_are_rejected(self):
        for w in (12,16):
            self.assertTrue(violations([(80,172,w,4)],[(81,172,w,4)]))

    def test_vertical_half_columns_also_reject_long_edge_facing_motion(self):
        for w in (6,8):
            bad=violations([(80,172,w,16)],[(81,172,w,16)])
            self.assertEqual(bad[0]['reason'],'long_edge_facing_transport')
            self.assertTrue(violations([(80,172,w,16)],[(80,173,w,16)]))

    def test_horizontal_strip_also_rejects_long_edge_facing_motion(self):
        bad=violations([(80,172,12,4)],[(80,173,12,4)])
        self.assertEqual(bad[0]['reason'],'long_edge_facing_transport')

    def test_square_blocks_may_move_in_either_direction(self):
        self.assertFalse(violations([(80,176,8,8)],[(81,176,8,8)]))
        self.assertFalse(violations([(80,176,8,8)],[(80,177,8,8)]))

    def test_stationary_collection_is_not_movement(self):
        self.assertFalse(violations([(46,172,16,16)],[(46,172,16,16)]))

    def test_B07_short_edge_leading_exception_is_explicit_and_one_cell_only(self):
        self.assertFalse(violations([(80,172,16,6)],[(79,172,16,6)],allow_long_axis=True))
        self.assertTrue(violations([(80,172,16,6)],[(80,173,16,6)],allow_long_axis=True))
        self.assertTrue(violations([(80,172,6,16)],[(81,172,6,16)],allow_long_axis=True))
        self.assertFalse(violations([(80,172,6,16)],[(80,173,6,16)],allow_long_axis=True))
        self.assertTrue(violations([(80,172,16,6)],[(78,172,16,6)],allow_long_axis=True))
        self.assertTrue(violations([(80,172,16,6)],[(79,172,16,6)]))

    def test_stepped_exception_is_not_granted_to_ordinary_droplets(self):
        from scene import Scene,Drop
        S=Scene()
        d=S.add(Drop('ordinary',(48,180,6,8),1.8))
        d.set_cells({(x,y) for x in range(48,54) for y in range(184,188)}
                    |{(x,y) for x in range(48,51) for y in range(180,184)})
        with self.assertRaisesRegex(AssertionError,'液滴不是完整矩形'):S.step()


if __name__=='__main__':unittest.main()
