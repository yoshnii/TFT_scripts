"""v3 动作层 —— 一切在 Scene 上进行; 每步全部液滴通电; 会撞的先后走。"""
import math, geom, contour
from scene import Scene, Drop, Worm, _gap, _overlap

T_LOAD, T_DISP, T_ASP, T_MAG, T_RESUS_MAG = 60, 12, 8, 60, 30  # 秒 (待实测); T_DISP 按一次分注 40 µL 给
SLUG_MAX_UL = 40.0                                             # 一次分注 (一个枪头) 的最大体积: 一列 16 道一次给够; 参考脚本 TE 分液是 12 µL 供 8 道
BLOB = {1: (54, 'L', 132), 2: (124, 'R', 28), 4: (212, 'L', 42)}  # 单列同高汇液框容量39.6µL；实际还受液滴电极面积向上取整限制
# 'L': 大滴左边缘锚在 X, 向右生长 (蠕虫从左侧空隙来); 'R': 右边缘锚在 X, 向左生长 (蠕虫从右侧空隙来)
MIXN = 8

class Lane:
    def __init__(self, c, r):
        self.c, self.r = c, r; self.X0, self.Y0 = geom.zone(c, r)
        self.odd = geom.odd(c); self.id = f"{'ABCDEF'[c-1]}{r}"; self.name = f"L{self.id}"   # name = 常驻液滴名
        self.mag = self.X0 + geom.MAG_DX
    def rect(self, X, L): return (X, self.Y0, L, geom.LANE_H)
    def park_x(self, mode, L):
        """常驻液滴停靠位左 X。tec: TEC 端; far: 磁铁端;
        reagent: 试剂经空隙进入时让开进液端 (奇数列右移, 偶数列在 TEC 端);
        waste: 其他道排废时让开排废侧空隙 (奇数列在 TEC 端, 偶数列右移)。"""
        if mode == "tec": return self.X0
        if mode == "tec_center":
            assert L <= geom.TEC_L, f"{self.id} 液滴宽度 {L} 超出 TEC 区"
            return self.X0 + (geom.TEC_L - L) // 2
        if mode == "far": return self.X0 + geom.LANE_L - L
        if mode == "reagent": return self.X0+geom.LANE_L-L if self.odd else self.X0
        if mode == "waste":   return self.X0 if self.odd else (self.X0 + geom.LANE_L - L)
        raise ValueError(mode)

def lanes_all(cols=range(1, 7)): return [Lane(c, r) for c in cols for r in range(1, 17)]

# ── 通用步进 ─────────────────────────────────────────────────────────
def hold_all(S, secs, amp=geom.HOLD_VOLT):
    if secs>0:S.step(round(secs*1000),amp,changed=[])

def settle_loaded(S):
    """承接与运行统一一格孔距，保持原承接位置接续，不再额外下移一格。"""
    ds=[d for d in S.drops.values() if getattr(d,'receiving',False)]
    if not ds:return
    for d in ds:
        d.receiving=False
    S.step(geom.HOLD_MS,changed=[d.name for d in ds])

def move_rects(S, moves, touching=()):
    """moves: {name: X_to}; 每步各液滴沿 X 走 1 格, 直到全部到位。"""
    settle_loaded(S)
    active = {n: x for n, x in moves.items() if S.get(n).rect[0] != x}
    while active:
        for n, x in list(active.items()):
            d = S.get(n); X, Y, W, H = d.rect
            X += 1 if x > X else -1; d.rect = (X, Y, W, H)
            if X == x: active.pop(n)
        S.step(changed=list(moves), touching=touching)

def park_all(S, lanes, mode):
    if mode == 'tec_center':
        return park_half(S, lanes, 'left')
    move_rects(S, {ln.name: ln.park_x(mode, S.get(ln.name).rect[2]) for ln in lanes})

def assert_half(S, lanes, side, tag='L', allow_590_cross=False):
    """检查实际电极轮廓，而非只检查包围框中心或磁铁位置。"""
    if side not in ('left', 'right'):
        raise ValueError(side)
    for ln in lanes:
        d = S.get(f'{tag}{ln.id}')
        exception = side=='right' and (abs(d.vol-5.00)<1e-7 or allow_590_cross and abs(d.vol-5.90)<1e-7)
        if d.vol > 0:
            (geom.magnetic_shape(d.vol, exception) if side=='right' else geom.half_shape(d.vol))
        lo = ln.X0 + (geom.TEC_L if side == 'right' else 0)
        if exception:lo=ln.X0+geom.LANE_L-geom.lane_shape(d.vol)[0]
        hi = lo + geom.TEC_L
        if exception:hi=ln.X0+geom.LANE_L
        py=geom.physical_zone(ln.c,ln.r)[1]
        if not all(lo <= x < hi and py <= y < py + geom.PHYSICAL_LANE_H
                   for x, y in d.target_cells()):
            raise ValueError(f'{ln.id} {d.vol:.2f}µL实际电极越过{side}半区边界')

def park_half(S, lanes, side, tag='L', allow_590_cross=False):
    """先独立等面积整形，再逐格平移；温控/磁控生效前须完整落在半区。"""
    import rigid
    lanes = list(lanes)
    if side not in ('left', 'right'):
        raise ValueError(side)
    # 预检整列，避免最后一孔超容量时前面孔已产生部分动作。
    targets=[(S.get(f'{tag}{ln.id}'),geom.lane_target(ln.c,ln.r,S.get(f'{tag}{ln.id}').vol,side)) for ln in lanes]
    move_rects(S,{d.name:min(rc[0],ln.X0+geom.LANE_L-d.rect[2]) for ln,(d,rc) in zip(lanes,targets)})
    rigid.reshape_many(S,[(d,rc) for d,rc in targets if d.rect!=rc])
    assert_half(S, lanes, side, tag, allow_590_cross)

# ── 加样孔 (S / B) ──────────────────────────────────────────────────
def load_holes(S, lanes, which, vol, tag, manual=False, receiving_shape=None):
    """所有道: 孔下方按体积算好的方框先通电, 等待分注 (液体一次落在方框上) → 该方框就是液滴, 命名 tag+lane。"""
    for ln in lanes:
        rect=(geom.s_block if which == "S" else geom.b_block)(ln.c,ln.r,vol)
        if receiving_shape is not None:
            assert which=='S' and tag=='L' and abs(vol-1.30)<1e-8,'B01 only'
            w,h=receiving_shape
            rect=(ln.X0+geom.S_DX-w//2,geom.physical_zone(ln.c,ln.r)[1]+geom.LOAD_GAP,w,h)
        d=S.add(Drop(f"{tag}{ln.id}",rect,vol));d.receiving=True
        if receiving_shape is not None:
            d.electrode_area=w*h;d.margin_budget=w*h-geom.cells(vol)
            d.native_authorized_padding=True;d.b01_authorized_padding=True
    if manual: S.step(geom.HOLD_MS, geom.VOLT, changed=[f"{tag}{ln.id}" for ln in lanes])
    else: hold_all(S, T_LOAD, geom.VOLT)

# ── 泳道内: 合并 / 混匀 / 劈裂 ─────────────────────────────────────
def merge_all_legacy(S, lanes, guest_tag, host_tag="L"):
    """每道: 客液滴走到宿主旁 (间距 2) → 贴合 → 合成一块 (锚在宿主远离客的一侧)。"""
    lanes=list(lanes)
    dry=[getattr(S.get(f'{host_tag}{ln.id}'),'magnet_only',False) for ln in lanes]
    if any(dry):
        assert all(dry),'干磁珠回溶不能混入已有液相反应区'
        # 干磁珠不是一滴待融合液体。新洗脱液整体逐格覆盖磁珠位置，16道同步。
        sync=dict(guest_tag=guest_tag,host_tag=host_tag,rows=[ln.r for ln in lanes],
                  ready_step=S.nsteps-1,host_volumes={f'{host_tag}{ln.id}':0 for ln in lanes},
                  mode='dry_bead_rewetting')
        targets={}
        for ln in lanes:
            g,h=S.get(f'{guest_tag}{ln.id}'),S.get(f'{host_tag}{ln.id}')
            assert h.vol==0 and g.rect[2]>=h.rect[2]
            targets[g.name]=h.rect[0]+h.rect[2]-g.rect[2]
        move_rects(S,targets)
        sync['contact_step']=S.nsteps-1
        for ln in lanes:
            g,h=S.get(f'{guest_tag}{ln.id}'),S.get(f'{host_tag}{ln.id}')
            assert g.rect[0]<=h.rect[0] and g.rect[0]+g.rect[2]>=h.rect[0]+h.rect[2]
            S.remove(g.name);S.remove(h.name);g.name=h.name;g.allow=();S.add(g)
        S.step(geom.HOLD_MS,changed=[f'{host_tag}{ln.id}' for ln in lanes])
        sync['merge_step']=S.nsteps-1
        S.reaction_merge_events=getattr(S,'reaction_merge_events',[]);S.reaction_merge_events.append(sync)
        S.step(2000,changed=[])
        return
    # 整列试剂已就绪、转运队列已清空，才允许将临时紧凑轮廓整理为完整矩形。
    # 先让宿主退到反应区右端，再用原轮廓移开客滴，避免扩宽时穿过宿主。
    prepare=[];whole=[]
    for ln in lanes:
        g=S.get(f'{guest_tag}{ln.id}')
        if g.vol>0 and len(g.target_cells())!=g.rect[2]*g.rect[3]:
            W,H=geom.lane_shape(g.vol)
            if H<=2:whole.append((ln,g))          # 完整矩形只能是细长条（如34格=17×2）：保持整高紧凑块直接贴合
            elif W*H==geom.cells(g.vol):prepare.append((ln,g,W,H))
    if whole:
        # 整高紧凑块：缺角移到远离宿主的外侧下角（底行整段逐格平移），贴合后与宿主直接组成"矩形缺一角"。
        for ln,g in whole:
            h=S.get(f'{host_tag}{ln.id}')
            assert g.rect[0]<h.rect[0] and len(h.target_cells())==h.rect[2]*h.rect[3]==h.rect[2]*geom.LANE_H,'宿主须为整高完整矩形且在客滴右侧'
            n=len(g.target_cells());H=geom.LANE_H;W=-(-n//H);miss=W*H-n
            x0=g.rect[0]+g.rect[2]-W;assert x0>=ln.X0
            want=frozenset((x0+i,ln.Y0+j) for i in range(W) for j in range(H))-frozenset((x0+i,ln.Y0+H-1) for i in range(miss))
            path=slide_bottom(g.target_cells(),want) if g.rect==(x0,ln.Y0,W,H) else None
            g.rect=(x0,ln.Y0,W,H);g.set_cells(want);g.morph_path=path or None
        S.step(MORPH_MS,changed=[g.name for ln,g in whole])
    if prepare:
        move_rects(S,{f'{host_tag}{ln.id}':ln.X0+geom.LANE_L-S.get(f'{host_tag}{ln.id}').rect[2]
                      for ln,g,W,H in prepare})
        moves={g.name:ln.X0+geom.LANE_L-S.get(f'{host_tag}{ln.id}').rect[2]-geom.MIN_GAP-W
               for ln,g,W,H in prepare}
        assert all(x>=ln.X0 for ln,g,W,H in prepare for x in [moves[g.name]])
        move_rects(S,moves)
        import rigid
        rigid.reshape_many(S,[(g,(g.rect[0],ln.Y0,W,H)) for ln,g,W,H in prepare])
    tgt = {}
    for ln in lanes:
        g, h = S.get(f"{guest_tag}{ln.id}"), S.get(f"{host_tag}{ln.id}")
        if g.rect[0] < h.rect[0]: tgt[g.name] = h.rect[0] - g.rect[2] - geom.MIN_GAP
        else:                     tgt[g.name] = h.rect[0] + h.rect[2] + geom.MIN_GAP
    move_rects(S, tgt)
    sync=dict(guest_tag=guest_tag,host_tag=host_tag,rows=[ln.r for ln in lanes],
              ready_step=S.nsteps-1,
              host_volumes={f'{host_tag}{ln.id}':S.get(f'{host_tag}{ln.id}').vol for ln in lanes})
    assert all(_gap(S.get(f'{guest_tag}{ln.id}').rect,S.get(f'{host_tag}{ln.id}').rect)==geom.MIN_GAP for ln in lanes)
    pairs, targets = [], {}
    for ln in lanes:                                  # 贴合
        g, h = S.get(f"{guest_tag}{ln.id}"), S.get(f"{host_tag}{ln.id}")
        X = h.rect[0] - g.rect[2] if g.rect[0] < h.rect[0] else h.rect[0] + h.rect[2]
        targets[g.name] = X; pairs.append((g.name, h.name))
    move_rects(S, targets, touching=pairs)
    sync['contact_step']=S.nsteps-1
    S.step(geom.HOLD_MS, touching=pairs, changed=[p[0] for p in pairs])
    targets=[]
    for ln in lanes:                                  # 合并
        g, h = S.get(f"{guest_tag}{ln.id}"), S.get(f"{host_tag}{ln.id}")
        L,H = geom.lane_shape(round(g.vol+h.vol,2))
        X = h.rect[0] + h.rect[2] - L if g.rect[0] < h.rect[0] else h.rect[0]
        union = (min(g.rect[0],h.rect[0]), ln.Y0,
                 max(g.rect[0]+g.rect[2],h.rect[0]+h.rect[2])-min(g.rect[0],h.rect[0]),geom.LANE_H)
        merged=g.target_cells()|h.target_cells()
        h.rect = union; h.vol = round(h.vol + g.vol, 2); S.remove(g.name)
        if any(g is q for _,q in whole):
            # 两块直接合并为"矩形缺外侧下角"；底行整段逐格滑到规范位置（缺角在右下），每帧只动2格。
            canon=contour.fill(union,len(merged));assert union==(X,ln.Y0,L,H)
            h.set_cells(canon);h.morph_path=slide_bottom(merged,canon) or None
        targets.append((h,(X,ln.Y0,L,H)))
    S.step(geom.HOLD_MS,changed=[h.name for h,_ in targets])
    sync['merge_step']=S.nsteps-1
    S.reaction_merge_events=getattr(S,'reaction_merge_events',[])
    S.reaction_merge_events.append(sync)
    import rigid
    rigid.reshape_many(S,targets)
    S.step(2000, changed=[f"{host_tag}{ln.id}" for ln in lanes])

def merge_all(S,lanes,guest_tag,host_tag='L'):
    """就近同步接触后再整形，不先将两滴拉至反应区两端或改变各自形状。"""
    import rigid
    settle_loaded(S);lanes=list(lanes)
    dry=all(getattr(S.get(f'{host_tag}{ln.id}'),'magnet_only',False) for ln in lanes)
    if dry:
        target={}
        for ln in lanes:
            g,h=S.get(f'{guest_tag}{ln.id}'),S.get(f'{host_tag}{ln.id}')
            target[g.name]=max(ln.X0,min(ln.X0+geom.LANE_L-g.rect[2],h.rect[0]+h.rect[2]-g.rect[2]))
        move_rects(S,target)
        for ln in lanes:
            g,h=S.get(f'{guest_tag}{ln.id}'),S.get(f'{host_tag}{ln.id}')
            S.remove(g.name);S.remove(h.name);g.name=h.name;g.allow=();S.add(g)
        S.step(geom.HOLD_MS,changed=[ln.name for ln in lanes])
        return
    event=dict(guest_tag=guest_tag,host_tag=host_tag,rows=[ln.r for ln in lanes],ready_step=S.nsteps-1,
               host_volumes={S.get(f'{host_tag}{ln.id}').name:S.get(f'{host_tag}{ln.id}').vol for ln in lanes},
               mode='direct_nearby_fusion',precontact_reshape=False,
               initial_host_rects={ln.id:list(S.get(f'{host_tag}{ln.id}').rect) for ln in lanes})
    contact={};pairs=[];fixed_guests={}
    for ln in lanes:
        g,h=S.get(f'{guest_tag}{ln.id}'),S.get(f'{host_tag}{ln.id}')
        if guest_tag=='TAMP' and h.vol<g.vol:
            # A08: keep the assembled 2µL reagent fixed; move the 0.5µL aliquot.
            contact[h.name]=(g.rect[0]+g.rect[2] if g.rect[0]<h.rect[0]
                             else g.rect[0]-h.rect[2])
            fixed_guests[g.name]=g.rect
            event['mode']='small_aliquot_approaches_fixed_AMP'
        elif getattr(g,'native_packet',False) and g.rect[3]==6:
            # 整高G16必须留在S孔左侧；由宿主靠近，不让试剂从孔下横穿。
            contact[h.name]=g.rect[0]+g.rect[2];fixed_guests[g.name]=g.rect
        else:
            contact[g.name]=h.rect[0]-g.rect[2] if g.rect[0]<h.rect[0] else h.rect[0]+h.rect[2]
        pairs.append((g.name,h.name))
    move_rects(S,contact,touching=pairs);event['contact_step']=S.nsteps-1
    if fixed_guests:
        assert all(S.get(n).rect==rc for n,rc in fixed_guests.items())
        if event['mode']=='direct_nearby_fusion':event['mode']='host_approach_avoids_sample_hole'
    else:assert all(list(S.get(f'{host_tag}{ln.id}').rect)==event['initial_host_rects'][ln.id] for ln in lanes)
    S.step(geom.HOLD_MS,touching=pairs,changed=[])
    final=[];natural=[];needs_settle=False
    for ln in lanes:
        g,h=S.get(f'{guest_tag}{ln.id}'),S.get(f'{host_tag}{ln.id}')
        cells=g.target_cells()|h.target_cells();bounds=contour.bounds(cells)
        assert len(cells)==len(g.target_cells())+len(h.target_cells())
        total=round(h.vol+g.vol,10);w,H=geom.lane_shape(total);x0,y0=geom.physical_zone(ln.c,ln.r)
        target_area=geom.electrode_cells(total)
        retired=len(cells)-target_area
        assert retired>=0 and (not retired or getattr(g,'native_authorized_padding',False)
                              or getattr(h,'a06_authorized_padding',False)),('未授权取消承接余量',g.name,retired)
        candidates=[]
        for x in range(x0,x0+geom.LANE_L-w+1):
            for y in range(y0,y0+geom.PHYSICAL_LANE_H-H+1):
                rc=(x,y,w,H);target=contour.fill(rc,target_area)
                if not (target&g.target_cells() and target&h.target_cells()):continue
                if rigid.rect_ok(S,h.name,rc,allow={g.name}):
                    candidates.append(((-len(target&cells),abs(y-ln.Y0),abs(x-bounds[0])),rc))
        assert candidates,('就近融合后无安全完整矩形',ln.id,total,bounds)
        rc=min(candidates)[1]
        final.append((h,rc));natural.append((h,g,cells,bounds,total))
    for h,g,cells,bounds,total in natural:
        target_area=geom.electrode_cells(total);retired=len(cells)-target_area
        if retired:
            S.native_padding_events.append(dict(kind='retire_at_chemical_fusion',name=g.name,
                actual_ul=total,extra_cells=retired,step=S.nsteps))
        h.vol=total;h.electrode_area=target_area;h.port_gap=None
        h.margin_budget=target_area-geom.cells(total);S.remove(g.name)
        if len(cells)==bounds[2]*bounds[3] and not retired:h.rect=bounds;h.set_cells(cells)
        else:
            # 两滴先按原形贴合保持；随后直接换成等面积完整框，避免输出L形合并中间帧。
            rc=next(rc for d,rc in final if d is h);h.rect=rc;h.set_cells(contour.fill(rc,target_area))
            needs_settle=True
    S.step(geom.LONG_MS if needs_settle else geom.HOLD_MS,changed=[h.name for h,_ in final]);event['merge_step']=S.nsteps-1
    S.reaction_merge_events=getattr(S,'reaction_merge_events',[]);S.reaction_merge_events.append(event)
    rigid.reshape_many(S,final)

def merge_chain(S,lanes,guest_tags,host_tag='L'):
    for tag in guest_tags:merge_all(S,lanes,tag,host_tag)

def merge_chain_legacy(S, lanes, guest_tags, host_tag="L"):
    """同一试剂的多份完整矩形小滴在宿主同一侧（进液端）依次排队：
    全道同步逐格靠到 MIN_GAP → 依次贴合成一串 → 一次融合，再逐格整形为 lane_shape。
    guest_tags 由宿主向外排列。各份不先彼此汇合，避免形成细长条。"""
    lanes=list(lanes)
    host_of=lambda ln:S.get(f'{host_tag}{ln.id}')
    approach={}
    for ln in lanes:
        inner=host_of(ln).rect[0]
        for t in guest_tags:
            g=S.get(f'{t}{ln.id}')
            assert g.rect[0]+g.rect[2]<=inner and g.rect[1]==ln.Y0,'各份须在宿主进液侧且与宿主上沿对齐'
            inner-=geom.MIN_GAP+g.rect[2];approach[g.name]=inner
    move_rects(S,approach)
    sync=dict(guest_tag='+'.join(guest_tags),host_tag=host_tag,rows=[ln.r for ln in lanes],
              ready_step=S.nsteps-1,host_volumes={host_of(ln).name:host_of(ln).vol for ln in lanes})
    contact,pairs={},[]
    for ln in lanes:
        inner,chain=host_of(ln).rect[0],[host_of(ln).name]
        for t in guest_tags:
            g=S.get(f'{t}{ln.id}');inner-=g.rect[2];contact[g.name]=inner
            pairs.extend((g.name,other) for other in chain);chain.append(g.name)
    move_rects(S,contact,touching=pairs)
    sync['contact_step']=S.nsteps-1
    S.step(geom.HOLD_MS,touching=pairs,changed=[p[0] for p in pairs])
    targets=[]
    for ln in lanes:
        h=host_of(ln);guests=[S.get(f'{t}{ln.id}') for t in guest_tags]
        total=round(h.vol+sum(g.vol for g in guests),2);L,H=geom.lane_shape(total)
        left=min(g.rect[0] for g in guests);right=h.rect[0]+h.rect[2]
        h.rect=(left,ln.Y0,right-left,geom.LANE_H);h.vol=total
        for g in guests:S.remove(g.name)
        targets.append((h,(right-L,ln.Y0,L,H)))
    S.step(geom.HOLD_MS,changed=[h.name for h,_ in targets])
    sync['merge_step']=S.nsteps-1
    S.reaction_merge_events=getattr(S,'reaction_merge_events',[])
    S.reaction_merge_events.append(sync)
    import rigid
    rigid.reshape_many(S,targets)
    S.step(2000, changed=[f"{host_tag}{ln.id}" for ln in lanes])

def slide_bottom(cells,target):
    """两轮廓除最底行外完全相同、底行各为连续一段且等长：每帧将底行整段平移1格，返回逐帧轮廓（不含起点）。"""
    cells=frozenset(cells);target=frozenset(target)
    yb=max(y for x,y in cells)
    if yb!=max(y for x,y in target):return None
    rest=frozenset(p for p in cells if p[1]!=yb)
    if rest!=frozenset(p for p in target if p[1]!=yb):return None
    a=sorted(x for x,y in cells if y==yb);b=sorted(x for x,y in target if y==yb)
    if len(a)!=len(b) or a!=list(range(a[0],a[0]+len(a))) or b!=list(range(b[0],b[0]+len(b))):return None
    frames=[];x=a[0];step=1 if b[0]>x else -1
    while x!=b[0]:
        x+=step;frames.append(rest|frozenset((i,yb) for i in range(x,x+len(a))))
    return frames

def mix_all(S, lanes, cycles, span=None, tag="L",full_region=False):
    """在当前位置附近往复: 每道在 [X, X+span] 之间来回 (span 默认到 TEC 段末端)。"""
    lanes=list(lanes)
    if full_region:
        move_rects(S,{f'{tag}{ln.id}':ln.X0 for ln in lanes})
        return mix_range(S,{f'{tag}{ln.id}':(ln.X0,ln.X0+geom.LANE_L-S.get(f'{tag}{ln.id}').rect[2]) for ln in lanes},cycles)
    seq = []
    for ln in lanes:
        d = S.get(f"{tag}{ln.id}"); lo = d.rect[0]
        hi = lo + (span if span is not None else max(1, geom.TEC_L - d.rect[2]))
        hi = min(hi, ln.X0 + geom.LANE_L - d.rect[2]); seq.append((d, lo, hi))
    for _ in range(cycles):
        for direction in (+1, -1):
            steps = max(hi - lo for _, lo, hi in seq)
            for _ in range(steps):
                for d, lo, hi in seq:
                    X = d.rect[0] + direction
                    if lo <= X <= hi: d.rect = (X, d.rect[1], d.rect[2], d.rect[3])
                S.step(changed=[d.name for d, _, _ in seq])

def mix_range(S, spec, cycles):
    """spec: {name: (lo, hi)} 每个液滴在 [lo, hi] 之间往复 (左 X), 全部同步。"""
    for _ in range(cycles):
        for direction in (+1, -1):
            for _ in range(max(hi - lo for lo, hi in spec.values())):
                for n, (lo, hi) in spec.items():
                    d = S.get(n); X = d.rect[0] + direction
                    if lo <= X <= hi: d.rect = (X, d.rect[1], d.rect[2], d.rect[3])
                S.step(changed=list(spec))

def split_all_legacy(S, lanes, keep_vol, away_tag, tag="L"):
    """选共同整数高度，完整母矩形原位分区后逐格拉开，不造缺角母滴。"""
    lanes=list(lanes)
    keep_cells=geom.cells(keep_vol)
    plan, moves = {}, {}
    for ln in lanes:
        d=S.get(f'{tag}{ln.id}');away_cells=geom.cells(d.vol-keep_vol)
        options=[h for h in range(1,geom.LANE_H+1) if keep_cells%h==away_cells%h==0
                 and (keep_cells+away_cells)//h+geom.CUT_GAP<=geom.LANE_L]
        assert options,'该分样比例不能直接切成同高完整矩形'
        H=max(options);Lk,La=keep_cells//H,away_cells//H
        plan[ln.name]=(d,Lk,La,H)
    import rigid
    rigid.reshape_many(S,[(d,(d.rect[0],ln.Y0,Lk+La,H))
                          for ln in lanes for d,Lk,La,H in [plan[ln.name]]])
    move_rects(S,{ln.name:ln.X0+geom.CUT_GAP//2 for ln in lanes})
    specs=[]
    for ln in lanes:                                                 # 子块各外移 CUT_GAP/2，保留块回到 Xk
        d,Lk,La,H=plan[ln.name]
        Xk=ln.X0 if ln.odd else ln.X0+La+geom.CUT_GAP
        Xa=ln.X0+Lk+geom.CUT_GAP if ln.odd else ln.X0
        keep=((Xk,ln.Y0,Lk,H),keep_vol,d.name)
        away=((Xa,ln.Y0,La,H),round(d.vol-keep_vol,3),f'{away_tag}{ln.id}')
        children=(keep,away) if ln.odd else (away,keep)
        specs.append((d,tuple(c[0] for c in children),tuple(c[1] for c in children),tuple(c[2] for c in children)))
    import rigid
    rigid.split_many(S,specs)

def split_all(S,lanes,keep_vol,away_tag,tag='L'):
    """保留半份或四分之一；四分之一由两次对半获得，废份在独立区域再合并。"""
    import rigid
    lanes=list(lanes);settle_loaded(S)
    volumes={S.get(f'{tag}{ln.id}').vol for ln in lanes}
    assert len(volumes)==1
    total=next(iter(volumes));assert round(total/keep_vol,6) in (2,4),'仅支持两次内对半取样'
    if total==2. and keep_vol==.5:
        from native_packets import quarter_cdna
        return quarter_cdna(S,lanes,tag,away_tag)
    # 为切分两侧各1格逐帧外移预留边界。
    move_rects(S,{f'{tag}{ln.id}':ln.X0+geom.CUT_GAP//2 for ln in lanes})
    names=[(f'{tag}{ln.id}',f'{away_tag}{ln.id}') for ln in lanes]
    rigid.bisect_many(S,[S.get(f'{tag}{ln.id}') for ln in lanes],names)
    move_rects(S,{f'{away_tag}{ln.id}':ln.X0+geom.LANE_L-S.get(f'{away_tag}{ln.id}').rect[2] for ln in lanes})
    if round(total/keep_vol)==4:
        move_rects(S,{f'{tag}{ln.id}':ln.X0+geom.CUT_GAP//2 for ln in lanes})
        rigid.bisect_many(S,[S.get(f'{tag}{ln.id}') for ln in lanes],
                          [(f'{tag}{ln.id}',f'{away_tag}extra{ln.id}') for ln in lanes])
        merge_all(S,lanes,away_tag+'extra',away_tag)

# ── 磁控 ───────────────────────────────────────────────────────────
def to_magnet_all(S, lanes, tag="L", right_half=False, allow_590_cross=False):
    if right_half:
        return park_half(S, lanes, 'right', tag, allow_590_cross)
    moves = {}
    for ln in lanes:
        d = S.get(f"{tag}{ln.id}"); moves[d.name] = ln.mag - d.rect[2] // 2
    move_rects(S, moves)
    for ext in (2, 4):                                 # 在磁铁上拉长 (离共用排废空隙一端留 2 格: 另一奇偶列先排废时液柱要从旁边过)
        for ln in lanes:
            d = S.get(f"{tag}{ln.id}"); L0 = geom.rect_len(d.vol, geom.LANE_H)
            lo = ln.X0 + (0 if ln.odd else geom.MIN_GAP); hi = ln.X0 + geom.LANE_L - L0 - ext - (geom.MIN_GAP if ln.odd else 0)
            X = max(lo, min(ln.mag - (L0 + ext) // 2, hi))
            d.rect = ln.rect(X, L0 + ext)
        S.step(2000, changed=[f"{tag}{ln.id}" for ln in lanes])

def pellet_split(S, lanes, pellet_vol, liquid_tag, tag="L"):
    """磁铁升起后: 磁珠团 (3 格宽, 磁铁上) 留作常驻 tag; 液相成为 liquid_tag 液滴, 位于排废侧, 间距 2。"""
    pairs = []
    for ln in lanes:
        d = S.get(f"{tag}{ln.id}"); Lq = geom.rect_len(d.vol - pellet_vol, geom.LANE_H)
        pel = ln.rect(ln.mag - 1, 3)
        if ln.odd:  Xq = ln.mag + 2 + geom.MIN_GAP                     # 液相在右
        else:       Xq = ln.mag - 1 - geom.MIN_GAP - Lq                # 液相在左
        Xq = max(ln.X0, min(Xq, ln.X0 + geom.LANE_L - Lq))
        q = S.add(Drop(f"{liquid_tag}{ln.id}", ln.rect(Xq, Lq), d.vol - pellet_vol))
        d.rect, d.vol = pel, pellet_vol; pairs.append((d.name, q.name))
    S.step(2000, touching=pairs, changed=[n for p in pairs for n in p])

def resuspend_all(S, lanes, cycles=6, tag="L"):
    move_rects(S, {f"{tag}{ln.id}":ln.X0 for ln in lanes})
    mix_range(S, {f"{tag}{ln.id}":(ln.X0,ln.X0+geom.LANE_L-S.get(f"{tag}{ln.id}").rect[2]) for ln in lanes},cycles)

# ── 空隙转运: 试剂配送 / 废液排出 ─────────────────────────────────
def _plan_path(w, targets):
    """把蠕虫头部依次送到 targets=[(axis, value)]: axis 'x' → head_box.x==value, 'y' → head_box.y==value。"""
    for axis, val in targets:
        while True:
            hb = w.head_box(); cur = hb[0] if axis == "x" else hb[1]
            if cur == val: break
            d = ("+X" if val > cur else "-X") if axis == "x" else ("+Y" if val > cur else "-Y")
            yield d

def _tick(S, gens, changed, touching=()):
    """让每个活动生成器走一步 (grow 或 advance), 然后记录一步。"""
    done = []
    for w, g in gens:
        try:
            d = next(g)
            (w.grow if not w.full() else w.advance)(d)
        except StopIteration:
            done.append((w, g))
    for x in done: gens.remove(x)
    S.step(changed=changed, touching=touching)

def slug_at_hole(S, name, ul, gx):
    """在离空隙 gx 最近的顶边 E 孔下起一个按体积算好的承接方框 (液体一次落上去), 返回以它为初始切片的液柱。
    出口: 空隙在方框左/右侧 → 从方框底部 4 行水平出去; 空隙在方框正下方 → 从底边垂直出去。"""
    k = geom.nearest_hole(gx); bx, by, W, H = geom.e_block(k, ul); cw = geom.CH_W
    cols = list(range(bx, bx + W))
    if gx + cw <= bx:   cols.sort(reverse=True); first = (bx - 1, by + H - cw, 1, cw)          # 远 → 近 (右 → 左)
    elif gx >= bx + W:  cols.sort();              first = (bx + W, by + H - cw, 1, cw)
    else:
        cx = gx + cw / 2; cols.sort(key=lambda x: -abs(x + 0.5 - cx)); first = (gx, by + H, cw, 1)   # 最远的列先收
    w = S.add(Worm(name, ul, cw, [(x, by, 1, H) for x in cols], first)); w.exit_y = by + H; return w   # exit_y: 方框底边 (液柱在此转入空隙下行)

def deliver_slug(S, reagent, vol, cols, tag):
    """[旧法: 液柱] 试剂 reagent (每道 vol µL) 送到 cols 各列的全部 16 道, 命名 tag+lane。
    每列: E 孔下承接方框一次接住整段液体 → 拉成液柱进入本列进液空隙下行 → 逐行剥离一份到泳道进液端 (间距 2 停住)。
    cols 必须同奇偶 (共用同一组空隙时分两阶段调用)。"""
    b = max(1, min(16, int(SLUG_MAX_UL // vol)))
    Lin = geom.rect_len(vol, geom.LANE_H); k_peel = max(1, math.ceil(geom.cells(vol) / geom.CH_W))
    for r0 in range(1, 17, b):
        rows = list(range(r0, min(r0 + b, 17)))
        worms = []
        for c in cols:
            gx = geom.gap_x(geom.reagent_gap(c))
            worms.append((slug_at_hole(S, f"W{reagent}{c}", vol * len(rows), gx), c, gx))
        hold_all(S, T_DISP, geom.VOLT)                                   # 承接方框通电, 等待分注
        # 阶段 1: 全部液柱同步走到各自空隙 (水平出口的先横移到空隙上方, 再向下; 垂直出口的直接向下)
        gens = [(w, _plan_path(w, [("x", gx), ("y", w.exit_y)])) for w, c, gx in worms]
        while gens: _tick(S, gens, [w.name for w, _, _ in worms])
        # 阶段 2: 逐行下行 + 剥离 (各列同步, 行相同)
        for r in rows:                                                   # 剥离后头部可能退回转运带/方框, 所以每行都先对准空隙再下行
            gens = [(w, _plan_path(w, [("x", gx), ("y", geom.zone(c, r)[1] + 1)])) for w, c, gx in worms]
            while gens: _tick(S, gens, [w.name for w, _, _ in worms])
            pairs, names = [], []
            for w, c, gx in worms:                                        # 剥离: 泳道进液端起一块与蠕虫贴合
                ln = Lane(c, r); X = ln.X0 if ln.odd else ln.X0 + geom.LANE_L - Lin; Lb = Lin
                host = S.drops.get(ln.name); name = f"{tag}{ln.id}"
                if host:                                                  # 进液端放不下 → 块直接延伸到常驻液滴 (洗脱液进磁珠团)
                    hx0, hx1 = host.rect[0], host.rect[0] + host.rect[2]
                    if ln.odd and X + Lin + geom.MIN_GAP > hx0:
                        Lb = hx0 - X; pairs.append((name, host.name))
                    elif (not ln.odd) and hx1 + geom.MIN_GAP > X:
                        X = hx1; Lb = ln.X0 + geom.LANE_L - X; pairs.append((name, host.name))
                t = S.add(Drop(name, ln.rect(X, Lb), vol)); pairs.append((w.name, t.name)); names.append(t.name)
            S.step(2000, touching=pairs, changed=names)
            touch2, chg = [], []
            for w, c, gx in worms:                                        # 颈缩断开: 蠕虫头缩 k 片, 块内移 2 格
                ln = Lane(c, r); t = S.get(f"{tag}{ln.id}"); w.shrink_head(cells=geom.cells(vol))
                nx = t.rect[0] + (geom.MIN_GAP if ln.odd else -geom.MIN_GAP); Lb = t.rect[2]
                host = S.drops.get(ln.name)
                gap = (host.rect[0] - (nx + Lb) if ln.odd else nx - (host.rect[0] + host.rect[2])) if host else 99   # 带符号: 已贴合时为负
                if host and gap < geom.MIN_GAP:
                    # 内移会贴上常驻液滴 → 直接合并进常驻 (洗脱液进磁珠团)
                    L = geom.rect_len(host.vol + t.vol, geom.LANE_H)
                    X = host.rect[0] + host.rect[2] - L if ln.odd else host.rect[0]
                    host.rect = ln.rect(X, L); host.vol += t.vol; S.remove(t.name); chg.append(host.name)
                else:
                    t.rect = ln.rect(nx, Lb); chg.append(t.name)
            S.step(2000, changed=chg + [w.name for w, _, _ in worms])
        for w, c, gx in worms: S.remove(w.name)                           # 最后一份剥完, 液柱耗尽
        S.step(geom.HOLD_MS, changed=[])

def _blob_rect(fk, need):
    ax, side, _ = BLOB[fk]
    return (ax, geom.BLOB_Y, need, geom.BLOB_H) if side == "L" else (ax - need, geom.BLOB_Y, need, geom.BLOB_H)

def egress(S, items, blob_state, touching=(), final=True):
    """items: [(lane, worm)] —— 液柱已在场景中 (泳道里的液滴原样切成列, 头在排废端)。
    同列按液柱长度分组错开 (组内同步); 各列的第 g 组同时走 (三列的排废空隙 / F 孔互不相同, 互不干扰);
    进本列排废空隙 → 下行到底部转运带 → 逐条并入 F 孔大滴 (间距到 2 即停); 大滴满或组末 → 延伸到 F 孔切线块 → 等待抽走。"""
    by_col = {}
    for ln, w in items: by_col.setdefault(ln.c, []).append((ln, w))
    plan = {}
    for c, lst in by_col.items():
        gk = geom.waste_gap(c); gx = geom.gap_x(gk); fk = geom.WASTE_HOLE[gk]; ax, side, bmax = BLOB[fk]
        Lw = max(max(w.n, len(w.slices)) for _, w in lst)          # 液柱满长
        m = max(1, math.ceil((Lw + 4 + geom.MIN_GAP) / 9))     # 上一条的头(4格)+间距 不得追上下一条仍横在泳道里的尾巴
        groups = [sorted([(ln, w) for ln, w in lst if (16 - ln.r) % m == g0], key=lambda t: -t[0].r) for g0 in range(m)]
        plan[c] = (gx, fk, ax, side, bmax, groups)
    def path(w, gx, fk, ax, side):
        yield from _plan_path(w, [("x", gx), ("y", geom.BOT_Y)])
        while True:                                               # 动态靠近: 与大滴间距到 2 就停
            blob, hb = blob_state.get(fk), w.head_box()
            if blob:
                if _gap(hb, blob.rect) <= geom.MIN_GAP: return
                yield "+X" if side == "L" else "-X"
            else:
                if hb[0] == ax: return
                yield "+X" if ax > hb[0] else "-X"
    for g0 in range(max(len(p[5]) for p in plan.values())):
        act = [(p, p[5][g0]) for p in plan.values() if g0 < len(p[5]) and p[5][g0]]
        if not act: continue
        gens = [(w, path(w, p[0], p[1], p[2], p[3])) for p, grp in act for ln, w in grp]
        names = [w.name for _, grp in act for ln, w in grp]; done = set()
        while gens or len(done) < len(names):
            if gens: _tick(S, gens, names, touching)
            active = {w.name for w, _ in gens}
            for p, grp in act:
                gx, fk, ax, side, bmax, _ = p
                for ln, w in grp:
                    if w.name in done or w.name in active: continue
                    done.add(w.name); blob = blob_state.get(fk)
                    need = math.ceil(geom.cells((blob.vol if blob else 0) + w.vol) / geom.BLOB_H)
                    if blob and need > bmax:
                        _aspirate(S, blob_state, fk); blob = None; need = math.ceil(geom.cells(w.vol) / geom.BLOB_H)
                    if blob:
                        S.step(geom.HOLD_MS, touching=[(w.name, blob.name)] + list(touching), changed=[w.name])
                        S.remove(w.name); blob.vol += w.vol; blob.rect = _blob_rect(fk, need)
                    else:
                        S.remove(w.name); blob = blob_state[fk] = S.add(Drop(f"BLOB{fk}", _blob_rect(fk, need), w.vol))
                    S.step(2000, changed=[blob.name], touching=touching)
    if final:                                                     # 整段排完再抽 (中途只在大滴满时抽), 少用枪头
        for p in plan.values(): _aspirate(S, blob_state, p[1])

def lane_slices(rect, exit_right, skip=()):
    """泳道里的液滴矩形 → 按列切片 (整块都带电), 排废端在头、远端在尾; skip 里的列 (磁珠团) 跳过。"""
    X, Y, W, H = rect
    cols = [x for x in range(X, X + W) if x not in skip]
    cols.sort(reverse=not exit_right)
    return [(x, Y, 1, H) for x in cols]

def worm_from_drop(S, ln, d, name):
    """把泳道里的液滴 d 原样换成液柱 (切片 = 它自己的每一列), 头在排废端。"""
    S.remove(d.name)
    return S.add(Worm(name, d.vol, geom.CH_W, lane_slices(d.rect, ln.odd)))

def _aspirate(S, blob_state, fk):
    blob = blob_state.get(fk)
    if not blob: return
    X, Y, W, H = blob.rect
    # 固定形状单独平移，把孔放在下边中间。满滴吸液时入口处可能还排着液滴；
    # 若F1居中会撞上它们，选择可安全整体平移到的相邻底边孔，而不是角对孔。
    assert W >= 2, f"F{fk} 取液框太窄，无法以一条边接近孔"
    dock_hole = fk
    if getattr(S, 'manual_aspirations', None) is not None:
        candidates = sorted(geom.HOLE_X, key=lambda k: abs(geom.HOLE_X[k]-W//2-X))
        found = False
        for k in candidates:
            target = geom.HOLE_X[k]-W//2
            if not (0 <= target and target+W <= geom.GRID_W): continue
            path = range(min(X,target), max(X,target)+1)
            if all(not _overlap((xx,Y,W,H),q) and _gap((xx,Y,W,H),q)>=geom.MIN_GAP
                   for xx in path for n,d in S.drops.items() if n != blob.name for q in d.rects()):
                dock_hole, dock_x, found = k, target, True
                break
        assert found, f"{blob.name} 无法安全居中停靠到底边孔"
    else:
        hx = geom.HOLE_X[fk]
        dock_x = max(hx-W+1, min(X,hx-1))
    move_rects(S, {blob.name: dock_x})
    X, Y, W, H = blob.rect
    blob.rect = (X, Y, W, geom.F_TANG_Y + 4 - Y)
    S.step(geom.HOLD_MS, changed=[blob.name])
    if getattr(S, 'manual_aspirations', None) is not None:
        info = dict(hole=f"F{dock_hole}", volume_ul=round(blob.vol, 3),
                    drop_names=[blob.name], rect=list(blob.rect), kind='底部废液',
                    rounding_gap_cells=0.5)
        S.manual_aspirations.append((S.relays[-1], info))
        # 场景中的移除属于吸液完成后下一 section 的第一帧，不是定时自动断电。
        S.remove(blob.name); blob_state[fk] = None
        S.step(geom.HOLD_MS, changed=[])
        return
    hold_all(S, T_ASP, geom.VOLT)
    S.remove(blob.name); blob_state[fk] = None
    S.step(geom.HOLD_MS, changed=[])

# ── 液珠分液 (参考 PCR-free WGS "4μl一拆8 → 移动到门口 → 进反应区" 与 ASI96 "分液 → 分液移动 → 移动到N列") ──
DISPENSE_MODE = "drops"          # "drops": 顶部空白带先拆成液珠再逐道送入 (单列); "slug": 旧的液柱逐行剥离
CUT_MS, MORPH_MS = 1000, 4000    # 切分逐格；允许的中等幅度形变终态驻留4秒
SLOT_X0, BAND_YC = 12, 12        # 液珠排成一行: 最左槽位 X, 顶部空白带中心 Y

def _shape(cells):
    """液滴的紧凑矩形: 高 2..16, 宽 ≥ 2, 面积 ≥ cells。"""
    h = max(2, min(16, round(math.sqrt(cells / 1.2)))); return max(2, math.ceil(cells / h)), h

def _morph(S, specs, steps=2, dur=MORPH_MS):
    """specs: [(drop, W, H, cx)] 同步分几步变形成 W×H (中心 X=cx, 垂直居中于顶部空白带)。"""
    start = {d.name: d.rect for d, *_ in specs}
    for i in range(1, steps + 1):
        for d, W, H, cx in specs:
            x0, y0, w0, h0 = start[d.name]
            w = round(w0 + (W - w0) * i / steps); h = round(h0 + (H - h0) * i / steps)
            d.rect = (round(cx - w / 2), round(BAND_YC - h / 2), w, h)
        S.step(dur, changed=[d.name for d, *_ in specs])

def _cut(S, specs):
    """一步同步切分: specs=[(drop, wc, hc, cx, (nameL, nameR))] → 每个液滴中间 2 列断电, 变成两个 wc×hc 的子液滴 (间距 2)。"""
    out = []
    for d, wc, hc, cx, names in specs:
        y = round(BAND_YC - hc / 2); half = round(d.vol / 2, 3)
        a = S.add(Drop(names[0], (round(cx) - wc - 1, y, wc, hc), half))
        b = S.add(Drop(names[1], (round(cx) + 1, y, wc, hc), round(d.vol - half, 3)))
        S.remove(d.name); out.append((a, b))
    S.step(CUT_MS, changed=[n.name for ab in out for n in ab]); return out

def _tick_traffic(S, items, corner=None, corner_lim=None):
    """items: [(worm, gen)] 按优先级 (排头在前)。每个液珠每步至多走一格; 下一格若与其他液滴距离 < MIN_GAP 就原地等待。
    corner: 空隙顶端的拐角区 (矩形); 拐角区里有别的液滴时, 其他液滴不得进入 x < corner_lim (在拐角外排队, 给拐弯的液珠展宽留位)。"""
    moved = 0
    for w, g in items:
        if getattr(w, "pending", None) is None:
            try: w.pending = next(g)
            except StopIteration: w.pending = "done"
        if w.pending == "done": continue
        s = w.next_slice(w.pending); ok = 0 <= s[0] and s[0] + s[2] <= geom.GRID_W and 0 <= s[1] and s[1] + s[3] <= geom.GRID_H
        if ok and corner and s[0] < corner_lim and not any(r[0] < corner_lim for r in w.rects()):   # 想进拐角区: 里面有人就等
            for n2, d2 in S.drops.items():
                if n2 != w.name and any(_overlap(corner, r) for r in d2.rects()): ok = False; break
        if ok:
            for n2, d2 in S.drops.items():
                if n2 == w.name: continue
                for r in d2.rects():
                    if _overlap(s, r) or _gap(s, r) < geom.MIN_GAP: ok = False; break
                if not ok: break
        if ok: (w.grow if not w.full() else w.advance)(w.pending); w.pending = None; moved += 1
    S.step(changed=[w.name for w, _ in items])
    S.stall = 0 if moved else getattr(S, "stall", 0) + 1
    if S.stall > 50:
        info = [(w.name, w.pending, w.head_box(), len(w.slices)) for w, _ in items if w.pending not in (None, "done")]
        raise RuntimeError(f"排队死锁 @step{S.nsteps}: " + "; ".join(f"{n} 想走{d} 头{hb} 片{k}" for n, d, hb, k in info[:6]))

def deliver_drops(S, reagent, vol, cols, tag):
    """液珠法: E 孔下方框一次接住 n 道的量 → 顶部空白带里逐级对半切分成 n 个液珠 (每级: 变形成 2w+2 宽 → 中间断 2 列 → 子液滴分开)
    → 液珠排成一行 → 依次沿进液空隙下行, 各自转入本行泳道进液端 (整体进泳道后离空隙 2 格), 命名 tag+lane。
    目前只支持单列 (顶部空白带整条给这一列用)。"""
    assert len(cols) == 1, "液珠分液版目前只支持单列; 多列请用 deliver_slug"
    c = cols[0]; gx = geom.gap_x(geom.reagent_gap(c)); assert geom.odd(c), "单列版按奇数列 (进液在 TEC 侧) 实现"
    vc = geom.cells(vol); n_max = max(1, min(16, int(SLUG_MAX_UL // vol)))
    n_disp = 1 << int(math.floor(math.log2(n_max)))                     # 每次分注供 2^k 道
    rows_all = list(range(16, 0, -1))                                     # 先送最深的行 (排头去第 16 行)
    wf, hf = _shape(vc); p = max(wf, geom.CH_W) + 2          # 槽位间距: 液珠进空隙后会展宽到通道宽 4, 间距按 4 算
    for start in range(0, 16, n_disp):
        rows = rows_all[start:start + n_disp]; n = len(rows); levels = int(round(math.log2(n))) if n > 1 else 0
        def centroid(a, b): return (SLOT_X0 + a * p + SLOT_X0 + b * p + wf) / 2
        # 1) E 孔下承接方框
        k = geom.nearest_hole(gx); bx, by, W, H = geom.e_block(k, vol * n)
        root = S.add(Drop(f"D{reagent}", (bx, by, W, H), vol * n)); S.step(geom.HOLD_MS, changed=[root.name])
        hold_all(S, T_DISP, geom.VOLT)
        # 2) 变成根节点形状并移到级联中心
        w0, h0 = _shape(vc * n); _morph(S, [(root, w0, h0, bx + W / 2)])
        move_rects(S, {root.name: round(centroid(0, n - 1) - w0 / 2)})
        nodes = [(root, 0, n - 1)]
        for lev in range(levels):                                          # 3) 逐级切分 (同级所有液滴同步)
            child_cells = vc * n / (2 ** (lev + 1)); wc, hc = _shape(child_cells)
            _morph(S, [(d, 2 * wc + 2, hc, centroid(a, b)) for d, a, b in nodes])       # 同步变形成 2w+2 宽
            pairs = _cut(S, [(d, wc, hc, centroid(a, b), (f"D{reagent}{lev}L{i}", f"D{reagent}{lev}R{i}")) for i, (d, a, b) in enumerate(nodes)])
            new = []
            for (l, r), (d, a, b) in zip(pairs, nodes):
                mid = (a + b + 1) // 2; new += [(l, a, mid - 1), (r, mid, b)]
            nodes = new
            move_rects(S, {d.name: round(centroid(a, b) - wc / 2) for d, a, b in nodes})   # 4) 分开到各自槽位中心
        # 5) 排队进反应区: 槽位 j → rows[j]
        items = []
        for j, (d, a, b) in enumerate(sorted(nodes, key=lambda t: t[1])):
            ln = Lane(c, rows[j]); S.remove(d.name)
            w = S.add(Worm(f"{tag}{ln.id}", d.vol, geom.CH_W, lane_slices(d.rect, exit_right=False)))   # 头在左端 (朝空隙)
            def path(w=w, ln=ln):
                yield from _plan_path(w, [("x", gx)])                   # 沿空白带向左到空隙上方
                yield from _plan_path(w, [("y", ln.Y0 + 1)])            # 沿空隙下行到本行
                w.lane_cols = (ln.Y0, geom.LANE_H)
                while min(s[0] for s in w.slices) < ln.X0 + geom.MIN_GAP: yield "+X"   # 整体进泳道, 离空隙 2 格
            items.append((w, path()))
        corner = (gx - geom.MIN_GAP, 2, geom.CH_W + 2 * geom.MIN_GAP, 22); corner_lim = gx + geom.CH_W + geom.MIN_GAP
        while items:
            _tick_traffic(S, items, corner, corner_lim)
            done = [(w, g) for w, g in items if w.pending == "done"]
            for w, g in done:                                             # 进完泳道: 液珠变成泳道矩形 (tag+lane)
                xs = [s[0] for s in w.slices]; X = min(xs); L = max(xs) - X + 1; ln_y = w.lane_cols[0]
                S.remove(w.name); S.add(Drop(w.name, (X, ln_y, L, geom.LANE_H), w.vol)); items.remove((w, g))
            if done: S.step(changed=[w.name for w, _ in done])

def deliver(S, reagent, vol, cols, tag):
    if DISPENSE_MODE == "drops" and len(cols) == 1: return deliver_drops(S, reagent, vol, cols, tag)
    return deliver_slug(S, reagent, vol, cols, tag)
