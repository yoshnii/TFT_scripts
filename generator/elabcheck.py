#!/usr/bin/env python3
"""
elabcheck — xPeLabTFT 工程静态校验 (v3: 支持 --chip 泳道几何、并行液滴间距、逗号/分号同步表; 大工程线性扫描)

    python3 elabcheck.py <工程.elabprj | 流程.elab> [--chip ChipArea_xxx.csv]

只读，不改动任何文件。发现 ERROR 时退出码为 1，可用于交付前把关。
"""
import sys, os, re, csv, glob, hashlib, argparse, collections

# ── 芯片几何(默认值取自 ChipArea_192_256_96_V0.3, 可用 --chip 覆盖) ──────────
GRID_X, GRID_Y = 192, 256
COLS, PITCH, Y0 = 12, 18, 30          # 12 组, 组间距 18 电极, 首组 y=30
SAME_DROPLET = 9                      # 中心距 < 9 电极视作同一液滴的合并/分裂

A = lambda s: dict(re.findall(r'(\w+)="([^"]*)"', s))
BOX = lambda rc: [tuple(map(int, t)) for t in
                  re.findall(r'\((\d+),(\d+)\)\((\d+),(\d+)\)', rc)]

class Report:
    def __init__(self):
        self.items = []
    def add(self, level, check, msg, where=""):
        self.items.append((level, check, msg, where))
    def __getattr__(self, lv):
        if lv in ("error", "warn", "info"):
            return lambda *a: self.add(lv.upper(), *a)
        raise AttributeError(lv)
    def dump(self):
        order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        mark = {"ERROR": "✗", "WARN": "!", "INFO": "·"}
        n = collections.Counter(i[0] for i in self.items)
        for lv, chk, msg, where in sorted(self.items, key=lambda i: order[i[0]]):
            print(f"  {mark[lv]} [{chk}] {msg}" + (f"\n        {where}" if where else ""))
        print(f"\n  合计: {n['ERROR']} 个错误 · {n['WARN']} 个警告 · {n['INFO']} 条提示")
        return 1 if n["ERROR"] else 0

# ── 解析 ────────────────────────────────────────────────────────────────────
def parse(path):
    d = open(path, encoding="utf-8", errors="replace").read()
    flows = []
    for m in re.finditer(r"<Flow\b([^>]*)>(.*?)</Flow>", d, re.S):
        f = A(m.group(1)); f["cmds"] = []
        for cm in re.finditer(r"<Command\b([^>]*?)(?:/>|>(.*?)</Command>)", m.group(2), re.S):
            c = A(cm.group(1))
            c["relays"] = [A(it) for it in
                           re.findall(r"<RelayItem\b([^>]*?)/>", cm.group(2) or "")]
            f["cmds"].append(c)
        flows.append(f)
    return A(re.search(r"<Script[^>]*>", d).group()), flows

LANES = []   # 由芯片 CSV 的 Rec 行得到的泳道矩形 (脚本坐标: x=像素Y, y=像素X)
def load_chip(csv_path):
    global LANES
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    LANES = [(float(r["StartY"]), float(r["StartX"]), float(r["Height"]), float(r["Width"]))
             for r in rows if r["Type"] == "Rec"]
    return len(rows)

_LANE_MEMO = {}
def lane_of(b):
    v = _LANE_MEMO.get(b, -1)
    if v != -1: return v
    cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
    v = None
    for i, (x, y, w, h) in enumerate(LANES):
        if x - 1 <= cx <= x + w + 1 and y - 1 <= cy <= y + h + 1:
            v = i; break
    _LANE_MEMO[b] = v
    return v

def _boxes(r):
    """RelayItem → 电极块列表 (按 x 排序, 解析一次缓存)。"""
    b = r.get("_bs")
    if b is None:
        b = r["_bs"] = sorted(BOX(r.get("RelayCode", "")))
    return b

# ── 各项检查 ────────────────────────────────────────────────────────────────
def check_bounds(flows, R):
    bad = []
    for f in flows:
        for c in f["cmds"]:
            for k, r in enumerate(c["relays"]):
                for x, y, w, h in _boxes(r):
                    if x < 0 or y < 0 or x + w > GRID_X or y + h > GRID_Y:
                        bad.append(f'{f["Name"]}/{c["Name"]} 第{k+1}步 ({x},{y})({w},{h})')
    if bad:
        R.warn("越界", f"{len(bad)} 处电极块超出 {GRID_X}×{GRID_Y} 电极阵列（越出部分无电极，通常无害）", bad[0])
    else:
        R.info("越界", f"全部电极在芯片 {GRID_X}×{GRID_Y} 范围内")

def check_landing(flows, R):
    """同一流程内, 各组搬运/驻留终点相对 Y0+PITCH×(n−1) 的偏移应一致; 只报该流程内的离群值。"""
    import statistics
    total_bad, matched = 0, 0
    for f in flows:
        offs = []
        for c in f["cmds"]:
            m = re.search(r"(?:移动到|移液到|移液)(\d+)列|(?:维持)(\d+)列?$", c["Name"])
            if not m or not c["relays"]:
                continue
            n = int(m.group(1) or m.group(2))
            box = BOX(c["relays"][-1].get("RelayCode", ""))
            if not box or not (1 <= n <= COLS):
                continue
            cy = min(b[1] + b[3] / 2 for b in box)
            offs.append((c["Name"], n, cy - (Y0 + PITCH * (n - 1))))
        groups = {}
        for name, n, o in offs:                      # 同一流程内按指令模板分组 (移液到N列 / 维持N列 …)
            groups.setdefault(re.sub(r"\d+", "N", name), []).append((name, n, o))
        for key, g in groups.items():
            if len(g) < 3:
                continue
            matched += len(g)
            med = statistics.median(o for _, _, o in g)
            for name, n, o in g:
                if abs(o - med) > 0.75:
                    total_bad += 1
                    R.warn("落点", f'[{f["Name"]}] {name} 相对同流程同类指令其余各组偏 {o-med:+.1f} 电极')
    if matched and not total_bad:
        R.info("落点", f"{matched} 条搬运/驻留终点在各自流程内组间一致")

def check_pairs(flows, R):
    """同一驱动步内两两电极块: 不同泳道重叠 → 交叉污染; 不同泳道间距 < MIN_GAP → 间距违规。
    块按 x 排序, 对每个块只向后扫到 x 方向已相距 ≥ MIN_GAP 为止 (O(n·k) 而非 O(n²))。"""
    cx = lambda b: b[0] + b[2] / 2
    cy = lambda b: b[1] + b[3] / 2
    def gap(a, b):
        gx = max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2])
        gy = max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3])
        return max(gx, gy)
    def distinct(a, b):
        if LANES:
            la, lb = lane_of(a), lane_of(b)
            return la is not None and lb is not None and la != lb
        return abs(cx(a) - cx(b)) >= SAME_DROPLET or abs(cy(a) - cy(b)) >= SAME_DROPLET
    cross, merge, bad, big, first_x, first_g = 0, 0, 0, 0, None, None
    for f in flows:
        for c in f["cmds"]:
            for k, r in enumerate(c["relays"]):
                bs = _boxes(r)
                for b in bs:
                    if b[2] * b[3] > MAX_CELLS: big += 1
                n = len(bs)
                for i in range(n):
                    a = bs[i]; xlim = a[0] + a[2] + MIN_GAP
                    for j in range(i + 1, n):
                        b = bs[j]
                        if b[0] >= xlim: break
                        g = gap(a, b)
                        if g >= MIN_GAP: continue
                        if g < 0:                                   # 重叠
                            if distinct(a, b):
                                cross += 1
                                first_x = first_x or f'{f["Name"]}/{c["Name"]} 第{k+1}步 {a} × {b}'
                            else:
                                merge += 1
                        elif distinct(a, b):                        # 0 ≤ 间距 < MIN_GAP
                            bad += 1
                            first_g = first_g or f'{f["Name"]}/{c["Name"]} 第{k+1}步 {a} ↔ {b} 间距 {g}'
    if cross: R.error("交叉污染", f"{cross} 处不同样本的电极块重叠", first_x)
    else:     R.info("交叉污染", f"无跨样本电极重叠（{merge} 处同液滴合并/分裂属正常）")
    if bad: R.error("液滴间距", f"{bad} 处不同样本液滴间距 < {MIN_GAP} 格", first_g)
    else:   R.info("液滴间距", f"不同样本液滴间距均 ≥ {MIN_GAP} 格")
    if big: R.warn("单滴体积", f"{big} 个电极块面积 > {MAX_CELLS} 格 (= 5.2 µL 上限)")

MIN_GAP, MAX_CELLS = 2, 104     # 并行液滴最小间距 2 格; 单滴 ≤ 5.2 µL = 104 格 (TR-TFT-015)

def check_drive(flows, R):
    p = collections.Counter()
    for f in flows:
        for c in f["cmds"]:
            for r in c["relays"]:
                p[(r.get("Amp"), r.get("Freq"), r.get("WaveForm"))] += 1
    if not p:
        return
    main = p.most_common(1)[0]
    R.info("驱动参数", f"主参数 {main[0][0]}V / {main[0][1]}Hz / 波形{main[0][2]}，{main[1]} 步")
    for k, v in p.most_common()[1:]:
        R.warn("驱动参数", f"另有 {v} 步使用 {k[0]}V / {k[1]}Hz / 波形{k[2]}，请确认是否有意为之")

def check_links(flows, R):
    for f in flows:
        ids = {c["ID"] for c in f["cmds"]}
        for c in f["cmds"]:
            ref = c.get("PreTask", "").split(",")[0]
            if ref not in ("-1", "") and ref not in ids:
                R.error("引用", f'[{f["Name"]}] {c["Name"]} 的 PreTask 指向不存在的 ID {ref}')
        for s in filter(None, re.split(r"[,;]", f.get("SyncEndingCmdList", ""))):
            if s not in ids:
                R.error("引用", f'[{f["Name"]}] SyncEndingCmdList 含不存在的 ID {s}')

def check_coverage(flows, R):
    for f in flows:
        seen = {int(m.group(1)) for c in f["cmds"]
                if (m := re.search(r"(?:分液|移动到|维持|反应|样本)(\d+)", c["Name"]))}
        seen = {n for n in seen if 1 <= n <= COLS}
        if seen and len(seen) < COLS and not re.search(r"奇数|偶数", f["Name"]):
            R.info("组覆盖", f'[{f["Name"]}] 覆盖 {len(seen)}/{COLS} 组（子集脚本属正常）')

def check_project(prj, R):
    """.elabprj: 引用文件是否齐全 / BuildMd5 是否与文件现状一致。"""
    base = os.path.dirname(prj)
    d = open(prj, encoding="utf-8", errors="replace").read()
    for m in re.finditer(r"<file\b([^>]*?)/>", d):
        a = A(m.group(1)); name = a.get("include")
        for suf in (".elab", ".elab.layout"):
            if not os.path.exists(os.path.join(base, name + suf)):
                R.error("工程", f"{name}{suf} 缺失")
        p = os.path.join(base, name + ".elab")
        if not os.path.exists(p):
            continue
        cur = hashlib.md5(open(p, "rb").read()).hexdigest()
        rec = a.get("BuildMd5", "")
        if not rec:
            R.warn("构建", f"{name} 从未成功编译过（BuildMd5 为空）")
        elif rec != cur:
            R.warn("构建", f"{name} 自上次编译后已被修改，编译产物已过期",
                   f"记录 {rec[:12]}… / 当前 {cur[:12]}…")

def check_thermal(flows, R):
    for f in flows:
        hs = [c for c in f["cmds"] if c.get("Component", "").startswith("Heater")]
        if not hs:
            continue
        sp = sorted({c["Params"].split(",")[0] for c in hs
                     if c["Action"] == "CMD_TEMP_ON" and c.get("Params")}, key=float)
        dly = [c["Timeout"] for c in f["cmds"]
               if c.get("Component") == "Delay" and int(c.get("Timeout", 0)) >= 60]
        R.info("温控", f'[{f["Name"]}] 设定点 {"/".join(sp)}°C，保温 {"/".join(sorted(set(dly), key=int))} s')

# ── 主流程 ──────────────────────────────────────────────────────────────────
def main():
    global MIN_GAP
    ap = argparse.ArgumentParser(description="xPeLabTFT 工程静态校验（只读）")
    ap.add_argument("target", help=".elabprj 工程文件，或单个 .elab 流程文件")
    ap.add_argument("--chip", help="ChipArea_*.csv，用于确定芯片边界")
    ap.add_argument("--min-gap", type=int, default=MIN_GAP, choices=(2, 3, 4), help="不同样本液滴最小间距（格）")
    args = ap.parse_args()
    MIN_GAP = args.min_gap
    R = Report()

    if args.chip:
        R.info("芯片", f"已载入 {os.path.basename(args.chip)}（{load_chip(args.chip)} 个要素）")

    if args.target.endswith(".elabprj"):
        check_project(args.target, R)
        base = os.path.dirname(args.target)
        files = sorted(glob.glob(os.path.join(base, "*.elab")))
    else:
        files = [args.target]

    print(f"\n═══ elabcheck · {os.path.basename(args.target)} ═══\n")
    for p in files:
        head, flows = parse(p)
        n = sum(len(f["cmds"]) for f in flows)
        r = sum(len(c["relays"]) for f in flows for c in f["cmds"])
        R.info("文件", f"{os.path.basename(p)}：{len(flows)} 流程 / {n} 指令 / {r} 驱动步"
                       f"（芯片 {head.get('ChipsetType')}）")
        for fn in (check_bounds, check_landing, check_pairs,
                   check_drive, check_links, check_coverage, check_thermal):
            fn(flows, R)
    sys.exit(R.dump())

if __name__ == "__main__":
    main()
