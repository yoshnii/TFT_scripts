"""v3 构建: python3 build.py [--out DIR] [--cols 6]"""
import argparse, os, sys, time
from pathlib import Path
import xml.etree.ElementTree as ET
import geom, protocol
from project_structure import prepare_scripts
from manual_sections import write_loading_order, write_aspiration_order
ap = argparse.ArgumentParser(); ap.add_argument("--out", default="out/PTseq_SIRO96_v3"); ap.add_argument("--cols", type=int, default=6)
ap.add_argument('--script-name', default='PTseq_16_A', choices=['A_cDNA_PCR', 'PTseq_16_A'])
ap.add_argument('--rotation',type=int,choices=(0,180),default=None,
                help='Physical publication orientation; when omitted, retain the existing project orientation.')
a = ap.parse_args()
from electrode_address_compensation import address_rotation
address_compensation=address_rotation(a.out)
if a.rotation is None:
    manifests=list(Path(a.out).glob('*.elabprj'))
    a.rotation=180 if any(ET.parse(p).getroot().get('ChipModel','').endswith('_R180') for p in manifests) else 0
t0 = time.time()
P = protocol.Protocol(tuple(range(1, a.cols + 1))); segs = P.build()
CHIP = "ChipArea_192_256_96_SIRO"; os.makedirs(a.out, exist_ok=True); prj = os.path.basename(a.out.rstrip("/"))
scripts, rows, padded = prepare_scripts(segs, CHIP, a.cols, a.script_name)
if a.cols == 1:
    print(f"高压尾帧保持补齐：{len(padded)} 个指令，消除按整秒调度的空段。")
    write_loading_order(a.out, rows)
    write_aspiration_order(a.out, scripts)
    print(f"手工加液：{len(rows)} 个承接帧结束现有流程组；全部 A/R1/B/C 步骤归入 {scripts[0].name}。")
for s in scripts:
    open(f"{a.out}/{s.name}.elab", "w", encoding="utf-8").write(s.xml())
    open(f"{a.out}/{s.name}.elab.layout", "w", encoding="utf-8").write(s.layout_xml())
mf = "\n".join(f'    <Flow ID="{i+1}" Name="{s.name}" Include="{s.name}.elab" Repeats="1" Timeout="{sum(f.timeout*f.repeats for f in s.flows)}" />' for i, s in enumerate(scripts))
open(f"{a.out}/main.elab", "w", encoding="utf-8").write(f'<Script Name="main" Version="v0.0.0" Type="3" ChipsetType="{CHIP}" ActiveTec="1">\n{mf}\n</Script>\n')
open(f"{a.out}/main.elab.layout", "w", encoding="utf-8").write('<Script Name="main" Version="v0.0.0.1">\n' + "\n".join(f' <Flow Type="1" Position="74,{45+100*i}" Name="{s.name}" ID="{i+1}"/>' for i, s in enumerate(scripts)) + "\n</Script>\n")
files = "\n".join(f'    <file include="{n}" BuildMd5=""/>' for n in ["main"] + [s.name for s in scripts])
open(f"{a.out}/{prj}.elabprj", "w", encoding="utf-8").write(f'<?xml version="1.0" encoding="UTF-8"?>\n<project Target="{prj}" ActiveTec="1" Smart8Dir="" Description="" Instrument="3" TargetVersion="v0.0.0.1" ChipModel="{CHIP}" InstrumentName="TFT" DbName="">\n{files}\n</project>\n')
if a.rotation==180:
    from rotate_project_180 import write_publication
    write_publication(Path(a.out))
    print('发布方向：布局与全部步骤电极整体180°；温控/外部磁控身份不变。')
    if address_compensation==180:
        from electrode_address_compensation import write_publication as write_addresses
        write_addresses(Path(a.out),fresh_layout_output=True)
        print('寻址补偿：保持R180布局；全部电极指令另补偿180°（设备映射待实机确认）。')
nrel = sum(len(c.relays) for s in scripts for f in s.flows for c in f.cmds); tot = sum(f.timeout * f.repeats for s in scripts for f in s.flows)
sz = sum(os.path.getsize(f"{a.out}/{s.name}.elab") for s in scripts) / 1e6
print(f"{a.out}: {len(scripts)} 文件 / {sum(len(s.flows) for s in scripts)} Flow / {sum(len(f.cmds) for s in scripts for f in s.flows)} 指令 / {nrel} RelayItem / {sz:.1f} MB / 标称 {tot/3600:.2f} h / 生成耗时 {time.time()-t0:.0f}s")
