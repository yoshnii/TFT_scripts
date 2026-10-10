"""工程文件表示完整方案；单列方案的全部实验阶段属于同一个脚本。"""
from model import Script
from manual_sections import split_sections, align_high_voltage_timing


def prepare_scripts(segs, chipset, cols, workflow_name='PTseq_16_A'):
    names = {'A': 'A_cDNA_PCR', 'R1': 'R1_purify', 'Q': 'Q_quant',
             'B': 'B_ligation', 'C': 'C_purify2'}
    # 先按原有阶段分配编号和手工停靠点，避免层级调整改变既有编号。
    stages = [(seg, Script(names[seg], flows, chipset, 1000*(i+1)))
              for i, (seg, flows) in enumerate(segs.items())]
    scripts = [s for _, s in stages]
    rows, padded = [], []
    if cols == 1:
        rows = split_sections(scripts)
        padded = align_high_voltage_timing(scripts)
    for seg, script in stages:
        for flow in script.flows:
            flow.segment = seg  # 动画仍区分生化阶段，不作为并列工程文件。
    if cols == 1:
        scripts = [Script.combine(workflow_name, scripts)]
        for row in rows:
            row['script'] = row['next_script'] = scripts[0].name
    return scripts, rows, padded
