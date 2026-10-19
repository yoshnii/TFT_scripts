"""Focused tests for the explicitly approved transport spacing exceptions."""
import unittest
from types import SimpleNamespace
from scene import Drop
from transport_spacing import required,well


class TransportSpacingTests(unittest.TestCase):
    def setUp(self):
        self.S=SimpleNamespace(strict_transport=True,outlet_staging_gap=4)
        self.a=Drop('a',(46,80,4,4),.75)
        self.b=Drop('b',(46,94,4,4),.75)
        self.a.native_packet=self.b.native_packet=True

    def test_independent_vertical_queue(self):
        self.assertEqual(required(self.S,self.a,self.a.rect,self.b,self.b.rect),6)

    def test_reaction_retains_existing_gap(self):
        self.assertEqual(required(self.S,self.a,(10,28,4,4),self.b,(18,28,4,4)),3)

    def test_outlet_staging_and_turn(self):
        self.assertEqual(required(self.S,self.a,(46,163,4,4),self.b,(54,172,8,16)),4)
        self.assertEqual(required(self.S,self.a,(46,153,4,4),self.b,(54,172,8,16)),6)

    def test_top_staging(self):
        self.assertEqual(required(self.S,self.a,(10,4,4,4),self.b,(20,4,4,4)),6)

    def test_C02_entry_only(self):
        well_rect=(13,163,8,5)
        self.assertEqual(required(self.S,self.a,(6,153,4,5),self.b,well_rect),6)
        self.a.c02_entry_exception=True
        self.assertEqual(required(self.S,self.a,(6,153,4,5),self.b,well_rect),3)
        self.assertEqual(required(self.S,self.a,(6,153,4,5),self.b,(6,163,4,5)),6)

    def test_complete_well_bounds(self):
        self.assertEqual(well((10,27,10,6)),(1,1))
        self.assertIsNone(well((6,28,4,4)))
        self.assertIsNone(well((10,28,10,6)))


if __name__=='__main__':unittest.main()
