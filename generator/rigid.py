"""刚体液珠模型 (单列版, 2026-09-15, 按用户规则):
  · 每个通电方块上都有液体: 液滴 = 一个矩形, 面积 ≈ 体积 (20 格/µL)。
  · 移动 = 矩形整体平移 (每步 1 格), 形状不变; 形变 = 原地换成另一个与旧矩形重叠的矩形; 二者不混在一步里。
  · 切分: 母滴实际格数 = 两个子滴格数之和；两半交替每帧外移1格，间隙逐帧增至4格。
  · 经用户允许的连通轮廓逐格等面积形变；不允许液柱/贪吃蛇。
参考: PCR-free WGS `4μl一拆8 → 移动到门口 → 进反应区`, ASI96 `分液 → 分液移动 → 移动到N列`, `分离 → 移液 → 排废液`。"""
import math, geom
from scene import Drop, _gap, _overlap
import ops
from ops import Lane, hold_all, move_rects, BLOB, T_DISP, T_ASP, CUT_MS, MORPH_MS, SLOT_X0, BAND_YC, SLUG_MAX_UL, _blob_rect, _aspirate

DIRS = {"+X": (1, 0), "-X": (-1, 0), "+Y": (0, 1), "-Y": (0, -1)}

# ── 基本动作 ───────────────────────────────────────────────────────────
def in_reaction(r):
    x, y, w, h = r
    row = (y - 27) // 9 + 1
    if not (1 <= row <= 16):
        return False
    for col in range(1, 7):
        x0, y0 = geom.zone(col, row)
        # 行内和门口的整高矩形受9格行距约束，最多留3格。
        # 完整进入通道后纵向错开即可留4格；不可在转移前要求同一行距留4格。
        if (x0-geom.CH_W <= x and x+w <= x0+geom.LANE_L+geom.CH_W
                and y0 <= y and y+h <= y0+geom.LANE_H):
            return True
    return False


def rect_ok(S, name, r, allow=frozenset()):
    """r 在片内, 且与其他液滴 (allow 里的除外) 间距 ≥ MIN_GAP。"""
    if not (0 <= r[0] and r[0] + r[2] <= geom.GRID_W and 0 <= r[1] and r[1] + r[3] <= geom.GRID_H): return False
    for n2, d2 in S.drops.items():
        if n2 == name or n2 in allow: continue
        for q in d2.rects():
            row_limited = (in_reaction(r) and in_reaction(q)
                           and (r[1]-27)//9 != (q[1]-27)//9)
            bottom_limited=(171<=r[1] and r[1]+r[3]<=174) or (171<=q[1] and q[1]+q[3]<=174)
            waste_queue=(getattr(S.get(name),'waste_grid_packet',False) and getattr(d2,'waste_grid_packet',False))
            waste_dock=(getattr(S.get(name),'waste_pipeline_clearance',False) and d2.name.startswith('BLOB'))
            entry_limited=(getattr(S.get(name),'controlled_entry',False) and r[1]>=26)
            clearance = geom.MIN_GAP if row_limited or bottom_limited or waste_queue or waste_dock or entry_limited else geom.PREFERRED_GAP
            if _overlap(r, q) or _gap(r, q) < clearance: return False
    return True

def reshape(S, d, rect, dur=MORPH_MS, touching=()):
    """独立小幅形变：长宽协同调整，不先扩成包络大框再收回。"""
    import contour
    target=contour.fill(rect,geom.cells(d.vol) if d.vol else len(d.target_cells()))
    if not d.target_cells()&target:
        # 形变不能兼任跨空白区搬运；先保持轮廓逐格平移到目标附近。
        run(S,[(d,go(d,[("y",rect[1])]))],allow=frozenset(getattr(d,'allow',())))
        if not d.target_cells()&target:
            run(S,[(d,go(d,[("x",rect[0])]))],allow=frozenset(getattr(d,'allow',())))
    reshape_many(S,[(d,rect)],dur,touching)

def reshape_plan(d,rect):
        # 电极格数由Drop计划液量决定；Scene以连通轮廓逐格交换，矩形只是目标包围框。
        assert rect[2]*rect[3]>=geom.cells(d.vol),'目标包围框不足以承载计划液量'
        if d.rect!=tuple(rect):yield tuple(rect)
def reshape_many(S,specs,dur=MORPH_MS,touching=()):
    active=[(d,reshape_plan(d,rc)) for d,rc in specs]
    while active:
        changed=[];remaining=[]
        for d,g in active:
            try:d.rect=next(g);changed.append(d.name);remaining.append((d,g))
            except StopIteration:pass
        active=remaining
        if changed:S.step(dur,changed=changed,touching=touching)

def piece_shape(cells):
    """能走空隙 (宽 ≤ 4) 也能待在泳道 (高 ≤ 6) 的紧凑矩形, 面积 ≥ cells。"""
    w = 4 if cells > 6 else 2; h = max(1, math.ceil(cells / w))
    if h > 6: w, h = 4, math.ceil(cells / 4)          # 只能在空隙/空白带里用的高块
    return w, h

def tick(S, items, rule=None, allow=frozenset(), dur=geom.STEP_MS):
    """items: [(drop, gen)] 按优先级。gen 产出 "+X"/"-X"/"+Y"/"-Y" (平移 1 格) 或 ("reshape", rect) (原地形变)。
    每步: 每个液滴至多做一个动作; 目标矩形与其他液滴距离 < MIN_GAP 或 rule 不允许就原地等。全部液滴每步都通电。"""
    frame_dur=dur
    for d, g in items:
        if getattr(d, "pending", None) is None:
            try: d.pending = next(g)
            except StopIteration: d.pending = "done"
        if d.pending == "done": continue
        act = d.pending
        if act == 'wait':
            d.pending=None
            continue
        if isinstance(act, tuple): cand = tuple(act[1])
        else: dx, dy = DIRS[act]; cand = (d.rect[0] + dx, d.rect[1] + dy, d.rect[2], d.rect[3])
        al = allow | set(getattr(d, "allow", ()))
        if rect_ok(S, d.name, cand, al) and (rule is None or rule(d, cand)):
            if getattr(S,'pipeline_active',False) and not getattr(d,'transport_started',False):
                d.transport_started=True
                S.pipeline_events.append(dict(kind='transport_start',port=d.port,row=d.row,step=S.nsteps))
            if isinstance(act, tuple): assert _overlap(d.rect, cand), f"形变不连续 {d.name}"
            if isinstance(act,tuple) and act[0]=='merge':
                host=S.get(act[2]);old_name=d.name
                S.remove(old_name);S.remove(host.name)
                d.name=host.name;d.vol=round(d.vol+host.vol,2);d.allow=()
                S.add(d)
                S.pipeline_events.append(dict(kind='reagent_collect',port=d.port,row=d.row,
                                              incoming_ul=act[3],total_ul=d.vol,step=S.nsteps))
            if isinstance(act,tuple):
                frame_dur=max(frame_dur,act[2] if act[0]=='hold' else MORPH_MS)
            d.rect = cand; d.pending = None; S.moved = getattr(S, "moved", 0) + 1
        elif getattr(d,'dynamic_path',False):
            # 汇液框合并后边界会逐格移动；重新求停靠间距，不锁住旧目标。
            d.pending=None
    S.step(frame_dur, changed=[d.name for d, _ in items], touching=[(d.name, a) for d, _ in items for a in getattr(d, "allow", ())])
    S.stall = 0 if getattr(S, "moved", 0) else getattr(S, "stall", 0) + 1; S.moved = 0
    if S.stall > 60:
        info = [(d.name, d.pending, d.rect) for d, _ in items if d.pending not in (None, "done")]
        raise RuntimeError(f"排队死锁 @step{S.nsteps}: " + "; ".join(f"{n} 想{a} 在{r}" for n, a, r in info[:6]))

def run(S, items, rule=None, allow=frozenset(), on_done=None):
    """跑到所有液滴的 gen 结束。on_done(drop) 在某液滴结束时调用一次 (如并入大滴)。"""
    items = list(items); finished = set()
    while any(getattr(d, "pending", None) != "done" for d, _ in items):
        tick(S, items, rule, allow)
        for d, g in items:
            if d.pending == "done" and d.name not in finished:
                finished.add(d.name)
                if on_done: on_done(d)
    for d, _ in items: d.pending = None

def go(d, targets):
    """产出把 d.rect 左上角平移到 targets=[("x", X) | ("y", Y)] 的方向序列。"""
    for axis, val in targets:
        while True:
            cur = d.rect[0] if axis == "x" else d.rect[1]
            if cur == val: break
            yield ("+X" if val > cur else "-X") if axis == "x" else ("+Y" if val > cur else "-Y")

def split_many(S, specs, axis='x', offset=None, dur=CUT_MS, touching=()):
    """先就地等面积分区，再交替移动两半；每帧间隙最多增加一格。

    specs: (母滴, 最终两框, 两液量, 两名称[, 就地两轮廓])。
    同级所有母滴同步拆分；分离期间仅豁免同母滴子滴的短间距。
    """
    import contour
    offset=geom.CUT_GAP//2 if offset is None else offset
    assert axis in ('x','y') and offset>0
    coordinate=0 if axis=='x' else 1
    prepared=[];morphs=[]
    for spec in specs:
        parent,finals,vols,names,*custom=spec
        starts=[]
        for i,rc in enumerate(finals):
            rc=list(rc);rc[coordinate]+=offset if i==0 else -offset;starts.append(tuple(rc))
        footprints=tuple(map(frozenset,custom[0])) if custom else tuple(
            contour.fill(rc,geom.cells(v) if v>0 else rc[2]*rc[3]) for rc,v in zip(starts,vols))
        assert abs(sum(vols)-parent.vol)<1e-7,'切分液量不守恒'
        assert not footprints[0]&footprints[1],'切分子滴电极重叠'
        union=footprints[0]|footprints[1]
        assert len(union)==len(parent.target_cells()) and contour.connected(union),'切分分区不守恒或不连通'
        assert all(contour.connected(cells) for cells in footprints),'切分子滴不连通'
        x,y,w,h=parent.rect
        assert all(x<=a<x+w and y<=b<y+h for a,b in union),'切分起点不在母滴内'
        if union!=parent.target_cells():
            parent.set_cells(union);morphs.append(parent.name)
        prepared.append((parent,starts,vols,names,footprints))
    if morphs:S.step(MORPH_MS,changed=morphs,touching=touching)
    groups=[];pairs=list(touching);events=[]
    S.split_events=getattr(S,'split_events',[])
    for parent,starts,vols,names,footprints in prepared:
        assert parent.target_cells()==footprints[0]|footprints[1],'分区时改变母滴位置'
        S.remove(parent.name);children=[]
        for rc,v,n,cells in zip(starts,vols,names,footprints):
            child=S.add(Drop(n,rc,v));child.set_cells(cells)
            child.allow=tuple(getattr(parent,'allow',()))
            children.append(child)
            pairs.extend((child.name,a) for a in child.allow)
        pairs.append(tuple(d.name for d in children));groups.append(tuple(children))
        event=dict(parent=parent.name,children=list(names),axis=axis,
                   area=len(footprints[0]|footprints[1]),volumes=list(vols),
                   partition_frame=S.nsteps,gaps=[],positions=[],separation_frames=[],
                   partition_preserves_mother_cells=True,_parent_key=id(parent),
                   _child_keys=[id(d) for d in children])
        events.append(event);S.split_events.append(event)
    def gap(a,b):return min(_gap(r,q) for r in a.rects() for q in b.rects())
    def record_frame(duration,changed):
        S.step(duration,changed=changed,touching=pairs)
        for (a,b),event in zip(groups,events):
            assert not a.target_cells()&b.target_cells(),'分离时子滴重叠'
            g=gap(a,b)
            if event['gaps']:assert 0<=g-event['gaps'][-1]<=1,('切分间隙跳格',event['parent'],event['gaps'],g)
            event['gaps'].append(g);event['positions'].append([list(a.rect),list(b.rect)])
            event['separation_frames'].append(S.nsteps-1)
    record_frame(geom.HOLD_MS,[d.name for ab in groups for d in ab])
    for _ in range(offset):
        for side,sign in ((0,-1),(1,1)):
            changed=[]
            for children in groups:
                d=children[side];previous=d.target_cells();rc=list(d.rect);rc[coordinate]+=sign;d.rect=rc
                dx,dy=(sign,0) if axis=='x' else (0,sign)
                assert d.target_cells()==frozenset((x+dx,y+dy) for x,y in previous),'分离时改变子滴形状'
                changed.append(d.name)
            record_frame(dur if side==1 and _==offset-1 else geom.STEP_MS,changed)
    for children,spec in zip(groups,specs):
        assert tuple(d.rect for d in children)==tuple(map(tuple,spec[1])),'逐格分离未到预定终点'
    return groups


def cut_equal(S, d, left, right, vols=None, names=None, dur=CUT_MS):
    """等面积分区后逐帧拉开至 CUT_GAP。返回 (a, b)。"""
    assert left[2] * left[3] + right[2] * right[3] == d.rect[2] * d.rect[3], f"切分面积不守恒 {d.rect} → {left},{right}"
    assert _gap(left, right) == geom.CUT_GAP, f"切分后间距应为 {geom.CUT_GAP}: {left} {right}"
    names = names or (d.name + "a", d.name + "b")
    if vols is None:
        va = round(d.vol * (left[2] * left[3]) / (d.rect[2] * d.rect[3]), 3); vols = (va, round(d.vol - va, 3))
    return split_many(S,[(d,(left,right),vols,names)],dur=dur)[0]

def split_x(d, wl, wr):
    """母矩形沿 X 切，左右子块各外移 CUT_GAP/2 格。"""
    x, y, w, h = d.rect; assert wl + wr == w
    delta = geom.CUT_GAP // 2
    return (x - delta, y, wl, h), (x + wl + delta, y, wr, h)

# ── 分液: 顶部级联拆分 → 液珠排队进反应区 ─────────────────────────────
def plan_cascade(vc, n, wf, hf, band_h=20):
    """选槽距 p 和每级子块形状: 第 lev 级切出的子块 (wc×hc) 两两并排成母块 2wc×hc, 母块宽必须 ≤ 母块槽距 (2^(levels-lev)·p) − 2。"""
    levels = int(round(math.log2(n))) if n > 1 else 0
    for p in range(wf + geom.PREFERRED_GAP, 60):
        shapes, ok = [], True
        for lev in range(levels):
            if lev == levels - 1: wc, hc = wf, hf
            else:
                cells = vc * n / 2 ** (lev + 1); wmax = (2 ** (levels - lev) * p - geom.CUT_GAP - geom.PREFERRED_GAP) // 2
                h = max(2, min(band_h, round(math.sqrt(cells / 1.2)))); w = math.ceil(cells / h)
                if w > wmax: w = wmax; h = math.ceil(cells / w)
                if h > band_h or w < 2: ok = False; break
                wc, hc = w, h
            if 2 * wc + geom.CUT_GAP > 2 ** (levels - lev) * p - geom.PREFERRED_GAP: ok = False; break
            shapes.append((wc, hc))
        if ok and SLOT_X0 + (n - 1) * p + wf <= geom.GRID_W - 4: return p, shapes
    raise RuntimeError(f"级联放不下: cells/滴={vc}, n={n}")

def deliver_drops_legacy(S, reagent, vol, cols, tag, on_before_load=None, on_load=None):
    assert len(cols) == 1 and geom.odd(cols[0]), "刚体液珠版目前只支持单个奇数列"
    c = cols[0]; gx = geom.gap_x(geom.reagent_gap(c)); vc = geom.cells(vol)
    n_max = max(1, min(16, int(SLUG_MAX_UL // vol))); n_disp = 1 << int(math.floor(math.log2(n_max)))
    rows_all = list(range(16, 0, -1))
    transport_w, transport_h = piece_shape(vc)
    wf, hf = transport_w, transport_h
    if hf > 20:
        # 大体积在顶部先保持矮矩形，避开第一排；到入口上方后
        # 单独原地形变为4格宽的通道矩形，不边走边变形。
        wf = math.ceil(vc / 20); hf = math.ceil(vc / wf)
    for start in range(0, 16, n_disp):
        rows = rows_all[start:start + n_disp]; n = len(rows); levels = int(round(math.log2(n))) if n > 1 else 0
        p, shapes = plan_cascade(vc, n, wf, hf)                           # 同级液滴拆分/槽位间距均留4格
        def centroid(a, b): return (2 * SLOT_X0 + (a + b) * p + wf) / 2
        # 1) 承接方框
        k = geom.nearest_hole(gx); bx, by, W, H = geom.e_block(k, vol * n)
        if on_before_load: on_before_load()
        root = S.add(Drop(f"D{reagent}", (bx, by, W, H), vol * n)); S.step(geom.HOLD_MS, changed=[root.name])
        if on_load:
            on_load(dict(hole=f"E{k}", reagent=reagent, volume_ul=round(vol*n, 2),
                         per_lane_ul=vol, lanes=f"{'ABCDEF'[c-1]}{min(rows)}-{'ABCDEF'[c-1]}{max(rows)}",
                         batch=start//n_disp+1, batches=math.ceil(16/n_disp)))
            # 下一流程组先重发接液孔相切框和全部常驻液滴，再单独形变。
            S.step(geom.HOLD_MS, changed=[root.name])
        else:
            hold_all(S, T_DISP, geom.VOLT)
        # 2) 原地变成级联根形状 → 平移到级联中心
        w0, h0 = ops._shape(vc * n); reshape(S, root, (round(bx + W / 2 - w0 / 2), round(BAND_YC - h0 / 2), w0, h0))
        move_rects(S, {root.name: round(centroid(0, n - 1) - w0 / 2)})
        nodes = [(root, 0, n - 1)]
        for lev in range(levels):                                          # 3) 逐级等体积对半切 (同级同步)
            last = lev == levels - 1
            wc, hc = shapes[lev]
            for d, a, b in nodes:                                          # 原地变形成 2wc×hc (面积 = 两个子块)
                cx = centroid(a, b); d.rect = (round(cx - wc), round(BAND_YC - hc / 2), 2 * wc, hc)
            S.step(MORPH_MS, changed=[d.name for d, _, _ in nodes])
            new = [];specs=[];ranges=[]
            for i, (d, a, b) in enumerate(nodes):                          # 切: 两半各向外挪 CUT_GAP/2
                mid = (a + b + 1) // 2; l, r = split_x(d, wc, wc)
                half = round(d.vol / 2, 3)
                specs.append((d,(l,r),(half,round(d.vol-half,3)),(f"D{reagent}{lev}L{i}",f"D{reagent}{lev}R{i}")))
                ranges.append((a,mid,b))
            for (l,r),(a,mid,b) in zip(split_many(S,specs),ranges):new.extend([(l,a,mid-1),(r,mid,b)])
            nodes = new
            move_rects(S, {d.name: round(centroid(a, b) - wc / 2) for d, a, b in nodes})   # 分开到各自槽位 (平移)
        # 4) 排队进反应区: 槽位 j → rows[j]; 排头 (最左) 先走
        items = []
        for j, (d, a, b) in enumerate(sorted(nodes, key=lambda t: t[1])):
            ln = Lane(c, rows[j]); d.name_new = f"{tag}{ln.id}"
            S.drops.pop(d.name); d.name = d.name_new; S.drops[d.name] = d
            def path(d=d, ln=ln):
                yield from go(d, [("x", gx)])                                          # 沿空白带向左到空隙上方
                if d.rect[2] > geom.CH_W:
                    yield ("reshape", (gx, max(0, d.rect[1]), transport_w, transport_h))
                if d.rect[3] > geom.LANE_H:                                            # 高块: 下到门口, 原地变成泳道高、伸进泳道
                    yield from go(d, [("y", ln.Y0 - (d.rect[3] - geom.LANE_H) // 2)])
                    Lin = max(5, math.ceil(geom.cells(d.vol) / geom.LANE_H))
                    yield ("reshape", (gx, ln.Y0, Lin, geom.LANE_H))
                else:
                    yield from go(d, [("y", ln.Y0 + (geom.LANE_H - d.rect[3]) // 2)])
                yield from go(d, [("x", ln.X0 + geom.PREFERRED_GAP)])
            items.append((d, path()))
        run(S, items)

def reagent_parts(cells):
    """四个E孔分担同一批试剂；每孔先拆16份，子滴宽≤3、高≤6，面积恰等于计划电极数。"""
    import itertools
    shapes={n:min(((w,n//w) for w in range(1,4) if n%w==0 and n//w<=6),
                  key=lambda q:(q[0],-q[1])) for n in range(1,19)
            if any(n%w==0 and n//w<=6 for w in range(1,4))}
    for count in range(1,min(4,cells)+1):
        candidates=[p for p in itertools.combinations_with_replacement(shapes,count) if sum(p)==cells]
        if candidates:
            parts=min(candidates,key=lambda p:sum((v-cells/count)**2 for v in p))
            return [(v,shapes[v]) for v in parts]
    raise ValueError(f'当前体积{cells}格无法用最多4孔的固定小滴方案配送')


def compact_receiving_shape(area):
    """精确面积的紧凑母框；不按最终16份小滴的横排长度倒推宽度。"""
    candidates=[(w,area//w) for w in range(4,geom.E_BLOCK_W_MAX+1)
                if area%w==0 and 8<=area//w<=18 and w>=area//w]
    assert candidates, f'没有适合顶部接液的紧凑整格母框：{area}格'
    return min(candidates,key=lambda q:(q[0]/q[1],q[1]))


def fixed_cascade_shape(vc,n):
    """母框由最终小滴的整数行列组成：切分时不必换长宽。

    两排小滴，8份优先；每帧沿一轴对半切，最后沿另一轴切。
    限制母框长宽比≤2，子滴宽≤4、高≤6，面积严格为计划格数。
    """
    nx=n//2
    choices=[(w,vc//w) for w in range(1,5) if vc%w==0 and vc//w<=6
             and max(w*nx,2*(vc//w))/min(w*nx,2*(vc//w))<=2]
    if not choices:return None
    return min(choices,key=lambda q:(max(q[0]*nx,2*q[1])/min(q[0]*nx,2*q[1]),
                                     max(q)/min(q)))


def loading_plan(cells):
    """优先两孔各供8道；需要拆成两份时四孔各供8道。

    大体积用最多四孔各供全16道的一部分。所有孔一次手工加入，
    不新增反复加一点的人工批次；各道最后收到的总格数完全相同。
    """
    import itertools
    if fixed_cascade_shape(cells,8):
        return [(cells,8,list(range(16,8,-1))),
                (cells,8,list(range(8,0,-1)))]
    choices=[v for v in range(1,cells) if fixed_cascade_shape(v,8)]
    pairs=[(a,b) for a,b in itertools.combinations_with_replacement(choices,2) if a+b==cells]
    if pairs:
        pair=min(pairs,key=lambda p:abs(p[0]-p[1]))
        return [(v,8,rows) for v in pair
                for rows in (list(range(16,8,-1)),list(range(8,0,-1)))]
    choices=[v for v in range(1,cells+1) if fixed_cascade_shape(v,16)]
    for count in range(1,5):
        parts=[p for p in itertools.combinations_with_replacement(choices,count) if sum(p)==cells]
        if parts:
            part=min(parts,key=lambda p:sum((v-cells/count)**2 for v in p))
            return [(v,16,list(range(16,0,-1))) for v in part]
    raise ValueError(f'{cells}格无法用最多四孔、固定形状小滴配送')


def deliver_drops(S, reagent, vol, cols, tag, on_before_load=None, on_load=None,controlled=False):
    assert len(cols)==1 and geom.odd(cols[0]), "仅支持单奇数列"
    c=cols[0]; gx=geom.gap_x(geom.reagent_gap(c))
    initial_volumes={r:S.get(Lane(c,r).name).vol for r in range(1,17)}
    if on_before_load: on_before_load()
    roots=[]; parts=loading_plan(geom.cells(vol));root_grids={}
    if controlled:
        assert (reagent,geom.cells(vol)) in (('G8',14),('EDTA',4)),'只改变已点名的B02/B10配送'
        parts=[(geom.cells(vol),16,list(range(16,0,-1)))]
    for k,(vc,n,rows) in enumerate(parts,1):
        if controlled:
            wf,hf,nx,ny=(2,7,8,2) if reagent=='G8' else (2,2,4,4)
        else:
            wf,hf=fixed_cascade_shape(vc,n);nx,ny=n//2,2
        W,H=wf*nx,hf*ny
        rc=(geom.HOLE_X[k]-W//2,geom.E_TANG_Y,W,H)
        root=S.add(Drop(f"D{reagent}E{k}",rc,round(vc*n*geom.CELL_UL,2)))
        roots.append((root,wf,hf,k,rows))
        root_grids[id(root)]=(nx,ny,13 if controlled and reagent=='EDTA' else 14)
    S.step(geom.HOLD_MS,changed=[d.name for d,_,_,_,_ in roots])
    info=dict(hole='/'.join(f'E{k}' for _,_,_,k,_ in roots),reagent=reagent,
              volume_ul=round(vol*16,2),per_lane_ul=vol,lanes=f"{'ABCDEF'[c-1]}1-{'ABCDEF'[c-1]}16",
              port_volumes={f'E{k}':d.vol for d,_,_,k,_ in roots},batch=1,batches=1)
    if on_load: on_load(info)
    else: hold_all(S,T_DISP,geom.VOLT)
    S.step(geom.HOLD_MS,changed=[])
    # 各孔就地同步拆分。保留原母框、小滴尺寸与切分动作，不再共用一个拆分工位。
    run(S,[(d,go(d,[("y",root_grids[id(d)][2]-d.rect[3]//2)])) for d,_,_,_,_ in roots])
    fields=[]
    for root,wf,hf,k,rows in roots:
        nx,ny,cy=root_grids[id(root)];p=wf+geom.PREFERRED_GAP
        left=geom.HOLE_X[k]-(nx*wf+(nx-1)*geom.PREFERRED_GAP)//2
        fields.append(dict(wf=wf,hf=hf,k=k,rows=rows,p=p,left=left,nx=nx,ny=ny,cy=cy,nodes=[(root,0,nx-1)]))
    S.pipeline_events=getattr(S,'pipeline_events',[])
    S.pipeline_events.append(dict(kind='start',reagent=reagent,per_lane_ul=vol,
                                  initial_volumes=initial_volumes,step=S.nsteps))
    for level in range(max(int(math.log2(f['nx'])) for f in fields)):
        moves={};ports=[];specs=[];metadata=[]
        for f in fields:
            if level>=int(math.log2(f['nx'])):continue
            ports.append(f['k']);f['nodes_next']=[]
            for d,a,b in f['nodes']:
                wc=d.rect[2]//2;mid=(a+b+1)//2;l,r=split_x(d,wc,wc)
                half=round(d.vol/2,2)
                specs.append((d,(l,r),(half,half),(d.name+'L',d.name+'R')))
                metadata.append((f,a,mid,b))
        S.pipeline_events.append(dict(kind='cut',ports=ports,axis='x',step=S.nsteps))
        for (l,r),(f,a,mid,b) in zip(split_many(S,specs),metadata):
            f['nodes_next'].extend([(l,a,mid-1),(r,mid,b)])
        for f in fields:
            if 'nodes_next' not in f:continue
            f['nodes']=f.pop('nodes_next')
            for d,a,b in f['nodes']:
                center=f['left']+(a+b)*f['p']/2+f['wf']/2
                moves[d.name]=round(center-d.rect[2]/2)
        move_rects(S,moves)
    packets=[];delta=geom.CUT_GAP//2
    for f in fields:f['ynodes']=[(d,0,f['ny']-1) for d,a,b in f['nodes']]
    for level in range(max(int(math.log2(f['ny'])) for f in fields)):
        specs=[];metadata=[];ports=[]
        for f in fields:
            if level>=int(math.log2(f['ny'])):continue
            f['ynext']=[];ports.append(f['k'])
            for d,a,b in f['ynodes']:
                x,y,w,h=d.rect;half=round(d.vol/2,2);hh=h//2;mid=(a+b+1)//2
                specs.append((d,((x,y-delta,w,hh),(x,y+hh+delta,w,hh)),
                              (half,half),(d.name+'T',d.name+'B')));metadata.append((f,a,mid,b))
        S.pipeline_events.append(dict(kind='cut',ports=ports,axis='y',step=S.nsteps))
        for (top,bottom),(f,a,mid,b) in zip(split_many(S,specs,axis='y'),metadata):
            f['ynext'].extend([(top,a,mid-1),(bottom,mid,b)])
        moves=[]
        for f in fields:
            if 'ynext' not in f:continue
            f['ynodes']=f.pop('ynext');py=f['hf']+geom.PREFERRED_GAP
            top=f['cy']-(f['ny']*f['hf']+(f['ny']-1)*geom.PREFERRED_GAP)//2
            for d,a,b in f['ynodes']:
                target=round(top+(a+b)*py/2+f['hf']/2-d.rect[3]/2)
                if f['ny']>2:moves.append((d,go(d,[('y',target)])))
        if moves:run(S,moves)
    for f in fields:
        for j,d in enumerate(sorted((d for d,a,b in f['ynodes']),key=lambda d:(-d.rect[1],d.rect[0]))):
            ln=Lane(c,f['rows'][j]);S.remove(d.name)
            d.name=f'{tag}E{f["k"]}_{ln.id}';d.port=f['k'];d.row=ln.r;S.add(d)
            if controlled and reagent=='G8':d.controlled_entry=True
            packets.append((d,ln))
    S.step(CUT_MS,changed=[d.name for d,_ in packets])
    # 连续配送仅汇集同种试剂，不与原产物接触。所有道备齐后统一融合。
    locks={}
    channel_width=max(d.rect[2] for d,_ in packets)
    def path(d,ln):
        yield from go(d,[("x",gx),("y",ln.Y0+(geom.LANE_H-d.rect[3])//2)])
        while ln.id in locks:yield 'wait'
        locks[ln.id]=d
        staging_name=f'{tag}{ln.id}'
        collected=S.drops.get(staging_name)
        entry=ln.X0+geom.PREFERRED_GAP
        if collected:entry=min(entry,collected.rect[0]-d.rect[2]-geom.PREFERRED_GAP)
        assert entry>=gx+channel_width+geom.PREFERRED_GAP,'入口整形会阻断转运通道'
        yield from go(d,[("x",entry)])
        if controlled and reagent=='G8':
            yield ('reshape',(entry,ln.Y0,7,2))
        S.pipeline_events.append(dict(kind='enter',port=d.port,row=d.row,step=S.nsteps))
        # 进入反应区保持原小滴形状，不再强制扩展到6格高。
        host=S.drops.get(staging_name)
        if host is None:
            # 各道试剂固定停靠右边界，与产物间隔4格；后续同种试剂从左汇入。
            right=S.get(ln.name).rect[0]-geom.PREFERRED_GAP
            yield from go(d,[("x",right-d.rect[2])])
            old_name=d.name;S.remove(old_name);d.name=staging_name;S.add(d)
            S.pipeline_events.append(dict(kind='reagent_collect',port=d.port,row=d.row,
                                          incoming_ul=d.vol,total_ul=d.vol,step=S.nsteps))
            locks.pop(ln.id)
            return
        yield from go(d,[("x",host.rect[0]-d.rect[2]-geom.PREFERRED_GAP)])
        d.allow=(host.name,)
        yield from go(d,[("x",host.rect[0]-d.rect[2])])
        yield ('hold',d.rect,geom.HOLD_MS)
        right=host.rect[0]+host.rect[2];total=round(host.vol+d.vol,2)
        union=(d.rect[0],ln.Y0,right-d.rect[0],geom.LANE_H)
        incoming=d.vol
        yield ('merge',union,host.name,incoming)
        L,H=geom.lane_shape(total)
        reserve=channel_width+geom.PREFERRED_GAP if total<vol-1e-7 else 0
        if right-L-reserve < gx+channel_width+geom.PREFERRED_GAP:
            # 连续运输尚未清空：暂保紧凑等面积轮廓，整列就绪后再整成完整矩形。
            # 不能在通道还在行进时将34格拉成17格宽并堵住入口。
            L,H=geom.rect_len(total,geom.LANE_H),geom.LANE_H
        for rect in reshape_plan(d,(right-L,ln.Y0,L,H)):yield ('reshape',rect)
        yield ('hold',d.rect,2000)
        locks.pop(ln.id)
    S.pipeline_active=True
    try:
        run(S,[(d,path(d,ln)) for d,ln in sorted(packets,key=lambda p:(-p[1].r,p[0].port))])
    finally:S.pipeline_active=False
    lanes=[Lane(c,r) for r in range(1,17)]
    for ln in lanes:
        assert abs(S.get(ln.name).vol-initial_volumes[ln.r])<1e-6,'全道备齐前产物不得融合'
        assert abs(S.get(f'{tag}{ln.id}').vol-vol)<1e-6,'该道试剂未备齐'
        assert _gap(S.get(f'{tag}{ln.id}').rect,S.get(ln.name).rect)>=geom.PREFERRED_GAP
    S.pipeline_events.append(dict(kind='all_ready',rows=list(range(1,17)),step=S.nsteps))
    # 再次发出静态就绪帧，所有液滴继续上电；沿用全道同距、同步接触/融合动作。
    S.step(geom.HOLD_MS,changed=[])
    ops.merge_all(S,lanes,tag)
    final_volumes={r:S.get(Lane(c,r).name).vol for r in range(1,17)}
    assert all(abs(final_volumes[r]-initial_volumes[r]-vol)<1e-6 for r in final_volumes)
    assert not any(n.startswith(tag+'E') for n in S.drops)
    S.pipeline_events.append(dict(kind='finish',reagent=reagent,final_volumes=final_volumes,step=S.nsteps))

# ── 排废: 泳道里的废液切成能走空隙的小块, 依次出去 → 底边孔大滴 ─────────────
def _door_rule(gx_exit):
    """出泳道时对门线附近做 MIN_GAP 格交通检查，避免出门和纵向液滴互卡。"""
    line = gx_exit - geom.MIN_GAP
    def rule(d, cand):
        if cand[0] + cand[2] > line and d.rect[0] + d.rect[2] <= line:
            strip = (line, cand[1] - geom.MIN_GAP, geom.CH_W + geom.MIN_GAP, cand[3] + 2 * geom.MIN_GAP)
            for n2, d2 in d.S.drops.items():
                if n2 != d.name and any(_overlap(strip, q) for q in d2.rects()): return False
        return True
    return rule

WASTE_COLLECT_MAX_W = 24  # 每孔同高汇液框最多24×6，避免132×6的超长母滴。
WASTE_PORT_ORDER = (4,3,2,1)  # 先远孔再近孔；底部上层转运带与停靠框分开。


def _to_blob_path(d, gx, fk, blob_state):
    """先在Y172的固定形状转运带横移，再下行到F孔旁。

    已收集的废液在Y183..189保持，不阻断后续液滴的底部横移。
    """
    yield from go(d, [("y", geom.BOT_Y)])
    while True:
        blob = blob_state.get(fk)
        left=blob.rect[0] if blob else geom.HOLE_X[fk]-d.rect[2]//2
        tgt=left-geom.PREFERRED_GAP-d.rect[2]
        if d.rect[0]!=tgt:yield "+X" if tgt>d.rect[0] else "-X"
        elif d.rect[1]!=geom.BLOB_Y:yield "+Y"
        else:return

def _merge_into_blob(S, d, fk, blob_state, final):
    blob = blob_state.get(fk)
    need = (blob.rect[2] if blob else 0) + d.rect[2]
    assert need<=WASTE_COLLECT_MAX_W, '汇液容量须在转运前预留'
    if blob:
        assert d.rect[1]==blob.rect[1] and d.rect[3]==blob.rect[3], '汇液必须同高，不能形成L形再瞬间重塑'
        target=blob.rect[0]-d.rect[2]
        ops.move_rects(S,{d.name:target},touching=[(d.name,blob.name)])
        S.step(geom.HOLD_MS, touching=[(d.name, blob.name)], changed=[d.name])                # 贴合
        x=min(d.rect[0],blob.rect[0]); S.remove(d.name)
        blob.vol=round(blob.vol+d.vol,3);blob.rect=(x,blob.rect[1],need,blob.rect[3])
        S.step(geom.HOLD_MS,changed=[blob.name])
        ops.move_rects(S,{blob.name:geom.HOLE_X[fk]-need//2})
    else:
        # 首滴不能从离汇液区4格处凭空变成大滴：先整体平移，再单独形变。
        ops.move_rects(S, {d.name:geom.HOLE_X[fk]-d.rect[2]//2})
        S.remove(d.name); blob = blob_state[fk] = S.add(Drop(f"BLOB{fk}", d.rect, d.vol))
    S.step(2000, changed=[blob.name])


def flush_waste_ports(S,blob_state):
    """F孔最终停靠为等面积紧凑矩形；不能形成者拆为两份，分别等人工吸出。"""
    import contour
    occupied=[(k,blob_state.get(k)) for k in WASTE_PORT_ORDER if blob_state.get(k)]
    if not occupied:return
    tails=[]
    for k,d in occupied:
        # 零计划载液的磁珠清理框不套用液体体积换算，保持珠位电极格数。
        area=len(d.target_cells());parts=contour.compact_parts(area)
        if len(parts)==2:
            run(S,[(d,go(d,[("y",174)]))])
            source=sorted(d.target_cells(),key=lambda p:(p[1],p[0]))
            chunks=(source[:parts[0]],source[parts[0]:]);finals=[]
            for i,chunk in enumerate(chunks):
                shift=-3 if i==0 else 3
                cells=frozenset((x,y+shift) for x,y in chunk)
                assert contour.connected(cells)
                finals.append(contour.bounds(cells))
            vols=tuple(round(len(chunk)*geom.CELL_UL,2) if d.vol else 0 for chunk in chunks)
            d,tail=split_many(S,[(d,finals,vols,(d.name,d.name+'_tail'),chunks)],axis='y',offset=3)[0]
            blob_state[k]=d
            W,H=contour.compact(len(tail.target_cells()))
            reshape(S,tail,(geom.HOLE_X[k]-W//2,178,W,H))
            run(S,[(tail,go(tail,[("x",geom.HOLE_X[k]-27-W//2),("y",172)]))])
            tails.append((k,tail))
        W,H=contour.compact(len(d.target_cells()))
        reshape(S,d,(geom.HOLE_X[k]-W//2,189-H,W,H))
    def aspirate(items):
        for k,d in items:
            assert len(d.target_cells())==d.rect[2]*d.rect[3]
            assert max(d.rect[2:])/min(d.rect[2:])<=2 and d.rect[1]+d.rect[3]==189
        S.step(geom.HOLD_MS,changed=[])
        info=dict(hole='/'.join(f'F{k}' for k,d in items),volume_ul=round(sum(d.vol for k,d in items),3),
                  port_volumes={f'F{k}':d.vol for k,d in items},drop_names=[d.name for k,d in items],
                  rects=[list(d.rect) for k,d in items],kind='紧凑框多孔底部废液',rounding_gap_cells=.5)
        S.manual_aspirations.append((S.relays[-1],info))
        for k,d in items:S.remove(d.name);blob_state[k]=None
        S.step(geom.HOLD_MS,changed=[])
    aspirate([(k,blob_state[k]) for k,_ in occupied])
    if tails:
        for k,d in tails:
            run(S,[(d,go(d,[("x",geom.HOLE_X[k]-d.rect[2]//2),("y",189-d.rect[3])]))])
            blob_state[k]=d
        aspirate(tails)


def send_waste_pieces(S,pieces,gx,blob_state,final=False):
    """给并行小滴预留F1-F4容量；只在真的需要人工吸液时拆流程组。"""
    pending=list(pieces)
    while pending:
        reserved={k:blob_state[k].rect[2] if blob_state.get(k) else 0 for k in WASTE_PORT_ORDER}
        batch=[]
        while pending:
            ln,d=pending[0]
            assert d.rect[3]==geom.BLOB_H
            fk=next((k for k in WASTE_PORT_ORDER if reserved[k]+d.rect[2]<=WASTE_COLLECT_MAX_W),None)
            if fk is None:break
            pending.pop(0);reserved[fk]+=d.rect[2];d.waste_fk=fk;d.S=S;d.dynamic_path=True
            def path(ln=ln,d=d,fk=fk):
                yield from go(d,[("x",gx)])
                yield from _to_blob_path(d,gx,fk,blob_state)
            batch.append((d,path()))
        if batch:
            run(S,batch,rule=_door_rule(gx),
                on_done=lambda d:_merge_into_blob(S,d,d.waste_fk,blob_state,False))
        if pending:flush_waste_ports(S,blob_state)
    if final:flush_waste_ports(S,blob_state)

def egress_pieces(S, items, blob_state, final=True):
    """items: [(lane, drop)] 泳道里的废液滴 (奇数列, 排废端在右)。每滴先原地拉成 k 块的总宽, 再等体积对半切成 k 块 (k=1/2/4),
    然后各块 (靠空隙的先) 平移: 出泳道 → 空隙下行 → 大滴区 → 并入大滴。"""
    S_ = S
    for ln, d in items:
        d.S = S_
        # 仅在明确的磁珠清理动作中重新启用输送电极，不增加载液体积。
        d.magnet_only=False
    pieces = []                                                            # (lane, drop)
    for ln, d in items:
        cells = geom.cells(d.vol) if d.vol else d.rect[2]*d.rect[3]; k = 1
        while math.ceil(cells / k) > 24: k *= 2
        pw, ph = (d.rect[2],d.rect[3]) if not d.vol else piece_shape(math.ceil(cells / k))
        if k == 1:
            if (d.rect[2], d.rect[3]) != (pw, ph):                        # 原地变成能走空隙的形状 (靠排废端)
                x1 = d.rect[0] + d.rect[2]; reshape(S, d, (x1 - pw, ln.Y0 + (geom.LANE_H - ph) // 2, pw, ph))
            pieces.append((ln, d)); continue
        margin = (geom.CUT_GAP // 2) * int(math.log2(k))
        W = k * pw; lim = ln.X0 + geom.LANE_L - margin
        x0 = d.rect[0] + margin if d.rect[0] + margin + W <= lim else lim - W
        assert x0 >= d.rect[0] + margin, f"{d.name} 泳道里放不下 {k} 块"
        reshape(S, d, (x0, ln.Y0 + (geom.LANE_H - ph) // 2, W, ph))
        lev = [d]; kk = k
        while kk > 1:                                                      # 同级同步，逐帧逐格拉开
            specs=[]
            for q in lev:
                l, r = split_x(q, q.rect[2] // 2, q.rect[2] // 2); half = round(q.vol / 2, 3)
                specs.append((q,(l,r),(half,round(q.vol-half,3)),(q.name+'L',q.name+'R')))
            lev=[q for ab in split_many(S,specs) for q in ab]; kk //= 2
        pieces += [(ln, q) for q in lev]
    gk = geom.waste_gap(items[0][0].c); gx = geom.gap_x(gk); fk = geom.WASTE_HOLE[gk]
    order = sorted(pieces, key=lambda t: (-t[0].r, -t[1].rect[0]))          # 深行先、靠空隙的先
    for ln,d in pieces:
        if d.rect[3]!=geom.BLOB_H or d.rect[1]!=ln.Y0:
            reshape(S,d,(d.rect[0],ln.Y0,d.rect[2],geom.BLOB_H))
    send_waste_pieces(S,order,gx,blob_state,final)

# ── 液相移离磁珠: 分离 → 移液 → 排废 (咬一块送一块, 剩余液体每次向排废端滑动, 磁珠团始终通电) ─────────
def supernatant_bites(S, lanes, blob_state, pellet_vol, final=True):
    """液相矩形从右端逐块切分并排废；磁珠由磁铁保持位置。
    pellet_vol=0 时全部液相离开后仅保留磁珠位置记录，不输出磁珠占位液滴电极。"""
    rem, pel = {}, {}
    for ln in lanes:
        d = S.get(ln.name); liq = round(d.vol - pellet_vol, 3)
        p = S.add(Drop(f"PEL{ln.id}", ln.rect(ln.mag - 1, 3), pellet_vol)); pel[ln.id] = p
        S.drops.pop(ln.name); d.name = f"LIQ{ln.id}"; d.vol = liq; S.drops[d.name] = d; rem[ln.id] = d
        d.allow = (p.name,); p.allow = (d.name,)
    S.step(2000, changed=[p.name for p in pel.values()], touching=[(d.name, pel[i].name) for i, d in rem.items()])
    gk = geom.waste_gap(lanes[0].c); gx = geom.gap_x(gk); fk = geom.WASTE_HOLE[gk]
    bite = 0
    while rem:
        touch = [(rem[i].name, pel[i].name) for i in rem]
        # 剩余液相右移到泳道右端（为切分外移预留 CUT_GAP/2 格），每步平移1格。
        run(S, [(d, go(d, [("x", ln.X0 + geom.LANE_L - geom.CUT_GAP//2 - d.rect[2])])) for ln in lanes for d in [rem.get(ln.id)] if d])
        pieces, gone, specs, split_lanes = [], [], [], []
        for ln in lanes:
            d = rem.get(ln.id)
            if not d: continue
            if d.rect[2] <= 5 and geom.cells(d.vol) <= geom.CH_W*geom.LANE_H:
                # 最后一块只有能装进4×6通道块时才整体出去；否则再切一次。
                pw, ph = piece_shape(geom.cells(d.vol)); x1 = d.rect[0] + d.rect[2]
                if (d.rect[2], d.rect[3]) != (pw, ph):
                    reshape(S,d,(x1-pw,ln.Y0,pw,geom.BLOB_H),touching=touch)
                pieces.append((ln, d)); gone.append(ln.id); continue
            wl = d.rect[2] - 4; l, r = split_x(d, wl, 4)                  # 切下右端 4 格宽 (等体积, 两边各挪 1)
            vr = round(round(geom.cells(d.vol)*4/d.rect[2])*geom.CELL_UL,2)
            specs.append((d,(l,r),(round(d.vol-vr,2),vr),(d.name,f"BITE{ln.id}_{bite}")))
            split_lanes.append(ln)
        if specs:
            for ln,(d,q) in zip(split_lanes,split_many(S,specs,touching=touch)):
                rem[ln.id]=d;pieces.append((ln,q))
        S.step(CUT_MS, changed=[q.name for _, q in pieces] + [rem[i].name for i in rem], touching=touch)
        # 最后一块仍从本孔磁珠旁分离，离开前保留本孔分离接触例外。
        # 不豁免其他孔或其他液滴；平移离开后自然达到正常间距。
        for i in gone: del rem[i]
        for _, q in pieces: q.S = S
        send_waste_pieces(S,sorted(pieces,key=lambda t:-t[0].r),gx,blob_state)
        bite += 1
    for ln in lanes:                                                       # 磁珠团恢复为常驻名
        p = pel[ln.id]; S.drops.pop(p.name); p.name = ln.name; p.allow = ()
        p.magnet_only=(p.vol==0);S.drops[ln.name] = p
    if final:flush_waste_ports(S,blob_state)

# ── 洗脱后产物离开磁珠团（两块各外移 CUT_GAP/2，磁珠团回磁铁中心） ─────────
def product_off_pellet(S, lanes, product_vol, pellet_vol):
    if pellet_vol == 0:
        pairs=[]
        for ln in lanes:
            d=S.get(ln.name);assert abs(d.vol-product_vol)<1e-8
            S.drops.pop(d.name);d.name=f'P{ln.id}';S.drops[d.name]=d
            p=S.add(Drop(ln.name,ln.rect(ln.mag-1,3),0))
            p.magnet_only=True
            d.allow=(p.name,);pairs.append((d.name,p.name))
        S.step(geom.HOLD_MS,changed=[n for p in pairs for n in p],touching=pairs)
        move_rects(S,{f'P{ln.id}':geom.s_block(ln.c,ln.r,product_vol)[0] for ln in lanes},touching=pairs)
        # 离开磁控半区后恢复完整矩形，磁捕获所需的缺角不带到取液终态。
        reshape_many(S,[(S.get(f'P{ln.id}'),geom.s_block(ln.c,ln.r,product_vol)) for ln in lanes],touching=pairs)
        return
    pairs = []
    for ln in lanes:
        d = S.get(ln.name); Lp = geom.rect_len(product_vol, geom.LANE_H)
        reshape(S, d, (ln.mag - 1 - geom.CUT_GAP//2 - Lp, ln.Y0, Lp + 3, geom.LANE_H), dur=MORPH_MS)
    specs=[]
    for ln in lanes:
        d = S.get(ln.name); Lp = geom.rect_len(product_vol, geom.LANE_H)
        l, r = split_x(d, Lp, 3)
        specs.append((d,(l,r),(product_vol,round(d.vol-product_vol,3)),(f'P{ln.id}',ln.name)))
    split_many(S,specs)

# ── 定量段: 定量滴切成 4 块 1 µL, 每块原地拉成底部两行 2×10, 从压扁的文库下面穿过, 到泳道末端原地变回 4×5, 出空隙 ──
def quant_pass(S, lanes, blob_state, tag="Q", final=True):
    """前提: 文库已压成顶部两行 (X0+23..34, Y0..Y0+2), 定量滴 tag+lane (≈4 µL, 14×6) 在泳道左半区。
    定量滴 → 原地 16×5 → 等体积切成两半 (8×5) → 右半再切成两块 4×5 → 两块依次: 原地变成底部两行 2×10 → 在文库下面平移到末端 (右端 4 格伸进空隙)
    → 原地变回 4×5 (在空隙里) → 下行 → 大滴; 然后左半右移让出 X0, 同样切成两块送走。"""
    gk = geom.waste_gap(lanes[0].c); gx = geom.gap_x(gk); fk = geom.WASTE_HOLE[gk]
    def cut_all(drops, suffix):
        specs=[]
        for q in drops:
            l, r = split_x(q, q.rect[2] // 2, q.rect[2] // 2); half = round(q.vol / 2, 3)
            specs.append((q,(l,r),(half,round(q.vol-half,3)),(q.name+suffix[0],q.name+suffix[1])))
        return split_many(S,specs)
    def send(pairs):                                                       # pairs: [(lane, drop)] 同步走
        for ln, q in pairs: q.S = S
        def path(ln, q):
            yield ("reshape", (q.rect[0], ln.Y0 + 4, 10, 2))                             # 原地变成底部两行 2×10 (20 格)
            yield from go(q, [("x", ln.X0 + geom.LANE_L - 6)])                             # 平移到末端, 右端 4 格伸进空隙
            yield ("reshape", (ln.X0 + geom.LANE_L, ln.Y0 + 1, 4, 5))                      # 在空隙里原地变回 4×5
            yield from _to_blob_path(q, gx, fk, blob_state)
        run(S, [(q, path(ln, q)) for ln, q in sorted(pairs, key=lambda t: -t[0].r)], rule=_door_rule(gx),
            on_done=lambda q: _merge_into_blob(S, q, fk, blob_state, final))
    for ln in lanes:
        q = S.get(f"{tag}{ln.id}"); reshape(S, q, (ln.X0 + 3, ln.Y0, 16, 5))             # 13..29
    halves = cut_all([S.get(f"{tag}{ln.id}") for ln in lanes], ("L", "R"))                # 12..20 / 22..30
    move_rects(S, {l.name: ln.X0 for ln, (l, r) in zip(lanes, halves)})                   # 左半退到 X0 (10..18)
    rq = cut_all([r for l, r in halves], ("L", "R"))                                      # 21..25 / 27..31
    send([(ln, rr) for ln, (rl, rr) in zip(lanes, rq)]); send([(ln, rl) for ln, (rl, rr) in zip(lanes, rq)])
    move_rects(S, {l.name: ln.X0 + 2 for ln, (l, r) in zip(lanes, halves)})               # 左半右移到 12..20
    lq = cut_all([l for l, r in halves], ("L", "R"))                                      # 11..15 / 17..21
    send([(ln, lr) for ln, (ll, lr) in zip(lanes, lq)]); send([(ln, ll) for ln, (ll, lr) in zip(lanes, lq)])
    if final: _aspirate(S, blob_state, fk)

# 正式单列工程使用小矩形分排汇液；旧横条汇液只保留作历史算法对照。
from waste_grid import egress_pieces, supernatant_bites
