"""计时/循环/依赖图离线回归；不证明执行器整轮停机、温度达到或液体行为。"""
import contextlib
import io
import math
import unittest
import xml.etree.ElementTree as ET

import geom
import ops
import protocol
from model import Cmd, Flow, Script
from scene import Drop, Relay


def seeded(volume=2.0, cols=(1,), offset=0):
    p = protocol.Protocol(cols)
    w, h = geom.lane_shape(volume)
    for lane in p.ALL:
        y=geom.physical_zone(lane.c,lane.r)[1]+1 if geom.running_gap(volume)==1 else lane.Y0
        p.S.add(Drop(lane.name, (lane.X0 + offset, y, w, h), volume))
    p.S.step(changed=[])
    p.S.flush()
    return p


def loop(flow):
    return next(c for c in flow.cmds if hasattr(c, 'mix_timing'))


class RevisionTimingTests(unittest.TestCase):
    def test_flow_preserves_parallel_graph_and_counts_critical_path(self):
        start = Cmd('定位', 'HighVoltage', 'CMD_SCRIPTCYCLE', 3)
        hv = Cmd('单轮', 'HighVoltage', 'CMD_SCRIPTCYCLE', 55)
        delay = Cmd('计时', 'Delay', 'CMD_DELAY', 1800)
        hv.pre, delay.pre = (start, 'FS'), (hv, 'SS')
        f = Flow('并行混匀', [start, hv, delay], sync=[delay, hv], chain_commands=False)
        script = Script('test', [f], 'test', 1000)
        root = ET.fromstring(script.xml())
        commands = root.find('Flow').findall('Command')
        self.assertEqual(commands[-1].get('PreTask'), f'{hv.id},SS')
        self.assertEqual(f.timeout, 1803)
        self.assertEqual(root.find('Flow').get('SyncEndingCmdList'), f'{delay.id},{hv.id}')
        serial = Flow('串联', [Cmd('a', '', '', 3), Cmd('b', '', '', 4)])
        self.assertEqual(serial.timeout, 7)

    def test_a03_preserves_1800_seconds_with_closed_single_round(self):
        p = seeded(offset=8)
        p.mix('A', 'A03 往复混匀', seconds=1800)
        self.assertEqual(len(p.segs['A']), 1)
        f = p.segs['A'][0]
        position, hv, timer = f.cmds
        lane = p.ALL[0]
        span = geom.LANE_L - p.S.get(lane.name).rect[2]
        self.assertEqual(hv.params, 'SCRIPTCYCLE_TRUE')
        self.assertEqual(len(hv.relays), 2 * span + 1)
        self.assertEqual(hv.relays[0].codes, hv.relays[-1].codes)
        self.assertEqual(position.relays[-1].codes, hv.relays[0].codes)
        self.assertEqual(timer.timeout, 1800)
        self.assertEqual(timer.pre, (hv, 'SS'))
        self.assertEqual(hv.pre, (position, 'FS'))
        self.assertEqual(f.sync, [timer, hv])
        self.assertEqual(f.timeout, position.timeout + 1800)
        self.assertTrue(all(p.S.get(l.name).rect[0] == l.X0 for l in p.ALL))
        self.assertTrue(all(r.codes and r.amp > 0 for c in f.cmds for r in c.relays))

    def test_intended_cycles_change_timer_without_expanding_trace(self):
        traces = []
        for cycles in (2, 8, 12, 20):
            p = seeded()
            p.mix('B', 'B06 混匀', cycles=cycles)
            f = p.segs['B'][0]
            hv = loop(f)
            traces.append([(r.codes, r.dur, r.amp) for r in hv.relays])
            self.assertEqual(f.cmds[-1].timeout, cycles * 42)
            self.assertEqual(hv.mix_timing['intended_cycles'], cycles)
            self.assertFalse(hv.mix_timing['hardware_stop_verified'])
        self.assertTrue(all(trace == traces[0] for trace in traces))

    def test_nonresident_mix_keeps_other_liquid_powered_and_state_closed(self):
        p = seeded(volume=.3, offset=28)
        for l in p.ALL:
            p.S.add(Drop(f'Q{l.id}', (l.X0 + 5, l.Y0, 2, 3), .3))
        p.mix('Q', 'Q03 混匀', spec={f'Q{l.id}': (l.X0 + 5, l.X0 + 14) for l in p.ALL})
        hv = loop(p.segs['Q'][0])
        self.assertEqual(len(hv.relays), 19)
        self.assertEqual(hv.relays[0].codes, hv.relays[-1].codes)
        self.assertTrue(all(len(r.codes) == 32 for r in hv.relays))
        self.assertTrue(all(p.S.get(l.name).rect[0] == l.X0 + 28 for l in p.ALL))

    def test_static_holds_are_single_frames_and_manual_gates_survive(self):
        p = seeded()
        held = p.hold('磁吸等待', 180)
        self.assertEqual([r.dur for r in held.relays], [180000])
        ops.hold_all(p.S, 120)
        compact = p.cmd('旧动作层的静态保持')
        self.assertEqual([r.dur for r in compact.relays], [120000])
        p.S.step(1000, changed=[])
        gate = p.S.relays[-1]
        p.S.manual_aspirations.append((gate, {'hole': 'S', 'volume_ul': 32}))
        p.S.step(1000, changed=[])
        c = p.cmd('手工边界')
        self.assertEqual(len(c.relays), 2)
        self.assertIs(c.relays[0], gate)
        self.assertEqual(c.aspiration_gates[0][0], 0)

    def test_a04_is_compact_heater_driven_continuous_hold(self):
        p = seeded()
        p.thermal('A', 'A04 cDNA合成', [(37, 600), (85, 5)], lid=105)
        f = p.segs['A'][0]
        hv = f.cmds[-1]
        self.assertEqual(len(hv.relays), 1)
        self.assertEqual(hv.relays[0].dur, geom.LONG_MS)
        self.assertGreater(hv.relays[0].amp, 0)
        self.assertEqual(hv.params, 'SCRIPTCYCLE_TRUE')
        self.assertEqual([c.params for c in f.cmds if c.comp == 'Heater1'], ['37,1', '85,1', '4,1'])
        self.assertEqual([c.timeout for c in f.cmds if c.comp == 'Delay'], [600, 5, 30])
        self.assertEqual(hv.pre, (next(c for c in f.cmds if c.comp == 'Heater1'), 'SS'))

    def test_pcr_exception_alternates_1000_off_on_then_restores_in_a10(self):
        p = seeded()
        ops.park_all(p.S, p.ALL, 'tec_center')
        p.S.flush()
        p.thermal('A', 'A09 循环', [(95, 30), (60, 60), (72, 45)], repeats=30,
                  final_4c=False, park=False, pulse=True)
        self.assertEqual(len(p.segs['A']), 1)
        p.thermal('A', 'A10 终延伸', [(72, 60)], park=False)
        f, restore = p.segs['A']
        hv = f.cmds[-1]
        self.assertEqual(f.repeats, 30)
        self.assertEqual(f.timeout, 315)
        self.assertEqual([r.dur for r in hv.relays], [1000, 1000])
        self.assertEqual([r.amp for r in hv.relays], [0, geom.HOLD_VOLT])
        self.assertEqual(hv.relays[0].codes, hv.relays[1].codes)
        self.assertEqual(hv.params, 'SCRIPTCYCLE_TRUE')
        self.assertIn(hv, f.sync)
        self.assertEqual([c.params for c in f.cmds if c.comp == 'Heater1'], ['95,1', '60,1', '72,1'])
        self.assertEqual([c.timeout for c in f.cmds if c.comp == 'Delay'], [30, 60, 45])
        self.assertEqual(restore.repeats, 1)
        self.assertTrue(restore.name.startswith('A10 '))
        self.assertIn('恢复', restore.cmds[0].name)
        self.assertGreater(restore.cmds[0].relays[-1].amp, 0)
        self.assertEqual(restore.cmds[0].relays[-1].codes, hv.relays[-1].codes)
        with self.assertRaisesRegex(AssertionError, '只有A09'):
            p.thermal('A', 'A08 预变性', [(95, 180)], pulse=True)

    def test_parallel_heaters_are_not_rechained_by_flow_constructor(self):
        p = seeded(cols=(1, 2))
        p.thermal('B', 'B04 末端修复', [(37, 240)])
        f = p.segs['B'][0]
        first = [next(c for c in f.cmds if c.comp == f'Heater{col}') for col in (1, 2)]
        self.assertEqual(first[0].pre, first[1].pre)
        self.assertEqual(f.timeout, first[0].pre[0].timeout + 390)

    def test_bead_binding_keeps_incubation_and_capture_outside_loop(self):
        for seg, vol, cycles, incubation, capture in (
                ('R1', 5.0, 20, 300, 180), ('C', 5.9, 12, 180, 120)):
            with self.subTest(seg=seg):
                p = seeded(vol)
                p.bead_capture(seg, '结合磁吸', cycles, 19)
                mixing, stationary = p.segs[seg]
                hv = loop(mixing)
                self.assertEqual(hv.mix_timing['intended_cycles'], cycles)
                waits = [c for c in stationary.cmds if len(c.relays) == 1
                         and c.relays[0].dur >= 120000]
                self.assertEqual([c.relays[0].dur for c in waits], [incubation*1000, capture*1000])
                self.assertFalse(stationary.sync)
                self.assertEqual([e['target'] for e in p.magnet_events], ['down', 'up'])


class CompleteProtocolTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = protocol.Protocol((1,))
        with contextlib.redirect_stderr(io.StringIO()):
            cls.segs = cls.p.build()
        cls.flows = [f for flows in cls.segs.values() for f in flows]

    def test_all_mixing_is_compact_closed_parallel_timed_and_positioned(self):
        mixing = [f for f in self.flows if any(hasattr(c, 'mix_timing') for c in f.cmds)]
        self.assertGreaterEqual(len(mixing), 18)
        for f in mixing:
            with self.subTest(flow=f.name):
                hv = loop(f)
                timer = f.cmds[-1]
                self.assertEqual(hv.params, 'SCRIPTCYCLE_TRUE')
                self.assertEqual(timer.comp, 'Delay')
                self.assertEqual(timer.pre, (hv, 'SS'))
                self.assertEqual(f.sync, [timer, hv])
                self.assertEqual(hv.relays[0].codes, hv.relays[-1].codes)
                self.assertLessEqual(len(hv.relays), 2*geom.LANE_L+1)
                self.assertTrue(all(r.amp > 0 for r in hv.relays))
                self.assertIn('左端定位', hv.pre[0].name)
        for prefix in ('A03 ', 'A07 ', 'B03 ', 'B08 '):
            matches = [f for f in self.flows if f.name.startswith(prefix)]
            self.assertEqual(len(matches), 1)
            self.assertTrue(hasattr(loop(matches[0]), 'mix_timing'))
        a03 = next(f for f in mixing if f.name.startswith('A03 '))
        self.assertEqual(a03.cmds[-1].timeout, 1800)

    def test_only_a09_contains_power_off_and_a10_starts_with_recovery(self):
        off = [(f, c, r) for f in self.flows for c in f.cmds for r in c.relays if r.amp == 0]
        self.assertEqual(len(off), 1)
        f, _, _ = off[0]
        self.assertTrue(f.name.startswith('A09 循环 '))
        self.assertEqual(f.repeats, 30)
        following = self.flows[self.flows.index(f) + 1]
        self.assertTrue(following.name.startswith('A10 '))
        self.assertIn('恢复', following.cmds[0].name)
        self.assertTrue(all(r.codes and r.amp > 0 for c in following.cmds for r in c.relays))
        thermal = [f for f in self.flows if any(c.action == 'CMD_TEMP_ON' for c in f.cmds)]
        self.assertEqual(len(thermal), 6)
        self.assertTrue(all(len(f.cmds[-1].relays) == 1 for f in thermal if f is not off[0][0]))

    def test_b05_loads_120ul_same_premix_and_retains_060ul_per_lane(self):
        gate = next(c for f in self.flows if f.name.startswith('B05 ') for c in f.cmds
                    if hasattr(c, 'manual_load'))
        self.assertEqual(gate.manual_load['per_lane_ul'], 1.2)
        self.assertEqual(gate.manual_load['volume_ul'], 1.2)
        self.assertIn('19.20µL', gate.manual_load['lanes'])
        self.assertIn('非稀释', gate.manual_load['reagent'])
        names = [c.name for f in self.flows if f.name.startswith('B05 ') for c in f.cmds]
        self.assertTrue(any('0.60µL保留、0.60µL弃液' in n for n in names))
        event = next(e for e in self.p.S.reaction_merge_events if e['guest_tag'] == 'K')
        self.assertTrue(all(v == 2 for v in event['host_volumes'].values()))

    def test_cleanup_only_c06_changes_to_two_rounds_with_48ul_carrier(self):
        self.assertEqual([(e['stage'], e['mix_cycles'], e['total_ul'])
                          for e in self.p.bead_cleanup_events], [('R1', 12, 48), ('C', 2, 48)])
        c = next(f for f in self.flows if f.name.startswith('C06 ') and '磁珠清理重悬' in f.name)
        hv = loop(c)
        self.assertEqual(hv.mix_timing['intended_cycles'], 2)
        self.assertEqual(c.cmds[-1].timeout, math.ceil(2*sum(r.dur for r in hv.relays)/1000))
        self.assertFalse(self.p.S.drops)
        self.assertEqual(self.p.S.manual_aspirations[-1][1]['volume_ul'], 48)

    def test_elution_windows_preserve_nominal_300_and_180_seconds(self):
        for e in self.p.elution_events:
            self.assertEqual(e['initial_mix_cycles'], 12)
            self.assertEqual(e['incubation_seconds'], 300 if e['stage'] == 'R1' else 180)
            if e['stage'] == 'R1':
                self.assertEqual([p['start_seconds'] for p in e['periodic_mixing']], [120, 240])
                self.assertTrue(all(p['end_seconds'] <= 300 for p in e['periodic_mixing']))
                prefix = 'R05 ' if e['round'] == 1 else 'R06 '
                waits = [c.timeout for f in self.flows if f.name.startswith(prefix)
                         for c in f.cmds if c.name.startswith(f"第{e['round']}次：洗脱孵育剩余等待")]
                shaking = [f.timeout for f in self.flows if f.name.startswith(prefix) and '轻晃' in f.name]
                self.assertEqual(sum(waits) + sum(shaking), 300)

    def test_serialized_ids_dependencies_and_sync_references_are_unique(self):
        scripts = [Script(seg, flows, 'test', 1000*(i+1)) for i, (seg, flows) in enumerate(self.segs.items())]
        combined = Script.combine('test', scripts)
        root = ET.fromstring(combined.xml())
        ids = [e.get('ID') for e in root.iter() if e.tag in ('Flow', 'Command')]
        self.assertEqual(len(ids), len(set(ids)))
        for f in root.findall('Flow'):
            local = {c.get('ID') for c in f}
            for c in f:
                before = c.get('PreTask').split(',')[0]
                self.assertTrue(before == '-1' or before in local)
            self.assertTrue(set(filter(None, f.get('SyncEndingCmdList', '').split(','))) <= local)
        self.assertNotIn('60004', ids)  # Timer使用Script常规分配；60000段留给主代理的手工边界分配。


if __name__ == '__main__':
    unittest.main()
