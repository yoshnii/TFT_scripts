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
    """Nested complete rectangles; each edge retreats at most one cell/frame.

    Keep the original upper edge and one-grid hole clearance. First remove
    bottom rows down to three, then retract both sides towards the S hole.
    The final None means collection is complete and this envelope is off.
    """
    x, y, w, h = map(int, rect)
    assert w > 0 and h > 0 and x <= hole_x < x+w
    result = [(x, y, w, h)]
    while h > 3:
        h -= 1
        result.append((x, y, w, h))
    while w > 2:
        left = int(x < hole_x)
        right = int(x+w-1 > hole_x)
        assert left or right
        x += left
        w -= left+right
        result.append((x, y, w, h))
    while h > 1:
        h -= 1
        result.append((x, y, w, h))
    if w > 1:
        x = hole_x
        w = 1
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
                  '下侧逐行撤电极→左右收窄→末帧撤除回收电极（磁铁保持升起）',
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
