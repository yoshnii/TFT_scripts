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
    workflow_name=escape(rows[0]['script']) if rows else 'A_cDNA_PCR'
    html = ('<!doctype html><meta charset="utf-8"><title>单列手工加液流程组</title>'
            '<style>body{font:15px/1.65 system-ui;margin:24px;background:white;color:black}'
            'table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:5px 9px}'
            'p{max-width:1000px}</style><h1>现有流程文件内的手工加液流程组</h1>'
            '<p>main 只引用一套完整方案 '+workflow_name+'；A、R1、B、C 的所有流程组均在该文件内。'
            '同级脚本文件预留给不同列或整片等完整方案版本，不再用来拆分生化阶段。'
            '流程组就是 ASI96 中“样本1ul”那种包含若干指令的组，不是新的流程文件或独立工程。'
            '承接电极上电后该组结束，手工加液完成后再运行后续组。'
            '公共试剂孔位和逐孔体积见下表；B02为E1单孔11.2µL，B10为E1单孔3.2µL，其他配送孔位保持原版。'
            '孔下先以紧凑母框承接，保持形状整体移到顶部后，按母框既有行列逐级对半切分。'
            'B02仍由E1一次加入11.20µL，顶部按长尺寸对半切成每道一份0.70µL完整矩形；'
            'B10也先在顶部等分。需要转形时与平移分开，形变终态驻留4秒；'
            '先下排后上排进入通道。顶部等分后保持小液滴形状穿过通道，'
            '进入反应区后才单独逐格调整到融合高度。顶部小滴先在X≥10处下降到Y≥10，再进入左侧进液空隙，'
            '与左上角大孔至少保持4格。洗脱液由各道S孔直接加入。'
            '所有液滴使用完整矩形；加液、运行和取液统一距所有孔至少一格，常规安全带从3格高扩至5格高，优先采用紧凑矩形（例如1.60µL为8×4）。'
            '仅5.00/5.90µL可跨中线但不越出本反应区；5.90µL实际液量118格，承接框120格，多开2格不代表额外加液。'
            'B05每道加入1.20µL同配比接头预混液，对半弃去0.60µL后投入0.60µL；不是补水稀释。'
            '底部排废与S孔产物回收仅在实际手工吸液点结束流程组，'
            '孔下废液框与其他常驻液滴一起保持上电；吸出后手工运行下一组，才取消废液电极。'
            '详细停靠顺序见同目录手工吸液顺序.csv；普通移动、混匀、温控和 PCR 循环不拆开。</p>'
            '<p>每个承接指令只有一帧静态电极和 1000 ms Duration，其他常驻液滴也包含在该帧。'
            '1000 ms 是承接帧时长，不是人工加液的时间限制。'
            '需使用对当前流程组的调试运行模式；从 main 开始整工程自动运行并不会'
            '因为这些流程组边界自动等待人工。软件需求说明书说明调试结束保持最后电极状态，'
            '实际硬件的持续保持行为仍需空载确认。</p>'
            '<p>温控设定、循环次数和试剂体积不变。结合孵育补入纯化1 300s、纯化2 180s；'
            '洗脱孵育为300s/180s；结合磁捕获180s/120s，洗脱后磁捕获120s/120s。'
            '这些磁铁升降为外部控制时序，不是软件电机指令，不新增磁控确认停靠。'
            '液相吸出后磁珠由磁铁保持位置，不当作液滴自行搬运。两次产物吸出后，'
            'R06/C06分别经S孔另加3.00µL/道载液（16道共48µL），覆盖磁珠后降磁铁，'
            'R06全区混匀保持原计时，C06仅往返两轮再随载液排废；这48µL不是第三次洗脱产物，不计入回收文库。'
            '汇液采用同高矩形逐格接触，合并后的矩形保持形状逐格复位，不从L形瞬间重塑。'
            '底部转运在Y172，原形小框按完整行列自然拼成矩形，下边固定Y188。'
            'A05各孔全部就绪后统一吸液，具体孔位和体积以手工吸液顺序.csv为准。R04每道废液分成长边近半的完整小矩形，'
            '按孔整批流水线调度，不按四滴一批启停。各排废步骤按整步统一分配F1–F4：每批仍拼成原有紧凑矩形，'
            '跨批保留在不同孔，四孔放不下下一整批时才停靠吸液；同次停靠先远孔后近孔，后送的小滴不经过已停靠的汇液框。'
            'R04各道液相同步平移、同步整形成完整矩形。各排废步骤的底部收集均为流水线：'
            '各道切下的小块同时横移进废液通道、整列下行；后一排不等前一排停靠完即可沿底部运输带跟进排队；'
            '本批小滴全部到达底部运输带后即切下一批；F1汇液框位于通道出口下方，该孔仍逐排放行。'
            'C06为程序最后一步：48µL排珠载液一次装入F1–F4后统一吸液，吸液时反应区内不留液体。'
            '所有收集框一起保持上电，等待手工吸液。'
            '孔圆心位于矩形下边中央，不再位于角上；'
            '具体孔位按流程组名称及手工吸液顺序.csv操作。'
            'CSV孔上缘为189.5格，电极下边采用最接近且不侵入孔的整格189，'
            '仍有0.5格几何量化间隙，实际接液接触需上机确认。高压指令总时长'
            '按秒对齐，不足一秒的尾段延长最后电极帧保持，避免合并预览出现无电极空段。'
            '人工等待仅在手工加液或吸液边界，不对普通移液、混匀或温控新增人工等待。</p><table><thead><tr>'
            + ''.join(f'<th>{escape(h)}</th>' for h in headers)
            + '</tr></thead><tbody>'+table+'</tbody></table>')
    with open(os.path.join(out, '手工加液运行.html'), 'w', encoding='utf-8') as fh:
        fh.write(html)
