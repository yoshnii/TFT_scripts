"""Regression fixtures for collection direction and group-only reshaping."""
import unittest
from audit_bottom_motion import violations


class BottomMotionTests(unittest.TestCase):
    def test_old_wide_rows_moving_horizontally_are_rejected(self):
        for w in (12,16):
            self.assertTrue(violations([(80,172,w,4)],[(81,172,w,4)]))

    def test_vertical_half_columns_move_only_horizontally(self):
        for w in (6,8):
            self.assertFalse(violations([(80,172,w,16)],[(81,172,w,16)]))
            self.assertTrue(violations([(80,172,w,16)],[(80,173,w,16)]))

    def test_square_blocks_may_move_in_either_direction(self):
        self.assertFalse(violations([(80,176,8,8)],[(81,176,8,8)]))
        self.assertFalse(violations([(80,176,8,8)],[(80,177,8,8)]))

    def test_stationary_collection_is_not_movement(self):
        self.assertFalse(violations([(46,172,16,16)],[(46,172,16,16)]))


if __name__=='__main__':unittest.main()
