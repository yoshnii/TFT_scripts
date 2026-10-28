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
        x0, y0 = geom.physical_zone(col, row)
        # 行内和门口的整高矩形受9格行距约束，最多留3格。
        # 完整进入通道后纵向错开即可留4格；不可在转移前要求同一行距留4格。
        if (x0-geom.CH_W <= x and x+w <= x0+geom.LANE_L+geom.CH_W
                and y0 <= y and y+h <= y0+geom.PHYSICAL_LANE_H):
            return True
    return False


def rect_ok(S, name, r, allow=frozenset()):
    """r 在片内, 且与其他液滴 (allow 里的除外) 间距 ≥ MIN_GAP。"""
    if not (0 <= r[0] and r[0] + r[2] <= geom.GRID_W and 0 <= r[1] and r[1] + r[3] <= geom.GRID_H): return False
    for names,reserved in getattr(S,'ready_cut_reservations',()):
        if name not in names and (_overlap(r,reserved) or _gap(r,reserved)<6):return False
    d=S.drops.get(name)
    gap=geom.LOAD_GAP if getattr(d,'receiving',False) or getattr(d,'port_dock',False) else (getattr(S,'clearance_context',None) or getattr(d,'port_gap',None) or geom.running_gap(d.vol))
    if not geom.keep_out_ok(r) or geom.between_lanes(r) or not geom.reaction_holes_ok(r,gap): return False
    for n2, d2 in S.drops.items():
        if n2 == name or n2 in allow: continue
        for q in d2.rects():
            row_limited = (in_reaction(r) and in_reaction(q)
                           and (r[1]-27)//9 != (q[1]-27)//9)
            bottom_limited=(171<=r[1] and r[1]+r[3]<=174) or (171<=q[1] and q[1]+q[3]<=174)
            waste_queue=(getattr(S.get(name),'waste_grid_packet',False) and getattr(d2,'waste_grid_packet',False))
            waste_dock=(getattr(S.get(name),'waste_pipeline_clearance',False) and d2.name.startswith('BLOB'))
            entry_limited=(getattr(S.get(name),'controlled_entry',False) and r[1]>=23)
            native_queue=getattr(S.get(name),'native_packet',False) and getattr(d2,'native_packet',False)
            compact_mothers=getattr(S.get(name),'compact_mother_clearance',False) and getattr(d2,'compact_mother_clearance',False)
            from transport_spacing import required,well,local_cut_gap
            own_well=well(r) is not None and well(r)==well(q)
            clearance = geom.MIN_GAP if own_well or row_limited or bottom_limited or waste_queue or waste_dock or entry_limited or native_queue or compact_mothers else geom.PREFERRED_GAP
            clearance=max(clearance,required(S,d,r,d2,q))
            local=local_cut_gap(S,d,r,d2,q)
            if local is not None:clearance=local
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

def tick(S, items, rule=None, allow=frozenset(), dur=geom.STEP_MS, record=True):
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
        clock=getattr(S,'top_mother_clock',None)
        if clock and clock.parallel_run and not clock.can_move(d):continue
        if isinstance(act, tuple): cand = tuple(act[1])
        else: dx, dy = DIRS[act]; cand = (d.rect[0] + dx, d.rect[1] + dy, d.rect[2], d.rect[3])
        al = allow | set(getattr(d, "allow", ()))
        intermediate=False
        if isinstance(act,tuple) and act[0]=='reshape' and d.rect[2:]!=tuple(reversed(cand[2:])):
            ow,oh=d.rect[2:];tw,th=cand[2:]
            if max(ow/tw,tw/ow,oh/th,th/oh)>2.5:
                n=len(d.target_cells());metric=lambda w,h:abs(math.log(w/tw))+abs(math.log(h/th))
                options=[]
                for w in range(1,min(n,geom.GRID_W)+1):
                    if n%w:continue
                    h=n//w
                    if max(ow/w,w/ow,oh/h,h/oh)>2.5 or metric(w,h)>=metric(ow,oh):continue
                    ys=[cand[1]]
                    if getattr(d,'row',None) and h<=6:
                        y0=27+9*(d.row-1);ys.append(max(y0,min(cand[1],y0+6-h)))
                    for y in ys:
                        rc=(cand[0],y,w,h)
                        if _overlap(d.rect,rc) and rect_ok(S,d.name,rc,al):options.append((metric(w,h),rc))
                assert options,('没有温和等面积矩形形变路径',d.name,d.rect,cand)
                cand=min(options)[1];intermediate=True
        if rect_ok(S, d.name, cand, al) and (rule is None or rule(d, cand)):
            if getattr(S,'pipeline_active',False) and not getattr(d,'transport_started',False):
                d.transport_started=True
                S.pipeline_events.append(dict(kind='transport_start',port=d.port,row=d.row,step=S.nsteps))
            if isinstance(act, tuple): assert _overlap(d.rect, cand), f"形变不连续 {d.name}"
            if isinstance(act,tuple) and act[0] in ('merge','merge_native'):
                host=S.get(act[2]);old_name=d.name
                combined_area=len(d.target_cells())+len(host.target_cells())
                combined_margin=getattr(d,'margin_budget',0)+getattr(host,'margin_budget',0)
                retired_padding=(getattr(d,'transport_padding_cells',0)
                                 +getattr(host,'transport_padding_cells',0))
                if act[0]=='merge_native':
                    assert not retired_padding
                    assert d.rect[1]==host.rect[1] and d.rect[3]==host.rect[3]
                    assert cand[2]*cand[3]==combined_area
                    d.native_authorized_padding=(getattr(d,'native_authorized_padding',False)
                                                 or getattr(host,'native_authorized_padding',False))
                if retired_padding:
                    assert reagent_padding_allowed(d,host)
                    assert cand[2]*cand[3]==combined_area-retired_padding
                    S.transport_padding_events.append(dict(kind='retire',step=S.nsteps,
                        cells=retired_padding,actual_volume_ul=round(d.vol+host.vol,2),
                        reason='G14运输承接余量在反应区汇合后取消；不增加液量'))
                S.remove(old_name);S.remove(host.name)
                d.name=host.name;d.vol=round(d.vol+host.vol,10);d.allow=()
                d.electrode_area=combined_area-retired_padding
                # Fractional half-drops do not have additive rounded cell counts
                # (0.625µL + 0.625µL = 25 cells, not 12 + 12).
                d.margin_budget=d.electrode_area-geom.cells(d.vol)
                assert d.margin_budget>=0
                d.transport_padding_cells=0
                S.add(d)
                S.pipeline_events.append(dict(kind='reagent_collect',port=d.port,row=d.row,
                                              incoming_ul=act[3],total_ul=d.vol,step=S.nsteps))
            if isinstance(act,tuple):
                frame_dur=max(frame_dur,act[2] if act[0]=='hold' else geom.HOLD_MS if act[0]=='merge_native' else MORPH_MS)
            d.rect = cand; d.pending = act if intermediate else None; S.moved = getattr(S, "moved", 0) + 1
        elif getattr(d,'dynamic_path',False):
            # 汇液框合并后边界会逐格移动；重新求停靠间距，不锁住旧目标。
            d.pending=None
    changed=[d.name for d,_ in items if S.drops.get(d.name) is d]
    touching=[(d.name,a) for d,_ in items if S.drops.get(d.name) is d for a in getattr(d,'allow',())]
    if not record:return changed,touching,frame_dur
    S.step(frame_dur, changed=changed, touching=touching)
    S.stall = 0 if getattr(S, "moved", 0) or getattr(S,'top_delivery_advanced',False) else getattr(S, "stall", 0) + 1; S.moved = 0
    if S.stall > 60:
        info = [(d.name, d.pending, d.rect) for d, _ in items if d.pending not in (None, "done")]
        raise RuntimeError(f"排队死锁 @step{S.nsteps}: " + "; ".join(f"{n} 想{a} 在{r}" for n, a, r in info[:6]))

def run(S, items, rule=None, allow=frozenset(), on_done=None):
    """跑到所有液滴的 gen 结束。on_done(drop) 在某液滴结束时调用一次 (如并入大滴)。"""
    ops.settle_loaded(S)
    items = list(items); finished = set()
    clock=getattr(S,'top_mother_clock',None)
    independent=bool(clock and getattr(S,'top_parallel_moves',False)
                     and any(getattr(d,'top_slow_mother',False) for d,_ in items))
    if independent:clock.begin_run(S)
    while any(getattr(d, "pending", None) != "done" for d, _ in items):
        tick(S, items, rule, allow)
        for d, g in items:
            if d.pending == "done" and d.name not in finished:
                finished.add(d.name)
                if on_done: on_done(d)
    for d, _ in items: d.pending = None
    if independent:clock.end_run(S)

def go(d, targets):
    """产出把 d.rect 左上角平移到 targets=[("x", X) | ("y", Y)] 的方向序列。"""
    for axis, val in targets:
        while True:
            cur = d.rect[0] if axis == "x" else d.rect[1]
            if cur == val: break
            yield ("+X" if val > cur else "-X") if axis == "x" else ("+Y" if val > cur else "-Y")

def split_many(S, specs, axis='x', offset=None, dur=CUT_MS, touching=()):
    """先就地等面积分区，两半每帧同时向相反方向各移动一格。

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
        assert not getattr(parent,'transport_padding_cells',0),'运输承接余量须在融合时取消，不能当作液量继续切分'
        starts=[]
        for i,rc in enumerate(finals):
            rc=list(rc);rc[coordinate]+=offset if i==0 else -offset;starts.append(tuple(rc))
        parent_area=len(parent.target_cells())
        footprints=tuple(map(frozenset,custom[0])) if custom else tuple(
            contour.fill(rc,round(parent_area*v/parent.vol) if parent.vol and parent_area!=geom.cells(parent.vol) else geom.cells(v) if v>0 else rc[2]*rc[3]) for rc,v in zip(starts,vols))
        assert abs(sum(vols)-parent.vol)<1e-7,'切分液量不守恒'
        assert min(geom.cells(v) for v in vols)>=.4*sum(geom.cells(v) for v in vols),'禁止大滴剥离小尾滴'
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
            child=S.add(Drop(n,rc,v));child.electrode_area=len(cells);child.port_gap=getattr(parent,'port_gap',None) or geom.running_gap(parent.vol);child.set_cells(cells)
            child.margin_budget=len(cells)-geom.cells(v)
            assert 0<=child.margin_budget<=getattr(parent,'margin_budget',0)
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
            if event['gaps']:assert g-event['gaps'][-1]==2,('切分间隙跳格',event['parent'],event['gaps'],g)
            event['gaps'].append(g);event['positions'].append([list(a.rect),list(b.rect)])
            event['separation_frames'].append(S.nsteps-1)
    record_frame(geom.HOLD_MS,[d.name for ab in groups for d in ab])
    for _ in range(offset):
        changed=[]
        for side,sign in ((0,-1),(1,1)):
            for children in groups:
                d=children[side];previous=d.target_cells();rc=list(d.rect);rc[coordinate]+=sign;d.rect=rc
                dx,dy=(sign,0) if axis=='x' else (0,sign)
                assert d.target_cells()==frozenset((x+dx,y+dy) for x,y in previous),'分离时改变子滴形状'
                changed.append(d.name)
        record_frame(dur if _==offset-1 else geom.STEP_MS,changed)
    for children,spec in zip(groups,specs):
        assert tuple(d.rect for d in children)==tuple(map(tuple,spec[1])),'逐格分离未到预定终点'
    return groups

def bisect_many(S,drops,names=None,touching=()):
    """沿母滴长尺寸原生截为完整矩形；优先等分，不剥小尾巴。"""
    import contour
    ops.settle_loaded(S)
    drops=list(drops);specs=[];axes=[]
    # 紧凑母滴的长尺寸可能为奇数（20格的5×4）；不能按格数排序截半，
    # 否则会把完整矩形切成缺角。先选可沿长尺寸等分的完整母框，独立驻留4秒。
    prepare=[]
    for d in drops:
        x,y,w,h=d.rect;n=len(d.target_cells())
        if in_reaction(d.rect) and (h>w or (n%2==0 and w%2)):
            choices=[(W,n//W) for W in range(2,geom.LANE_L-geom.CUT_GAP+1,2)
                     if n%W==0 and n//W<=geom.LANE_H and W>=n//W]
            assert choices,('完整母框无法沿长尺寸对半',d.name,n)
            W,H=min(choices,key=lambda q:max(q)/min(q))
            prepare.append((d,(x,y,W,H)))
    reshape_many(S,prepare,touching=touching)
    for i,d in enumerate(drops):
        x,y,w,h=d.rect
        # 反应区内避免上下拉开碰到孔/相邻行：先适度整成横向近矩形。
        if in_reaction(d.rect) and h>w:
            W,H=max(w,h),min(w,h)
            reshape(S,d,(x,y+h-H,W,H))
        x,y,w,h=d.rect;axis='x' if w>=h else 'y';axes.append(axis)
        cut=(w if axis=='x' else h)//2
        parts=(frozenset((a,b) for a,b in d.target_cells() if (a-x if axis=='x' else b-y)<cut),
               frozenset((a,b) for a,b in d.target_cells() if (a-x if axis=='x' else b-y)>=cut))
        half=len(parts[0])
        assert half>0 and all(contour.connected(p) for p in parts),'无法连通近等分'
        finals=[];delta=geom.CUT_GAP//2
        for j,part in enumerate(parts):
            rc=list(contour.bounds(part));rc[0 if axis=='x' else 1]+=(-delta if j==0 else delta);finals.append(tuple(rc))
        ns=names[i] if names is not None else (d.name+'L',d.name+'R')
        specs.append((d,tuple(finals),tuple(round(len(p)*geom.CELL_UL,2) for p in parts),ns,parts))
    result=[]
    for axis in ('x','y'):
        chosen=[s for s,a in zip(specs,axes) if a==axis]
        if chosen:result.extend(split_many(S,chosen,axis=axis,touching=touching))
    return result


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
    # wf>=hf使每级X切均沿长尺寸，最后Y切也沿长尺寸；精确面积由轮廓而非包络面积决定。
    choices=[(w,h) for h in range(2,7) for w in range(h,9)
             if w*h>=vc and w*h-vc<=max(3,w) and max(w*nx,2*h)/min(w*nx,2*h)<=4
             and nx*w+(nx-1)*geom.PREFERRED_GAP<=50]
    if not choices:return None
    return min(choices,key=lambda q:(q[0]*q[1]-vc,max(q)/min(q),q[0]))


def loading_plan(cells):
    """优先两孔各供8道；需要拆成两份时四孔各供8道。

    大体积用最多四孔各供全16道的一部分。所有孔一次手工加入，
    不新增反复加一点的人工批次；各道最后收到的总格数完全相同。
    """
    import itertools
    if cells<=48 and fixed_cascade_shape(cells,8):
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


def _b02_two_part_fields(S,root,k,rows):
    """B02 每道 0.70µL=14格，不存在既能走4格宽通道、又不超过6格反应区高度的完整矩形。
    E1 母框 16×14 不变：先沿 Y 不等分为 16×8（每道 2×4=0.40µL）与 16×6（每道 2×3=0.30µL），
    两块平移到顶部空白区左右并排，再按原有方式沿 X、Y 逐级对半切。全部小滴位于 Y≥SAFE_Y。"""
    x,y,w,h=root.rect
    assert (w,h)==(16,14) and abs(root.vol-11.2)<1e-7,'B02 母框须为 E1 单孔 16×14'
    upper,lower=split_many(S,[(root,((x,y-geom.CUT_GAP//2,16,8),(x,y+8+geom.CUT_GAP//2,16,6)),
                                  (6.4,4.8),(root.name+'a',root.name+'b'))],axis='y')[0]
    nx,wf,p=8,2,2+geom.PREFERRED_GAP
    span=nx*wf+(nx-1)*geom.PREFERRED_GAP
    lefts=(geom.SAFE_X+geom.PREFERRED_GAP,geom.SAFE_X+geom.PREFERRED_GAP+span+2*geom.PREFERRED_GAP)
    fields=[]
    # 切后两排：2×4 在 Y10–13/18–21，2×3 在 Y11–13/18–20；Y≤22 不进入 TEC 起始行 23。
    for d,hf,left,top in ((upper,4,lefts[0],12),(lower,3,lefts[1],13)):
        fields.append(dict(wf=wf,hf=hf,k=k,part='a' if hf==4 else 'b',rows=rows,p=p,left=left,
                           nx=nx,ny=2,cy=top+hf,nodes=[(d,0,nx-1)],start=(left+(span-16)//2,top)))
    run(S,[(f['nodes'][0][0],go(f['nodes'][0][0],[('x',f['start'][0]),('y',f['start'][1])])) for f in fields])
    return fields

def _b02_long_axis_packets(S,root,c,tag):
    """E1单孔11.20µL；按长尺寸对半切，最终每道一滴0.70µL，不恢复旧的两份配送。"""
    run(S,[(root,go(root,[('y',7),('x',120)]))])
    reshape(S,root,(114,10,28,8))
    reshape(S,root,(100,12,56,4))
    nodes=[(root,0,7)]
    for level in range(3):
        specs=[];meta=[]
        for d,a,b in nodes:
            l,r=split_x(d,d.rect[2]//2,d.rect[2]//2);half=round(d.vol/2,2)
            specs.append((d,(l,r),(half,half),(d.name+'L',d.name+'R')));meta.append((a,(a+b+1)//2,b))
        new=[]
        for (left,right),(a,mid,b) in zip(split_many(S,specs),meta):new.extend(((left,a,mid-1),(right,mid,b)))
        nodes=new
        move_rects(S,{d.name:round(51+11*(a+b)-d.rect[2]/2) for d,a,b in nodes})
    reshape_many(S,[(d,(d.rect[0]-4,13,14,2)) for d,a,b in nodes])
    specs=[]
    for d,a,b in nodes:
        l,r=split_x(d,7,7);specs.append((d,(l,r),(.7,.7),(d.name+'a',d.name+'b')))
    leaves=[d for pair in split_many(S,specs) for d in pair];packets=[]
    for row,d in zip(range(16,0,-1),sorted(leaves,key=lambda d:d.rect[0])):
        ln=Lane(c,row);S.remove(d.name);d.name=f'{tag}E1_{ln.id}';d.port=1;d.row=row;d.part='';S.add(d);packets.append((d,ln))
    return packets

def reagent_padding_allowed(*drops):
    """只允许G14的0.50µL运输滴携带此次获准的两格承接余量。"""
    return all(not getattr(d,'transport_padding_cells',0) or
               (getattr(d,'g14_transport_padding',False) and d.vol==.5
                and d.transport_padding_cells==2) for d in drops)

def compact_g14_transport_packets(S,packets):
    """0.50µL液量仍是10格；只将2×5承接电极扩为3×4，独立稳定4秒。"""
    changed=[]
    S.transport_padding_events=getattr(S,'transport_padding_events',[])
    for d,ln in packets:
        if d.vol!=.5 or d.rect[2:]!=(2,5):continue
        assert len(d.target_cells())==10 and not getattr(d,'margin_budget',0)
        x,y,_,_=d.rect
        d.electrode_area=12;d.margin_budget=2;d.transport_padding_cells=2
        d.g14_transport_padding=True;d.rect=(x,y,3,4)
        S.transport_padding_events.append(dict(kind='add',step=S.nsteps,name=d.name,
            source_cells=10,target_cells=12,actual_volume_ul=.5,
            rect=list(d.rect),settle_ms=geom.LONG_MS,
            reason='用户批准R01的10格小滴采用12格3×4运输承接框'))
        changed.append(d.name)
    if changed:S.step(geom.LONG_MS,changed=changed)

def deliver_drops(S, reagent, vol, cols, tag, on_before_load=None, on_load=None,controlled=False):
    assert len(cols)==1 and geom.odd(cols[0]), "仅支持单奇数列"
    c=cols[0]; gx=geom.gap_x(geom.reagent_gap(c))
    initial_volumes={r:S.get(Lane(c,r).name).vol for r in range(1,17)}
    if on_before_load: on_before_load()
    roots=[]; parts=loading_plan(geom.cells(vol));root_grids={}
    if controlled:
        assert (reagent,geom.cells(vol)) in (('G8',14),('EDTA',4)),'只改变已点名的B02/B10配送'
        parts=[(geom.cells(vol),16,list(range(16,0,-1)))]
    if reagent=='G16':parts=[(34,8,list(range(16,8,-1))),(34,8,list(range(8,0,-1)))]
    for k,(vc,n,rows) in enumerate(parts,1):
        if controlled:
            wf,hf,nx,ny=(2,7,8,2) if reagent=='G8' else (2,2,8,2)
        else:
            wf,hf=(6,6) if reagent=='G16' else fixed_cascade_shape(vc,n);nx,ny=n//2,2
        W,H=wf*nx,hf*ny
        capacity=wf*hf*n
        recv=compact_receiving_shape(capacity)
        rc=(geom.HOLE_X[k]-recv[0]//2,geom.E_TANG_Y,*recv)
        root=S.add(Drop(f"D{reagent}E{k}",rc,round(vc*n*geom.CELL_UL,2)))
        if reagent=='G16':root.electrode_area=capacity;root.margin_budget=capacity-geom.cells(root.vol)
        root.receiving=True
        roots.append((root,wf,hf,k,rows))
        root_grids[id(root)]=(nx,ny,13 if controlled and reagent=='EDTA' else 14)
    S.step(geom.HOLD_MS,changed=[d.name for d,_,_,_,_ in roots])
    info=dict(hole='/'.join(f'E{k}' for _,_,_,k,_ in roots),reagent=reagent,
              volume_ul=round(vol*16,2),per_lane_ul=vol,lanes=f"{'ABCDEF'[c-1]}1-{'ABCDEF'[c-1]}16",
              port_volumes={f'E{k}':d.vol for d,_,_,k,_ in roots},batch=1,batches=1)
    if on_load: on_load(info)
    else: hold_all(S,T_DISP,geom.VOLT)
    S.step(geom.HOLD_MS,changed=[])
    S.pipeline_events=getattr(S,'pipeline_events',[])
    two_part=False
    if controlled and reagent=='G8':
        packets=_b02_long_axis_packets(S,roots[0][0],c,tag)
    else:
        # 各孔就地同步拆分。保留原母框、小滴尺寸与切分动作，不再共用一个拆分工位。
        run(S,[(d,go(d,[("y",root_grids[id(d)][2]-d.rect[3]//2)])) for d,_,_,_,_ in roots])
        reshape_many(S,[(d,(geom.HOLE_X[k]-wf*root_grids[id(d)][0]//2,
                            root_grids[id(d)][2]-hf*root_grids[id(d)][1]//2,
                            wf*root_grids[id(d)][0],hf*root_grids[id(d)][1])) for d,wf,hf,k,rows in roots])
        two_part=False  # B02原先8:6不等分改为同一母滴逐级对半，末级每道14格。
        fields=[]
        if two_part:
            root,wf,hf,k,rows=roots[0]
            fields=_b02_two_part_fields(S,root,k,rows)
        for root,wf,hf,k,rows in ([] if two_part else roots):
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
                part=f.get('part','')
                d.name=f'{tag}E{f["k"]}{part}_{ln.id}';d.port=f['k'];d.row=ln.r;d.part=part;S.add(d)
                packets.append((d,ln))
    if reagent=='G14':
        groups={}
        for d,ln in packets:groups.setdefault((d.port,d.rect[1]),[]).append(d)
        spread={}
        for (port,y),ds in groups.items():
            ds.sort(key=lambda d:d.rect[0]);width=sum(d.rect[2] for d in ds)+8*(len(ds)-1)
            left=geom.HOLE_X[port]-width//2
            for d in ds:spread[d.name]=left;left+=d.rect[2]+8
        move_rects(S,spread)
    specs=[];meta=[];kept=[]
    for d,ln in packets:
        x,y,w,h=d.rect
        if reagent=='G14' and w>geom.CH_W and len(d.target_cells())==w*h:
            wl=w//2;wr=w-wl;l,r=split_x(d,wl,wr)
            phys=geom.cells(d.vol);a=round(phys*wl/w);va=round(a*.05,2)
            specs.append((d,(l,r),(va,round(d.vol-va,2)),(d.name+'p0',d.name+'p1')));meta.append((ln,d.port))
        else:kept.append((d,ln))
    if specs:
        for children,(ln,k) in zip(split_many(S,specs),meta):
            for j,d in enumerate(children):d.port=k;d.row=ln.r;d.part='p'+str(j);kept.append((d,ln))
    packets=kept
    if reagent=='G14':compact_g14_transport_packets(S,packets)
    S.step(CUT_MS,changed=[d.name for d,_ in packets])
    # 连续配送仅汇集同种试剂，不与原产物接触。所有道备齐后统一融合。
    locks={}
    channel_width=min(geom.CH_W,max(d.rect[2] for d,_ in packets))
    def path(d,ln):
        d.controlled_entry=True
        entry_height=5 if getattr(S,'clearance_context',None)==1 else geom.LANE_H
        def shape(n):
            choices=[(w,n//w) for w in range(1,37) if n%w==0 and n//w<=entry_height]
            return min(choices,key=lambda p:max(p)/min(p))
        # 左上角大孔：X<SAFE_X 的区域只在 Y≥SAFE_Y 通过；先横移到 SAFE_X，再下到安全高度。
        assert d.rect[0]>=geom.SAFE_X,'顶部分液区不得进入左上角大孔禁入范围'
        yield from go(d,[("x",geom.SAFE_X)])
        if d.rect[1]<geom.SAFE_Y:yield from go(d,[("y",geom.SAFE_Y)])
        if d.rect[2]>geom.CH_W:
            n=len(d.target_cells());w,h=min(((w,n//w) for w in range(1,5) if n%w==0),key=lambda p:max(p)/min(p))
            yield ('reshape',(geom.SAFE_X,d.rect[1],w,h))
        # B02 两份小滴与产物上沿对齐，接触后直接组成近矩形；其他试剂保持竖直居中。
        lane_y=geom.physical_zone(ln.c,ln.r)[1]+1 if entry_height==5 else ln.Y0
        yield from go(d,[("x",gx),("y",lane_y)])
        if d.rect[3]>entry_height or d.rect[2]>geom.CH_W:
            # 通道内保持4格宽；到达本道入口后单独适度整形成底部安全带形状，驻留4s。
            w,h=shape(len(d.target_cells()))
            yield ('reshape',(gx,lane_y,w,h))
        if d.part=='b':
            # 2×3 只在本道 2×4 停靠后进入，停在其左侧。
            while ln.id in locks or f'{tag}{ln.id}' not in S.drops:yield 'wait'
        else:
            while ln.id in locks:yield 'wait'
        locks[ln.id]=d
        staging_name=f'{tag}2{ln.id}' if d.part=='b' else f'{tag}{ln.id}'
        ahead=S.get(f'{tag}{ln.id}') if d.part=='b' else None
        collected=S.drops.get(staging_name)
        entry=ln.X0+(geom.MIN_GAP if reagent=='G16' else geom.PREFERRED_GAP)
        if collected:entry=min(entry,collected.rect[0]-d.rect[2]-geom.PREFERRED_GAP)
        if ahead:entry=min(entry,ahead.rect[0]-d.rect[2]-geom.PREFERRED_GAP)
        if collected:
            d.allow=(collected.name,)
            entry=max(gx+channel_width,entry)
        else:assert entry>=gx+channel_width+geom.MIN_GAP,'入口整形会阻断转运通道'
        yield from go(d,[("x",entry)])
        S.pipeline_events.append(dict(kind='enter',port=d.port,row=d.row,step=S.nsteps))
        # 进入反应区保持原小滴形状，不再强制扩展到6格高。
        host=S.drops.get(staging_name)
        if host is None:
            # 各道试剂固定停靠右边界，与产物间隔4格；后续同种试剂从左汇入。
            right=(ahead.rect[0] if ahead else S.get(ln.name).rect[0])-(geom.MIN_GAP if reagent=='G16' else geom.PREFERRED_GAP)
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
        retired_padding=getattr(d,'transport_padding_cells',0)+getattr(host,'transport_padding_cells',0)
        # 保持各自原形直至接触，随后才用完整紧凑框承接融合液。
        # 此时取消运输余量，不先拉回2×5，也不输出不同高度拼出的L形中间框。
        W,H=shape(len(d.target_cells())+len(host.target_cells())-retired_padding)
        union=(right-W,lane_y,W,H)
        incoming=d.vol
        yield ('merge',union,host.name,incoming)
        L,H=shape(len(d.target_cells()))
        reserve=channel_width+geom.PREFERRED_GAP if total<vol-1e-7 else 0
        if right-L-reserve < gx+channel_width+geom.PREFERRED_GAP and not getattr(S,'clearance_context',None):
            # 连续运输尚未清空：暂保紧凑等面积轮廓，整列就绪后再整成完整矩形。
            # 不能在通道还在行进时将34格拉成17格宽并堵住入口。
            L,H=geom.rect_len(total,geom.LANE_H),geom.LANE_H
        for rect in reshape_plan(d,(right-L,lane_y,L,H)):yield ('reshape',rect)
        yield ('hold',d.rect,2000)
        locks.pop(ln.id)
    S.pipeline_active=True
    try:
        run(S,[(d,path(d,ln)) for d,ln in sorted(packets,key=lambda p:(-p[1].r,p[0].port,p[0].part))])
    finally:S.pipeline_active=False
    lanes=[Lane(c,r) for r in range(1,17)]
    for ln in lanes:
        assert abs(S.get(ln.name).vol-initial_volumes[ln.r])<1e-6,'全道备齐前产物不得融合'
        if two_part:
            a,b=S.get(f'{tag}{ln.id}'),S.get(f'{tag}2{ln.id}')
            assert abs(a.vol+b.vol-vol)<1e-6,'该道试剂未备齐'
            assert (a.rect[2:],b.rect[2:])==((2,4),(2,3)) and len(a.target_cells())==8 and len(b.target_cells())==6
            assert _gap(b.rect,a.rect)>=geom.PREFERRED_GAP
        else:
            assert abs(S.get(f'{tag}{ln.id}').vol-vol)<1e-6,'该道试剂未备齐'
        assert _gap(S.get(f'{tag}{ln.id}').rect,S.get(ln.name).rect)>=geom.MIN_GAP
    S.pipeline_events.append(dict(kind='all_ready',rows=list(range(1,17)),step=S.nsteps))
    # 再次发出静态就绪帧，所有液滴继续上电；沿用全道同距、同步接触/融合动作。
    S.step(geom.HOLD_MS,changed=[])
    if two_part:ops.merge_chain(S,lanes,[tag,f'{tag}2'])
    else:ops.merge_all(S,lanes,tag)
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
            d.port_dock=False
            p=S.add(Drop(ln.name,ln.rect(ln.mag-1,3),0))
            p.magnet_only=True
            d.allow=(p.name,);pairs.append((d.name,p.name))
        S.step(geom.HOLD_MS,changed=[n for p in pairs for n in p],touching=pairs)
        ordinary=[]
        for ln in lanes:
            d=S.get(f'P{ln.id}')
            if d.rect[3]>geom.LANE_H:
                w,h=geom.lane_shape(d.vol)
                ordinary.append((d,(ln.X0+36-w,ln.Y0,w,h)))
        reshape_many(S,ordinary,touching=pairs)
        move_rects(S,{f'P{ln.id}':geom.s_block(ln.c,ln.r,product_vol)[0] for ln in lanes},touching=pairs)
        # 离开磁控半区后恢复完整矩形，磁捕获所需的缺角不带到取液终态。
        for ln in lanes:S.get(f'P{ln.id}').port_dock=True
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

# 正式单列入口使用母框原生等分；前面的旧实现保留给独立历史试验。
from waste_grid import egress_pieces, supernatant_bites
from native_packets import deliver as deliver_drops
from native_waste import egress_pieces, supernatant_bites
