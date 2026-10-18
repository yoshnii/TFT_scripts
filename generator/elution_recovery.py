"""Manual aspiration envelopes: withdraw electrodes, not simulated liquid mass.

Reference: ASI96 260828_5and8 Flow2389 Command2390/2391 and
260902_96sampleV2_1 Flow2329 Command2330..2341. Adapt their withdrawal
principle to the SIRO S holes; do not copy the reference chip coordinates.
"""
import math

import geom
from model import Cmd
from scene import Relay


def withdrawal_path(rect, hole_x):
    """Approved two-stage aspiration: edge withdrawal, then a rigid 6x4 push.

    Keep the upper edge and one-grid hole clearance. Withdraw the bottom row,
    then the right edge to hole_x+6, then the left edge to hole_x. Move the
    resulting 6x4 block left three single-cell steps to center it under S.
    None is the final electrode-off frame, not aspiration sensor feedback.
    """
    x, y, w, h = map(int, rect)
    assert h == 5 and w in (12, 20) and x <= hole_x and x+w >= hole_x+6
    result = [(x, y, w, h)]
    while h > 4:
        h -= 1
        result.append((x, y, w, h))
    while x+w > hole_x+6:
        w -= 1
        result.append((x, y, w, h))
    while x < hole_x:
        x += 1
        w -= 1
        result.append((x, y, w, h))
    assert (x, w, h) == (hole_x, 6, 4)
    for _ in range(3):
        x -= 1
        result.append((x, y, w, h))
    result.append(None)
    return result


def withdrawal_relays(rects, hole_xs, retained_codes=(), duration_ms=geom.STEP_MS,
                      amp=geom.VOLT):
    """All wells withdraw together; unrelated electrode codes stay on."""
    assert len(rects) == len(hole_xs) and rects
    paths = [withdrawal_path(rc, hx) for rc, hx in zip(rects, hole_xs)]
    assert len({len(path) for path in paths}) == 1, '本回收批次的轨迹长度必须一致'
    retained_codes = list(retained_codes)
    return [Relay(retained_codes+[geom.emit(rc) for rc in frame if rc is not None],
                  duration_ms, amp) for frame in zip(*paths)]


def recovery_command(scene, lanes, product_vol, round_no):
    """Only elution recovery uses decreasing masks; no partial-mass fiction."""
    assert scene.manual_aspirations is not None
    names = [f'P{ln.id}' for ln in lanes]
    products = [scene.get(name) for name in names]
    rects = [d.rect for d in products]
    for ln, d in zip(lanes, products):
        assert abs(d.vol-product_vol) < 1e-8
        assert d.rect == geom.s_block(ln.c, ln.r, product_vol)
        assert len(d.rects()) == 1 and geom.reaction_holes_ok(d.rect, 1)
    retained = [geom.emit(rc) for name, d in scene.drops.items()
                if name not in names for rc in d.rects()]
    relays = withdrawal_relays(rects, [ln.X0+geom.S_DX for ln in lanes], retained)
    info = dict(hole=f'S孔×{len(lanes)}', volume_ul=round(product_vol*len(lanes), 2),
                per_lane_ul=product_vol, drop_names=names,
                rects=[list(rc) for rc in rects], kind='逐道洗脱产物',
                progressive_recovery=True, start_frame=1,
                end_frame=len(relays), final_recovery_electrodes=[],
                operator_action='运行本指令期间配合手工吸液，末帧回收电极撤除')
    command = Cmd(f'第{round_no}次产物S孔回收 · 运行期间配合手工吸液：'
                  '逐侧撤电至6×4→保持6×4左移三格至S孔下→末帧撤电（磁铁保持升起）',
                  'HighVoltage', 'CMD_SCRIPTCYCLE',
                  math.ceil(sum(r.dur for r in relays)/1000),
                  'SCRIPTCYCLE_FALSE', relays)
    command.aspiration_gates = [(len(relays)-1, info)]
    scene.manual_aspirations.append((relays[-1], info))
    # The external, manual aspiration is accounted once, only at completion.
    for name in names:
        scene.remove(name)
    scene.nsteps += len(relays)
    return command
