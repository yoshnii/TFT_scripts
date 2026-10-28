"""Local release: direct mother routes without changing split or reaction policy."""
import hashlib
import json
import subprocess
import unittest
from pathlib import Path
import xml.etree.ElementTree as E

import rigid
from scene import Scene, Drop
from update_direct_routes_local import BASE, REPORT, TARGET
from update_transport_cleanup_local import last_hv
from waste_recovery import boxes

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT/'projects/PTseq_SIRO16_A_10x'


def source(name):
    return subprocess.check_output(['git', 'show', BASE+':projects/PTseq_SIRO16_A_10x/'+name], cwd=ROOT)


class ParkingClearanceTests(unittest.TestCase):
    def test_reserve_must_clear_expanded_right_child_by_six_cells(self):
        scene = Scene()
        scene.strict_transport = True
        child = scene.add(Drop('CHILD', (139, 4, 12, 5), 3.))
        mother = scene.add(Drop('DG14E3', (154, 4, 24, 10), 10.))
        mother.electrode_area = 240
        mother.margin_budget = 40
        mother.native_authorized_padding = True
        self.assertFalse(rigid.rect_ok(scene, mother.name, mother.rect))
        self.assertFalse(rigid.rect_ok(scene, mother.name, (156, 4, 24, 10)))
        self.assertTrue(rigid.rect_ok(scene, mother.name, (157, 4, 24, 10)))
        self.assertTrue(rigid.rect_ok(scene, mother.name, (158, 4, 24, 10)))


class DirectRoutesReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = {int(f.get('ID')): f for f in E.fromstring(source('PTseq_16_A.elab'))}
        cls.new = {int(f.get('ID')): f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
        cls.proof = json.loads((PROJECT/REPORT).read_text())

    def test_only_two_delivery_sections_changed(self):
        self.assertEqual(self.old.keys(), self.new.keys())
        changed = {i for i in self.old if E.tostring(self.old[i]) != E.tostring(self.new[i])}
        self.assertEqual(changed, TARGET)
        for fid in TARGET:
            a, b = self.old[fid], self.new[fid]
            self.assertEqual({k: v for k, v in a.attrib.items() if k != 'Timeout'},
                             {k: v for k, v in b.attrib.items() if k != 'Timeout'})
            self.assertEqual(len(a), len(b))
            for x, y in zip(a, b):
                self.assertEqual({k: v for k, v in x.attrib.items() if k != 'Timeout'},
                                 {k: v for k, v in y.attrib.items() if k != 'Timeout'})

    def test_receiving_and_downstream_boundary_masks_unchanged(self):
        for receiving, delivery in ((1002, 50000), (2000, 50002)):
            self.assertEqual(E.tostring(self.old[receiving]), E.tostring(self.new[receiving]))
            self.assertEqual(self.new[delivery][0][0].get('RelayCode'),
                             self.old[delivery][0][0].get('RelayCode'))
            self.assertEqual(last_hv(self.new[delivery]).get('RelayCode'),
                             last_hv(self.old[delivery]).get('RelayCode'))
        for fid in TARGET:
            # No new carrier shapes hidden in the regenerated trajectories.
            shapes = lambda f: {rc[2:] for r in f.iter('RelayItem')
                                for rc in boxes(r.get('RelayCode', ''))}
            self.assertEqual(shapes(self.old[fid]), shapes(self.new[fid]))

    def test_proven_root_detours_removed_not_required_avoidance(self):
        a, r = self.proof['cases']
        roots = {p['name']: p for p in a['root_routes']}
        self.assertEqual((roots['DG4E2']['start_x'], roots['DG4E2']['end_x'],
                          roots['DG4E2']['max_x'], roots['DG4E2']['right_cells']), (102, 40, 102, 0))
        roots = {p['name']: p for p in r['root_routes']}
        self.assertEqual((roots['DG14E3']['start_x'], roots['DG14E3']['max_x'],
                          roots['DG14E3']['right_cells']), (154, 158, 4))
        self.assertEqual(roots['DG14E4']['right_cells'], 0)

    def test_actual_liquid_leaf_geometry_and_equal_splitting(self):
        expected = [('G4', .4, 2., 16, [3, 4], .4, 14),
                    ('G14', 2.5, 5., 64, [3, 5], .625, 60)]
        for case, values in zip(self.proof['cases'], expected):
            self.assertEqual((case['reagent'], case['real_ul_per_lane_added'],
                              case['real_ul_per_lane_final'], case['leaf_count'],
                              case['leaf_shape_w_h'], case['leaf_ul'], case['split_count']), values)
            self.assertTrue(case['scene']['static_geometry_passed'])
            self.assertEqual(case['scene']['equal_x_splits'], values[-1])
            for split in case['split_shapes']:
                first, final = split['positions'][0], split['positions'][-1]
                self.assertEqual(first[0][2:], first[1][2:])
                self.assertEqual(first[0][2]*2, first[1][0]-first[0][0]+first[1][2])
                self.assertEqual(first[0][0]-final[0][0], final[1][0]-first[1][0])
                self.assertEqual(final[1][0]-final[0][0]-final[0][2], 6)

    def test_hold_rules_and_all_lanes_ready_before_fusion(self):
        for case in self.proof['cases']:
            self.assertGreater(case['reaction_contact_step'], case['all_ready_step'])
            self.assertTrue(case['receiving_section_unchanged'])
            self.assertTrue(all(p['all_reshape_holds_at_least_4000ms'] for p in case['hold_trace']))
        self.assertIsNone(self.proof['cases'][0]['slow_mother_rest_ms'])
        self.assertEqual(self.proof['cases'][1]['slow_mother_rest_ms'], 3000)

    def test_layout_loading_aspiration_and_operator_table_unchanged(self):
        for name in ('PTseq_SIRO16_A_10x.elabprj', 'PTseq_16_A.elab.layout',
                     'ChipArea_192_256_96_SIRO.csv', '手工加液顺序.csv', '手工吸液顺序.csv'):
            self.assertEqual((PROJECT/name).read_bytes(), source(name))
        self.assertEqual((PROJECT/'手工加液运行.html').read_text().split('<table', 1)[1],
                         source('手工加液运行.html').decode().split('<table', 1)[1])

    def test_times_hash_and_scan_evidence(self):
        p = self.proof
        self.assertEqual(p['total_saved_seconds'], 69)
        self.assertEqual({i: (int(self.old[i].get('Timeout')), int(self.new[i].get('Timeout')))
                          for i in TARGET}, {50000: (581, 512), 50002: (2308, 2308)})
        for fid in TARGET:
            self.assertEqual(int(self.new[fid].get('Timeout')),
                             sum(int(r.get('Duration')) for r in self.new[fid].iter('RelayItem'))//1000)
        self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),
                         sum(int(f.get('Timeout'))*int(f.get('Repeats', '1')) for f in self.new.values()))
        self.assertEqual(p['script_sha256'], hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
        self.assertEqual(p['full_native_reverse_candidate_count'], 1003)
        self.assertEqual(sum(g['candidate_count'] for g in p['scan_review']), 1003)
        self.assertFalse(p['GUI_check'])
        self.assertFalse(p['physical_validation'])


if __name__ == '__main__':
    unittest.main()
