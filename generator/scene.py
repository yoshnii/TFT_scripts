"""场景: 芯片上所有液滴。每一步 (Relay) 都包含全部液滴的电极块 —— 用户要求: 液滴必须时刻带电。"""
from collections import deque
import geom

class Relay:
    __slots__ = ("codes", "dur", "amp")
    def __init__(self, codes, dur, amp): self.codes, self.dur, self.amp = codes, dur, amp
    def xml(self, i):
        return (f'<RelayItem ItemID="{i}" RelayCode="{";".join(self.codes)}" Duration="{self.dur}" '
                f'WaveForm="{geom.WAVE}" Freq="{geom.FREQ}" Amp="{self.amp}" />')

def _overlap(a, b):
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])
def _gap(a, b):
    gx = max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2])
    gy = max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3])
    return max(gx, gy)

class Drop:
    """静止液滴: 一个矩形 (X,Y,W,H)。"""
    def __init__(self, name, rect, vol):
        self.name, self.rect, self.vol = name, tuple(int(v) for v in rect), vol
    @property
    def rect(self):return self._rect
    @rect.setter
    def rect(self,value):
        value=tuple(map(int,value));old=getattr(self,'_rect',None)
        if old and old[2:]==value[2:] and getattr(self,'contour_signature',None)==(old,getattr(self,'vol',None)):
            dx,dy=value[0]-old[0],value[1]-old[1]
            self.contour_cells=frozenset((x+dx,y+dy) for x,y in self.contour_cells)
            self.contour_signature=(value,self.vol)
        self._rect=value
    def rects(self):
        # 无计划液相的磁珠仅作为磁铁位置记录，不当作0.9µL液滴继续上电。
        if getattr(self,'magnet_only',False):
            assert self.vol==0,'有液体时不得关闭液滴电极'
            return []
        import contour
        return contour.rectangles(self.target_cells())
    def target_cells(self):
        import contour
        signature=(self.rect,self.vol)
        if getattr(self,'contour_signature',None)==signature:return self.contour_cells
        n=geom.cells(self.vol) if self.vol>0 else self.rect[2]*self.rect[3]
        return contour.fill(self.rect,n)
    def set_cells(self,cells):
        self.contour_signature=(self.rect,self.vol);self.contour_cells=frozenset(cells)

class Worm(Drop):
    """转运中的液滴: 由电极切片组成的队列 (尾 → 头)。初始切片就是液滴当前的整块 (孔下承接方框 / 泳道里的液滴, 按 1 格切开),
    液体始终整体带电; 头部沿通道 (宽 w) 一格一格前进, 尾部按面积守恒收缩 —— 不会"贪吃蛇"式凭空长出。"""
    def __init__(self, name, vol, w, slices, first=None):
        self.name, self.vol, self.w = name, vol, w
        self.slices = deque(tuple(int(v) for v in s) for s in slices)
        self.first = first                                     # 第一步的头部切片 (方框出口), 之后按方向自动推算
        self.cells = max(1, geom.cells(vol))                   # 液体面积 (格)
        self.n = max(1, -(-self.cells // w))                   # 完全进入通道后的切片数
    def area(self): return sum(s[2] * s[3] for s in self.slices)
    def head_box(self):
        last = list(self.slices)[-self.w:]
        x0 = min(s[0] for s in last); y0 = min(s[1] for s in last)
        x1 = max(s[0] + s[2] for s in last); y1 = max(s[1] + s[3] for s in last)
        return (x0, y0, x1 - x0, y1 - y0)
    def next_slice(self, d):
        if self.first: f, self.first = self.first, None; return f
        hs = self.slices[-1]; x, y, w, h = self.head_box()
        if d in ("+X", "-X"):
            if getattr(self, "lane_cols", None): y, h = self.lane_cols   # 进入泳道: 列高固定为泳道高 (6), 顶在泳道上沿
            elif hs[2] == 1:                                   # 沿列继续: 保持列高; 比通道宽的 (泳道 6 / 方框) 收窄到通道宽, 贴底 (方框从底部几行出液)
                y, h = hs[1], hs[3]
                if h > self.w: y, h = y + h - self.w, self.w
            else: h = max(h, self.w)                           # 由行转列: 取通道宽
            return (x + w, y, 1, h) if d == "+X" else (x - 1, y, 1, h)
        else:
            if hs[3] == 1:                                     # 沿行继续
                x, w = hs[0], hs[2]
                if w > self.w: x, w = x + (w - self.w) // 2, self.w
            else: w = max(w, self.w)                           # 由列转行: 取通道宽 (左沿对齐空隙)
            return (x, y + h, w, 1) if d == "+Y" else (x, y - 1, w, 1)
    def grow(self, d):    self.slices.append(self.next_slice(d))                  # 头伸一格, 尾不动 (面积不足时)
    def advance(self, d):                                                          # 头进一格, 尾按面积守恒收缩
        self.slices.append(self.next_slice(d))
        while len(self.slices) > 1 and self.area() - self.slices[0][2] * self.slices[0][3] >= self.cells:
            self.slices.popleft()
    def shrink_head(self, k=None, cells=None):
        """剥离一份 (按面积, 格): 先收通道里的头部切片 (行 / 通道宽的列); 头部退到方框 (高柱) 时改从尾部远端收 —— 方框作为储液池从远端缩, 出口不变。"""
        need = cells if cells is not None else k * self.w; popped = 0
        while need > 0 and len(self.slices) > 1:
            h = self.slices[-1]
            if h[2] == 1 and h[3] > self.w:                                        # 头部已是方框列: 从尾部 (远端) 收
                t = self.slices[0]; a = t[2] * t[3]
                if a <= need: self.slices.popleft(); popped += a; need -= a
                else: self.slices[0] = (t[0], t[1] + need, 1, t[3] - need) if t[2] == 1 else (t[0], t[1], t[2] - need, t[3]); popped += need; need = 0
            else:
                a = h[2] * h[3]; self.slices.pop(); popped += a; need -= a
        self.cells = max(1, self.cells - popped); self.vol = max(0.0, self.vol - popped * geom.CELL_UL)
        self.n = max(1, -(-self.cells // self.w))
    def full(self): return self.area() >= self.cells
    def head(self): return self.slices[-1]
    def rects(self):
        """把切片合并成最少的矩形 (与入队顺序无关)。"""
        xs, ys, out = {}, {}, []
        for X, Y, W, H in self.slices:
            if W == 1: xs.setdefault((Y, H), []).append(X)
            else:      ys.setdefault((X, W), []).append(Y)
        for (Y, H), lst in xs.items():
            for v in sorted(set(lst)):
                if out and out[-1][4] == "x" and out[-1][1] == Y and out[-1][3] == H and out[-1][0] + out[-1][2] == v:
                    out[-1] = (out[-1][0], Y, out[-1][2] + 1, H, "x")
                else: out.append((v, Y, 1, H, "x"))
        for (X, W), lst in ys.items():
            for v in sorted(set(lst)):
                if out and out[-1][4] == "y" and out[-1][0] == X and out[-1][2] == W and out[-1][1] + out[-1][3] == v:
                    out[-1] = (X, out[-1][1], W, out[-1][3] + 1, "y")
                else: out.append((X, v, W, 1, "y"))
        return [r[:4] for r in out]

class Scene:
    def __init__(self):
        self.drops, self.relays, self.nsteps = {}, [], 0
        self.contour_previous={};self.contour_events=[]
    def add(self, d): assert d.name not in self.drops, d.name; self.drops[d.name] = d; return d
    def remove(self, name): self.drops.pop(name, None)
    def get(self, name): return self.drops[name]
    def step(self, dur=geom.STEP_MS, amp=geom.VOLT, touching=(), changed=None):
        import contour
        # 后台流水线（底部排废运输）在每一帧先走一步，与前台动作记录在同一帧。
        background=getattr(self,'background',None)
        if background and not getattr(self,'_in_background',False):
            self._in_background=True
            try:moved,touch,bdur=background(self)
            finally:self._in_background=False
            touching=tuple(touching)+tuple(touch)
            if changed is not None:changed=list(changed)+list(moved)
            dur=max(dur,bdur)
        current={id(d):d for d in self.drops.values() if not isinstance(d,Worm) and d.rects()}
        plans=[]
        removed=[info for key,info in self.contour_previous.items() if key not in current]
        for key,d in current.items():
            target=d.target_cells();old=self.contour_previous.get(key)
            if old and d.vol==old['vol'] and len(target)==len(old['cells']):
                dx=d.rect[0]-old['rect'][0];dy=d.rect[1]-old['rect'][1]
                translated=frozenset((x+dx,y+dy) for x,y in old['cells'])
                if d.rect[2:]==old['rect'][2:] and target==translated:continue
                source=old['cells']
            elif old and d.vol>old['vol']:
                x,y,w,h=d.rect
                guests=[p for p in removed if p['vol']>0 and all(x<=a<x+w and y<=b<y+h for a,b in p['cells'])]
                source=old['cells']|frozenset().union(*(p['cells'] for p in guests))
                if len(source)!=len(target) or not contour.connected(source):continue
            else:continue
            if source!=target:
                # 调用方可给出逐帧路径（每帧只改相邻少量格），否则用通用等面积形变。
                path=getattr(d,'morph_path',None);d.morph_path=None
                if path:
                    assert path[-1]==target and all(len(p)==len(target) for p in path),'指定形变路径须等面积并止于目标'
                plans.append((d,iter(path) if path else iter(contour.morph(source,target)),target))
                d.set_cells(source)
                self.contour_events.append(dict(name=d.name,area=len(source),volume_ul=d.vol,step=self.nsteps))
        active=plans
        self.contour_morphing=True
        while active:
            next_active=[];moved=[]
            for d,g,target in active:
                try:d.set_cells(next(g));next_active.append((d,g,target));moved.append(d.name)
                except StopIteration:d.set_cells(target)
            active=next_active
            if moved:self._record(1000,amp,touching,moved)
        self.contour_morphing=False
        self._record(dur,amp,touching,changed)
        self.contour_previous={key:dict(object=d,rect=d.rect,vol=d.vol,cells=d.target_cells()) for key,d in current.items()}

    def _record(self, dur=geom.STEP_MS, amp=geom.VOLT, touching=(), changed=None):
        """记录一步: 所有液滴通电。校验: 越界; 变化的液滴与其余液滴不重叠且间距 ≥ MIN_GAP (touching 里的对除外)。"""
        import contour
        for d in self.drops.values():
            if isinstance(d,Worm) or not d.rects():continue
            cells=d.target_cells()
            assert contour.connected(cells),('液滴轮廓不连通',d.name)
            if d.vol>0:assert len(cells)==geom.cells(d.vol),('电极面积与液量不一致',d.name,d.vol,len(cells))
        codes, allr = [], [(n, r) for n, d in self.drops.items() for r in d.rects()]
        for n, r in allr:
            assert 0 <= r[0] and r[0] + r[2] <= geom.GRID_W and 0 <= r[1] and r[1] + r[3] <= geom.GRID_H, f"越界 {n} {r}"
            assert geom.keep_out_ok(r), f"液滴离未使用孔过近 {n} {r} @step{self.nsteps}"
            assert not geom.between_lanes(r), f"液滴越出反应区、落在两行之间 {n} {r} @step{self.nsteps}"
            codes.append(geom.emit(r))
        chk = {n for n in changed if n in self.drops} if changed is not None else set(self.drops)
        tset = {frozenset(p) for p in touching}
        for n1 in chk:
            for r1 in self.drops[n1].rects():
                for n2, r2 in allr:
                    if n2 == n1 or frozenset((n1, n2)) in tset: continue
                    assert not _overlap(r1, r2), f"重叠 {n1}{r1} ~ {n2}{r2} @step{self.nsteps}"
                    assert _gap(r1, r2) >= geom.MIN_GAP, f"间距<{geom.MIN_GAP} {n1}{r1} ~ {n2}{r2} @step{self.nsteps}"
        self.relays.append(Relay(codes, dur, amp)); self.nsteps += 1
    def flush(self):
        r, self.relays = self.relays, []; return r
