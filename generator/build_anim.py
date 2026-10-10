"""把全流程录成动画数据: 重跑生成器, 每一步记录 (时长, 电压, 变化的液滴→矩形列表+体积), 加上 段/流程/指令 标记。
    python3 build_anim.py [--out anim_data.json] [--cols 6]
输出紧凑 JSON (由 anim.html 模板读取; make_html.py 负责 gzip+base64 嵌入)。"""
import argparse, json, sys, time
import geom, ops, protocol, scene

class Recorder:
    def __init__(self):
        self.names, self.prev, self.steps, self.marks, self.flows = {}, {}, [], [], []
        self.snapshots, self.receiving = {}, {}
    def nid(self, n): return self.names.setdefault(n, len(self.names))
    def on_step(self, S, dur, amp):
        cur = {n: (tuple(tuple(r) for r in d.rects()), round(d.vol, 2)) for n, d in S.drops.items() if d.rects()}
        relay = S.relays[-1]
        self.snapshots[id(relay)] = cur
        self.receiving[tuple(relay.codes)] = cur
        ch = []
        for n in self.prev.keys() - cur.keys(): ch += [self.nid(n), 0]                    # 消失
        for n, (rects, vol) in cur.items():
            if self.prev.get(n) == (rects, vol): continue
            ch += [self.nid(n), len(rects), int(round(vol * 100))] + [v for r in rects for v in r]
        self.prev = cur
        self.steps.append([dur, amp, ch])

rec = Recorder()
_step = scene.Scene._record
def step(self, dur=geom.STEP_MS, amp=geom.VOLT, touching=(), changed=None):
    _step(self, dur, amp, touching, changed); rec.on_step(self, dur, amp)
scene.Scene._record = step

_cmd = protocol.Protocol.cmd
def cmd(self, name):
    start = getattr(self, "_mark_start", 0); n = len(rec.steps)
    if n > start: rec.marks.append([start, name])
    self._mark_start = n
    return _cmd(self, name)
protocol.Protocol.cmd = cmd
_flow = protocol.Protocol.flow
def flow(self, seg, name, cmds, repeats=1):
    first = getattr(self, "_flow_mark", 0)
    if len(rec.marks) > first: rec.flows.append([first, seg, name, repeats])
    self._flow_mark = len(rec.marks)
    return _flow(self, seg, name, cmds, repeats)
protocol.Protocol.flow = flow
# quant() 里直接 append 的 Q04 Flow 不经过 flow(): 用 segs 里的顺序补标记
_append = list.append

ap = argparse.ArgumentParser(); ap.add_argument("--out", default="out/anim_data.json"); ap.add_argument("--cols", type=int, default=6)
ap.add_argument('--script-name', default='PTseq_16_A', choices=['A_cDNA_PCR', 'PTseq_16_A'])
a = ap.parse_args(); t0 = time.time()
P = protocol.Protocol(tuple(range(1, a.cols + 1)))
segs = P.build()
# 与正式工程走相同的分组和时间补齐，按最终 RelayItem 重建动画，
# 而不是只播放生成中的 Scene.step（后者会漏掉补入的接液保持帧）。
from project_structure import prepare_scripts
scripts, _, _ = prepare_scripts(segs, 'ChipArea_192_256_96_SIRO', a.cols, a.script_name)
rec.steps, rec.marks, rec.flows, rec.prev = [], [], [], {}
for script in scripts:
    for f in script.flows:
        rec.flows.append([len(rec.marks), f.segment, f.name, f.repeats])
        for c in f.cmds:
            if not c.relays:
                continue
            rec.marks.append([len(rec.steps), f'{c.id} {c.name}'])
            for r in c.relays:
                cur = rec.snapshots.get(id(r))
                if cur is None:
                    cur = rec.receiving[tuple(r.codes)]
                ch = []
                for n in rec.prev.keys() - cur.keys():
                    ch += [rec.nid(n), 0]
                for n, (rects, vol) in cur.items():
                    if rec.prev.get(n) != (rects, vol):
                        ch += [rec.nid(n), len(rects), int(round(vol*100))] + [v for r0 in rects for v in r0]
                rec.steps.append([r.dur, r.amp, ch])
                rec.prev = cur
lanes = [(c, r, *geom.zone(c, r)) for c in range(1, a.cols + 1) for r in range(1, 17)]
data = {"grid": [geom.GRID_W, geom.GRID_H], "lanes": lanes, "holes": geom.HOLE_X, "gaps": [geom.gap_x(k) for k in range(7)],
        "names": [n for n, _ in sorted(rec.names.items(), key=lambda t: t[1])],
        "steps": rec.steps, "marks": rec.marks, "flows": rec.flows,
        "total_ms": sum(s[0] for s in rec.steps)}
json.dump(data, open(a.out, "w"), separators=(",", ":"))
import os
print(f"{a.out}: {len(rec.steps)} 步 / {len(rec.marks)} 指令标记 / {len(rec.flows)} 流程 / {os.path.getsize(a.out)/1e6:.1f} MB / {time.time()-t0:.0f}s", file=sys.stderr)
