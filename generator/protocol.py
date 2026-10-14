"""PTseq 单列10×流程：体积按TFT片上试剂体积表_单列.xlsx，温控按POCS；手工边界保持上电。
20261009：多E孔顶部等分、固定小滴转运、S孔两次洗脱、全区重悬、明确结合/洗脱孵育、手工吸液后续行。
仅完成离线几何/计划体积校验，未验证液体、磁控或芯片执行。"""
import geom, ops, rigid
from ops import Lane, lanes_all
from scene import Scene, Drop, Worm, Relay
from model import Cmd, Flow
import ops as _ops

ODD, EVEN = (1, 3, 5), (2, 4, 6)
PELLET = 0.0                     # 不计划保留0.30µL液相；0代表计划液相残留，不代表磁珠物理干燥
QUANT_ALIQUOT, WS_VOL = 0.30, 3.75   # 定量份 = 2×3 电极块 0.30 µL (HTML 0.25; 电极粒度取整), Qubit 工作液 3.75 µL
T_DARK, T_READ = 120, 60             # 避光孵育 / 读数窗口 (秒)
BEAD_BIND_MIX = {'R1':20, 'C':12}    # 原10/6轮加倍；工程增强，不是SOP给出的次数
BEAD_ELUTE_MIX = 12                  # 原6轮加倍，固定矩形全区往返
BEAD_CLEANUP_UL = 3.00               # ASI96 260902_96sampleV2_1：TE分液2267，每滴10×6=60格；额外排珠载液。
BEAD_CAPTURE_SECONDS = {'R1':180, 'C':120}  # POCS 2.2.2.1 / 2.3.6.2
BEAD_ELUTE_CAPTURE_SECONDS = {'R1':120, 'C':30}  # POCS 2.2.2.5 / 2.3.6.7

class Protocol:
    def __init__(self, cols=(1, 2, 3, 4, 5, 6)):
        self.S, self.blob, self.segs = Scene(), {}, {}
        self.cols = cols; self.ALL = lanes_all(cols)
        self.odd_l = [l for l in self.ALL if l.odd]; self.even_l = [l for l in self.ALL if not l.odd]
        self.rigid = len(self.cols) == 1 and ops.DISPENSE_MODE == "drops"          # 单列: 刚体液珠模型 (rigid.py)
        self.deliver = rigid.deliver_drops if self.rigid else ops.deliver_slug
        self.S.manual_aspirations = [] if self.rigid else None
        self.magnet_state='down'  # 运行前必须由操作者/外部控制器确认；不是电机反馈
        self.magnet_events=[]
        self.elution_events=[]
        self.bead_cleanup_events=[]
    def magnet(self, target, reason):
        """记录外部磁铁动作时序；不伪造XaxisMotor指令或位置确认。"""
        assert target in ('up','down')
        self.magnet_events.append(dict(target=target,reason=reason,frame=self.S.nsteps,
                                       previous=self.magnet_state,hardware_control=False))
        self.magnet_state=target
    # ── 记录 ──
    def cmd(self, name):
        rel = [r for r in self.S.flush() if r.codes]  # 无液相/无电极时不输出空驱动帧。
        if not rel: return None
        c = Cmd(name, "HighVoltage", "CMD_SCRIPTCYCLE", max(1, round(sum(r.dur for r in rel) / 1000)), "SCRIPTCYCLE_FALSE", rel)
        events = {id(r): info for r, info in (self.S.manual_aspirations or [])}
        c.aspiration_gates = [(i, events[id(r)]) for i, r in enumerate(rel) if id(r) in events]
        return c
    def flow(self, seg, name, cmds, repeats=1):
        cmds = [c for c in cmds if c]
        if cmds:
            self.segs.setdefault(seg, []).append(Flow(name, cmds, repeats))
            import sys; print(f"  [{seg}] {name}: {sum(len(c.relays) for c in cmds)} 步", file=sys.stderr, flush=True)
    def phases(self):
        return [(c, "A/C/E" if c == 1 else "B/D/F", [l for l in self.ALL if l.odd == (c == 1)]) for c in (1, 2) if any((l.odd == (c == 1)) for l in self.ALL)]
    def loading_cmd(self, info):
        """只下发承接/保持电极，当前 section 到此结束；加液后运行下一 section。"""
        name = (f"【手工加液・流程组结束】{info['hole']} {info['reagent']} {info['volume_ul']:.2f}µL"
                f" · {info['lanes']} · 第{info.get('batch',1)}/{info.get('batches',1)}批 · 加好后运行下一流程组")
        c = self.cmd(name)
        assert c and len(c.relays) == 1, "承接等待必须为独立的一条静态电极状态"
        c.manual_load = dict(info)
        return c
    def load_samples(self, seg, title, which, vol, tag, reagent):
        ops.load_holes(self.S, self.ALL, which, vol, tag, manual=True)
        info = dict(hole=f"{which}孔×{len(self.ALL)}", reagent=reagent, volume_ul=vol,
                    per_lane_ul=vol, lanes=f"A1-A16（每道{vol:.2f}µL）", batch=1, batches=1)
        self.flow(seg, title, [self.loading_cmd(info)])
        self.S.step(geom.HOLD_MS,changed=[])
    # ── 复合动作 ──
    def add_reagent(self, seg, title, reagent, vol, park=True):
        S, cmds = self.S, []
        if park: ops.park_all(S, self.ALL, "reagent"); cmds.append(self.cmd("常驻液滴让开进液端"))
        for ph, lab, lanes in self.phases():
            cols = [c for c in self.cols if geom.odd(c) == (ph == 1)]
            if self.rigid:
                def before_load():
                    c = self.cmd(f"{reagent} 上一批液珠配送完成（保持所有常驻液滴）")
                    if c: cmds.append(c)
                def on_load(info):
                    gate = self.loading_cmd(info)
                    # 让开进液端/上一批配送的最后一帧已包含新接液框。
                    # 承接指令保持完全相同的状态，不在组边界更换电极。
                    if cmds and cmds[-1] is not None and cmds[-1].relays:
                        receiving = gate.relays[0]
                        cmds[-1].relays.append(Relay(list(receiving.codes), geom.HOLD_MS, receiving.amp))
                    cmds.append(gate)
                self.deliver(S, reagent, vol, cols, f"T{reagent}", on_before_load=before_load, on_load=on_load,
                             controlled=reagent in ('G8','EDTA'))
            else:
                self.deliver(S, reagent, vol, cols, f"T{reagent}")
            cmds.append(self.cmd(f"{reagent} 加液已完成 → 顶部拆成液珠 → 经空隙逐道送入 {lab} 列"))
        left = [l for l in self.ALL if f"T{reagent}{l.id}" in S.drops]
        if left: ops.merge_all(S, left, f"T{reagent}"); cmds.append(self.cmd(f"{reagent} 与各道液滴合并"))
        self.flow(seg, title, cmds)
    def mix(self, seg, title, cycles=ops.MIXN, span=None, center_tec=False):
        ops.mix_all(self.S, self.ALL, cycles, span,full_region=True)
        if center_tec:
            ops.park_all(self.S, self.ALL, "tec_center")
        self.flow(seg, title, [self.cmd("往复混匀 (全部液滴带电)")])
    def thermal(self, seg, title, program, repeats=1, lid=None, final_4c=True, park=True):
        """温控与高压保持并行。Heater 参数与参考工程一致: 目标温度,精度(1℃)。"""
        S, cmds, root = self.S, [], None
        if park:
            ops.park_all(S, self.ALL, "tec_center"); root = self.cmd("液滴停 TEC 区")
            if root: cmds.append(root)
        ops.assert_half(S, self.ALL, 'left')
        seq = list(program) + ([(4, 30)] if final_4c and (not program or program[-1][0] != 4) else [])
        heater_ends, heater_first = [], []
        for col in self.cols:
            prev = None
            for temp, secs in seq:
                on = Cmd(f"Heater{col}_{temp}℃", f"Heater{col}", "CMD_TEMP_ON", 60, f"{temp},1")
                delay = Cmd(f"保温 {temp}℃ {secs}s", "Delay", "CMD_DELAY", secs, "")
                on.pre = (prev, "FS") if prev else ((root, "FS") if root else None)
                delay.pre = (on, "FS"); prev = delay
                if not heater_first: heater_first.append(on)
                cmds.extend([on, delay])
            if prev: heater_ends.append(prev)
        # 合并预览按 Temp on 的 Timeout 计算温度切换时段，不能只覆盖 Delay。
        hold_secs = sum(s + 60 for _, s in seq)
        ops.hold_all(S, hold_secs)
        hv = self.cmd("液滴持续通电保持（循环至温控结束）")
        if hv:
            hv.params = "SCRIPTCYCLE_TRUE"   # ASI96 的循环高压 + 同时结束方式；此处只有静态保持
            if root: hv.pre = (root, "SS")
            elif heater_first: hv.pre = (heater_first[0], "SS")
            cmds.append(hv)
        if not cmds: return
        suffix = f" [热盖{lid}℃]" if lid else ""
        f = Flow(title + suffix, cmds, repeats, sync=heater_ends + ([hv] if hv else []))
        # Flow 构造器默认串联，这里恢复已设定的温控/高压并行分支。
        if root:
            root.pre = None
            for col_i, col in enumerate(self.cols):
                first = next((c for c in cmds if c.name.startswith(f"Heater{col}_")), None)
                if first: first.pre = (root, "FS" if col_i == 0 else "SS")
            if hv: hv.pre = (root, "SS")
        elif heater_first and hv:
            heater_first[0].pre = None; hv.pre = (heater_first[0], "SS")
        self.segs.setdefault(seg, []).append(f)
    def egress_phases(self, seg_cmds, tag, label, touching=()):
        """tag+lane 液滴 → 蠕虫 → 排废。偶数列先 (其分离块贴着左侧空隙), 奇数列后。"""
        S = self.S
        if self.rigid:
            rigid.egress_pieces(S, [(ln, S.get(f"{tag}{ln.id}")) for ln in self.ALL], self.blob, final=True,
                                collect_a05=label.startswith('cDNA'))
            seg_cmds.append(self.cmd(f"{label} 切成小块 → 依次出空隙 → 底边孔大滴 → 抽走")); return
        for lanes, lab, fin in ((self.even_l, "B/D/F", False), (self.odd_l, "A/C/E", True)):
            if not lanes: continue
            items = [(ln, ops.worm_from_drop(S, ln, S.get(f"{tag}{ln.id}"), f"E{ln.id}")) for ln in lanes]
            S.step(changed=[w.name for _, w in items], touching=touching)
            ops.egress(S, items, self.blob, touching, final=fin or lanes is self.odd_l or not self.odd_l)
            seg_cmds.append(self.cmd(f"{label} {lab} 列 → 空隙 → 底边孔" + ("抽走" if fin or not self.odd_l else "大滴")))
    def split_waste(self, seg, title, keep_vol, label):
        S, cmds = self.S, []
        ops.split_all(S, self.ALL, keep_vol, "A"); cmds.append(self.cmd(f"劈裂: 保留 {keep_vol:.2f}µL"))
        self.egress_phases(cmds, "A", label)
        self.flow(seg, title, cmds)
    def bead_capture(self, seg, title, mix_cycles, mix_span):
        S, cmds = self.S, []
        self.magnet('down',f'{seg} 结合混匀前')
        ops.park_all(S, self.ALL, "tec"); cmds.append(self.cmd("[外部磁控]确认磁铁已下降 · 液滴停 TEC 区"))
        ops.mix_all(S, self.ALL, mix_cycles, mix_span,full_region=True); cmds.append(self.cmd(f"磁铁保持下降 · 全反应区往复混匀 (结合) {mix_cycles}轮"))
        incubation=300 if seg=='R1' else 180
        ops.hold_all(S,incubation)
        cmds.append(self.cmd(f"[外部磁控]磁铁未升起 · 室温结合孵育 {incubation}s · 全液滴保持上电"))
        right_half = self.rigid
        ops.to_magnet_all(S,self.ALL,right_half=right_half,allow_590_cross=seg=='C')
        cmds.append(self.cmd(("5.90µL已授权例外：右端停靠，向左跨中线2列；118格守恒" if seg=='C'
                              else "等面积整形后逐格移入右半区（必要时允许缺角）") if right_half else "移至磁铁位并拉长"))
        self.magnet('up',f'{seg} 全部液滴到右侧后捕获')
        capture=BEAD_CAPTURE_SECONDS[seg]
        ops.hold_all(S, capture); cmds.append(self.cmd(f"[外部磁控]磁铁升起 捕获 {capture}s 持续通电"))
        self.flow(seg, title, cmds)
    def supernatant_off(self, seg, title):
        """磁珠团留在磁铁位，液相按小矩形逐格切开，从排废端移走。"""
        S, cmds, touch = self.S, [], []
        assert self.magnet_state=='up','排液期间磁铁必须保持升起'
        if self.rigid:
            rigid.supernatant_bites(S, self.ALL, self.blob, PELLET, final=True,collect_three=seg=='R1')
            grouping='三批连续收集至F3/F2/F1，全部到齐后统一吸液；' if seg=='R1' else ''
            cmds.append(self.cmd("磁铁保持升起 · 分离 → 小矩形逐格切开移液 → 整排等齐汇液；"+grouping+"待手工吸出后续行，反应区只留磁珠"))
            self.flow(seg, title, cmds); return
        items_by_par = {True: [], False: []}
        for ln in self.ALL:
            d = S.get(ln.name); liq = d.vol - PELLET; big = d.rect
            d.rect = ln.rect(ln.mag - 1, 3); d.vol = PELLET                      # 磁珠团: 磁铁中心 ±1 的 3 列, 始终带电
            sl = ops.lane_slices(big, ln.odd, skip=range(ln.mag - 1, ln.mag + 2))  # 液相 = 拉长液滴的其余各列, 排废端在头
            w = S.add(Worm(f"E{ln.id}", liq, geom.CH_W, sl)); items_by_par[ln.odd].append((ln, w)); touch.append((w.name, d.name))
        S.step(2000, touching=touch, changed=[w.name for k in items_by_par for _, w in items_by_par[k]] + [ln.name for ln in self.ALL])
        cmds.append(self.cmd("磁珠团按住, 液相从磁珠旁拉出"))
        for par, lab in ((False, "B/D/F"), (True, "A/C/E")):
            if items_by_par[par]:
                fin = par or not items_by_par[True]
                ops.egress(S, items_by_par[par], self.blob, touch, final=fin); cmds.append(self.cmd(f"液相 {lab} 列 → 空隙 → 底边孔" + ("抽走" if fin else "大滴")))
        self.flow(seg, title, cmds)
    def elute(self, seg, title, reagent, vol, product_vol, resus_cycles=6):
        S, cmds = self.S, []
        self.add_reagent(seg, f"{title} · 送入洗脱液", reagent, vol, park=False)     # 直接进磁珠团
        ops.resuspend_all(S, self.ALL, resus_cycles); cmds.append(self.cmd("[外部磁控]磁铁落下 · 重悬往复"))
        ops.hold_all(S, ops.T_RESUS_MAG); cmds.append(self.cmd(f"[外部磁控]磁铁升起 {ops.T_RESUS_MAG}s 持续通电"))
        # 产物离开磁珠团: 奇数列去左 (TEC 端), 偶数列先去右侧让路
        pairs = []
        if self.rigid:
            rigid.product_off_pellet(S, self.ALL, product_vol, PELLET); cmds.append(self.cmd("原地拉长 → 等体积切开: 产物左挪 1 格, 磁珠团右挪 1 格回磁铁中心"))
        else:
          for ln in self.ALL:
            d = S.get(ln.name); Lp = geom.rect_len(product_vol, geom.LANE_H)
            pel = ln.rect(ln.mag - 1, 3)
            Xp = ln.mag - 1 - geom.MIN_GAP - Lp if ln.odd else ln.mag + 2 + geom.MIN_GAP
            p = S.add(Drop(f"P{ln.id}", ln.rect(Xp, Lp), product_vol)); d.rect, d.vol = pel, round(d.vol - product_vol, 2); pairs.append((d.name, p.name))   # 珠团带走洗脱液余量
          S.step(2000, touching=pairs, changed=[n for pr in pairs for n in pr]); cmds.append(self.cmd("产物液滴离开磁珠团"))
        ops.move_rects(S, {f"P{ln.id}": ln.X0 for ln in self.odd_l}); c = self.cmd("A/C/E 产物移至 TEC 端"); cmds.append(c)
        # 磁珠团排废 (偶数列先: 向左经 TEC 区出; 奇数列向右出)
        if self.rigid:
            rigid.egress_pieces(S, [(ln, S.get(ln.name)) for ln in self.ALL], self.blob, final=True)
            cmds.append(self.cmd("[外部磁控]磁铁落下 · 磁珠团 → 出空隙 → 底边孔大滴 → 抽走"))
        for lanes, lab in ((self.even_l, "B/D/F"), (self.odd_l, "A/C/E")):
            if not lanes or self.rigid: continue
            fin = lanes is self.odd_l or not self.odd_l
            items = [(ln, ops.worm_from_drop(S, ln, S.get(ln.name), f"E{ln.id}")) for ln in lanes]
            S.step(changed=[w.name for _, w in items]); ops.egress(S, items, self.blob, final=fin)
            cmds.append(self.cmd(f"[外部磁控]磁铁落下 · 磁珠 {lab} 列 → 空隙 → 底边孔" + ("抽走" if fin else "大滴")))
        for ln in self.ALL:                                                    # 产物成为新的常驻液滴
            p = S.drops.pop(f"P{ln.id}"); p.name = ln.name; S.drops[ln.name] = p
        ops.move_rects(S, {ln.name: ln.X0 for ln in self.even_l}); cmds.append(self.cmd("B/D/F 产物移至 TEC 端"))
        self.flow(seg, title, cmds)

    def elute_offchip(self, seg, title, reagent, vol, product_vol, round_no, final=False, resus_cycles=BEAD_ELUTE_MIX):
        """在同一批磁珠上执行一次大体积洗脱；产物移到 S 孔下并手工吸出。
        final=False 时保留磁珠供第二次洗脱；final=True 后才排出磁珠。"""
        S, cmds = self.S, []
        assert self.magnet_state=='up','加洗脱液时先保持磁铁升起，防止干珠提前移动'
        ops.load_holes(S,self.ALL,'S',vol,'E',manual=True)
        gate=self.loading_cmd(dict(hole='S孔×16',reagent=reagent,volume_ul=vol,
                                  per_lane_ul=vol,lanes=f'A1-A16（每道{vol:.2f}µL）',batch=1,batches=1))
        self.flow(seg,f'{title} · S孔承接',[gate])
        S.step(geom.HOLD_MS,changed=[])
        ops.merge_all(S,self.ALL,'E');cmds.append(self.cmd('S孔加液完成 · 磁铁保持升起 · 洗脱液逐格覆盖磁珠后再降下'))
        self.magnet('down',f'{seg} 第{round_no}次洗脱液覆盖磁珠后重悬前')
        ops.resuspend_all(S, self.ALL, resus_cycles); cmds.append(self.cmd(f"第{round_no}次：磁铁落下 · 固定矩形扫过整个反应区 · 往返{resus_cycles}轮"))
        incubation=300 if seg=='R1' else 180
        if seg=='R1':
            # POCS 2.2.2.4：5min洗脱期间每2min轻晃。往返时间计入300s，不能另加300s。
            elapsed=0.0;periodic=[]
            for at in (120,240):
                ops.hold_all(S,at-elapsed)
                before=sum(r.dur for r in S.relays)
                ops.resuspend_all(S,self.ALL,2)
                elapsed=at+(sum(r.dur for r in S.relays)-before)/1000
                assert elapsed<=incubation,'轻晃动作超过洗脱孵育窗口'
                periodic.append(dict(start_seconds=at,cycles=2,end_seconds=elapsed))
            ops.hold_all(S,incubation-elapsed)
        else:
            periodic=[];ops.hold_all(S,incubation)
        self.elution_events.append(dict(stage=seg,round=round_no,initial_mix_cycles=resus_cycles,
                                        incubation_seconds=incubation,periodic_mixing=periodic))
        cmds.append(self.cmd(f'第{round_no}次：室温洗脱孵育 {incubation}s · '+
                             ('第120/240s磁铁下降状态轻晃各2轮，动作时间包含在孵育内 · ' if periodic else '')+
                             '全液滴保持上电'))
        ops.to_magnet_all(S,self.ALL,right_half=True)
        cmds.append(self.cmd(f'第{round_no}次：固定形状逐格移回磁铁侧，再升磁铁'))
        self.magnet('up',f'{seg} 第{round_no}次洗脱孵育完成且液滴到右侧后')
        capture=BEAD_ELUTE_CAPTURE_SECONDS[seg]
        ops.hold_all(S, capture); cmds.append(self.cmd(f"第{round_no}次：磁铁升起 {capture}s · 全液滴持续通电"))
        rigid.product_off_pellet(S, self.ALL, product_vol, PELLET)
        cmds.append(self.cmd(f"第{round_no}次：产物 {product_vol:.2f}µL 与磁珠团分离"))
        ops.move_rects(S, {f"P{ln.id}": ln.X0 + geom.S_DX - S.get(f"P{ln.id}").rect[2] // 2 for ln in self.ALL})
        S.step(geom.HOLD_MS,changed=[])
        info=dict(hole='S孔×16',volume_ul=round(product_vol*len(self.ALL),2),
                  per_lane_ul=product_vol,drop_names=[f'P{ln.id}' for ln in self.ALL],
                  rects=[list(r) for ln in self.ALL for r in S.get(f'P{ln.id}').rects()],kind='逐道洗脱产物')
        S.manual_aspirations.append((S.relays[-1],info))
        cmds.append(self.cmd(f"第{round_no}次产物移至 S 孔下，等待手工吸出"))
        for ln in self.ALL: S.remove(f"P{ln.id}")
        S.step(geom.HOLD_MS, changed=[])
        cmds.append(self.cmd(f"第{round_no}次产物已离片，不预留液相；磁铁留珠"))
        if final:
            self.flow(seg,title,cmds)
            self.cleanup_beads(seg,title,reagent)
            return
        self.flow(seg, title, cmds)

    def cleanup_beads(self,seg,title,reagent):
        """两次产物取走后，必须另加真实载液，降磁铁重悬，随后整滴排废；不是第三次产物洗脱。"""
        S=self.S
        assert self.magnet_state=='up','排珠加液前磁铁必须保持升起'
        assert all(S.get(ln.name).vol==0 and getattr(S.get(ln.name),'magnet_only',False) for ln in self.ALL)
        ops.load_holes(S,self.ALL,'S',BEAD_CLEANUP_UL,'E',manual=True)
        gate=self.loading_cmd(dict(hole='S孔×16',reagent=f'{reagent} 排珠载液（非回收产物）',
                                   volume_ul=BEAD_CLEANUP_UL,per_lane_ul=BEAD_CLEANUP_UL,
                                   lanes=f'A1-A16（每道{BEAD_CLEANUP_UL:.2f}µL）',batch=1,batches=1))
        self.flow(seg,f'{title} · 排珠载液承接（额外加入，非洗脱回收）',[gate])
        S.step(geom.HOLD_MS,changed=[])
        ops.merge_all(S,self.ALL,'E')
        cmds=[self.cmd('排珠载液逐格覆盖磁珠 · 磁铁仍升起 · 每道真实液量3.00µL')]
        self.magnet('down',f'{seg} 两次产物吸出且额外3.00µL载液覆盖磁珠后，重悬排珠')
        ops.resuspend_all(S,self.ALL,BEAD_ELUTE_MIX)
        cmds.append(self.cmd(f'[外部磁控]磁铁下降 · 3.00µL载液带磁珠全反应区往返{BEAD_ELUTE_MIX}轮'))
        event=dict(stage=seg,per_lane_ul=BEAD_CLEANUP_UL,total_ul=round(len(self.ALL)*BEAD_CLEANUP_UL,2),
                   reagent=reagent,loading_hole='S孔×16',mix_cycles=BEAD_ELUTE_MIX,
                   reference='260902_96sampleV2_1.elab Flow2267 Command2268：8滴×60格',
                   waste_begin_frame=S.nsteps,magnet_state=self.magnet_state)
        self.bead_cleanup_events.append(event)
        assert all(S.get(ln.name).vol==BEAD_CLEANUP_UL for ln in self.ALL)
        # C06为程序最后一步：整步48µL一次装入F1–F4，吸液停靠时反应区内不留液体。
        rigid.egress_pieces(S,[(ln,S.get(ln.name)) for ln in self.ALL],self.blob,final=True,single_stop=seg=='C')
        event['waste_end_frame']=S.nsteps
        cmds.append(self.cmd('3.00µL载液与磁珠一起排至底边孔，等待手工吸出；不回收文库'))
        self.flow(seg,f'{title} · 磁珠清理（48µL额外载液排废，非文库）',cmds)


    # ── 片上定量 (Q 段): 定量滴从进液侧进、劈成两半、拉成底部两行的液指从压扁的文库下面穿过、出排废空隙 ──
    def quant(self, seg, title):
        S, ALL = self.S, self.ALL
        def R(ln, x, dy, w, h): return (ln.X0 + x, ln.Y0 + dy, w, h)
        def shapes(names_rects, dur=2000, touching=()):
            for n, rc in names_rects.items(): S.get(n).rect = tuple(rc)
            S.step(dur, changed=list(names_rects), touching=touching)
        # Q01 文库到排废端 (留 2 格) → 向进液侧拉长 2 格 → 劈出 2×3 定量份 → 定量份移到进液侧等 WS
        cmds = []
        if self.rigid:                                                     # 等体积切: 5×6 文库 → 左 1 列 (0.30µL, 左挪 1) + 4×6 文库 (右挪 1); 小块再原地变 2×3
            ops.move_rects(S, {ln.name: ln.X0 + 28 for ln in ALL})
            for ln in ALL:
                d = S.get(ln.name); assert d.rect[2] == 5, d.rect
                S.remove(d.name); S.add(Drop(f"Q{ln.id}", R(ln, 27, 0, 1, 6), QUANT_ALIQUOT)); S.add(Drop(ln.name, R(ln, 30, 0, 4, 6), round(d.vol - QUANT_ALIQUOT, 2)))
            S.step(ops.CUT_MS, changed=[f"Q{ln.id}" for ln in ALL] + [ln.name for ln in ALL])
            shapes({f"Q{ln.id}": R(ln, 26, 0, 2, 3) for ln in ALL}, dur=ops.MORPH_MS)
        else:
          ops.move_rects(S, {ln.name: (ln.X0 + 34 - S.get(ln.name).rect[2] if ln.odd else ln.X0 + 2) for ln in ALL})
          shapes({ln.name: (R(ln, 34 - S.get(ln.name).rect[2] - 2, 0, S.get(ln.name).rect[2] + 2, 6) if ln.odd
                          else R(ln, 2, 0, S.get(ln.name).rect[2] + 2, 6)) for ln in ALL})
          pairs = []
          for ln in ALL:
            d = S.get(ln.name); d.vol = round(d.vol - QUANT_ALIQUOT, 2); L = geom.rect_len(d.vol, geom.LANE_H)
            d.rect = R(ln, 34 - L, 0, L, 6) if ln.odd else R(ln, 2, 0, L, 6)
            q = S.add(Drop(f"Q{ln.id}", R(ln, 34 - L - 3, 0, 2, 3) if ln.odd else R(ln, 2 + L + 1, 0, 2, 3), QUANT_ALIQUOT))   # 颈缩: 间距 1
            pairs.append((d.name, q.name))
          S.step(2000, touching=pairs, changed=[n for pr in pairs for n in pr])
          shapes({f"Q{ln.id}": (R(ln, 34 - S.get(ln.name).rect[2] - 4, 0, 2, 3) if ln.odd else R(ln, 2 + S.get(ln.name).rect[2] + 2, 0, 2, 3)) for ln in ALL})
        cmds.append(self.cmd(f"文库停排废端 → 劈出定量份 {QUANT_ALIQUOT:.2f}µL (2×3)"))
        ops.move_rects(S, {f"Q{ln.id}": ln.X0 + 17 for ln in ALL}); cmds.append(self.cmd("定量份移到进液侧 (X0+17) 等 WS"))
        self.flow(seg, f"{title} · Q01 劈出定量份", cmds)
        # Q02 WS 经进液空隙送入 → 与定量份合并 (4.05 µL, 14×6)
        cmds = []
        for ph, lab, lanes in self.phases():
            cols = [c for c in self.cols if geom.odd(c) == (ph == 1)]
            self.deliver(S, "WS", WS_VOL, cols, "TWS"); cmds.append(self.cmd(f"WS {WS_VOL:.2f}µL 顶部拆成液珠 → 经空隙逐道送入 {lab} 列"))
        ops.merge_all(S, ALL, "TWS", host_tag="Q"); cmds.append(self.cmd("WS 与定量份合并"))
        self.flow(seg, f"{title} · Q02 加 Qubit 工作液 {WS_VOL:.2f}µL", cmds)
        # Q03 混匀 (文库那侧留 2 格)
        ops.mix_range(S, {f"Q{ln.id}": ((ln.X0 + 5, ln.X0 + 14) if ln.odd else (ln.X0 + 8, ln.X0 + 17)) for ln in ALL}, ops.MIXN)
        self.flow(seg, f"{title} · Q03 往复混匀", [self.cmd("往复混匀 (全部液滴带电)")])
        # Q04 避光 → 读数 (Quant 指令与保持通电指令并行启动)
        ops.hold_all(S, T_DARK); c_dark = self.cmd(f"[避光]{T_DARK}s 持续通电")
        ops.hold_all(S, T_READ); c_hold = self.cmd(f"[定量]读数期间持续通电 {T_READ}s")
        c_q = Cmd("[定量]Quant 读数 (DS 双链标曲)", "Quant", "CMD_QUANT_DEF", T_READ, "DS")
        f = Flow(f"{title} · Q04 避光{T_DARK//60}min → Quant 读数", [c_dark, c_hold, c_q], 1, sync=[c_hold, c_q]); c_q.pre = (c_hold, "SS")
        self.segs.setdefault(seg, []).append(f)
        if self.rigid:
            cmds = []
            for k in range(3):                                             # 文库 4×6 → 6×4 → 8×3 → 2×11 (原地形变, 顶部两行)
                shapes({ln.name: R(ln, *((28, 0, 6, 4), (26, 0, 8, 3), (23, 0, 11, 2))[k]) for ln in ALL})
            cmds.append(self.cmd("文库原地压成顶部两行 2×11 (离排废空隙 2 格)"))
            self.flow(seg, f"{title} · Q05 文库压扁", cmds)
            rigid.quant_pass(S, ALL, self.blob, "Q", final=True)
            self.flow(seg, f"{title} · Q06 定量滴切成 4 块 1µL → 逐块拉成底部两行 2×10 从文库下穿过 → 出空隙 → 抽走",
                      [self.cmd("定量滴 16×5 → 8×5 → 4×5 (等体积切) → 每块原地变 2×10 → 平移穿过 → 末端原地变回 4×5 → 出空隙 → 大滴 → 抽走")])
        else:
          self._quant_fingers(seg, title, R, shapes, ALL)
        # Q08 文库复原 → 回 TEC 端
        for k in range(3):
            shapes({ln.name: (R(ln, *((26, 0, 8, 3), (28, 0, 6, 4), (30, 0, 4, 6))[k]) if ln.odd else R(ln, *((2, 0, 8, 3), (2, 0, 6, 4), (2, 0, 4, 6))[k])) for ln in ALL})
        ops.move_rects(S, {ln.name: ln.X0 for ln in ALL})
        self.flow(seg, f"{title} · Q08 文库复原 → 回 TEC 端", [self.cmd("文库 2×11 → 4×6 → TEC 端")])

    def _quant_fingers(self, seg, title, R, shapes, ALL):
        S = self.S
        # Q05 定量滴劈成两半 → 文库压成顶部两行 2×11
        cmds = []
        shapes({f"Q{ln.id}": (R(ln, 2, 0, 17, 6) if ln.odd else R(ln, 15, 0, 17, 6)) for ln in ALL})
        pairs = []
        for ln in ALL:
            q = S.get(f"Q{ln.id}"); half = round(q.vol / 2, 3); q.vol = half
            q.rect = R(ln, 2, 0, 7, 6) if ln.odd else R(ln, 25, 0, 7, 6)                              # QA: 远离排废端的一半
            b = S.add(Drop(f"H{ln.id}", R(ln, 10, 0, 7, 6) if ln.odd else R(ln, 17, 0, 7, 6), half))   # QB: 靠排废端的一半, 间距 1
            pairs.append((q.name, b.name))
        S.step(2000, touching=pairs, changed=[n for pr in pairs for n in pr])
        shapes({f"H{ln.id}": (R(ln, 11, 0, 7, 6) if ln.odd else R(ln, 16, 0, 7, 6)) for ln in ALL})   # 断开: 间距 2
        ops.move_rects(S, {f"Q{ln.id}": ln.X0 + 27 for ln in self.even_l})                             # 偶数列 QA 再让 2 格
        cmds.append(self.cmd("定量滴劈成两个 2.0µL 半份"))
        for k in range(3):
            shapes({ln.name: (R(ln, *((28, 0, 6, 4), (26, 0, 8, 3), (23, 0, 11, 2))[k]) if ln.odd else R(ln, *((2, 0, 6, 4), (2, 0, 8, 3), (2, 0, 11, 2))[k])) for ln in ALL})
        cmds.append(self.cmd("文库压成顶部两行 2×11 (离排废空隙留 2 格)"))
        self.flow(seg, f"{title} · Q05 劈半 · 文库压扁", cmds)
        # Q06 / Q07 半份拉成底部两行 2×21 液指 → 从文库下面穿过 → 出排废空隙 (偶数列先, 奇数列后)
        def finger(tag, odd_shapes, even_shapes, title2, last=False):
            cmds = []
            for k in range(2):
                shapes({f"{tag}{ln.id}": R(ln, *(odd_shapes if ln.odd else even_shapes)[k]) for ln in ALL})
            cmds.append(self.cmd("半份拉成底部两行的 2×21 液指"))
            for lanes, lab in ((self.even_l, "B/D/F"), (self.odd_l, "A/C/E")):
                if not lanes: continue
                items = []
                for ln in lanes:
                    d = S.get(f"{tag}{ln.id}"); S.remove(d.name)
                    items.append((ln, S.add(Worm(f"E{ln.id}", d.vol, geom.CH_W, ops.lane_slices(d.rect, ln.odd)))))
                fin = last and (lanes is self.odd_l or not self.odd_l)
                S.step(changed=[w.name for _, w in items]); ops.egress(S, items, self.blob, final=fin)
                cmds.append(self.cmd(f"液指穿过文库 {lab} 列 → 排废空隙 → 底边孔" + ("抽走" if fin else "大滴")))
            self.flow(seg, title2, cmds)
        finger("H", ((11, 2, 10, 4), (11, 4, 21, 2)), ((15, 2, 10, 4), (3, 4, 21, 2)), f"{title} · Q06 半份1 穿过文库 → 排废")
        finger("Q", ((2, 2, 10, 4), (2, 4, 21, 2)), ((24, 2, 10, 4), (13, 4, 21, 2)), f"{title} · Q07 半份2 穿过文库 → 排废", last=True)

    # ── 流程主体 ──
    def build(self):
        if self.rigid:
            geom.magnetic_shape(round(4.20+1.70,2),allow_590_cross=True)
        S = self.S
        # 段 A
        self.load_samples("A", "A01 上样 1.60µL (S 孔切线块承接)", "S", 1.60, "L", "待测核酸")
        self.add_reagent("A", "A02 加 G4 cDNA合成反应液 0.40µL", "G4", 0.40)
        ops.park_all(S, self.ALL, "tec"); self.flow("A", "A03 液滴停 TEC 区", [self.cmd("液滴停 TEC 区")])
        self.mix("A", "A03 往复混匀", center_tec=True)
        self.thermal("A", "A04 cDNA合成 37℃10min→85℃5s→4℃", [(37, 600), (85, 5)], lid=105)
        self.split_waste("A", "A05 劈裂 留25%(0.50µL) 弃1.50µL", 0.50, "cDNA 弃液 1.50µL")
        self.add_reagent("A", "A06 加靶向扩增反应液 2.00µL(台面预混)", "AMP", 2.00)
        ops.park_all(S, self.ALL, "tec"); self.flow("A", "A07 液滴停 TEC 区", [self.cmd("液滴停 TEC 区")]); self.mix("A", "A07 往复混匀", center_tec=True)
        self.thermal("A", "A08 预变性 95℃3min", [(95, 180)], lid=105, final_4c=False)
        self.thermal("A", "A09 循环 95℃30s/60℃60s/72℃45s ×30", [(95, 30), (60, 60), (72, 45)], repeats=30, lid=105, final_4c=False, park=False)
        self.thermal("A", "A10 终延伸 72℃1min→4℃", [(72, 60)], lid=105, park=False)
        # 刷新点 1
        self.add_reagent("R1", "R01 加 G14 磁珠 2.50µL (1:1)", "G14", 2.50)
        self.bead_capture("R1", "R02-R03 混匀结合5min → 移至磁铁 → 磁吸", mix_cycles=BEAD_BIND_MIX['R1'], mix_span=19)
        self.supernatant_off("R1", "R04 液相移离磁珠 → 排废全部5.00µL（不保留计划液相）")
        self.elute_offchip("R1", "R05 纯化1第1次洗脱：溶解液 5.00µL → 产物离片", "G15", 5.00, 5.00, 1, final=False)
        self.elute_offchip("R1", "R06 纯化1第2次洗脱：溶解液 5.00µL → 产物离片", "G15", 5.00, 5.00, 2, final=True)
        # 两次洗脱产物下片 Qubit/手工均一化；以 1.30µL/道回片开始段 B。
        self.load_samples("B", "B01 均一化后产物 1.30µL 回片 (S 孔切线块承接)", "S", 1.30, "L", "均一化产物")
        self.add_reagent("B", "B02 加 Rapid End Repair ReadyMix 0.70µL", "G8", 0.70)
        ops.park_all(S, self.ALL, "tec"); self.flow("B", "B03 液滴停 TEC 区", [self.cmd("液滴停 TEC 区")]); self.mix("B", "B03 往复混匀", center_tec=True)
        self.thermal("B", "B04 末端修复加A 37℃4min→4℃", [(37, 240)], lid=105)
        ops.load_holes(S, self.ALL, "B", 1.00, "K", manual=True)
        c1 = self.loading_cmd(dict(hole="B孔×16", reagent="同配比条形码接头预混液（保留G11 0.20 + G15 0.40）", volume_ul=1.00,
                                   per_lane_ul=1.00, lanes="A1-A16（各自barcode，加1.00µL，分弃0.40µL，保留0.60µL）", batch=1, batches=1))
        S.step(geom.HOLD_MS,changed=[])
        specs=[]
        for ln in self.ALL:
            d=S.get(f'K{ln.id}');left,right=rigid.split_x(d,3,2)
            specs.append((d,(left,right),(.60,.40),(d.name,f'BKWA{ln.id}')))
        rigid.split_many(S,specs)
        c2=self.cmd('各道1.00µL接头液原位分成0.60µL保留、0.40µL弃液，逐格拉开')
        rigid.egress_pieces(S,[(ln,S.get(f'BKWA{ln.id}')) for ln in self.ALL],self.blob)
        c3=self.cmd('接头多加的0.40µL/道经废液通道排出（不走公共进液通道）')
        ops.merge_all(S,self.ALL,'K');c4=self.cmd('全部保留的0.60µL接头液就绪后与各道产物同步合并')
        self.flow('B','B05 接头预混液加1.00µL，分弃0.40µL，投入0.60µL（同配比）',[c1,c2,c3,c4])
        self.mix("B", "B06 往复混匀")
        self.add_reagent("B", "B07 加连接反应预混液 1.40µL (Buffer 1.00 + Ligase 0.40)", "LIG", 1.40)
        ops.park_all(S, self.ALL, "tec"); self.flow("B", "B08 液滴停 TEC 区", [self.cmd("液滴停 TEC 区")]); self.mix("B", "B08 往复混匀", center_tec=True)
        self.thermal("B", "B09 连接反应 25℃10min→4℃", [(25, 600)], lid=75)
        self.add_reagent("B", "B10 加 EDTA 终止液 0.20µL", "EDTA", 0.20)
        ops.move_rects(S,{ln.name:ln.X0 for ln in self.ALL})
        ops.mix_all(S,self.ALL,20,full_region=True)
        termination=self.cmd('B10 EDTA终止后轻柔往复混匀20轮（片上移液不等同枪头吹打）')
        previous=self.segs['B'][-1].cmds[-1];termination.pre=(previous,'FS')
        self.segs['B'][-1].cmds.append(termination)
        # 刷新点 2
        self.add_reagent("C", "C01 加磁珠(0.4××4.20µL) 1.70µL", "G16", 1.70)
        self.bead_capture("C", "C02-C03 混匀孵育3min → 移至磁铁 → 磁吸", mix_cycles=BEAD_BIND_MIX['C'], mix_span=19)
        self.supernatant_off("C", "C04 液相移离磁珠 → 排废全部5.90µL（不保留计划液相）")
        self.elute_offchip("C", "C05 纯化2第1次洗脱：EB 3.00µL → 成品离片", "G18", 3.00, 3.00, 1, final=False)
        self.elute_offchip("C", "C06 纯化2第2次洗脱：EB 3.00µL → 成品离片并汇聚 pooling", "G18", 3.00, 3.00, 2, final=True)
        return self.segs
