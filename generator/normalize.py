"""浓度驱动的每孔归一化 —— 中台计算核心 (host 端, 不碰任何设备协议)。

读定量结果 → 按「等质量 + 固定终体积」算每孔:
    保留体积 Vk = M / C   (含目标质量 M 的那部分文库)
    排废     = Ve − Vk    (切掉多余, 走排废空隙)
    加水     W  = Vf − Vk  (从水孔计量补到固定终体积 Vf)
结果: 每孔都 = 质量 M、体积 Vf、浓度 M/Vf。

约束:
  · 质量不能凭空造 → 目标 M ≤ min(Ve·Cᵢ)。默认取全片最小孔的质量。
  · 片上按体积计量的分辨率 = 一整列 (泳道高 LANE_H 格 × CELL_UL µL)。
    所以 Vk / W 会量化到 col_ul (~0.3 µL) 台阶; 模块把量化后的真实质量 CV 也算出来。

文件式两段 与 实时版 共用这段逻辑:
  · 文件式: 把 plan() 的结果喂给生成器, 重生成"排废多余 + 每孔加水 Wᵢ"的段落。
  · 实时版: 读到 Quant 回读后当场 plan(), 直接发对应电极帧。

用法:
    python3 normalize.py [quant.csv] [--Ve 1.50] [--Vf 2.00] [--target-mass auto|<ng>]
    CSV 表头含 well,conc  (conc = ng/µL); 不给文件则用内置 16 孔示例 (5–40 ng/µL)。
"""
import argparse, csv, math, statistics, sys
import geom   # CELL_UL, LANE_H

COL_UL = geom.LANE_H * geom.CELL_UL          # 一整列的体积 = 片上体积计量的最小台阶

def quant_vol(ul):
    """把体积量化到整列台阶 (向最近), 至少 1 列。返回 (量化体积, 列数)。"""
    cols = max(1, round(ul / COL_UL))
    return cols * COL_UL, cols

def plan(concs, Ve, Vf, target_mass=None):
    """concs: {well: conc_ng_per_ul}. 返回 (每孔计划列表, 汇总dict)。"""
    masses = {w: Ve * c for w, c in concs.items()}
    M = min(masses.values()) if (target_mass in (None, "auto")) else float(target_mass)
    if M > min(masses.values()):
        print(f"⚠ 目标质量 M={M:.2f}ng > 最小孔质量 {min(masses.values()):.2f}ng: 该孔无法达标, 只能保留全部。", file=sys.stderr)
    rows = []
    for w, c in concs.items():
        Vk_ideal = M / c                                  # 理想保留体积
        Vk_ideal = min(Vk_ideal, Ve)                      # 不能超过洗脱体积
        Vk, vk_cols = quant_vol(Vk_ideal)
        Vk = min(Vk, Ve)                                  # 量化后再夹一次
        discard = round(Ve - Vk, 3)
        W_ideal = max(0.0, Vf - Vk)                       # 加水补到终体积
        W, w_cols = (0.0, 0) if W_ideal < COL_UL / 2 else quant_vol(W_ideal)
        actual_mass = round(Vk * c, 3)                    # 量化后真实带走的质量
        final_conc = round(actual_mass / Vf, 3)
        rows.append(dict(well=w, conc=c, mass_in=round(masses[w], 2),
                         Vk=round(Vk, 2), vk_cols=vk_cols, discard=discard,
                         water=round(W, 2), Vf=Vf, mass_out=actual_mass, conc_out=final_conc))
    mo = [r["mass_out"] for r in rows]
    summ = dict(target_M=round(M, 3), Ve=Ve, Vf=Vf, col_ul=COL_UL,
                mass_in_cv=cv([r["mass_in"] for r in rows]), mass_out_cv=cv(mo),
                mass_out_min=min(mo), mass_out_max=max(mo))
    return rows, summ

def cv(xs):
    m = statistics.mean(xs)
    return 0.0 if m == 0 else round(100 * statistics.pstdev(xs) / m, 1)

def load_csv(path):
    concs = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            k = {c.lower(): c for c in r}
            concs[r[k.get("well", "well")]] = float(r[k.get("conc", "conc")])
    return concs

def demo_concs():
    # 16 孔示例: 5–40 ng/µL 的一个真实感分布
    vals = [5.2, 7.8, 9.1, 11.4, 12.0, 14.7, 16.3, 18.9, 21.0, 23.5, 26.1, 28.8, 31.2, 34.0, 37.5, 40.1]
    return {f"A{i+1}": v for i, v in enumerate(vals)}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?"); ap.add_argument("--Ve", type=float, default=1.50)
    ap.add_argument("--Vf", type=float, default=2.00); ap.add_argument("--target-mass", default="auto")
    a = ap.parse_args()
    concs = load_csv(a.csv) if a.csv else demo_concs()
    rows, summ = plan(concs, a.Ve, a.Vf, a.target_mass)
    print(f"洗脱体积 Ve={summ['Ve']}µL · 终体积 Vf={summ['Vf']}µL · 体积台阶 col={summ['col_ul']:.2f}µL · 目标质量 M={summ['target_M']}ng")
    print(f"{'孔':<5}{'浓度':>7}{'投入质量':>9}{'保留Vk':>8}{'(列)':>5}{'排废':>7}{'加水W':>7}{'成品质量':>9}{'成品浓度':>9}")
    for r in rows:
        print(f"{r['well']:<5}{r['conc']:>7.1f}{r['mass_in']:>9.2f}{r['Vk']:>8.2f}{r['vk_cols']:>5}{r['discard']:>7.2f}{r['water']:>7.2f}{r['mass_out']:>9.2f}{r['conc_out']:>9.2f}")
    print(f"\n归一化前 质量CV = {summ['mass_in_cv']}%  →  归一化后 质量CV = {summ['mass_out_cv']}%  (成品质量 {summ['mass_out_min']}–{summ['mass_out_max']} ng)")
