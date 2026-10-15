"""SIRO-Plus 芯片几何 —— 全部在像素系 (X 0–256 →, Y 0–192 ↓, 原点 Oil1)。
RelayCode 输出时转置: (x,y)(w,h) = (Y,X)(H,W)。2026-09-08 IDE 实测确认。"""
import math
GRID_W, GRID_H = 256, 192
COLS, ROWS = 6, 16
LANE_L, LANE_H, TEC_L = 36, 3, 18          # 常规转运安全带；实体反应区仍36×6
PHYSICAL_LANE_H = 6
LOAD_GAP, RUN_GAP = 1, 2
MAG_DX, S_DX, B_DX = 27, 13, 23             # 磁铁中心 / 样本孔 / barcode 孔 距泳道左端
CH_W = 4                                    # 列间空隙可用宽度
CELL_UL = 0.05
MIN_GAP = 3                                 # 相邻满高反应液滴：9 格行距减 6 格高度
PREFERRED_GAP = 4                           # 顶部拆分、通道排队和接近停靠优先留 4 格
CUT_GAP = 4                                # 对半切时两半各向外挪 2 格
VOLT, HOLD_VOLT, FREQ, WAVE = 55, 50, 20, 0
STEP_MS, HOLD_MS, LONG_MS = 1000, 1000, 4000
STRIDE = 2                                  # 空隙/转运带里的蠕动步长 (格); 泳道内恒为 1
TOP_Y = 8                                   # 顶部转运带 Y 8..12 (避开 E 孔 Y0.5–2.5)
BOT_Y = 172                                 # 底部转运带 Y 172..176 (第 16 行底 168)
BLOB_Y, BLOB_H = 183, 6                     # 汇液与通道小滴等高，接触后直接组成矩形；下边189接近F孔上缘
E_TANG_Y, F_TANG_Y = 4, 184                 # E孔边2.5→框4；F孔边189.5→框底188，约1格
E_BLOCK_H = 8                               # E 孔下承接方框最小高 8 格; 大体积时加高 (最高 22, 宽 ≤ 50: 相邻孔 54 格间距留 4); 液柱从其底部 4 行出去
E_BLOCK_W_MAX = 50
HOLE_X = {k: 54 * k for k in (1, 2, 3, 4)}  # E_k / F_k 的 X 中心

def cells(ul): return int(round(ul / CELL_UL))
def electrode_cells(ul):
    """唯一授权承接余量：5.90µL实际118格，采用120格完整承接框。"""
    return 120 if abs(ul-5.90)<1e-7 else cells(ul)

def running_gap(ul):
    return 1 if any(abs(ul-v)<1e-7 for v in (4.00,5.00,5.90)) else RUN_GAP
def rect_len(ul, h): return max(1, math.ceil(cells(ul) / h))

def lane_shape(ul):
    """下方4行内优先完整且不细长的矩形，必要时采用精确面积紧凑轮廓。"""
    n = electrode_cells(ul)
    special={80:(16,5),100:(20,5),120:(24,5)}
    if any(abs(ul-v)<1e-7 for v in (4.,5.,5.9)):return special[n]
    exact = [(w,n//w) for w in range(1,LANE_L+1)
             if n%w==0 and n//w<=LANE_H]
    if exact:
        best=min(exact,key=lambda q:(max(q)/min(q),q[0]<q[1],-q[1]))
        return best
    h=min(LANE_H,max(2,math.ceil(math.sqrt(n))));w=math.ceil(n/h)
    if w<=LANE_L:return (w,h)
    raise ValueError(f'{ul:.2f}µL无法在运行安全带内承载')

def magnetic_shape(ul, allow_590_cross=False):
    # 20261015：用户另行批准5.00µL为满足孔距跨中线；5.90µL既有例外继续保留。
    if abs(ul-5.00)<1e-7 or (allow_590_cross and abs(ul-5.90)<1e-7):
        return lane_shape(ul)
    return half_shape(ul)

class HalfRegionCapacityError(ValueError):
    """该液量无法完全落在温控/磁控半区；不得靠越界继续生成。"""

def half_shape(ul):
    """半区内优先完整整数矩形；必要时允许缺角，但不改变实际格数。"""
    n = electrode_cells(ul)
    if n <= 0 or abs(n * CELL_UL - ul) > 1e-7:
        raise ValueError(f'半区液量必须为正且是0.05µL整倍数：{ul}')
    capacity = TEC_L * PHYSICAL_LANE_H
    if n > capacity:
        raise HalfRegionCapacityError(
            f'{ul:.2f}µL需要{n}格，18×6半区只有{capacity}格（{capacity*CELL_UL:.2f}µL）；'
            '禁止跨越中线。必须先确认分批捕获或体系调整方案。')
    exact = [(w, n // w) for w in range(1, TEC_L + 1)
             if n % w == 0 and n // w <= PHYSICAL_LANE_H]
    if exact:
        return min(exact, key=lambda q: (max(q)/min(q), q[0] < q[1], -q[1]))
    candidates = [(w, h) for h in range(1, LANE_H + 1)
                  for w in range(1, TEC_L + 1) if w*h >= n]
    return min(candidates, key=lambda q: (q[0]*q[1]-n, max(q)/min(q), -q[1]))

def physical_zone(c,r):
    return 10+40*(c-1),27+9*(r-1)

def zone(c, r):
    """运行安全带左上角；CSV实体反应区仍为physical_zone的36×6。"""
    x,y=physical_zone(c,r)
    return x,y+RUN_GAP

def reaction_rect(ln, area, side=None):
    ul=area*CELL_UL
    w,h=magnetic_shape(ul,True) if side=='right' else half_shape(ul) if side=='left' else lane_shape(ul)
    x=ln.X0+(LANE_L-w if side=='right' else (TEC_L-w)//2 if side=='left' else (LANE_L-w)//2)
    return x,ln.Y0+LANE_H-h,w,h

def lane_target(c,r,ul,side=None):
    """整数完整矩形停靠；全孔检查，不把质心检查当作边界检查。"""
    x0,y0=physical_zone(c,r);n=electrode_cells(ul);gap=running_gap(ul)
    lo=x0+(TEC_L if side=='right' else 0);hi=x0+(TEC_L if side=='left' else LANE_L)
    if side=='right' and ul in (5.,5.9):lo=x0
    choices=[]
    for w in range(1,hi-lo+1):
        if n%w:continue
        h=n//w
        if h>PHYSICAL_LANE_H:continue
        for x in range(lo,hi-w+1):
            for y in range(y0,y0+PHYSICAL_LANE_H-h+1):
                rc=(x,y,w,h)
                if reaction_holes_ok(rc,gap):
                    centre=x0+(27 if side=='right' else 9 if side=='left' else 18)
                    choices.append(((max(w,h)/min(w,h),abs(x+w/2-centre),abs(y-y0-1)),rc))
    if not choices:raise ValueError(f'{ul}µL在{side}区域无完整安全矩形')
    return min(choices)[1]

def gap_x(k):
    """第 k 条空隙 (0..6) 的左 X; 宽 CH_W。0 = 左边缘, 6 = 右边缘。"""
    return {0: 6, 6: 246}.get(k, 10 + 40 * k - 4)

def reagent_gap(c): return c - 1 if c % 2 else c     # 奇数列: 左侧空隙 (TEC 端); 偶数列: 右侧 (磁铁端)
def waste_gap(c):   return c if c % 2 else c - 1     # 反之; 每对列共用中间空隙排废
def odd(c): return c % 2 == 1

def nearest_hole(x):
    return min(HOLE_X, key=lambda k: abs(HOLE_X[k] - x))

def e_block(k, ul):
    """顶边 E_k 孔下方的承接方框: 面积 ≥ 体积, 以孔为中心, 顶边切着孔。高度 8..22 随体积加高, 宽 ≤ 50 (10 µL → 25×8, 40 µL → 50×16)。"""
    n = cells(ul); H = min(22, max(E_BLOCK_H, math.ceil(n / E_BLOCK_W_MAX))); W = max(CH_W, math.ceil(n / H))
    assert W <= E_BLOCK_W_MAX, f"单次分注 {ul} µL 放不进顶边孔下方框"
    return (HOLE_X[k] - W // 2, E_TANG_Y, W, H)
def f_tangent(k):  return (HOLE_X[k] - 2, F_TANG_Y, 4, 4)      # 底边 F_k 孔上方切线块
def s_block(c, r, ul):
    """样本孔承接优先完整紧凑矩形；不为固定6格高度留下可避免的缺角。"""
    import contour
    X0,Y0=zone(c,r);shape=lane_shape(ul)
    # 大洗脱液保持原有整高回溶路径；可整除的紧凑框（32格→8×4）不补包围框。
    if shape:
        W,H=shape;return max(X0,min(X0+LANE_L-W,X0+S_DX-W//2)),physical_zone(c,r)[1]+1,W,H
    X0, Y0 = zone(c, r); L = rect_len(ul, LANE_H); return (X0 + S_DX - L // 2, Y0, L, LANE_H)
def b_block(c, r, ul):
    import contour
    X0,Y0=zone(c,r);shape=lane_shape(ul)
    assert shape,'接头液量不能形成反应区内的紧凑孔口矩形'
    W,H=shape;return X0+B_DX-W//2,Y0-1,W,H

WASTE_HOLE = {1: 1, 3: 2, 5: 4}             # 废液空隙 → 底边 F 孔 (g1→F1, g3→F2, g5→F4)

# 不参与本流程的孔（取自 ChipArea_192_256_96_SIRO.csv）：液体与孔边保持间距。
# 四角 4×4 大孔留 PREFERRED_GAP；两侧 2×2 小孔距固定进液空隙 X6 仅 3.4 格，留 MIN_GAP。
OIL_WELLS = ((2, 2, 4, 4), (250, 2, 4, 4), (2, 186, 4, 4), (250, 186, 4, 4))
SIDE_WELLS = tuple((0.6, y, 2, 2) for y in (20.3334, 41.666, 62.9986, 84.3312, 105.6638, 126.9964, 148.329, 169.6616)) \
    + tuple((253.5, y, 2, 2) for y in (39, 75, 111, 147))
KEEP_OUT = tuple((w, PREFERRED_GAP) for w in OIL_WELLS) + tuple((w, MIN_GAP) for w in SIDE_WELLS)
SAFE_X = OIL_WELLS[0][0] + OIL_WELLS[0][2] + PREFERRED_GAP   # 10：左上角大孔右侧的禁入边界
SAFE_Y = OIL_WELLS[0][1] + OIL_WELLS[0][3] + PREFERRED_GAP   # 10：左上角大孔下方的禁入边界

def well_gap(rect, well):
    """矩形电极与孔外接框的连续坐标间距（格）。"""
    x, y, w, h = rect; wx, wy, ww, wh = well
    gx = max(wx - (x + w), x - (wx + ww), 0); gy = max(wy - (y + h), y - (wy + wh), 0)
    return max(gx, gy)

def keep_out_ok(rect):
    return all(well_gap(rect, well) >= gap for well, gap in KEEP_OUT)

def between_lanes(rect):
    """矩形电极是否有格子位于某列反应区的横向范围内、却落在上下两行反应区之间的空行。"""
    x, y, w, h = rect
    if not any(x < zone(c, 1)[0] + LANE_L and zone(c, 1)[0] < x + w for c in range(1, COLS + 1)):
        return False
    return any(27 <= yy < 27 + 9 * ROWS and (yy - 27) % 9 >= PHYSICAL_LANE_H for yy in range(y, y + h))

REACTION_WELLS=tuple((physical_zone(c,r)[0]+dx-1,physical_zone(c,r)[1]-2,2,2)
                     for c in range(1,7) for r in range(1,17) for dx in (S_DX,B_DX))
EDGE_WELLS=tuple((x-1,y,2,2) for x in HOLE_X.values() for y in (.5,189.5))

def reaction_holes_ok(rect, gap=RUN_GAP):
    x,y,w,h=rect
    return all(well_gap(rect,hole)>=gap for hole in REACTION_WELLS
               if abs(hole[1]-y)<h+5 and hole[0]<x+w+gap and x<hole[0]+2+gap)

def emit(rc):
    X, Y, W, H = rc
    return f"({Y},{X})({H},{W})"
