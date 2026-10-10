"""浓度驱动归一化 —— 片上"手"的可跑工程 (A 列, 单列 16 道)。
接 normalize.py 的每孔计划: 每孔文库先修到保留体积 Vkᵢ (切掉多余排废), 再从水孔按 Wᵢ 计量不同大小的水滴送进本孔合并到固定终体积 Vf。
实时版(读 Quant 当场算)与文件式(重生成)共用这段"手"; 本工程不碰任何设备协议, 纯电极序列, 可 elabcheck / 上真芯片。
    python3 build_normalize.py [quant.csv] [--out DIR] [--Ve 1.50] [--Vf 2.00]
"""
import argparse, os, time
import geom, ops, rigid, normalize
from ops import Lane
from scene import Scene, Drop
from model import Cmd, Flow, Script

ap = argparse.ArgumentParser()
ap.add_argument("csv", nargs="?"); ap.add_argument("--out", default="out/normalize_test")
ap.add_argument("--Ve", type=float, default=1.50); ap.add_argument("--Vf", type=float, default=2.00)
a = ap.parse_args(); t0 = time.time()
COL = 1; GAP = geom.gap_x(geom.reagent_gap(COL)); HOLE = geom.nearest_hole(GAP)   # A 列: g0 + 最近 E 孔
concs = normalize.load_csv(a.csv) if a.csv else normalize.demo_concs()
rows, summ = normalize.plan(concs, a.Ve, a.Vf)
plan = {int(r["well"][1:]): r for r in rows}            # 行号 → 计划 (示例孔名 A{r})
LANES = [Lane(COL, r) for r in sorted(plan)]
S = Scene(); flows = []; blob = {}

def cmd(name):
    rel = S.flush()
    return Cmd(name, "HighVoltage", "CMD_SCRIPTCYCLE", max(1, round(sum(r.dur for r in rel) / 1000)), "SCRIPTCYCLE_FALSE", rel) if rel else None
def flow(name, cmds): flows.append(Flow(name, [c for c in cmds if c], 1))
def W(ln): return geom.rect_len(plan[ln.r]["Vf"], geom.LANE_H)   # 终体积宽度

# ── T01 起始: 每道洗脱文库 Ve, 停在 TEC 端 (常驻 L{r}) ──
for ln in LANES:
    L = geom.rect_len(a.Ve, geom.LANE_H); S.add(Drop(f"L{ln.r}", ln.rect(ln.X0, L), a.Ve))
ops.hold_all(S, 4, geom.VOLT)
flow(f"T01 洗脱文库 {a.Ve:.2f}µL/道 常驻 TEC 端", [cmd("起始态")])

# ── T02 按浓度修体积: 每道文库切到保留 Vkᵢ, 多余切下排废 (等体积切, 各道保留宽不同) ──
pieces = []
for ln in LANES:
    d = S.get(f"L{ln.r}"); Lk = max(1, geom.rect_len(plan[ln.r]["Vk"], geom.LANE_H)); Le = d.rect[2]
    if Le - Lk >= 1:                                   # 有多余 → 让 1 格给切口, 等体积切: 保留(左)Lk + 多余(右)
        rigid.reshape(S, d, ln.rect(ln.X0 + 1, Le))
        l, r = rigid.split_x(d, Lk, Le - Lk)
        S.remove(d.name); S.add(Drop(f"L{ln.r}", l, plan[ln.r]["Vk"]))
        pieces.append((ln, S.add(Drop(f"X{ln.r}", r, round(a.Ve - plan[ln.r]["Vk"], 3)))))
    else:
        d.vol = plan[ln.r]["Vk"]                        # 已 ≤ 保留, 全留
if pieces:
    S.step(ops.CUT_MS, changed=[p.name for _, p in pieces] + [f"L{ln.r}" for ln in LANES])
    # egress_pieces 会把单块多余液以右沿为锚原地变成走空隙的 pw 宽小块; 窄块 (wr < pw) 会向左伸到保留块边上 → 先右移 pw−wr 格
    shift = {}
    for _, p in pieces:
        cells = geom.cells(p.vol)
        if cells <= 24: pw, _ = rigid.piece_shape(cells); shift[p.name] = p.rect[0] + max(0, pw - p.rect[2])
    ops.move_rects(S, {n: x for n, x in shift.items() if x != S.get(n).rect[0]})
    rigid.egress_pieces(S, pieces, blob, final=True)
flow("T02 按浓度修体积: 各道切到保留 Vkᵢ, 多余排废", [cmd("等体积切 → 多余出空隙 → 底边孔抽走")])

# ── T03 常驻文库让到磁铁端, 清出 TEC 侧进液端给水 ──
ops.move_rects(S, {f"L{ln.r}": ln.X0 + geom.LANE_L - geom.rect_len(plan[ln.r]["Vk"], geom.LANE_H) for ln in LANES})
flow("T03 文库让到磁铁端 (清出进液端)", [cmd("常驻让位")])

# ── T04 逐孔加水: 每孔从 E 孔按 Wᵢ 计量水滴 → 走空隙进本孔 → 与文库合并到 Vf ──
def dispense_water(ln, vol):
    """从 HOLE 孔计量 vol µL 水滴, 经 g0 空隙送到 ln 行进液端 (X0), 返回该水滴。"""
    bx, by, bw, bh = geom.e_block(HOLE, vol)
    d = S.add(Drop(f"W{ln.r}", (bx, by, bw, bh), vol)); S.step(geom.HOLD_MS, changed=[d.name])
    ops.hold_all(S, ops.T_DISP, geom.VOLT)                       # 孔下方框承接分注
    w, h = rigid.piece_shape(geom.cells(vol)); d.S = S
    rigid.reshape(S, d, (bx, by + bh - h, w, h))                 # 原地缩成能走空隙的小块 (方框底部)
    def path():
        yield from rigid.go(d, [("y", geom.TOP_Y)])              # 上到顶部转运带
        yield from rigid.go(d, [("x", GAP)])                     # 横移到 g0 空隙上方
        yield from rigid.go(d, [("y", ln.Y0 + (geom.LANE_H - h) // 2)])   # 沿空隙下到本行
        yield from rigid.go(d, [("x", ln.X0)])                   # 进泳道进液端
    rigid.run(S, [(d, path())])
    return d

merged = []
for ln in LANES:
    Wi = plan[ln.r]["water"]
    if Wi < geom.CELL_UL * geom.LANE_H / 2:                      # 不需要加水
        merged.append(ln); continue
    w = dispense_water(ln, Wi)
    host = S.get(f"L{ln.r}"); wx1 = w.rect[0] + w.rect[2]      # 水在 X0, 文库在磁铁端 → 文库左移到间距 2
    ops.move_rects(S, {host.name: wx1 + geom.MIN_GAP})
    host.rect = (wx1, host.rect[1], host.rect[2], host.rect[3])  # 贴合 (直接设, move_rects 会强制间距≥2)
    S.step(geom.HOLD_MS, touching=[(host.name, w.name)], changed=[host.name])
    Lf = W(ln); S.remove(w.name)                                 # 合并成 Vf
    host.rect = ln.rect(ln.X0, Lf); host.vol = plan[ln.r]["Vf"]
    S.step(2000, changed=[host.name]); merged.append(ln)
    ops.move_rects(S, {host.name: ln.X0 + geom.LANE_L - Lf})    # 合并后回磁铁端, 让出进液端 (下一滴水要沿空隙经过本行)
flow("T04 逐孔加水: 每孔按 Wᵢ 计量 → 走空隙进孔 → 合并到 Vf", [cmd("逐孔计量加水 + 合并")])
ops.hold_all(S, 8, geom.VOLT); flow("T05 持续通电 8s (归一化完成, 各道 = Vf)", [cmd("保持")])

# ── 写工程 ──
CHIP = "ChipArea_192_256_96_SIRO"; os.makedirs(a.out, exist_ok=True); prj = os.path.basename(a.out.rstrip("/"))
s = Script("normalize_test", flows, CHIP, 1000)
open(f"{a.out}/{s.name}.elab", "w", encoding="utf-8").write(s.xml())
open(f"{a.out}/{s.name}.elab.layout", "w", encoding="utf-8").write(s.layout_xml())
open(f"{a.out}/main.elab", "w", encoding="utf-8").write(f'<Script Name="main" Version="v0.0.0" Type="3" ChipsetType="{CHIP}" ActiveTec="1">\n    <Flow ID="1" Name="{s.name}" Include="{s.name}.elab" Repeats="1" Timeout="{sum(f.timeout for f in s.flows)}" />\n</Script>\n')
open(f"{a.out}/main.elab.layout", "w", encoding="utf-8").write(f'<Script Name="main" Version="v0.0.0.1">\n <Flow Type="1" Position="74,45" Name="{s.name}" ID="1"/>\n</Script>\n')
open(f"{a.out}/{prj}.elabprj", "w", encoding="utf-8").write(f'<?xml version="1.0" encoding="UTF-8"?>\n<project Target="{prj}" ActiveTec="1" Smart8Dir="" Description="" Instrument="3" TargetVersion="v0.0.0.1" ChipModel="{CHIP}" InstrumentName="TFT" DbName="">\n    <file include="main" BuildMd5=""/>\n    <file include="{s.name}" BuildMd5=""/>\n</project>\n')
for f in s.flows: print(f"  {f.name}: {sum(len(c.relays) for c in f.cmds)} 步")
print(f"{a.out}: {sum(len(f.cmds) for f in s.flows)} 指令 / {sum(len(c.relays) for f in s.flows for c in f.cmds)} 步 / {time.time()-t0:.1f}s")
print(f"归一化: 质量CV {summ['mass_in_cv']}%→{summ['mass_out_cv']}%; 每孔终体积 {a.Vf}µL")
