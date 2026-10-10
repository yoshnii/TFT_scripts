"""指法测试工程: A 列 16 道, 验证"定量滴劈成两半、拉成底部两行的 2×20 液指、从压成顶部两行的文库下面穿过去、出排废空隙"。
    python3 build_fingertest.py [--out DIR] [--rows 16]
一道里只需一次分注 (S 孔 5.1 µL 有色水), 片上劈成 4.0 (定量滴替身) + 1.1 (文库替身)。"""
import argparse, os, time
import geom, ops
from ops import Lane
from scene import Scene, Drop, Worm
from model import Cmd, Flow, Script

ap = argparse.ArgumentParser(); ap.add_argument("--out", default="out/finger_test"); ap.add_argument("--rows", type=int, default=16)
a = ap.parse_args(); t0 = time.time()
COL = 1; LANES = [Lane(COL, r) for r in range(1, a.rows + 1)]
S = Scene(); flows = []; blob = {}

def cmd(name):
    rel = S.flush(); return Cmd(name, "HighVoltage", "CMD_SCRIPTCYCLE", max(1, round(sum(r.dur for r in rel) / 1000)), "SCRIPTCYCLE_FALSE", rel) if rel else None
def flow(name, cmds): flows.append(Flow(name, [c for c in cmds if c], 1))
def set_rects(rects, dur=2000, touching=()):
    """rects: {name: rect} 一步到位 (形状变化)。"""
    for n, rc in rects.items(): S.get(n).rect = tuple(rc)
    S.step(dur, changed=list(rects), touching=touching)
def R(ln, x, dy, w, h): return (ln.X0 + x, ln.Y0 + dy, w, h)

# ── T01 上样: S 孔下 17×6 方框 (5.1 µL) 通电等分注 ──
for ln in LANES: S.add(Drop(f"M{ln.r}", geom.s_block(ln.c, ln.r, 5.1), 5.1))
ops.hold_all(S, ops.T_LOAD, geom.VOLT)
flow("T01 上样 5.1µL (S 孔下 17×6 方框通电 60s 等分注)", [cmd("S 孔方框承接")])

# ── T02 劈裂: 定量滴替身 4.0 µL (左, 14×6) + 文库替身 1.1 µL (右, 4×6) → 文库移到右端 ──
set_rects({f"M{ln.r}": R(ln, 0, 0, 23, 6) for ln in LANES})                               # 拉长到 23 格
for ln in LANES:
    m = S.get(f"M{ln.r}"); m.name = f"Q{ln.r}"; S.drops[m.name] = S.drops.pop(f"M{ln.r}"); m.rect = R(ln, 0, 0, 14, 6); m.vol = 4.0
    S.add(Drop(f"L{ln.r}", R(ln, 15, 0, 4, 6), 1.1))                                       # 颈缩: 间距 1
S.step(2000, touching=[(f"Q{ln.r}", f"L{ln.r}") for ln in LANES], changed=[f"Q{ln.r}" for ln in LANES] + [f"L{ln.r}" for ln in LANES])
set_rects({f"L{ln.r}": R(ln, 16, 0, 4, 6) for ln in LANES})                               # 断开: 间距 2
ops.move_rects(S, {f"L{ln.r}": ln.X0 + 30 for ln in LANES})                               # 文库到右端 30..34 (离排废空隙留 2 格: 别的道的液指要从旁边下去)
flow("T02 劈成 定量滴 4.0µL(左) + 文库 1.1µL(右端)", [cmd("拉长 → 颈缩 → 断开 → 文库移到右端")])

# ── T03 定量滴劈成两半 2.0 + 2.0 ──
set_rects({f"Q{ln.r}": R(ln, 0, 0, 17, 6) for ln in LANES})
for ln in LANES:
    q = S.get(f"Q{ln.r}"); q.rect = R(ln, 0, 0, 7, 6); q.vol = 2.0
    S.add(Drop(f"H{ln.r}", R(ln, 8, 0, 7, 6), 2.0))
S.step(2000, touching=[(f"Q{ln.r}", f"H{ln.r}") for ln in LANES], changed=[f"Q{ln.r}" for ln in LANES] + [f"H{ln.r}" for ln in LANES])
set_rects({f"H{ln.r}": R(ln, 9, 0, 7, 6) for ln in LANES})
flow("T03 定量滴劈成两个 2.0µL 半份 (7×6 + 7×6)", [cmd("拉长 → 颈缩 → 断开")])

# ── T04 文库压成顶部两行 2×11 ──
for shape in ((28, 0, 6, 4), (26, 0, 8, 3), (23, 0, 11, 2)):
    set_rects({f"L{ln.r}": R(ln, *shape) for ln in LANES})
flow("T04 文库压成顶部两行 2×11 (4×6 → 6×4 → 8×3 → 11×2, 右端留 2 格)", [cmd("文库压扁")])

# ── T05 / T06 半份拉成底部两行 2×21 液指, 从文库下面穿过, 出排废空隙 ──
def finger_pass(tag, x0, title):
    for shape in ((x0, 2, 10, 4), (x0, 4, 21, 2)):                    # 7×6 → 10×4 (下 4 行) → 21×2 (下 2 行)
        set_rects({f"{tag}{ln.r}": R(ln, *shape) for ln in LANES})
    c1 = cmd("半份拉成底部两行的 2×21 液指")
    items = []
    for ln in LANES:
        d = S.get(f"{tag}{ln.r}"); S.remove(d.name)
        items.append((ln, S.add(Worm(f"E{ln.r}", d.vol, geom.CH_W, ops.lane_slices(d.rect, True)))))
    S.step(changed=[w.name for _, w in items])
    ops.egress(S, items, blob)
    flow(title, [c1, cmd("液指沿底部两行向右穿过文库 → 进排废空隙 → 底部转运带 → F1 大滴 → 抽走 (16 道按长度分 2 组错开)")])
finger_pass("H", 9, "T05 半份1 (右边那个) 穿过文库 → 排废")
finger_pass("Q", 0, "T06 半份2 (左边那个) 穿过文库 → 排废")

# ── T07 文库复原并回 TEC 端 ──
for shape in ((26, 0, 8, 3), (28, 0, 6, 4), (30, 0, 4, 6)):
    set_rects({f"L{ln.r}": R(ln, *shape) for ln in LANES})
ops.move_rects(S, {f"L{ln.r}": ln.X0 for ln in LANES}); ops.hold_all(S, 8, geom.VOLT)
flow("T07 文库复原 4×6 → 回 TEC 端 持续通电 8s", [cmd("文库复原")])

# ── 写工程 ──
CHIP = "ChipArea_192_256_96_SIRO"; os.makedirs(a.out, exist_ok=True); prj = os.path.basename(a.out.rstrip("/"))
s = Script("finger_test", flows, CHIP, 1000)
open(f"{a.out}/{s.name}.elab", "w", encoding="utf-8").write(s.xml()); open(f"{a.out}/{s.name}.elab.layout", "w", encoding="utf-8").write(s.layout_xml())
open(f"{a.out}/main.elab", "w", encoding="utf-8").write(f'<Script Name="main" Version="v0.0.0" Type="3" ChipsetType="{CHIP}" ActiveTec="1">\n    <Flow ID="1" Name="{s.name}" Include="{s.name}.elab" Repeats="1" Timeout="{sum(f.timeout for f in s.flows)}" />\n</Script>\n')
open(f"{a.out}/main.elab.layout", "w", encoding="utf-8").write(f'<Script Name="main" Version="v0.0.0.1">\n <Flow Type="1" Position="74,45" Name="{s.name}" ID="1"/>\n</Script>\n')
open(f"{a.out}/{prj}.elabprj", "w", encoding="utf-8").write(f'<?xml version="1.0" encoding="UTF-8"?>\n<project Target="{prj}" ActiveTec="1" Smart8Dir="" Description="" Instrument="3" TargetVersion="v0.0.0.1" ChipModel="{CHIP}" InstrumentName="TFT" DbName="">\n    <file include="main" BuildMd5=""/>\n    <file include="{s.name}" BuildMd5=""/>\n</project>\n')
for f in s.flows: print(f"  {f.name}: {sum(len(c.relays) for c in f.cmds)} 步 / {f.timeout/60:.1f} min")
print(f"{a.out}: {len(s.flows)} Flow / {sum(len(f.cmds) for f in s.flows)} 指令 / {sum(len(c.relays) for f in s.flows for c in f.cmds)} 步 / {sum(f.timeout for f in s.flows)/60:.1f} min / {time.time()-t0:.1f}s")
