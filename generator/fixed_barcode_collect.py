"""B07 only: collect unchanged 3x4 packets at a stationary F1 footprint."""
import contour
import geom
import rigid as R
from scene import Drop

BOX = (48, 172, 12, 16)


class FixedBarcodeCollection(Drop):
    def validate_fixed_collection(self):
        cells = self.target_cells()
        assert self.name == 'BLOB1' and self.vol > 0
        assert cells <= contour.fill(BOX, 192)
        assert self.collected_cells <= cells, 'Previously collected liquid lost power or moved'
        assert self.rect == contour.bounds(cells)
        self.collected_cells = cells


def accepts(pieces, plan):
    return (plan.get('packed') and plan['ports'] == [1] and len(pieces) == 16
            and all(d.name.startswith('BKWA') and d.rect[2:] == (3, 4)
                    and abs(d.vol - .6) < 1e-8 for _, d in pieces)
            and all(s.get('strict_staging') for s in plan['specs'].values()))


def send(S, pieces, gx, blobs, plan):
    import waste_grid as W
    assert gx == 46 and not blobs.get(1)
    W._pool(S)
    pending = sorted(pieces, key=lambda item: -item[0].r)
    state = {'entry': 0}
    staged = []

    def stage(d, ticket, x, y):
        yield from R.go(d, [('x', gx), ('y', 164)])
        while state['entry'] != ticket:
            yield 'wait'
        yield from R.go(d, [('y', 172)])
        released = False
        for action in R.go(d, [('x', x)]):
            yield action
            if not released and d.rect[0] >= gx + geom.CH_W + geom.MIN_GAP:
                state['entry'] += 1
                released = True
        assert released
        yield from R.go(d, [('y', y)])

    jobs = []
    for ticket, (_, d) in enumerate(pending):
        x, y = 68 + 7 * (7 - ticket % 8), 180 if ticket < 8 else 172
        d.S = S
        d.native_packet = d.waste_grid_packet = d.waste_pipeline_clearance = True
        d.allow = ()
        staged.append(d)
        jobs.append((d, stage(d, ticket, x, y)))
    R.run(S, jobs, rule=R._door_rule(gx))

    host_state = {'host': None, 'turn': 0}
    S.waste_grid_events = getattr(S, 'waste_grid_events', [])
    S.fixed_barcode_events = []
    # Clear the upper staging row first so lower-row packets can use the spine.
    staged.sort(key=lambda d: (d.rect[1], d.rect[0]))
    # Build each row left-to-right: an arriving packet never touches a filled
    # right-hand slot and then pulls away before reaching its own slot.
    slots = [(x, y) for y in (184, 180, 176, 172) for x in (48, 51, 54, 57)]
    def collect(d, index, x, y):
        # Small packets advance together; only the final docking order is serial.
        yield from R.go(d, [('x', 63)])
        while index >= 8 and host_state['turn'] < 8:
            yield 'wait'
        yield from R.go(d, [('y', 172)])
        while host_state['turn'] != index:
            yield 'wait'
        host = host_state['host']
        d.allow = ('BLOB1',) if host else ()
        yield from R.go(d, [('x', x), ('y', y)])
        cells = d.target_cells() | (host.target_cells() if host else frozenset())
        assert len(cells) == 12 * (index + 1) and contour.connected(cells)
        S.remove(d.name)
        if host is None:
            host = S.add(FixedBarcodeCollection('BLOB1', contour.bounds(cells), .6))
            host.collected_cells = frozenset()
        host.vol = round((index + 1) * .6, 10)
        host.rect = contour.bounds(cells)
        host.electrode_area = len(cells)
        host.margin_budget = 0
        host.set_cells(cells)
        host.tile_count = index + 1
        host.tile_shape, host.tile_area = (3, 4), 12
        host_state['host'] = host
        host_state['turn'] += 1
        S.fixed_barcode_events.append(dict(packet=index + 1, frame=S.nsteps,
            actual_volume_ul=host.vol, cells=len(cells), fixed_box=list(BOX)))
        S.waste_grid_events.append(dict(port=1, tile_shape=[3, 4], slot=index,
            merged_without_reshape=True, stationary_collection=True))
    R.run(S, [(d, collect(d, i, x, y)) for i, (d, (x, y)) in enumerate(zip(staged, slots))])
    host = host_state['host']
    assert host.rect == BOX and host.target_cells() == contour.fill(BOX, 192)
    blobs[1] = host
    return [d for _, d in pending]
