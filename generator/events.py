"""统计外部操作事件: 顶边 E 孔分注 / 底边 F 孔抽吸 / S、B 孔加样 (按流程段)。"""
import sys, math, collections, geom, ops, protocol, rigid
COLS = tuple(range(1, int(sys.argv[sys.argv.index("--cols") + 1]) + 1)) if "--cols" in sys.argv else tuple(range(1, 7))
ev = collections.OrderedDict(); cur = ["?"]
_deliver, _asp, _load = ops.deliver, ops._aspirate, ops.load_holes
def deliver(S, reagent, vol, cols, tag):
    b = max(1, min(16, int(ops.SLUG_MAX_UL // vol)))
    for r0 in range(1, 17, b):
        rows = list(range(r0, min(r0 + b, 17)))
        for c in cols:
            k = geom.nearest_hole(geom.gap_x(geom.reagent_gap(c)))
            ev.setdefault(cur[0], []).append(("E", f"E{k}", reagent, round(vol * len(rows), 2), 'ABCDEF'[c-1], f"{rows[0]}-{rows[-1]}"))
    return _deliver(S, reagent, vol, cols, tag)
def aspirate(S, blob_state, fk):
    blob = S.drops.get(f"BLOB{fk}")
    ev.setdefault(cur[0], []).append(("F", f"F{fk}", "废液", round(blob.vol, 1) if blob else None, "", ""))
    return _asp(S, blob_state, fk)
def load_holes(S, lanes, which, vol, tag):
    step = "A01 上样 1.60µL" if which == "S" else "B05 加 G11 条形码接头 0.50µL"
    ev.setdefault(step, []).append((which, f"{which} 孔×{len(lanes)}", "样本" if which == "S" else "G11 接头", vol, "", ""))
    return _load(S, lanes, which, vol, tag)
ops.deliver, ops._aspirate, ops.load_holes = deliver, aspirate, load_holes
ops.deliver_slug = deliver; rigid.deliver_drops = deliver; rigid._aspirate = aspirate
protocol.ops.deliver = deliver
_flow = protocol.Protocol.flow
def flow(self, seg, name, cmds, repeats=1):
    _flow(self, seg, name, cmds, repeats)
p = protocol.Protocol(COLS)
_cmd = p.cmd
def cmd(name):
    return _cmd(name)
# 用 flow 标题作为当前步名: 在每个复合动作前设置 cur
import types
for meth in ("add_reagent", "split_waste", "supernatant_off", "elute", "bead_capture", "quant"):
    orig = getattr(protocol.Protocol, meth)
    def wrap(self, seg, title, *a, _o=orig, **kw):
        cur[0] = title.split(" · ")[0]; return _o(self, seg, title, *a, **kw)
    setattr(protocol.Protocol, meth, wrap)
cur[0] = "A01 上样"; p2 = protocol.Protocol(COLS)
import io, contextlib
with contextlib.redirect_stderr(io.StringIO()): p2.build()
print("| 步骤 | 事件 | 孔 | 液体 | 每次体积 µL | 次数 |\n|---|---|---|---|---|---|")
for step, lst in ev.items():
    agg = collections.OrderedDict()
    for kind, hole, liq, v, col, rows in lst:
        key = (kind, hole, liq, v); agg[key] = agg.get(key, 0) + 1
    for (kind, hole, liq, v), n in agg.items():
        label = {"E": "顶边孔分注", "F": "底边孔抽吸", "S": "S 孔加样", "B": "B 孔加样"}[kind]
        print(f"| {step} | {label} | {hole} | {liq} | {v} | {n} |")
