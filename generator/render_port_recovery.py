"""Render released native masks over CSV holes; never simulate liquid removal."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as E

from PIL import Image, ImageDraw, ImageFont
from waste_recovery import boxes


def render(folder,out):
    source=folder/'PTseq_16_A.elab';sha=hashlib.sha256(source.read_bytes()).hexdigest()
    report=json.loads((folder/'孔旁回收排废局部校验_20261018.json').read_text())
    assert sha==report['source_sha256']
    flows={f.get('ID'):f for f in E.parse(source).getroot()}
    with (folder/'ChipArea_192_256_96_SIRO.csv').open(encoding='utf-8-sig') as f:
        features=list(csv.DictReader(f))
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',15)
    titlefont=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',20)
    for case in report['s_cases']+report['f_cases']:
        is_s=case in report['s_cases'];flow=flows[str(case['flow_id'])];cmd=flow[-1]
        start=0 if is_s else case['preserved_prefix_frames']-1
        frames=list(cmd)[start:]
        if is_s:
            window=(8,21,48,43);initial=case['initial_rect']
        else:
            initial=case['initial_rects'][0];cx=initial[0]+initial[2]/2
            window=(cx-12,168,cx+12,194)
        xmin,ymin,xmax,ymax=window;scale=8
        tw=max(300,int((xmax-xmin)*scale)+24);th=int((ymax-ymin)*scale)+83
        cols=4;img=Image.new('RGB',(cols*tw,70+((len(frames)+cols-1)//cols)*th),'white')
        draw=ImageDraw.Draw(img)
        draw.text((10,10),f"{case['section']}  exact native XML / hole overlap aspiration",fill='black',font=titlefont)
        draw.text((10,39),'Green = electrode mask, not measured liquid. Circles = CSV holes.',fill='black',font=font)
        for i,relay in enumerate(frames):
            ox=(i%cols)*tw+12;oy=70+(i//cols)*th+36
            def b(rc):
                x,y,w,h=rc
                return (ox+(x-xmin)*scale,oy+(y-ymin)*scale,ox+(x+w-xmin)*scale,oy+(y+h-ymin)*scale)
            draw.text((ox,oy-28),f"frame {start+i+1} / {relay.get('Duration')} ms",fill='black',font=font)
            for r in features:
                if r['Type'] not in ('Rec','TEC'):continue
                rc=tuple(float(r[k]) for k in ('StartX','StartY','Width','Height'))
                if xmin<=rc[0] and rc[0]+rc[2]<=xmax and ymin<=rc[1] and rc[1]+rc[3]<=ymax:
                    draw.rectangle(b(rc),fill='#f3e9f3',outline='black')
            active=[rc for rc in boxes(relay.get('RelayCode','')) if xmin<=rc[0] and rc[0]+rc[2]<=xmax and ymin<=rc[1] and rc[1]+rc[3]<=ymax]
            for rc in active:draw.rectangle(b(rc),fill='#6cdb5e',outline='#42ad35')
            for x in range(int(xmin),int(xmax)+1):
                draw.line((b((x,ymin,0,0))[0],oy,b((x,ymin,0,0))[0],oy+(ymax-ymin)*scale),fill='#cccccc')
            for y in range(int(ymin),int(ymax)+1):
                draw.line((ox,b((xmin,y,0,0))[1],ox+(xmax-xmin)*scale,b((xmin,y,0,0))[1]),fill='#cccccc')
            if not is_s:draw.line((ox,oy+(192-ymin)*scale,ox+(xmax-xmin)*scale,oy+(192-ymin)*scale),fill='black',width=2)
            for r in features:
                if r['Type'] not in ('ReagentWell','Magnet'):continue
                rc=tuple(float(r[k]) for k in ('StartX','StartY','Width','Height'))
                if xmin<=rc[0] and rc[0]+rc[2]<=xmax and ymin<=rc[1] and rc[1]+rc[3]<=ymax:
                    draw.ellipse(b(rc),outline='#1439a5' if r['Type']=='ReagentWell' else 'black',width=2,
                                 fill='black' if r['Type']=='Magnet' else None)
            first=[rc for rc in active if (rc[1]<33 if is_s else True)]
            text=' + '.join(f'{w}x{h}@{x},{y}' for x,y,w,h in first) or 'recovery OFF'
            draw.text((ox,oy+(ymax-ymin)*scale+7),text,fill='black',font=font)
        target=out/f"{case['section']}_all_frames.png";img.save(target);print(target)
    # All F ports, including their neighbors, in every final native frame.
    img=Image.new('RGB',(1060,8*126+45),'white');draw=ImageDraw.Draw(img)
    draw.text((12,8),'All F endpoints / exact native masks and CSV holes',fill='black',font=titlefont)
    for i,case in enumerate(report['f_cases']):
        oy=45+i*126
        draw.text((4,oy+3),case['section'],fill='black',font=font)
        def bb(rc):
            x,y,w,h=rc;return(40+x*3.9,oy+(y-168)*4,40+(x+w)*3.9,oy+(y+h-168)*4)
        cmd=flows[str(case['flow_id'])][-1]
        for rc in boxes(cmd[-1].get('RelayCode','')):
            if rc[1]>=168:draw.rectangle(bb(rc),fill='#6cdb5e',outline='#42ad35')
        draw.line((40,oy+96,1039,oy+96),fill='black',width=2)
        for r in features:
            if r['Type']=='ReagentWell' and float(r['StartY'])>185:
                rc=tuple(float(r[k]) for k in ('StartX','StartY','Width','Height'))
                draw.ellipse(bb(rc),outline='#1439a5',width=2)
    target=out/'F_all_ports_final.png';img.save(target);print(target)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
    print('SOURCE_SHA256',sha)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--out',type=Path)
    a=p.parse_args();out=a.out or Path(tempfile.mkdtemp(prefix='tft-port-overlap-'))
    out.mkdir(parents=True,exist_ok=True);render(a.folder,out)
