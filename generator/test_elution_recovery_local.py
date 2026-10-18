"""Only withdrawal envelopes and the four elution sections plus their boundaries."""
import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import subprocess
import unittest
import xml.etree.ElementTree as E

import geom
import protocol
from elution_recovery import recovery_command, withdrawal_path, withdrawal_relays
from manual_sections import split_sections, align_high_voltage_timing
from model import Script
from ops import lanes_all
from scene import Scene, Drop
from update_elution_recovery_local import transform, TARGETS

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'projects/PTseq_SIRO16_A_10x/'
BASE = 'b63f7c4'


def source(name):
    return subprocess.check_output(['git', 'show', f'{BASE}:{PROJECT}{name}'], cwd=ROOT).decode('utf-8-sig')


def cells(rc):
    return {(x, y) for x in range(rc[0], rc[0]+rc[2]) for y in range(rc[1], rc[1]+rc[3])} if rc else set()


class ElutionRecoveryTests(unittest.TestCase):
    def test_both_rectangles_only_withdraw_one_grid_edges(self):
        for vol, count in ((5., 16), (3., 12)):
            for ln in lanes_all((1,)):
                first = geom.s_block(ln.c, ln.r, vol)
                path = withdrawal_path(first, ln.X0+geom.S_DX)
                self.assertEqual(len(path), count)
                self.assertEqual(path[-2], (ln.X0+geom.S_DX, first[1], 1, 1))
                self.assertIsNone(path[-1])
                for a, b in zip(path, path[1:]):
                    self.assertLess(len(cells(b)), len(cells(a)))
                    self.assertTrue(cells(b) < cells(a))
                    if b:
                        self.assertTrue(geom.reaction_holes_ok(b, 1))
                        self.assertFalse(geom.between_lanes(b))
                        for i in range(2):
                            self.assertLessEqual(abs(a[i]-b[i]), 1)
                            self.assertLessEqual(abs((a[i]+a[i+2])-(b[i]+b[i+2])), 1)

    def test_all_wells_parallel_and_unrelated_electrodes_always_retained(self):
        lanes = lanes_all((1,));rects = [geom.s_block(ln.c, ln.r, 5.) for ln in lanes]
        retained = ['(28,60)(4,4)']
        relays = withdrawal_relays(rects, [ln.X0+geom.S_DX for ln in lanes], retained)
        self.assertEqual(len(relays), 16)
        self.assertEqual(relays[-1].codes, retained)
        for r in relays:
            self.assertEqual(r.dur, 1000);self.assertEqual(r.amp, 55)
            self.assertIn(retained[0], r.codes)
            self.assertIn(len(r.codes), (1, 17))

    def test_generator_records_full_external_aspiration_once(self):
        for vol in (5., 3.):
            scene = Scene();scene.manual_aspirations = [];lanes = lanes_all((1,))
            for ln in lanes:
                scene.add(Drop(f'P{ln.id}', geom.s_block(ln.c, ln.r, vol), vol))
                bead = scene.add(Drop(ln.name, ln.rect(ln.mag-1, 3), 0));bead.magnet_only = True
            c = recovery_command(scene, lanes, vol, 1)
            self.assertEqual(len(scene.manual_aspirations), 1)
            self.assertEqual(scene.manual_aspirations[0][1]['volume_ul'], vol*16)
            self.assertTrue(all(d.vol == 0 and d.magnet_only for d in scene.drops.values()))
            self.assertEqual(c.relays[-1].codes, [])
            self.assertEqual(c.params, 'SCRIPTCYCLE_FALSE')
            self.assertEqual(c.timeout, sum(r.dur for r in c.relays)//1000)

    def test_generator_native_sections_keep_recovery_as_one_section(self):
        for seg, vol in (('R1', 5.), ('C', 3.)):
            p = protocol.Protocol((1,));p.magnet_state = 'up'
            for ln in p.ALL:
                bead = p.S.add(Drop(ln.name, ln.rect(ln.mag-1, 3), 0));bead.magnet_only = True
            with contextlib.redirect_stderr(io.StringIO()):
                for round_no in (1, 2):
                    p.elute_offchip(seg, f'test elution {round_no}', 'G15' if seg == 'R1' else 'G18',
                                    vol, vol, round_no, final=False)
            script = Script('test', p.segs[seg], 'ChipArea_192_256_96_SIRO', 2000)
            split_sections([script]);align_high_voltage_timing([script])
            flows = [f for f in script.flows if '运行期间手工回收' in f.name]
            self.assertEqual(len(flows), 2)
            for f in flows:
                self.assertEqual(f.cmds[-1].relays[-1].codes, [])
                self.assertTrue(f.cmds[-1].manual_aspiration['progressive_recovery'])
                self.assertEqual(len(f.cmds[-1].relays), 16 if vol == 5. else 12)
            self.assertEqual([i['volume_ul'] for _, i in p.S.manual_aspirations], [vol*16, vol*16])

    def test_native_patch_preserves_every_other_command_and_adjacent_sections(self):
        text = source('PTseq_16_A.elab');layout = source('PTseq_16_A.elab.layout')
        main = source('main.elab');mapping = json.loads(source('步骤编号映射_20261016.json'))
        rows = list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv'))))
        new, lt, mt, _, updated_rows, report = transform(text, layout, main, mapping, copy.deepcopy(rows))
        before, after = E.fromstring(text), E.fromstring(new)
        target_ids = {str(v[0]) for v in TARGETS.values()}
        for index, (a, b) in enumerate(zip(before, after)):
            if a.get('ID') not in target_ids:
                self.assertEqual(E.tostring(a), E.tostring(b))
                continue
            self.assertEqual([c.get('ID') for c in a], [c.get('ID') for c in b])
            for x, y in zip(a[:-1], b[:-1]):self.assertEqual(E.tostring(x), E.tostring(y))
            self.assertEqual(a[-1][0].attrib, b[-1][0].attrib)
            self.assertEqual(b[-1][-1].get('RelayCode'), '')
            self.assertEqual(before[index-1].attrib, after[index-1].attrib)
            self.assertEqual(E.tostring(before[index+1]), E.tostring(after[index+1]))
        for old_row, row in zip(rows, updated_rows):
            if row['吸液流程组编号'] in target_ids:
                self.assertEqual(row['本次总液体µL（16道合计；排珠载液不计文库）'],
                                 old_row['本次总液体µL（16道合计；排珠载液不计文库）'])
                self.assertEqual(row['待吸液电极'], '')
            else:self.assertEqual(old_row, row)
        self.assertEqual(report['total_nominal_change_seconds'], 52)
        self.assertEqual(int(E.fromstring(mt)[0].get('Timeout')),
                         int(E.fromstring(main)[0].get('Timeout'))+52)
        E.fromstring(lt)

    def test_released_endpoints_match_the_checked_withdrawal_plan(self):
        root = E.parse(ROOT/PROJECT/'PTseq_16_A.elab').getroot()
        for label, (fid, cid, vol, _) in TARGETS.items():
            flow = next(f for f in root if f.get('ID') == str(fid))
            self.assertTrue(flow.get('Name').startswith(label+' '))
            command = flow[-1]
            self.assertEqual(command.get('ID'), str(cid))
            lanes = lanes_all((1,))
            relays = withdrawal_relays([geom.s_block(ln.c, ln.r, vol) for ln in lanes],
                                       [ln.X0+geom.S_DX for ln in lanes])
            self.assertEqual([r.attrib for r in command],
                             [E.fromstring(r.xml(i)).attrib for i, r in enumerate(relays, 1)])
            self.assertEqual(command.get('Timeout'), str(len(relays)))
            self.assertEqual(command.get('Params'), 'SCRIPTCYCLE_FALSE')


if __name__ == '__main__':unittest.main()
