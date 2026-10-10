"""把 anim_data.json 渲染成 ~1 分钟的视频帧 (PNG 序列): 每个流程按 sqrt(步数) 分配时长, 流程内均匀抽样 (快进), 带片头/片尾与信息面板。
    python3 make_video_frames.py out/anim_data.json out/frames [--seconds 60] [--fps 30]"""
import json, math, os, sys, argparse, shutil
from PIL import Image, ImageDraw, ImageFont

ap = argparse.ArgumentParser(); ap.add_argument("data"); ap.add_argument("outdir"); ap.add_argument("--seconds", type=float, default=60); ap.add_argument("--fps", type=int, default=30)
a = ap.parse_args()
D = json.load(open(a.data)); N = len(D["steps"]); GW, GH = D["grid"]
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"; FONTL = "/System/Library/Fonts/STHeiti Light.ttc"
def font(sz, light=False): return ImageFont.truetype(FONTL if light else FONT, sz)
F = {k: font(v) for k, v in {"h1": 30, "h2": 22, "b": 18, "s": 15, "xs": 13}.items()}; FL = {k: font(v, True) for k, v in {"b": 18, "s": 15, "xs": 13}.items()}

W, H = 1440, 810; S = 4                          # 画布 / 每格像素 (芯片 1024×768)
CX, CY = 20, 60                                  # 芯片左上角
PX = CX + GW * S + 24                            # 右侧面板左边
COL = {"bg": (244, 245, 242), "panel": (255, 255, 255), "ink": (28, 32, 36), "muted": (93, 102, 112), "line": (217, 221, 214), "accent": (15, 110, 99),
       "chip": (251, 251, 249), "zone": (230, 232, 227), "tec": (240, 220, 239), "gap": (238, 240, 236), "hole": (124, 140, 242), "mag": (28, 32, 36),
       "lib": (47, 111, 214), "reag": (47, 179, 107), "slug": (29, 138, 76), "waste": (224, 130, 58), "blob": (156, 91, 42), "bar": (216, 76, 138), "quant": (138, 92, 214)}
KIND_LABEL = {"lib": "文库 / 样本 / 产物", "reag": "试剂份（剥离到泳道）", "slug": "试剂液柱（孔下方框 → 空隙）", "waste": "废液 / 液指", "blob": "底边孔大滴", "bar": "条形码接头", "quant": "定量份 / 定量滴"}
def kind_of(n): return "blob" if n.startswith("BLOB") else "slug" if n[0] == "W" else "reag" if n[0] == "T" else "waste" if n[0] in "EA" else "bar" if n[0] == "K" else "quant" if n[0] in "QH" else "lib"
SEGNAME = {"A": "A 段 · cDNA 合成 → 靶向 PCR", "R1": "刷新点 1 · 磁珠纯化 + 洗脱", "Q": "Q 段 · 片上定量", "B": "B 段 · 末端修复 → 接头连接", "C": "刷新点 2 · 纯化 → 成品"}
def hms(ms): s = int(round(ms / 1000)); return f"{s//3600}:{s//60%60:02d}:{s%60:02d}"

# ── 静态芯片底图 ──
base = Image.new("RGB", (W, H), COL["bg"]); g = ImageDraw.Draw(base)
g.rectangle([CX, CY, CX + GW * S, CY + GH * S], fill=COL["chip"], outline=COL["line"])
for gx in D["gaps"]: g.rectangle([CX + gx * S, CY + 27 * S, CX + (gx + 4) * S - 1, CY + 168 * S], fill=COL["gap"])
for c, r, X0, Y0 in D["lanes"]:
    g.rectangle([CX + X0 * S, CY + Y0 * S, CX + (X0 + 36) * S - 1, CY + (Y0 + 6) * S - 1], fill=COL["zone"])
    g.rectangle([CX + X0 * S, CY + Y0 * S, CX + (X0 + 18) * S - 1, CY + (Y0 + 6) * S - 1], fill=COL["tec"])
    mx, my = CX + (X0 + 27.5) * S, CY + (Y0 + 3) * S; g.ellipse([mx - 2, my - 2, mx + 2, my + 2], fill=COL["mag"])
    for hx in (13, 23): x, y = CX + (X0 + hx) * S, CY + (Y0 - 1) * S; g.ellipse([x - 3, y - 3, x + 3, y + 3], outline=COL["hole"])
    if r == 1: g.text((CX + (X0 + 18) * S, CY + (Y0 - 7) * S), "ABCDEF"[c - 1], fill=COL["muted"], font=F["s"], anchor="mm")
    if c == 1: g.text((CX + (X0 - 2) * S, CY + (Y0 + 3) * S), str(r), fill=COL["muted"], font=FL["xs"], anchor="rm")
for k, hx in D["holes"].items():
    for hy, lab in ((1.5, "E"), (190.5, "F")):
        x, y = CX + hx * S, CY + hy * S; g.ellipse([x - 4, y - 4, x + 4, y + 4], outline=COL["hole"], width=2); g.text((x + 8, y), f"{lab}{k}", fill=COL["muted"], font=FL["xs"], anchor="lm")
g.text((CX, 22), "PTseq SIRO96 片上流程回放", fill=COL["ink"], font=F["h1"], anchor="lm")
g.text((CX + 420, 26), "96 道 · 5 段 · 42 流程 · %s 步 · 累计 %s（PCR ×30 循环按一次计）" % (f"{N:,}", hms(D["total_ms"])), fill=COL["muted"], font=FL["s"], anchor="lm")
# 图例
ly = H - 26
lx = CX
for k in ("lib", "reag", "slug", "waste", "blob", "bar", "quant"):
    g.rectangle([lx, ly - 7, lx + 14, ly + 7], fill=COL[k]); g.text((lx + 20, ly), KIND_LABEL[k], fill=COL["muted"], font=FL["xs"], anchor="lm"); lx += 20 + int(g.textlength(KIND_LABEL[k], font=FL["xs"])) + 22

# ── 时间轴 ──
cum = [0]; [cum.append(cum[-1] + s[0]) for s in D["steps"]]
marks = D["marks"]; flows = D["flows"]
mark_start = [m[0] for m in marks]; flow_start = [mark_start[f[0]] for f in flows]; flow_end = flow_start[1:] + [N]
def mark_of(i):
    lo, hi = 0, len(mark_start) - 1
    while lo < hi: mid = (lo + hi + 1) // 2; (lo := mid) if mark_start[mid] <= i else (hi := mid - 1)
    return lo
def flow_of(i):
    lo, hi = 0, len(flow_start) - 1
    while lo < hi: mid = (lo + hi + 1) // 2; (lo := mid) if flow_start[mid] <= i else (hi := mid - 1)
    return lo
INTRO, OUTRO = int(2.0 * a.fps), int(2.5 * a.fps)
budget = int(a.seconds * a.fps) - INTRO - OUTRO
weights = [8 + math.sqrt(fe - fs) for fs, fe in zip(flow_start, flow_end)]
frames_per_flow = [max(10, int(round(budget * w / sum(weights)))) for w in weights]
sample = []                                       # (step index, flow index)
for fi, (fs, fe, nf) in enumerate(zip(flow_start, flow_end, frames_per_flow)):
    for j in range(nf): sample.append((fs + int((fe - fs - 1) * j / max(1, nf - 1)), fi))
print(f"帧: 片头 {INTRO} + 正片 {len(sample)} + 片尾 {OUTRO} = {INTRO + len(sample) + OUTRO} ({(INTRO + len(sample) + OUTRO) / a.fps:.1f} s @ {a.fps} fps)", file=sys.stderr)

# ── 状态回放 ──
state = {}
def apply(i):
    ch = D["steps"][i][2]; p = 0
    while p < len(ch):
        nid, n = ch[p], ch[p + 1]
        if n == 0: state.pop(nid, None); p += 2; continue
        vol = ch[p + 2] / 100; p += 3; rects = []
        for _ in range(n): rects.append(tuple(ch[p:p + 4])); p += 4
        state[nid] = (rects, vol)
def draw_frame(i, fi, changed):
    im = base.copy(); d = ImageDraw.Draw(im)
    for nid, (rects, vol) in state.items():
        col = COL[kind_of(D["names"][nid])]
        for x, y, w, h in rects: d.rectangle([CX + x * S, CY + y * S, CX + (x + w) * S - 1, CY + (y + h) * S - 1], fill=col)
    # 右侧面板
    f = flows[fi]; m = marks[mark_of(i)]
    x0, y = PX, CY
    d.text((x0, y), SEGNAME.get(f[1], f[1]), fill=COL["accent"], font=F["b"]); y += 30
    title = f[2] + (f" （×{f[3]}）" if f[3] > 1 else "")
    for line in wrap(d, title, F["h2"], W - x0 - 20): d.text((x0, y), line, fill=COL["ink"], font=F["h2"]); y += 30
    y += 4
    for line in wrap(d, m[1], FL["s"], W - x0 - 20)[:3]: d.text((x0, y), line, fill=COL["muted"], font=FL["s"]); y += 22
    y += 14; d.line([x0, y, W - 20, y], fill=COL["line"]); y += 12
    d.text((x0, y), f"第 {i + 1:,} / {N:,} 步", fill=COL["ink"], font=FL["b"]); y += 26
    d.text((x0, y), f"流程时间 {hms(cum[i + 1])}    本步 {D['steps'][i][0]} ms · {D['steps'][i][1]} V", fill=COL["muted"], font=FL["s"]); y += 30
    d.line([x0, y, W - 20, y], fill=COL["line"]); y += 12
    d.text((x0, y), "A1 反应区 · 当前体系", fill=COL["muted"], font=F["s"]); y += 26
    rows = []
    for nid, (rects, vol) in state.items():
        n = D["names"][nid]
        if (n.endswith("A1") and n[:-2].isalpha() and n[0] != "W") or (n[0] == "W" and n.endswith("1")) or n == "BLOB1": rows.append((n, vol))
    rows.sort(key=lambda t: -t[1])
    for n, vol in rows[:7]:
        k = kind_of(n); d.rectangle([x0, y + 4, x0 + 12, y + 16], fill=COL[k]); d.text((x0 + 20, y), f"{KIND_LABEL[k].split('（')[0].split(' /')[0]}", fill=COL["ink"], font=FL["s"]); d.text((W - 20, y), f"{vol:.2f} µL", fill=COL["ink"], font=FL["s"], anchor="ra"); y += 24
    if not rows: d.text((x0, y), "（空）", fill=COL["muted"], font=FL["s"]); y += 24
    # 进度条 (整个流程, 分段刻度)
    by = H - 60; bx0, bx1 = CX, CX + GW * S
    d.rectangle([bx0, by, bx1, by + 6], fill=COL["line"])
    for fs in flow_start: x = bx0 + (bx1 - bx0) * fs / N; d.line([x, by - 2, x, by + 8], fill=(200, 204, 198))
    segs_seen = set()
    for fj, ff in enumerate(flows):
        if ff[1] in segs_seen: continue
        segs_seen.add(ff[1]); x = bx0 + (bx1 - bx0) * flow_start[fj] / N; d.text((x + 2, by + 10), ff[1], fill=COL["muted"], font=FL["xs"])
    d.rectangle([bx0, by, bx0 + (bx1 - bx0) * (i + 1) / N, by + 6], fill=COL["accent"])
    return im
def wrap(d, text, fnt, maxw):
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=fnt) > maxw and cur: lines.append(cur); cur = ch
        else: cur += ch
    if cur: lines.append(cur)
    return lines

if os.path.isdir(a.outdir): shutil.rmtree(a.outdir)
os.makedirs(a.outdir); k = 0
def save(im):
    global k; im.save(f"{a.outdir}/f{k:05d}.png", compress_level=1); k += 1
# 片头
intro = Image.new("RGB", (W, H), COL["bg"]); d = ImageDraw.Draw(intro)
d.text((W / 2, H / 2 - 60), "PTseq SIRO96 片上流程", fill=COL["ink"], font=font(52), anchor="mm")
d.text((W / 2, H / 2 + 6), "TFT 数字微流控 · 96 道 · 5 段 42 流程 · %s 步 · 约 5.8 h" % f"{N:,}", fill=COL["muted"], font=font(24, True), anchor="mm")
d.text((W / 2, H / 2 + 50), "本片为 1 分钟快进：每个流程按步数分配时长，画面中每个绿/蓝/紫块都是真实通电的电极", fill=COL["muted"], font=font(18, True), anchor="mm")
for _ in range(INTRO): save(intro)
# 正片
si = 0; cur_flow = -1
for i in range(N):
    apply(i)
    while si < len(sample) and sample[si][0] == i:
        save(draw_frame(i, sample[si][1], None)); si += 1
# 片尾
outro = Image.new("RGB", (W, H), COL["bg"]); d = ImageDraw.Draw(outro)
lines = ["顶边孔一次分注 40 µL（一列 16 道）→ 液柱经列间空隙逐道剥离", "带样本的液体只走自己的反应区和排废通道；三列并行排废，大滴满了才抽",
         "片上定量：定量滴劈半、拉成 2×21 液指从压扁的文库下面穿过", "全流程 16 780 步 · 5.8 h · 分注 66 次 · 抽吸 58 次"]
d.text((W / 2, H / 2 - 90), "要点", fill=COL["ink"], font=font(40), anchor="mm")
for j, l in enumerate(lines): d.text((W / 2, H / 2 - 30 + j * 40), l, fill=COL["muted"], font=font(22, True), anchor="mm")
for _ in range(OUTRO): save(outro)
print(f"{k} 帧 → {a.outdir}", file=sys.stderr)
