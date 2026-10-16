"""工程文件表示完整方案；单列方案的全部实验阶段属于同一个脚本。"""
from model import Script
from manual_sections import split_sections, align_high_voltage_timing
import re


def number_sections(scripts, rows):
    """只改显示名，不改变Flow/Command ID、循环或手工边界。"""
    counters={};names={}
    for script in scripts:
        for flow in script.flows:
            letter=flow.name[0]
            assert letter in 'ARBC'
            counters[letter]=counters.get(letter,0)+1
            flow.original_name=flow.name
            body=re.sub(r'^[ARBC]\d+(?:-[A-Z]?\d+)?\s+','',flow.name)
            flow.name=f'{letter}{counters[letter]:02d} {body}'
            names[(script.name,flow.id)]=flow.name
    for row in rows:
        row['section_name']=names[(row['script'],row['section_id'])]
        row['next_section_name']=names[(row['next_script'],row['next_section_id'])]


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
        number_sections(scripts, rows)
    for seg, script in stages:
        for flow in script.flows:
            flow.segment = seg  # 动画仍区分生化阶段，不作为并列工程文件。
    if cols == 1:
        scripts = [Script.combine(workflow_name, scripts)]
        for row in rows:
            row['script'] = row['next_script'] = scripts[0].name
    return scripts, rows, padded
