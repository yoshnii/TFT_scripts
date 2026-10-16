"""仅在实际手工加液/吸液边界结束 section，普通运动保持连续。"""
import csv
import os
from xml.sax.saxutils import escape
from model import Flow, Cmd, chain


def split_aspiration_commands(scripts):
    """原指令保留编号；仅在手工吸液帧后切开，追加编号使用60000段。"""
    next_id = 60000
    for script in scripts:
        for flow in script.flows:
            if not any(getattr(c, 'aspiration_gates', ()) for c in flow.cmds):
                continue
            assert flow.repeats == 1 and not flow.sync
            commands = []
            carry = []
            for c in flow.cmds:
                events = dict(getattr(c, 'aspiration_gates', ()))
                if not events:
                    if carry and carry[0].codes:
                        assert c.comp=='HighVoltage'
                        if hasattr(c,'manual_load'):
                            assert set(carry[0].codes)<=set(c.relays[0].codes)
                        elif carry[0].codes!=c.relays[0].codes:
                            c.relays=carry+c.relays
                    carry=[]
                    commands.append(c)
                    continue
                assert not carry,'吸液后保持帧必须先衔接下一条实际动作'
                start, part = 0, 0
                for end in sorted(set(events) | {len(c.relays)-1}):
                    relays = c.relays[start:end+1]
                    if not relays: continue
                    gate = events.get(end)
                    if not gate and part and len(relays)==1 and relays[0].dur<=1000:
                        # 吸液后的单帧驻留状态随下一项实际动作衔接，不单独生成60000段空续行。
                        carry=relays
                        start=end+1
                        continue
                    label = (f" · 【手工吸液・组结束】{gate['hole']} {gate['volume_ul']:.3f}µL · 吸出后运行下一组"
                             if gate else ' · 手工吸液已完成后的续行')
                    chunk = Cmd(c.name + label, c.comp, c.action,
                                max(1, round(sum(r.dur for r in relays)/1000)), c.params, relays)
                    chunk.id = c.id if part == 0 else next_id
                    if part: next_id += 1
                    chunk.original_command_id = c.id
                    if gate: chunk.manual_aspiration = gate
                    commands.append(chunk)
                    start, part = end+1, part+1
            flow.cmds = chain(commands)
            if carry:flow.carry_relays=carry
    ordered=[flow for script in scripts for flow in script.flows]
    for i,flow in enumerate(ordered[:-1]):
        carry=getattr(flow,'carry_relays',[])
        if not carry or not carry[0].codes:continue
        nxt=ordered[i+1].cmds[0]
        assert nxt.comp=='HighVoltage'
        if hasattr(nxt,'manual_load'):
            assert set(carry[0].codes)<=set(nxt.relays[0].codes)
        elif carry[0].codes!=nxt.relays[0].codes:
            nxt.relays=carry+nxt.relays


def align_high_voltage_timing(scripts):
    """按秒向上取整的调度尾段，以最后电极状态补满，不留无指令空段。"""
    changed = []
    for script in scripts:
        for flow in script.flows:
            for c in flow.cmds:
                if c.comp != 'HighVoltage' or not c.relays:
                    continue
                total = sum(r.dur for r in c.relays)
                padding = (-total) % 1000
                if padding:
                    c.relays[-1].dur += padding
                    changed.append((script.name, c.id, padding))
                c.timeout = (total + padding) // 1000
    return changed


def split_sections(scripts):
    # Script 已赋予原始编号；新增 section 使用独立编号段，不重编号已有指令。
    split_aspiration_commands(scripts)
    next_id, next_asp_id, rows, all_sections = 50000, 51000, [], []
    for script in scripts:
        sections = []
        for flow in script.flows:
            gates = [i for i, c in enumerate(flow.cmds)
                     if hasattr(c, 'manual_load') or hasattr(c, 'manual_aspiration')]
            if not gates:
                sections.append(flow)
                continue
            assert flow.repeats == 1 and not flow.sync
            start, parts, previous_kind = 0, [], None
            for stop in gates + [len(flow.cmds)-1]:
                if stop < start:
                    continue
                cmds = flow.cmds[start:stop+1]
                gate = getattr(cmds[-1], 'manual_load', None)
                asp = getattr(cmds[-1], 'manual_aspiration', None)
                suffix = ('加液承接（上电后手工加液）' if gate else
                          f"{asp['hole']} 待手工吸液（保持全部液滴上电）" if asp else
                          '吸液完成后的续行' if previous_kind == 'asp' else '加液后分液、移液及融合')
                batch = (f" · 第{gate['batch']}/{gate['batches']}批" if gate and gate.get('batches',1)>1 else '')
                part = Flow(flow.name + f' · {suffix}' + batch, cmds)
                if not parts: part.id = flow.id
                elif previous_kind == 'load':
                    part.id = next_id; next_id += 1
                else:
                    part.id = next_asp_id; next_asp_id += 1
                parts.append(part)
                if gate:
                    assert len(cmds[-1].relays) == 1
                    rows.append(dict(script=script.name, section_id=part.id,
                                     section_name=part.name, command_id=cmds[-1].id,
                                     **gate))
                start = stop + 1
                previous_kind = 'load' if gate else 'asp' if asp else None
            sections.extend(parts)
        script.flows = sections
        all_sections.extend((script.name, f) for f in sections)
    for row in rows:
        i = next(i for i, (name, f) in enumerate(all_sections)
                 if name == row['script'] and f.id == row['section_id'])
        row['next_script'], nxt = all_sections[i+1]
        row['next_section_id'], row['next_section_name'] = nxt.id, nxt.name
    return rows


def write_aspiration_order(out, scripts):
    sections = [(s.name, f) for s in scripts for f in s.flows]
    rows = []
    for i, (name, f) in enumerate(sections):
        c = f.cmds[-1]
        gate = getattr(c, 'manual_aspiration', None)
        if not gate: continue
        next_name, next_flow = sections[i+1] if i+1<len(sections) else ('',None)
        rows.append([name+'.elab', f.id, f.name, c.id, len(c.relays), gate['hole'],
                     gate['volume_ul'], ';'.join(geom_code(r) for r in gate.get('rects',[gate.get('rect')]) if r),
                     next_name+'.elab' if next_name else '',next_flow.id if next_flow else '',
                     next_flow.name if next_flow else '工程结束（最后取液后无需空续行）'])
    headers = ['流程文件','吸液流程组编号','吸液流程组名称','末尾指令','末尾帧号',
               '吸液孔','本次总液体µL（16道合计；排珠载液不计文库）','待吸液电极','下一流程组所在文件','下一个流程组编号','下一个流程组名称']
    with open(os.path.join(out,'手工吸液顺序.csv'),'w',encoding='utf-8-sig',newline='') as fh:
        writer = csv.writer(fh); writer.writerow(headers); writer.writerows(rows)
    print(f'手工吸液：{len(rows)} 个真实吸液停靠点；不在当前组自动移除电极。')
    return rows


def geom_code(rect):
    import geom
    return geom.emit(rect)


def write_loading_order(out, rows):
    headers = ['流程文件', '加液流程组编号', '加液流程组名称', '最后承接指令',
               '加液孔', '试剂', '本次µL（S/B孔为每道）', '各边缘孔µL', '泳道', '批次',
               '下一流程组所在文件', '下一个流程组编号', '下一个流程组名称']
    values = [[r['script']+'.elab', r['section_id'], r['section_name'], r['command_id'],
               r['hole'], r['reagent'], r['volume_ul'],r.get('port_volumes',''), r['lanes'],
               f"{r.get('batch',1)}/{r.get('batches',1)}", r['next_script']+'.elab',
               r['next_section_id'], r['next_section_name']] for r in rows]
    with open(os.path.join(out, '手工加液顺序.csv'), 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(values)
    table = ''.join('<tr>'+''.join(f'<td>{escape(str(v))}</td>' for v in row)+'</tr>'
                    for row in values)
    workflow_name=escape(rows[0]['script']) if rows else 'PTseq_16_A'
    html = ('<!doctype html><meta charset="utf-8"><title>单列手工加液流程组</title>'
            '<style>body{font:15px/1.7 system-ui;background:white;color:black;max-width:1300px;margin:30px auto;padding:0 20px}'
            'table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:6px 9px}</style>'
            '<h1>单列10× · 手工加液顺序 · 2026-10-16</h1>'
            '<p>main只引用完整方案 '+workflow_name+'；所有section归入同一流程文件。'
            'A/R/B/C各自连续编号且不重复，Flow与Command的数值ID不因显示名变化而重编。'
            '边缘E孔或S/B孔承接框上电后，本section结束；加好液后再手工运行下一section。'
            '普通移液、混匀及温控不新增确认弹窗或停靠。</p>'
            '<p>母液先按最终子滴行列布局定形，全部在顶部拆成完整矩形子滴，'
            '子滴保持原形穿过4格通道；不在入口排队逐滴形变4秒。'
            'G4为2×4/0.40µL，AMP每道两滴4×5/1.00µL；'
            'G14每道五滴3×4/0.50µL（首次等五分再二分）；'
            '末修液每道一滴3×5/0.70µL，连接液每道两滴同样的0.70µL；'
            'EDTA为2×2/0.20µL，纯化2磁珠每道两滴3×6/0.85µL。'
            'G14、0.70µL与0.85µL子滴带有已审核承接余量；实际试剂量不因此增加。'
            'G16整高小滴只在孔左侧停靠，由文库靠近融合，避免穿越孔下。'
            '各道试剂全部就绪后再同时与各自文库融合；只对完整合并液滴作必要4秒定形。</p>'
            '<p>所有框完整；移动每帧一格/1000ms，孔边至少一格，液滴间至少三格并优先四格。'
            '5.00/5.90µL允许跨中线但不出本well；禁止经过其他列TEC。'
            'cDNA弃液在出口原位对半为0.75+0.75µL，保持3×5下行；底部使用F1/F3，避开近孔与远孔就绪区干涉。'
            '纯化1弃5.00µL采用五等份1.00µL；纯化2实际5.90µL用120格承接，再二分成8滴0.7375µL/3×5。'
            '120格不是6.00µL实际液体，C04仅对子滴量化采用明确例外。'
            '底部必要形变仅作用于汇齐的整排，不逐个改变小滴形状；最终矩形距F孔边至少一格。'
            '手工吸液顺序及各孔体积以同目录手工吸液顺序.csv为准。</p>'
            '<p>单帧1000ms是承接电极时长，不是手工加液时间限制；需调试运行当前section。'
            '从main自动运行整个工程并不因section边界自动等人。调试结束保持末电极状态仍需实机确认。'
            '混匀沿用单轮闭合轨迹+并行计时+共同结束组，不复制多轮轨迹；'
            '计时结束能否恰在整轮末停机尚未实测。只有PCR30轮关1秒/开1秒，其余液滴保持上电。</p>'
            '<p>温控与生化体积不变；磁铁升降仍为外部操作时序，没有电机到位反馈。'
            '磁铁下降重悬，全部液滴到右侧后升起捕获；洗脱液从S孔先覆盖珠团，再下降重悬。'
            '纯化1两次各5.00µL、纯化2两次各3.00µL的产物均由S孔取走；'
            '两次回收后另加3.00µL/道排珠载液，载珠排废，不是第三次洗脱文库。'
            '末次排珠只往返两轮。F孔吸液前保留所有废液电极，吸走后运行下一section才取消。</p>'
            '<p>本页由代码生成。离线几何、体积与依赖图通过不代表真实分液比例、微缩生化、'
            '温控或磁控已经验证；尤其等五分和电极承接余量需水跑。</p><table><thead><tr>'
            + ''.join(f'<th>{escape(h)}</th>' for h in headers)
            + '</tr></thead><tbody>'+table+'</tbody></table>')
    with open(os.path.join(out, '手工加液运行.html'), 'w', encoding='utf-8') as fh:
        fh.write(html)
