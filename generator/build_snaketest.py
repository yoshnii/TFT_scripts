"""蛇形(液柱蠕动)分液 —— 独立可行性测试工程 (A 列)。
只做一件事: 用"贪吃蛇"式液柱分液 (ops.deliver_slug) 给若干道各加一份试剂, 供真芯片验证这种分液到底能不能走。
主流程不用这个 (主流程用 rigid 级联液珠, 见 rigid.py); 这里单独隔离出来测。
    python3 build_snaketest.py [--out DIR] [--rows 8] [--vol 2.0]
每列一次分注 = 一根 vol×rows µL 的液柱: E 孔下方框承接 → 拉成 4 格宽液柱 → 进 g0 空隙下行 → 逐行在泳道进液端剥离一份 (间距 2) → 与该道常驻样本合并。"""
import argparse, os, time
import geom, ops
from ops import Lane
from scene import Scene
from model import Cmd, Flow, Script

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="out/snake_test"); ap.add_argument("--rows", type=int, default=16); ap.add_argument("--vol", type=float, default=2.0)  # 注: deliver_slug 固定跑满 16 道, --rows 仅占位
a = ap.parse_args(); t0 = time.time()
COL = 1; LANES = [Lane(COL, r) for r in range(1, a.rows + 1)]
S = Scene(); flows = []

def cmd(name):
    rel = S.flush()
    return Cmd(name, "HighVoltage", "CMD_SCRIPTCYCLE", max(1, round(sum(r.dur for r in rel) / 1000)), "SCRIPTCYCLE_FALSE", rel) if rel else None
def flow(name, cmds): flows.append(Flow(name, [c for c in cmds if c], 1))

# ── T01 蛇形分液: 一根液柱进空道, 逐行各剥 vol µL ── 这就是被测动作 (贪吃蛇)
ops.deliver_slug(S, "R", a.vol, [COL], "T")
flow(f"T01 蛇形分液 {a.vol:.2f}µL/道 × 16 道 (一根 {a.vol*16:.0f}µL 液柱: E孔方框→拉成4格宽→进g0空隙下行→逐行剥离一份, 间距2)",
     [cmd(f"液柱蠕动分液 (贪吃蛇)")])
# 每道现留一份 TR{id} 在进液端; 持续通电保持
ops.hold_all(S, 8, geom.VOLT); flow("T02 持续通电 8s (各道各留一份, 验证间距/形状是否稳定)", [cmd("保持")])

# ── 写工程 ──
CHIP = "ChipArea_192_256_96_SIRO"; os.makedirs(a.out, exist_ok=True); prj = os.path.basename(a.out.rstrip("/"))
s = Script("snake_test", flows, CHIP, 1000)
open(f"{a.out}/{s.name}.elab", "w", encoding="utf-8").write(s.xml())
open(f"{a.out}/{s.name}.elab.layout", "w", encoding="utf-8").write(s.layout_xml())
open(f"{a.out}/main.elab", "w", encoding="utf-8").write(f'<Script Name="main" Version="v0.0.0" Type="3" ChipsetType="{CHIP}" ActiveTec="1">\n    <Flow ID="1" Name="{s.name}" Include="{s.name}.elab" Repeats="1" Timeout="{sum(f.timeout for f in s.flows)}" />\n</Script>\n')
open(f"{a.out}/main.elab.layout", "w", encoding="utf-8").write(f'<Script Name="main" Version="v0.0.0.1">\n <Flow Type="1" Position="74,45" Name="{s.name}" ID="1"/>\n</Script>\n')
open(f"{a.out}/{prj}.elabprj", "w", encoding="utf-8").write(f'<?xml version="1.0" encoding="UTF-8"?>\n<project Target="{prj}" ActiveTec="1" Smart8Dir="" Description="" Instrument="3" TargetVersion="v0.0.0.1" ChipModel="{CHIP}" InstrumentName="TFT" DbName="">\n    <file include="main" BuildMd5=""/>\n    <file include="{s.name}" BuildMd5=""/>\n</project>\n')
for f in s.flows: print(f"  {f.name}: {sum(len(c.relays) for c in f.cmds)} 步 / {f.timeout/60:.1f} min")
print(f"{a.out}: {len(s.flows)} Flow / {sum(len(f.cmds) for f in s.flows)} 指令 / {sum(len(c.relays) for f in s.flows for c in f.cmds)} 步 / {sum(f.timeout for f in s.flows)/60:.1f} min / {time.time()-t0:.1f}s")
