"""Only F-hole aspiration tails; liquid routing and pooling stay unchanged."""
import re

import geom
from scene import Relay, _gap


def boxes(code):
    return [(int(x), int(y), int(w), int(h)) for y, x, h, w in
            re.findall(r'\((\d+),(\d+)\)\((\d+),(\d+)\)', code)]


def waste_path(rect, hole_x):
    x, y, w, h = rect
    assert (w, h) in ((16, 16), (12, 16)) and y == 172
    assert x == hole_x-w//2
    target_w = 8 if w == 16 else 6
    path = [rect]
    while y+h < 192:
        y += 1
        path.append((x, y, w, h))
    while h > 6:
        y += 1
        h -= 1
        path.append((x, y, w, h))
    while w > target_w:
        x += 1
        w -= 1
        path.append((x, y, w, h))
        if w > target_w:
            w -= 1
            path.append((x, y, w, h))
    assert path[-1] == (hole_x-target_w//2, 186, target_w, 6)
    return path


def waste_relays(rects, hole_xs, retained_codes=()):
    paths = [waste_path(rc, hx) for rc, hx in zip(rects, hole_xs)]
    assert paths and len({len(p) for p in paths}) == 1
    retained_codes = list(retained_codes)
    retained = [rc for code in retained_codes for rc in boxes(code)]
    relays = []
    for i, frame in enumerate(zip(*paths)):
        for j, rc in enumerate(frame):
            x, y, w, h = rc
            assert 0 <= x < x+w <= 256 and 0 <= y < y+h <= 192
            target_hole = (hole_xs[j]-1,189.5,2,2)
            assert all(geom.well_gap(rc,hole) >= 1 for hole in
                       geom.REACTION_WELLS+geom.EDGE_WELLS if hole != target_hole)
            if i >= 4:
                assert x <= target_hole[0] and x+w >= target_hole[0]+2
                assert y <= 189.5 and y+h >= 191.5
            for other in list(frame[j+1:])+retained:
                assert _gap(rc, other) >= geom.MIN_GAP, ('孔旁收尾碰撞', rc, other)
        relays.append(Relay(retained_codes+[geom.emit(rc) for rc in frame],
                            1000 if i < 4 else 3000, geom.VOLT))
    relays.append(Relay(list(retained_codes),1000,geom.VOLT))
    return relays


def append_waste_tail(scene, items):
    """External suction permits shrinking masks, not ordinary shape changes.

    Scene's ordinary one-grid clearance and area rules remain untouched.
    First frame is the existing pooled wait mask, already recorded by flush.
    """
    names = {d.name for k, d in items}
    retained = [geom.emit(rc) for name, d in scene.drops.items()
                if name not in names for rc in d.rects()]
    relays = waste_relays([d.rect for k, d in items],
                          [geom.HOLE_X[k] for k, d in items], retained)
    assert set(scene.relays[-1].codes) == set(relays[0].codes)
    scene.relays.extend(relays[1:])
    scene.nsteps += len(relays)-1
    return []  # 孔口已撤电；其他仍在片液滴的电极由 retained 保留。
